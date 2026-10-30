"""Siklus hidup media sementara (P2).

Aturan
- Media proyek (video sumber, preview, hasil render, musik, transkrip, rencana) disimpan SEMENTARA:
  kedaluwarsa = min(aktivitas_terakhir + MEDIA_RETENTION_HOURS, upload + MEDIA_MAX_HOURS),
  atau grace_until bila lebih lama (masa tenggang migrasi proyek lama).
- Hanya aksi pengguna yang memperpanjang (touch): upload, analisis ulang, render, edit animasi,
  cari klip, upload musik, unduh hasil. Polling status & memutar preview TIDAK.
- Metadata (projects, renders, akun, unit/guidebook) tetap di database setelah media dihapus.

Keamanan terhadap race condition
- Job aktif (antre/jalan/berhenti) = "lease" atas media proyeknya. Upload yang sedang ditulis dan file
  yang sedang diunduh memegang lease di tabel media_leases (kedaluwarsa sendiri bila prosesnya mati).
- Kuota unit direservasi secara atomik: baris unit dikunci (PostgreSQL row lock; SQLite kunci tulis)
  sehingga dua upload bersamaan tidak bisa sama-sama lolos melewati kuota.
- Penghapusan dua fase: (1) tandai `menghapus` secara atomik, (2) periksa ulang job aktif SESUDAH
  penandaan; bila ada, kembalikan ke `tersedia`. Pengajuan job (reserve) mengunci baris media
  (FOR UPDATE) dan menolak bila tidak `tersedia`, sehingga tidak ada job baru setelah langkah (1).
- Proyek tanpa baris project_media = proyek lama: tidak pernah dihapus otomatis (lihat migrate_legacy).
"""
import logging
import os
import shutil
import socket
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import and_, delete, func, insert, or_, select, text, update

from . import db
from .config import (DATA_DIR, DISK_MIN_FREE_MB, DISK_MIN_FREE_PCT, INCOMPLETE_UPLOAD_HOURS, MEDIA_LIFECYCLE,
                     MEDIA_MAX_HOURS, MEDIA_RETENTION_HOURS, MEDIA_WARN_HOURS, PROJECTS_DIR,
                     STORAGE_QUOTA_MB_DEFAULT, TEMP_FILE_GRACE_MINUTES, UPLOAD_TMP_DIR)

log = logging.getLogger("video-editing.lifecycle")

HOUR = 3600.0
TOUCH_MIN_INTERVAL = 60.0          # tulis last_activity paling sering 1x per menit
DELETING_STUCK_AFTER = 600.0       # `menghapus` lebih dari ini = proses hapus sempat terputus
# tempfile.tempdir = UPLOAD_TMP_DIR, jadi semua file sementara proses (bukan hanya upload) ada di sana.
# Hanya sisa yang jauh lebih tua dari job terpanjang (JOB_TIMEOUT_MAX) yang dianggap tertinggal.
TMP_ORPHAN_HOURS = 24.0
LEASE_TTL = {"upload": 600.0, "download": 300.0}  # diperpanjang berkala selama masih dipakai
LEASE_RENEW_EVERY = 60.0
_OWNER = f"{socket.gethostname()}-{os.getpid()}"[:80]
ACTIVE_JOB = ("antre", "jalan", "berhenti")
_SWEEP_LOCK_KEY = 727275


class MediaGone(Exception):
    """Media proyek sudah kedaluwarsa/dihapus (HTTP 410)."""


class StorageFull(Exception):
    """Ruang disk server atau kuota workspace tidak mencukupi (HTTP 507)."""


# ------------------------------------------------------------------ perhitungan waktu

def expires_at(row: dict) -> float:
    normal = min(row["last_activity"] + MEDIA_RETENTION_HOURS * HOUR, row["uploaded_at"] + MEDIA_MAX_HOURS * HOUR)
    return max(normal, row["grace_until"] or 0)


def _expired_clause(now: float):
    """Kondisi SQL setara expires_at(row) < now."""
    pm = db.project_media.c
    normal_expired = or_(pm.last_activity + MEDIA_RETENTION_HOURS * HOUR < now,
                         pm.uploaded_at + MEDIA_MAX_HOURS * HOUR < now)
    return and_(normal_expired, or_(pm.grace_until.is_(None), pm.grace_until < now))


def _get(conn, pid: str) -> Optional[dict]:
    return db.row(conn.execute(select(db.project_media).where(db.project_media.c.project_id == pid)).first())


def describe(row: Optional[dict], now: Optional[float] = None) -> dict:
    """Ringkasan untuk UI/API."""
    now = now or time.time()
    if row is None:
        return {"state": "lama", "lifecycle": MEDIA_LIFECYCLE, "expires_at": None, "remaining": None,
                "warning": False, "bytes": None}
    exp = expires_at(row)
    state = row["state"]
    if state == "tersedia" and exp <= now:
        state = "kedaluwarsa"  # tinggal menunggu pembersihan berikutnya
    remaining = max(0.0, exp - now) if state == "tersedia" else 0.0
    return {
        "state": state, "lifecycle": MEDIA_LIFECYCLE, "expires_at": exp,
        "hard_expires_at": max(row["uploaded_at"] + MEDIA_MAX_HOURS * HOUR, row["grace_until"] or 0),
        "last_activity": row["last_activity"], "uploaded_at": row["uploaded_at"], "remaining": remaining,
        "warning": state == "tersedia" and remaining < MEDIA_WARN_HOURS * HOUR,
        "bytes": row["bytes"], "deleted_at": row["deleted_at"], "delete_reason": row["delete_reason"],
    }


def info(pid: str, now: Optional[float] = None) -> dict:
    with db.engine().connect() as c:
        return describe(_get(c, pid), now)


def info_many(pids: list[str], now: Optional[float] = None) -> dict:
    if not pids:
        return {}
    with db.engine().connect() as c:
        rows = {r.project_id: db.row(r) for r in
                c.execute(select(db.project_media).where(db.project_media.c.project_id.in_(pids)))}
    return {pid: describe(rows.get(pid), now) for pid in pids}


# ------------------------------------------------------------------ registrasi & aktivitas

def register(pid: str, now: Optional[float] = None, conn=None) -> None:
    """Proyek baru masuk siklus media sementara."""
    now = now or time.time()
    values = dict(project_id=pid, uploaded_at=now, last_activity=now, state="tersedia", bytes=0,
                  state_changed=now)
    if conn is not None:
        conn.execute(insert(db.project_media).values(**values))
    else:
        with db.engine().begin() as c:
            c.execute(insert(db.project_media).values(**values))


def _is_available(row: Optional[dict], now: float) -> bool:
    if row is None:
        return True  # proyek lama: media masih ada, tidak diatur siklus
    return row["state"] == "tersedia" and expires_at(row) > now


def _lock_media(conn, pid: str) -> Optional[dict]:
    """Kunci baris media proyek sampai transaksi selesai, lalu kembalikan isinya.

    UPDATE tanpa perubahan: di PostgreSQL = row lock (setara FOR UPDATE); di SQLite = kunci tulis
    database. Harus jadi perintah PERTAMA di transaksi (SQLite: hindari upgrade kunci baca -> tulis).
    """
    pm = db.project_media.c
    conn.execute(update(db.project_media).where(pm.project_id == pid).values(state=pm.state))
    return _get(conn, pid)


def _lock_unit(conn, unit_id: str) -> None:
    """Serialkan keputusan kuota per unit (lihat _lock_media)."""
    conn.execute(update(db.units).where(db.units.c.id == unit_id).values(id=db.units.c.id))


def require_present(pid: str) -> None:
    """Untuk job yang sudah diterima: media belum dihapus. Kedaluwarsa saat job menunggu di antrean
    tidak menggagalkan job (job aktif = lease); media dibersihkan segera setelah job selesai."""
    with db.engine().connect() as c:
        row = _get(c, pid)
    if row is not None and row["state"] != "tersedia":
        raise MediaGone(pid)


def require_available(pid: str, now: Optional[float] = None) -> None:
    now = now or time.time()
    with db.engine().connect() as c:
        if not _is_available(_get(c, pid), now):
            raise MediaGone(pid)


def touch(pid: str, now: Optional[float] = None) -> bool:
    """Aksi pengguna: perpanjang masa simpan (dibatasi MEDIA_MAX_HOURS sejak upload lewat rumus)."""
    now = now or time.time()
    pm = db.project_media.c
    with db.engine().begin() as c:
        row = _get(c, pid)
        if not _is_available(row, now):
            if row is not None:
                raise MediaGone(pid)
            return False
        return bool(c.execute(update(db.project_media).where(
            pm.project_id == pid, pm.state == "tersedia",
            pm.last_activity < now - TOUCH_MIN_INTERVAL).values(last_activity=now)).rowcount)


@contextmanager
def reserve(pid: str, now: Optional[float] = None) -> Iterator:
    """Transaksi untuk mengajukan job atas media proyek.

    Mengunci baris media (FOR UPDATE di PostgreSQL; SQLite: penulisan serial) sehingga penghapusan
    tidak bisa menandai `menghapus` di tengah pengajuan. Menaikkan MediaGone bila tidak tersedia.
    Yield koneksi transaksi; masukkan job memakai koneksi ini (jobs.submit(conn=...)).
    """
    now = now or time.time()
    pm = db.project_media.c
    with db.engine().begin() as c:
        row = _lock_media(c, pid)
        if not _is_available(row, now):
            raise MediaGone(pid)
        if row is not None:
            c.execute(update(db.project_media).where(pm.project_id == pid).values(last_activity=now))
        yield c


# ------------------------------------------------------------------ ukuran & disk

def folder_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def refresh_bytes(pid: str) -> int:
    size = folder_bytes(PROJECTS_DIR / pid)
    with db.engine().begin() as c:
        c.execute(update(db.project_media).where(db.project_media.c.project_id == pid).values(bytes=size))
    return size


def disk_status() -> dict:
    usage = shutil.disk_usage(DATA_DIR)
    reserve_bytes = max(DISK_MIN_FREE_MB * 1024 * 1024, usage.total * DISK_MIN_FREE_PCT / 100)
    return {"total": usage.total, "used": usage.used, "free": usage.free, "reserve": reserve_bytes,
            "usable": max(0, usage.free - reserve_bytes)}


def ensure_disk(incoming: int = 0) -> None:
    """Tolak pekerjaan baru bila sisa disk (dikurangi cadangan) tidak cukup."""
    st = disk_status()
    if incoming > st["usable"] or st["usable"] <= 0:
        raise StorageFull("Ruang penyimpanan server sedang hampir penuh. Coba lagi nanti, atau hubungi admin.")


def _unit_usage(conn, unit_id: Optional[str], now: float, exclude_pid: Optional[str] = None) -> int:
    """Media tersimpan unit + reservasi upload yang masih berjalan.

    Satu perintah SQL = satu snapshot. Dua query terpisah bisa salah hitung di PostgreSQL (READ
    COMMITTED): commit_upload lain yang selesai di antaranya memindahkan ukuran dari reservasi ke
    media tersimpan, sehingga file itu tidak terhitung sama sekali.
    """
    pm, pr, ml = db.project_media.c, db.projects.c, db.media_leases.c
    stored = (select(func.coalesce(func.sum(pm.bytes), 0))
              .select_from(db.project_media.join(db.projects, pr.id == pm.project_id))
              .where(pr.unit_id == unit_id, pm.state.in_(("tersedia", "menghapus"))))
    if exclude_pid:
        stored = stored.where(pm.project_id != exclude_pid)
    reserved = select(func.coalesce(func.sum(ml.bytes), 0)).where(ml.unit_id == unit_id, ml.expires_at > now)
    return int(conn.execute(select(stored.scalar_subquery() + reserved.scalar_subquery())).scalar() or 0)


def unit_usage(unit_id: Optional[str], now: Optional[float] = None) -> int:
    with db.engine().connect() as c:
        return _unit_usage(c, unit_id, now or time.time())


def unit_row(unit_id: Optional[str]) -> Optional[dict]:
    if not unit_id:
        return None
    with db.engine().connect() as c:
        return db.row(c.execute(select(db.units).where(db.units.c.id == unit_id)).first())


def unit_quota_bytes(unit: Optional[dict]) -> int:
    """0 = tanpa batas."""
    mb = unit.get("storage_quota_mb") if unit else None
    mb = STORAGE_QUOTA_MB_DEFAULT if mb is None else mb
    return int(mb * 1024 * 1024)


def _quota_error(unit: dict, used: int, quota: int) -> StorageFull:
    return StorageFull(f"Kuota penyimpanan sementara unit {unit['name']} penuh "
                       f"({used / 1e6:.0f} dari {quota / 1e6:.0f} MB). Unduh hasil render lalu hapus "
                       "proyek yang sudah selesai, atau tunggu media lama kedaluwarsa.")


def _check_quota(conn, unit: Optional[dict], incoming: int, now: float, exclude_pid: Optional[str] = None) -> None:
    quota = unit_quota_bytes(unit)
    if not unit or quota <= 0:
        return
    used = _unit_usage(conn, unit["id"], now, exclude_pid)
    if used + incoming > quota:
        raise _quota_error(unit, used, quota)


def ensure_quota(unit: Optional[dict], incoming: int = 0) -> None:
    """Pemeriksaan cepat (tidak atomik), mis. sebelum isi upload diterima. Penegakan sebenarnya
    terjadi di acquire_lease / commit_size."""
    with db.engine().connect() as c:
        _check_quota(c, unit, incoming, time.time())


# ------------------------------------------------------------------ lease (upload & unduhan aktif)

def acquire_lease(pid: str, kind: str, nbytes: int = 0, unit: Optional[dict] = None,
                  now: Optional[float] = None, ttl: Optional[float] = None) -> str:
    """Pegang media proyek selama upload/unduhan. nbytes > 0 = reservasi kuota unit (atomik)."""
    now = now or time.time()
    lid = uuid.uuid4().hex[:16]
    with db.engine().begin() as c:
        if nbytes and unit:
            _lock_unit(c, unit["id"])  # upload lain di unit yang sama menunggu sampai transaksi ini selesai
        row = _lock_media(c, pid)
        if not _is_available(row, now):
            raise MediaGone(pid)
        if nbytes:
            _check_quota(c, unit, nbytes, now)
        c.execute(insert(db.media_leases).values(
            id=lid, project_id=pid, unit_id=unit["id"] if unit else None, kind=kind, bytes=max(0, nbytes),
            owner=_OWNER, created=now, expires_at=now + (ttl or LEASE_TTL[kind])))
    return lid


def renew_lease(lid: str, now: Optional[float] = None, ttl: Optional[float] = None) -> None:
    now = now or time.time()
    ml = db.media_leases.c
    with db.engine().begin() as c:
        kind = c.execute(select(ml.kind).where(ml.id == lid)).scalar()
        if kind:
            c.execute(update(db.media_leases).where(ml.id == lid).values(expires_at=now + (ttl or LEASE_TTL[kind])))


def release_lease(lid: str, collect: bool = True) -> None:
    """Lepas lease (dan reservasi kuotanya). Bila media sudah kedaluwarsa, langsung dibersihkan."""
    ml = db.media_leases.c
    with db.engine().begin() as c:
        pid = c.execute(select(ml.project_id).where(ml.id == lid)).scalar()
        c.execute(delete(db.media_leases).where(ml.id == lid))
    if pid and collect:
        collect_if_expired(pid)


def commit_upload(lid: str, pid: str) -> int:
    """Upload selesai: ukuran aktual menggantikan reservasi dalam satu transaksi."""
    size = folder_bytes(PROJECTS_DIR / pid)
    with db.engine().begin() as c:
        c.execute(update(db.project_media).where(db.project_media.c.project_id == pid).values(bytes=size))
        c.execute(delete(db.media_leases).where(db.media_leases.c.id == lid))
    return size


def mark_uploaded(pid: str, now: Optional[float] = None) -> None:
    """Upload bertahap selesai: masa simpan dihitung sejak file lengkap diterima, bukan sejak upload dimulai."""
    now = now or time.time()
    with db.engine().begin() as c:
        c.execute(update(db.project_media).where(db.project_media.c.project_id == pid).values(
            uploaded_at=now, last_activity=now))


def commit_size(pid: str, unit: Optional[dict], now: Optional[float] = None) -> int:
    """Catat ukuran aktual media yang ukurannya baru diketahui di akhir (unduhan dari link).
    StorageFull bila melewati kuota unit; pemanggil membuang file yang baru ditambahkan."""
    now = now or time.time()
    size = folder_bytes(PROJECTS_DIR / pid)
    with db.engine().begin() as c:
        if unit:
            _lock_unit(c, unit["id"])
        _check_quota(c, unit, size, now, exclude_pid=pid)
        c.execute(update(db.project_media).where(db.project_media.c.project_id == pid).values(bytes=size))
    return size


def _expire_leases(now: float) -> int:
    """Lease milik proses yang mati: lepaskan proteksi & reservasi kuotanya."""
    with db.engine().begin() as c:
        return c.execute(delete(db.media_leases).where(db.media_leases.c.expires_at <= now)).rowcount


# ------------------------------------------------------------------ pembersihan

def _active_job_projects(conn, pids: Optional[list[str]] = None) -> set[str]:
    q = select(db.jobs.c.project_id).where(db.jobs.c.status.in_(ACTIVE_JOB)).distinct()
    if pids is not None:
        q = q.where(db.jobs.c.project_id.in_(pids))
    return {r[0] for r in conn.execute(q)}


def _busy_projects(conn, pids: Optional[list[str]] = None, now: Optional[float] = None) -> set[str]:
    """Proyek yang medianya sedang dipakai: job aktif (termasuk yang menunggu dipulihkan) atau lease hidup."""
    now = now or time.time()
    ml = db.media_leases.c
    q = select(ml.project_id).where(ml.expires_at > now).distinct()
    if pids is not None:
        q = q.where(ml.project_id.in_(pids))
    return _active_job_projects(conn, pids) | {r[0] for r in conn.execute(q)}


def collect_if_expired(pid: str, now: Optional[float] = None) -> bool:
    """Dipanggil setelah job/lease terakhir selesai: media yang sudah lewat masa simpan langsung dihapus,
    tidak menunggu putaran pembersihan berikutnya."""
    if not MEDIA_LIFECYCLE:
        return False
    now = now or time.time()
    with db.engine().connect() as c:
        row = _get(c, pid)
        if row is None or row["state"] != "tersedia" or expires_at(row) > now:
            return False
        if pid in _busy_projects(c, [pid], now):
            return False
    return delete_media(pid, "kedaluwarsa", now)


def _remove_media_files(pid: str) -> None:
    shutil.rmtree(PROJECTS_DIR / pid, ignore_errors=True)


def delete_media(pid: str, reason: str, now: Optional[float] = None) -> bool:
    """Hapus media satu proyek secara aman (dua fase). True bila benar-benar dihapus."""
    now = now or time.time()
    pm = db.project_media.c
    with db.engine().begin() as c:  # fase 1: klaim atomik
        claimed = c.execute(update(db.project_media).where(pm.project_id == pid, pm.state == "tersedia").values(
            state="menghapus", state_changed=now)).rowcount
    if not claimed:
        return False
    return _finish_delete(pid, reason, now)


def _finish_delete(pid: str, reason: str, now: float) -> bool:
    pm = db.project_media.c
    with db.engine().connect() as c:  # fase 2: periksa ulang lease SESUDAH klaim
        busy = pid in _busy_projects(c, [pid], now)
    if busy:
        with db.engine().begin() as c:
            c.execute(update(db.project_media).where(pm.project_id == pid, pm.state == "menghapus").values(
                state="tersedia", state_changed=now))
        log.info("Media %s sedang dipakai job, penghapusan ditunda.", pid)
        return False
    _remove_media_files(pid)
    with db.engine().begin() as c:
        c.execute(update(db.project_media).where(pm.project_id == pid).values(
            state="dihapus", bytes=0, deleted_at=now, delete_reason=reason, state_changed=now))
    log.info("Media proyek %s dihapus (%s).", pid, reason)
    return True


def _expired_candidates(now: float) -> list[str]:
    pm = db.project_media.c
    with db.engine().connect() as c:
        expired = [r[0] for r in c.execute(select(pm.project_id).where(pm.state == "tersedia", _expired_clause(now)))]
        busy = _busy_projects(c, expired, now) if expired else set()
    return [pid for pid in expired if pid not in busy]


def _stuck_deleting(now: float) -> list[str]:
    pm = db.project_media.c
    with db.engine().connect() as c:
        return [r[0] for r in c.execute(select(pm.project_id).where(
            pm.state == "menghapus", pm.state_changed < now - DELETING_STUCK_AFTER))]


def _incomplete_uploads(now: float) -> list[str]:
    pr, pm = db.projects.c, db.project_media.c
    with db.engine().connect() as c:  # hanya proyek terkelola: proyek lama tidak pernah dihapus otomatis
        stale = [r[0] for r in c.execute(select(pr.id).select_from(
            db.projects.join(db.project_media, pm.project_id == pr.id)).where(
            pr.status.in_(("baru", "mengunggah")), pr.created < now - INCOMPLETE_UPLOAD_HOURS * HOUR))]
        busy = _busy_projects(c, stale, now) if stale else set()
    return [pid for pid in stale if pid not in busy]


def _old_enough(path: Path, now: float) -> bool:
    try:
        return path.lstat().st_mtime < now - TEMP_FILE_GRACE_MINUTES * 60
    except OSError:
        return False


def _remove(path: Path) -> int:
    try:
        size = folder_bytes(path) if path.is_dir() else path.lstat().st_size
        shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
        return size
    except OSError:
        return 0


def _open_paths() -> Optional[set[str]]:
    """File & direktori kerja yang sedang dipakai proses di mesin/container ini (Linux /proc).
    None bila tidak bisa diketahui (mis. bukan Linux): pemanggil hanya mengandalkan lease & umur file."""
    proc = Path("/proc")
    if not (proc / "self" / "fd").is_dir():
        return None
    root = str(DATA_DIR.resolve())
    found: set[str] = set()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        links = [entry / "cwd"]
        try:
            links += [entry / "fd" / fd for fd in os.listdir(entry / "fd")]
        except OSError:
            pass  # proses sudah selesai / milik pengguna lain
        for link in links:
            try:
                target = os.readlink(link)
            except OSError:
                continue
            if target.startswith(root):
                found.add(target.removesuffix(" (deleted)"))
    return found


def _in_use(path: Path, open_paths: Optional[set[str]]) -> bool:
    if not open_paths:
        return False
    p = str(path.resolve())
    return any(o == p or o.startswith(p + os.sep) for o in open_paths)


def _orphan_candidates(folder: Path, source: Optional[str], renders_known: set) -> list[Path]:
    pid = folder.name
    out = []
    for f in folder.iterdir():
        name = f.name
        if f.is_file() and (name.endswith((".part", ".ytdl", ".tmp")) or ".part." in name
                            or (name.startswith("source.") and name != source and source)):
            out.append(f)
    renders_dir = folder / "renders"
    if renders_dir.is_dir():
        for f in renders_dir.iterdir():
            if f.is_dir() and f.name.startswith("tmp_"):
                out.append(f)
            elif f.suffix == ".ass":
                out.append(f)
            elif f.suffix == ".mp4" and (pid, f.name) not in renders_known:
                out.append(f)  # render gagal/terputus yang tidak tercatat
    return out


def clean_orphans(now: Optional[float] = None) -> dict:
    """File & folder sisa yang aman dihapus.

    Tidak pernah menyentuh: proyek dengan job aktif atau lease hidup (upload/unduhan berjalan),
    proyek lama, file yang sedang dibuka proses mana pun, dan file yang masih sangat baru.
    Umur file saja TIDAK cukup untuk menghapus: kepemilikan diperiksa ulang di bawah kunci proyek.
    """
    now = now or time.time()
    stats = {"folders": 0, "files": 0, "bytes": 0, "skipped_in_use": 0}
    if not PROJECTS_DIR.exists():
        return stats
    open_paths = _open_paths()
    with db.engine().connect() as c:
        projects = {r.id: r.source for r in c.execute(select(db.projects.c.id, db.projects.c.source))}
        managed = {r[0] for r in c.execute(select(db.project_media.c.project_id).where(
            db.project_media.c.state == "tersedia"))}
        renders_known = {(r.project_id, r.file) for r in c.execute(select(db.renders.c.project_id, db.renders.c.file))}
        busy = _busy_projects(c, None, now)

    for folder in PROJECTS_DIR.iterdir():
        if not folder.is_dir():
            continue
        pid = folder.name
        if pid not in projects:
            # Folder tanpa proyek di database (mis. sisa hapus yang terputus). Proyek versi lama
            # (project.json belum dimigrasikan) dibiarkan.
            if not (folder / "project.json").exists() and _old_enough(folder, now):
                if _in_use(folder, open_paths):
                    stats["skipped_in_use"] += 1
                    continue
                stats["bytes"] += _remove(folder)
                stats["folders"] += 1
            continue
        if pid not in managed or pid in busy:
            continue  # proyek lama atau sedang dipakai: jangan disentuh
        candidates = []
        for f in _orphan_candidates(folder, projects[pid], renders_known):
            if not _old_enough(f, now):
                continue
            if _in_use(f, open_paths):
                stats["skipped_in_use"] += 1
                continue
            candidates.append(f)
        if not candidates:
            continue
        with db.engine().begin() as c:
            # Kunci proyek lalu periksa ulang: job/upload/unduhan bisa saja mulai sejak daftar di atas dibuat.
            _lock_media(c, pid)
            if pid in _busy_projects(c, [pid], now):
                continue
            for f in candidates:
                stats["bytes"] += _remove(f)
                stats["files"] += 1

    # File sementara proses (tempfile diarahkan ke DATA_DIR/tmp) yang tertinggal karena crash.
    if UPLOAD_TMP_DIR.exists():
        for f in UPLOAD_TMP_DIR.iterdir():
            if not _old_enough(f, now - TMP_ORPHAN_HOURS * HOUR + TEMP_FILE_GRACE_MINUTES * 60):
                continue
            if _in_use(f, open_paths):
                stats["skipped_in_use"] += 1
                continue
            stats["bytes"] += _remove(f)
            stats["files"] += 1
    return stats


def _try_lock(conn) -> bool:
    if not db.is_postgres():
        return True
    return bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _SWEEP_LOCK_KEY}).scalar())


def sweep(now: Optional[float] = None) -> dict:
    """Satu putaran pembersihan. Aman dijalankan beberapa worker sekaligus (hanya satu yang bekerja)."""
    now = now or time.time()
    result = {"expired": 0, "resumed": 0, "incomplete": 0, "orphans": {}, "skipped": False}
    if not MEDIA_LIFECYCLE:
        result["skipped"] = True
        return result
    with db.engine().connect() as lock_conn:
        if not _try_lock(lock_conn):
            result["skipped"] = True
            return result
        try:
            from . import uploads
            result["uploads_expired"] = uploads.expire(now)  # upload terputus yang tidak dilanjutkan
            result["leases_expired"] = _expire_leases(now)
            for pid in _expired_candidates(now):
                result["expired"] += delete_media(pid, "kedaluwarsa", now)
            for pid in _stuck_deleting(now):  # hapus yang sempat terputus (worker mati di tengah jalan)
                result["resumed"] += _finish_delete(pid, "kedaluwarsa", now)
            for pid in _incomplete_uploads(now):  # upload yang tidak pernah selesai
                from . import storage
                storage.delete_project(pid)
                result["incomplete"] += 1
            result["orphans"] = clean_orphans(now)
        finally:
            if db.is_postgres():
                lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SWEEP_LOCK_KEY})
                lock_conn.commit()
    if result["expired"] or result["resumed"] or result["incomplete"] or result["orphans"].get("files") \
            or result["orphans"].get("folders"):
        log.info("Pembersihan media: %s", result)
    return result


# ------------------------------------------------------------------ migrasi proyek lama

def legacy_count() -> int:
    pr, pm = db.projects.c, db.project_media.c
    with db.engine().connect() as c:
        return int(c.execute(select(func.count()).select_from(
            db.projects.outerjoin(db.project_media, pm.project_id == pr.id)).where(pm.project_id.is_(None))).scalar())


def legacy_projects() -> list[dict]:
    pr, pm = db.projects.c, db.project_media.c
    with db.engine().connect() as c:
        rows = c.execute(select(pr.id, pr.name, pr.unit_id, pr.created)
                         .select_from(db.projects.outerjoin(db.project_media, pm.project_id == pr.id))
                         .where(pm.project_id.is_(None)).order_by(pr.created)).all()
    return [{"id": r.id, "name": r.name, "unit_id": r.unit_id, "created": r.created,
             "bytes": folder_bytes(PROJECTS_DIR / r.id)} for r in rows]


def migrate_legacy(apply: bool = False, grace_hours: float = MEDIA_MAX_HOURS,
                   now: Optional[float] = None) -> dict:
    """Masukkan proyek lama ke siklus media sementara. Default dry-run (tidak mengubah apa pun).

    Media TIDAK dihapus saat migrasi: setiap proyek mendapat masa tenggang `grace_hours` sejak
    sekarang, sehingga pengguna sempat mengunduh hasilnya.
    """
    now = now or time.time()
    items = legacy_projects()
    report = {"count": len(items), "bytes": sum(i["bytes"] for i in items), "applied": False,
              "grace_until": now + grace_hours * HOUR, "projects": items}
    if apply and items:
        with db.engine().begin() as c:
            for item in items:
                if _get(c, item["id"]) is None:
                    c.execute(insert(db.project_media).values(
                        project_id=item["id"], uploaded_at=now, last_activity=now, grace_until=now + grace_hours * HOUR,
                        state="tersedia", bytes=item["bytes"], state_changed=now))
        report["applied"] = True
    return report


def drop_project(pid: str, conn) -> None:
    conn.execute(delete(db.media_leases).where(db.media_leases.c.project_id == pid))
    conn.execute(delete(db.project_media).where(db.project_media.c.project_id == pid))
