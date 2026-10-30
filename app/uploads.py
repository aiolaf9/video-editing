"""Upload bertahap yang bisa dilanjutkan (P4), protokol tus 1.0.0 (https://tus.io/protocols/resumable-upload).

Ekstensi: creation, termination, checksum (sha1/sha256/md5). Klien tus standar mana pun dapat dipakai.

Alur
1. POST   /api/uploads           : validasi, buat proyek "mengunggah", reservasi kuota (lease P2), file kosong.
2. PATCH  /api/uploads/<id>      : potongan berikutnya, harus tepat di Upload-Offset saat ini.
3. HEAD   /api/uploads/<id>      : posisi terakhir yang diterima (untuk melanjutkan setelah putus/refresh).
4. Saat lengkap: ukuran & isi diverifikasi (ffprobe), file di-rename menjadi sumber, reservasi kuota diganti
   ukuran aktual, analisis masuk antrean. Status "selesai" baru diberikan setelah semua itu berhasil.
5. DELETE /api/uploads/<id>      : batalkan; file, proyek, dan reservasi dilepas.

Keamanan
- ID acak 128-bit + hanya pemilik sesi yang boleh mengakses (pengguna lain: 404, termasuk unit lain).
- Nama file tidak pernah dipakai sebagai path: hanya ekstensi (daftar putih) yang dipakai.
- Offset wajib cocok (chunk ganda/terlambat ditolak 409), chunk tidak boleh melewati Upload-Length,
  checksum per chunk (rusak -> 460, data chunk dibuang), satu penulis per sesi (flock, juga antar-proses).
- Disk: dicek saat mulai & selama menulis; kuota unit direservasi atomik di awal (tidak dobel saat resume).
- Data ditulis langsung ke disk per potongan kecil: tidak ada salinan kedua & tidak memuat file ke RAM.
"""
import base64
import binascii
import fcntl
import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import func, insert, select, update

from . import db, lifecycle, media, storage
from .config import (MAX_ACTIVE_UPLOADS_PER_USER, MAX_UPLOAD_MB, PROJECTS_DIR, UPLOAD_CHUNK_MAX_MB,
                     UPLOAD_RESUME_HOURS)

log = logging.getLogger("video-editing.uploads")

TUS_VERSION = "1.0.0"
TUS_EXTENSIONS = "creation,termination,checksum"
CHECKSUMS = {"sha1": hashlib.sha1, "sha256": hashlib.sha256, "md5": hashlib.md5}
# Status sesi (kolom VARCHAR(10)). "habis" = tidak dilanjutkan sampai batas waktu resume.
# "final" = file lengkap sedang diverifikasi/dijadikan sumber (diklaim atomik; dilanjutkan bila proses mati).
STATES = ("aktif", "final", "selesai", "batal", "gagal", "habis")
FINALIZE_STALE = 300.0  # "final" lebih lama dari ini tanpa kemajuan = proses yang memfinalisasi sudah mati
RESUME_SECONDS = UPLOAD_RESUME_HOURS * 3600
DISK_CHECK_EVERY = 64 * 1024 * 1024


class UploadError(Exception):
    def __init__(self, status: int, message: str, offset: Optional[int] = None):
        super().__init__(message)
        self.status, self.offset = status, offset


def max_size() -> int:
    return MAX_UPLOAD_MB * 1024 * 1024


def parse_metadata(header: Optional[str]) -> dict:
    """Upload-Metadata: "key base64,key2 base64" (nilai boleh kosong)."""
    out: dict[str, str] = {}
    for part in (header or "").split(","):
        part = part.strip()
        if not part:
            continue
        key, _, value = part.partition(" ")
        try:
            out[key] = base64.b64decode(value, validate=True).decode("utf-8") if value else ""
        except (binascii.Error, UnicodeDecodeError):
            raise UploadError(400, "Upload-Metadata tidak valid.")
    return out


def part_path(row: dict) -> Path:
    return PROJECTS_DIR / row["project_id"] / f"upload-{row['id']}.part"


def _row(c, uid: str) -> Optional[dict]:
    return db.row(c.execute(select(db.uploads).where(db.uploads.c.id == uid)).first())


def get(uid: str, user: dict) -> dict:
    """Sesi milik pengguna ini. Sesi orang lain (unit mana pun) diperlakukan tidak ada."""
    if not (isinstance(uid, str) and len(uid) == 32 and all(ch in "0123456789abcdef" for ch in uid)):
        raise UploadError(404, "Upload tidak ditemukan.")
    with db.engine().connect() as c:
        row = _row(c, uid)
    if row is None or row["user_id"] != user["id"]:
        raise UploadError(404, "Upload tidak ditemukan.")
    return row


def view(row: dict) -> dict:
    meta = json.loads(row.get("meta") or "{}")
    return {"id": row["id"], "filename": row["filename"], "size": row["size"], "offset": row["offset"],
            "state": row["state"], "error": row["error"], "project_id": row["project_id"], "kind": row["kind"],
            "job": meta.get("job"), "created": row["created"], "updated": row["updated"], "expires_at": row["expires_at"]}


def list_active(user: dict) -> list[dict]:
    with db.engine().connect() as c:
        rows = c.execute(select(db.uploads).where(db.uploads.c.user_id == user["id"], db.uploads.c.state == "aktif",
                                                  db.uploads.c.expires_at > time.time())
                         .order_by(db.uploads.c.created)).all()
    return [view(db.row(r)) for r in rows]


def create(user: dict, unit_id: Optional[str], size: int, metadata: dict, allowed_exts: set[str],
           now: Optional[float] = None) -> dict:
    now = now or time.time()
    if size <= 0:
        raise UploadError(400, "File kosong.")
    if size > max_size():
        raise UploadError(413, f"File terlalu besar. Maksimal {MAX_UPLOAD_MB} MB.")
    filename = Path(metadata.get("filename") or metadata.get("name") or "").name[:200]
    ext = Path(filename).suffix.lower()
    if ext not in allowed_exts:
        raise UploadError(400, f"Format {ext or '(tanpa ekstensi)'} tidak didukung.")
    with db.engine().connect() as c:
        active = c.execute(select(func.count()).where(db.uploads.c.user_id == user["id"],
                                                      db.uploads.c.state == "aktif")).scalar()
    if active >= MAX_ACTIVE_UPLOADS_PER_USER:
        raise UploadError(429, f"Anda sudah punya {active} upload yang belum selesai. Selesaikan atau batalkan dulu.")
    lifecycle.ensure_disk(size)
    meta = storage.create_project(Path(filename).stem or "Video", unit_id, user["id"])
    pid = meta["id"]
    storage.update_project(pid, status="mengunggah")
    uid = secrets.token_hex(16)
    try:
        # Reservasi kuota atomik untuk seluruh ukuran file, berlaku selama sesi bisa dilanjutkan.
        lease = lifecycle.acquire_lease(pid, "upload", nbytes=size, unit=lifecycle.unit_row(unit_id), now=now,
                                        ttl=RESUME_SECONDS)
        path = PROJECTS_DIR / pid / f"upload-{uid}.part"
        path.touch()
        with db.engine().begin() as c:
            c.execute(insert(db.uploads).values(
                id=uid, user_id=user["id"], unit_id=unit_id, project_id=pid, kind="video", filename=filename,
                ext=ext, size=size, offset=0, lease_id=lease, state="aktif",
                meta=json.dumps({"language": metadata.get("language") or None}), created=now, updated=now,
                expires_at=now + RESUME_SECONDS))
    except BaseException:
        storage.delete_project(pid)  # termasuk lease bila sempat dibuat
        raise
    with db.engine().connect() as c:
        return _row(c, uid)


def _lease_alive(c, lease_id: Optional[str], now: float) -> bool:
    if not lease_id:
        return False
    ml = db.media_leases.c
    return c.execute(select(ml.id).where(ml.id == lease_id, ml.expires_at > now)).first() is not None


def check_alive(row: dict, now: Optional[float] = None) -> dict:
    """Sesi aktif yang sudah lewat batas waktu, atau yang reservasi kuotanya sudah lepas, tidak boleh dipakai
    lagi (walau pembersihan belum sempat berjalan): langsung dinyatakan habis -> 410."""
    now = now or time.time()
    if row["state"] != "aktif":
        return row
    with db.engine().connect() as c:
        alive = row["expires_at"] > now and _lease_alive(c, row.get("lease_id"), now)
    if not alive:
        abort(row, "habis", "Upload tidak dilanjutkan sampai batas waktu.")
        raise UploadError(410, "Upload ini sudah kedaluwarsa. Unggah ulang file dari awal.")
    return row


def abort(row: dict, state: str = "batal", error: Optional[str] = None) -> None:
    """Hentikan sesi: file setengah jadi, proyek "mengunggah", dan reservasi kuota dilepas."""
    now = time.time()
    with db.engine().begin() as c:
        changed = c.execute(update(db.uploads).where(db.uploads.c.id == row["id"],
                                                     db.uploads.c.state.in_(("aktif", "final")))
                            .values(state=state, error=error, updated=now)).rowcount
    if not changed:
        return
    if row.get("lease_id"):
        lifecycle.release_lease(row["lease_id"], collect=False)
    if row.get("project_id"):
        try:
            meta = storage.load_project(row["project_id"])
        except storage.NotFound:
            meta = None
        if meta and meta.get("status") == "mengunggah":
            storage.delete_project(row["project_id"])
        else:
            part_path(row).unlink(missing_ok=True)
    log.info("Upload %s dihentikan (%s)%s", row["id"], state, f": {error}" if error else "")


def expire(now: Optional[float] = None) -> int:
    """Sesi yang tidak dilanjutkan sampai batas waktunya: dibersihkan (dipanggil lifecycle.sweep)."""
    now = now or time.time()
    with db.engine().connect() as c:
        rows = [db.row(r) for r in c.execute(select(db.uploads).where(
            db.uploads.c.state == "aktif", db.uploads.c.expires_at < now))]
    for row in rows:
        abort(row, "habis", "Upload tidak dilanjutkan sampai batas waktu.")
    return len(rows)


class ChunkWriter:
    """Satu PATCH: menulis potongan secara streaming tepat di offset yang disepakati."""

    def __init__(self, row: dict, offset: Optional[int], length: Optional[int], checksum: Optional[str]):
        if row["state"] != "aktif":
            raise UploadError(410 if row["state"] in ("batal", "habis") else 409,
                              "Upload ini sudah tidak aktif.", row["offset"])
        if offset is None:
            raise UploadError(400, "Header Upload-Offset wajib.")
        self.row, self.start = row, offset
        self.remaining = row["size"] - offset
        if length is not None and length > UPLOAD_CHUNK_MAX_MB * 1024 * 1024:
            raise UploadError(413, f"Potongan terlalu besar (maks {UPLOAD_CHUNK_MAX_MB} MB).")
        if length is not None and length > self.remaining:
            raise UploadError(413, "Potongan melebihi ukuran file yang diumumkan.")
        self.hasher, self.expected = None, None
        if checksum:
            algo, _, b64 = checksum.partition(" ")
            if algo not in CHECKSUMS:
                raise UploadError(400, "Algoritma checksum tidak didukung.")
            try:
                self.expected = base64.b64decode(b64, validate=True)
            except binascii.Error:
                raise UploadError(400, "Upload-Checksum tidak valid.")
            self.hasher = CHECKSUMS[algo]()
        lifecycle.ensure_disk(length or 0)
        self.fh = open(part_path(row), "r+b")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)  # satu penulis per sesi, juga antar-proses web
        except BlockingIOError:
            self.fh.close()
            raise UploadError(423, "Potongan lain untuk upload ini sedang dikirim.")
        try:
            with db.engine().connect() as c:
                fresh = _row(c, row["id"])  # baca ulang setelah memegang kunci
            if fresh["state"] != "aktif":
                raise UploadError(410, "Upload ini sudah tidak aktif.", fresh["offset"])
            check_alive(fresh)  # lewat batas waktu / lease lepas: tidak bisa diaktifkan lagi
            if offset != fresh["offset"]:
                raise UploadError(409, "Posisi upload tidak cocok.", fresh["offset"])
            # Database = sumber kebenaran: sisa tulisan dari proses yang mati sebelum mencatat offset dibuang.
            if os.fstat(self.fh.fileno()).st_size != offset:
                self.fh.truncate(offset)
            self.fh.seek(offset)
        except BaseException:
            self.close()
            raise
        self.written = 0
        self.committed = False
        self._since_disk_check = 0

    def write(self, data: bytes) -> None:
        if self.written + len(data) > self.remaining:
            raise UploadError(413, "Potongan melebihi ukuran file yang diumumkan.")
        self.fh.write(data)
        if self.hasher:
            self.hasher.update(data)
        self.written += len(data)
        self._since_disk_check += len(data)
        if self._since_disk_check >= DISK_CHECK_EVERY:
            self._since_disk_check = 0
            lifecycle.ensure_disk()

    def rollback(self) -> None:
        """Buang data potongan ini (gagal/rusak/terputus dengan checksum)."""
        self.fh.truncate(self.start)
        self.written = 0

    def commit(self, on_complete: Optional[Callable[[dict], None]] = None) -> dict:
        """Data aman di disk -> catat offset baru. Bila lengkap, finalisasi."""
        if self.hasher is not None and self.hasher.digest() != self.expected:
            self.rollback()
            raise UploadError(460, "Checksum potongan tidak cocok; potongan dibuang, kirim ulang.", self.start)
        self.fh.flush()
        os.fsync(self.fh.fileno())  # offset hanya dicatat setelah data benar-benar tersimpan
        now = time.time()
        new = self.start + self.written
        with db.engine().begin() as c:
            ok = c.execute(update(db.uploads).where(db.uploads.c.id == self.row["id"],
                                                    db.uploads.c.offset == self.start,
                                                    db.uploads.c.state == "aktif").values(
                offset=new, updated=now, expires_at=now + RESUME_SECONDS)).rowcount
        if not ok:
            self.rollback()
            raise UploadError(409, "Upload berubah saat potongan dikirim.")
        self.committed = True  # sejak sini data milik sesi: jangan dibuang walau langkah berikutnya gagal
        if self.row.get("lease_id"):
            lifecycle.renew_lease(self.row["lease_id"], now, ttl=RESUME_SECONDS)
        row = dict(self.row, offset=new, updated=now)
        if new == row["size"]:
            row = finalize(row, on_complete)
        return row

    def close(self) -> None:
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()


def _claim_finalize(row: dict, now: float) -> bool:
    """Hanya satu proses yang memfinalisasi. "final" yang macet (proses mati) boleh diambil alih."""
    u = db.uploads.c
    with db.engine().begin() as c:
        return bool(c.execute(update(db.uploads).where(
            u.id == row["id"], u.offset == u.size,
            ((u.state == "aktif") | ((u.state == "final") & (u.updated < now - FINALIZE_STALE)))).values(
            state="final", updated=now)).rowcount)


def finalize(row: dict, on_complete: Optional[Callable[[dict], None]] = None) -> dict:
    """File lengkap: verifikasi, jadikan video sumber proyek, ganti reservasi dengan ukuran aktual.

    Idempoten: bila proses mati di tengah jalan (mis. setelah rename), panggilan berikutnya (GET/HEAD status
    upload) melanjutkan dari langkah yang belum selesai. Kesalahan sementara (bukan file rusak) TIDAK menghapus
    file yang sudah lengkap: sesi kembali "aktif" dan finalisasi dicoba lagi.
    """
    now = time.time()
    if not _claim_finalize(row, now):
        with db.engine().connect() as c:
            return _row(c, row["id"])  # sudah/sedang difinalisasi proses lain
    part = part_path(row)
    source = f"source{row['ext']}"
    dst = PROJECTS_DIR / row["project_id"] / source
    try:
        if part.exists():  # langkah verifikasi & rename belum terjadi
            actual = part.stat().st_size
            if actual != row["size"]:
                raise UploadError(422, f"Ukuran file tidak cocok ({actual} dari {row['size']} byte).")
            try:
                info = media.probe(part)
            except media.MediaError:
                raise UploadError(422, "File bukan video yang bisa dibaca. Periksa file lalu unggah ulang.")
            if not info.get("width"):
                raise UploadError(422, "File tidak berisi gambar video.")
            os.replace(part, dst)  # rename, bukan salin
        elif not (dst.exists() and dst.stat().st_size == row["size"]):
            raise UploadError(422, "File upload tidak ditemukan di server. Unggah ulang.")
        lifecycle.commit_upload(row["lease_id"], row["project_id"])  # reservasi -> ukuran aktual (satu transaksi)
        lifecycle.mark_uploaded(row["project_id"])
        storage.update_project(row["project_id"], source=source, status="antre")  # terakhir: baru "jadi"
    except UploadError as exc:
        abort(row, "gagal", str(exc))
        raise
    except Exception as exc:
        log.exception("Finalisasi upload %s gagal sementara", row["id"])
        with db.engine().begin() as c:  # data tetap aman; dicoba lagi pada permintaan berikutnya
            c.execute(update(db.uploads).where(db.uploads.c.id == row["id"], db.uploads.c.state == "final")
                      .values(state="aktif", updated=time.time()))
        raise UploadError(503, "Server sedang sibuk menyimpan file. Coba lagi sebentar; file Anda tidak hilang.") from exc
    meta = json.loads(row.get("meta") or "{}")
    if on_complete:
        try:
            meta["job"] = on_complete(row)
        except Exception as exc:  # mis. antrean penuh: file tetap aman, analisis bisa dimulai ulang
            meta["job_error"] = str(exc)
    with db.engine().begin() as c:
        c.execute(update(db.uploads).where(db.uploads.c.id == row["id"]).values(
            state="selesai", updated=time.time(), lease_id=None, meta=json.dumps(meta)))
    log.info("Upload %s selesai (%d byte) -> proyek %s", row["id"], row["size"], row["project_id"])
    return dict(row, state="selesai", lease_id=None, meta=json.dumps(meta))


def recover(row: dict, on_complete: Optional[Callable[[dict], None]] = None) -> dict:
    """Dipanggil saat status upload dibaca: lanjutkan finalisasi yang terputus (restart/crash)."""
    now = time.time()
    if row["offset"] == row["size"] and (row["state"] == "aktif" or (
            row["state"] == "final" and row["updated"] < now - FINALIZE_STALE)):
        return finalize(row, on_complete)
    return row
