"""P4: upload bertahap (tus 1.0.0): resume, chunk ganda/rusak, kuota, disk, pembatalan, cleanup, keamanan."""
import base64
import hashlib
import os
import resource
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from conftest import login_client
from sqlalchemy import select, update

from app import db, jobs, lifecycle, main, transcribe, uploads
from app.config import PROJECTS_DIR

H = {"Tus-Resumable": "1.0.0"}
MB = 1024 * 1024
REPO = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ klien tus kecil untuk tes

def meta(name="video uji.mp4", **extra):
    items = {"filename": name, **extra}
    return ",".join(f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in items.items())


def create(c, size, name="video uji.mp4"):
    return c.post("/api/uploads", headers={**H, "Upload-Length": str(size), "Upload-Metadata": meta(name)})


def patch(c, url, offset, data, checksum=True, algo="sha256"):
    headers = {**H, "Upload-Offset": str(offset), "Content-Type": "application/offset+octet-stream"}
    if checksum:
        digest = hashlib.new(algo, data).digest() if checksum is True else checksum
        headers["Upload-Checksum"] = f"{algo} {base64.b64encode(digest).decode()}"
    return c.patch(url, content=data, headers=headers)


def send_all(c, url, data, start=0, step=MB):
    off = start
    while off < len(data):
        r = patch(c, url, off, data[off:off + step])
        assert r.status_code == 204, (r.status_code, r.text)
        off = int(r.headers["upload-offset"])
    return off


def session(uid):
    with db.engine().connect() as c:
        return db.row(c.execute(select(db.uploads).where(db.uploads.c.id == uid)).first())


def leases(pid):
    with db.engine().connect() as c:
        return c.execute(select(db.media_leases).where(db.media_leases.c.project_id == pid)).all()


def wait_job(c, job_id, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("selesai", "gagal", "batal"):
            return j
        time.sleep(0.2)
    raise AssertionError("job tidak selesai")


@pytest.fixture
def video_bytes(sample_video):
    return sample_video.read_bytes()


@pytest.fixture
def padded_video(sample_video, tmp_path):
    """Video valid sebesar ±100 MB (sampel + padding di akhir; ffprobe tetap membacanya)."""
    def make(size):
        path = tmp_path / f"besar-{size}.mp4"
        with path.open("wb") as fh:
            fh.write(sample_video.read_bytes())
            fh.truncate(size)
        return path
    return make


@pytest.fixture
def fake_ai(monkeypatch, fake_transcript):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)


@pytest.fixture
def unit_users(client):
    """Dua unit, masing-masing satu editor."""
    stamp = uuid.uuid4().hex[:8]
    out = {}
    for key in ("a", "b"):
        unit = client.post("/api/admin/units", json={"name": f"Upload {key} {stamp}"}).json()
        email = f"up{key}{stamp}@org.id"
        client.post("/api/admin/users", json={"email": email, "password": "password-123", "role": "editor",
                                              "unit_id": unit["id"]})
        out[key] = (login_client(email, "password-123"), unit)
    return out


# ------------------------------------------------------------------ 1. alur dasar & file besar

def test_full_upload_verified_and_no_copy(client, video_bytes, fake_ai):
    r = create(client, len(video_bytes))
    assert r.status_code == 201 and r.headers["tus-resumable"] == "1.0.0"
    url = r.headers["location"]
    uid = url.rsplit("/", 1)[1]
    row = session(uid)
    part = uploads.part_path(row)
    assert part.exists() and part.parent == PROJECTS_DIR / row["project_id"]  # langsung di folder proyek
    inode_before = None
    off = 0
    while off < len(video_bytes):
        r = patch(client, url, off, video_bytes[off:off + 200_000])
        assert r.status_code == 204
        off = int(r.headers["upload-offset"])
        if off < len(video_bytes):
            inode_before = part.stat().st_ino
    st = client.get(url).json()
    assert st["state"] == "selesai" and st["offset"] == len(video_bytes) and st["job"]
    src = PROJECTS_DIR / st["project_id"] / "source.mp4"
    assert src.read_bytes() == video_bytes
    assert src.stat().st_ino == inode_before  # rename, bukan salinan kedua
    assert not part.exists()
    assert wait_job(client, st["job"]["id"])["status"] == "selesai"
    proj = client.get(f"/api/projects/{st['project_id']}").json()
    assert proj["status"] == "siap" and proj["preview_ready"] and proj["media"]["bytes"] >= len(video_bytes)
    assert leases(st["project_id"]) == []  # reservasi kuota sudah diganti ukuran aktual


# ------------------------------------------------------------------ 2-5. putus, refresh, ganda, rusak

def test_resume_after_browser_refresh(client, video_bytes, fake_ai):
    url = create(client, len(video_bytes)).headers["location"]
    half = len(video_bytes) // 2
    off = 0
    while off < half:
        off = int(patch(client, url, off, video_bytes[off:min(half, off + 100_000)]).headers["upload-offset"])
    # "Refresh": sesi login baru, tanpa state di memori. Daftar upload belum selesai tersedia dari server.
    fresh = login_client(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])
    pending = {u["id"]: u for u in fresh.get("/api/uploads").json()["uploads"]}
    uid = url.rsplit("/", 1)[1]
    assert pending[uid]["offset"] == half
    head = fresh.head(url, headers=H)
    assert head.status_code == 200 and int(head.headers["upload-offset"]) == half
    assert int(head.headers["upload-length"]) == len(video_bytes)
    send_all(fresh, url, video_bytes, start=half, step=100_000)
    st = fresh.get(url).json()
    assert st["state"] == "selesai"
    assert (PROJECTS_DIR / st["project_id"] / "source.mp4").read_bytes() == video_bytes
    wait_job(client, st["job"]["id"])


def test_duplicate_chunk_rejected_without_duplication(client, video_bytes, fake_ai):
    url = create(client, len(video_bytes)).headers["location"]
    first = video_bytes[:300_000]
    assert patch(client, url, 0, first).status_code == 204
    again = patch(client, url, 0, first)  # retry yang terlambat / tab ganda
    assert again.status_code == 409 and int(again.headers["upload-offset"]) == 300_000
    stale = patch(client, url, 100_000, video_bytes[100_000:400_000])
    assert stale.status_code == 409
    send_all(client, url, video_bytes, start=300_000)
    st = client.get(url).json()
    assert (PROJECTS_DIR / st["project_id"] / "source.mp4").read_bytes() == video_bytes  # tidak ada byte ganda
    wait_job(client, st["job"]["id"])


def test_corrupt_chunk_discarded_then_resent(client, video_bytes, fake_ai):
    url = create(client, len(video_bytes)).headers["location"]
    uid = url.rsplit("/", 1)[1]
    chunk = video_bytes[:250_000]
    damaged = bytearray(chunk)
    damaged[1234] ^= 0xFF  # 1 byte rusak di jalan
    r = patch(client, url, 0, bytes(damaged), checksum=hashlib.sha256(chunk).digest())
    assert r.status_code == 460 and int(r.headers["upload-offset"]) == 0
    assert session(uid)["offset"] == 0 and uploads.part_path(session(uid)).stat().st_size == 0  # data dibuang
    assert patch(client, url, 0, chunk, algo="sha1").status_code == 204  # sha1 (wajib di spesifikasi) juga diterima
    send_all(client, url, video_bytes, start=250_000)
    st = client.get(url).json()
    assert (PROJECTS_DIR / st["project_id"] / "source.mp4").read_bytes() == video_bytes
    wait_job(client, st["job"]["id"])


def test_corrupt_file_fails_verification_and_cleans_up(client):
    junk = os.urandom(400_000)  # bukan video
    url = create(client, len(junk), "rusak.mp4").headers["location"]
    uid = url.rsplit("/", 1)[1]
    pid = session(uid)["project_id"]
    assert patch(client, url, 0, junk[:200_000]).status_code == 204
    r = patch(client, url, 200_000, junk[200_000:])
    assert r.status_code == 422 and "video" in r.json()["detail"]
    st = client.get(url).json()
    assert st["state"] == "gagal"  # tidak pernah dilaporkan sukses
    assert not (PROJECTS_DIR / pid).exists() and leases(pid) == []
    assert client.head(url, headers=H).status_code == 410


# ------------------------------------------------------------------ gangguan koneksi sungguhan

def _start_server(data_dir):
    """Server uvicorn sungguhan (proses terpisah). Kredensial admin tes yang sama: di PostgreSQL server ini memakai
    database sesi tes (admin sudah ada); di SQLite memakai database di data_dir."""
    import httpx

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, VIDEO_APP_DATA=str(data_dir), EMBEDDED_WORKERS="0",
               DATABASE_URL=os.environ.get("DATABASE_URL", ""))
    if not env["DATABASE_URL"]:
        env.pop("DATABASE_URL")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=REPO, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(150):
            try:
                if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        c = httpx.Client(base_url=base, timeout=60)
        assert c.post("/api/auth/login", json={"email": os.environ["ADMIN_EMAIL"],
                                               "password": os.environ["ADMIN_PASSWORD"]}).status_code == 200
        c.pid = proc.pid
        return proc, base, c
    except BaseException:
        _stop(proc)
        raise


def _stop(proc, kill=False):
    if kill:
        proc.kill()
    else:
        proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(10)


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """Server uvicorn sungguhan: memutus socket di tengah potongan tidak bisa disimulasikan TestClient."""
    proc, base, c = _start_server(tmp_path_factory.mktemp("live"))
    try:  # server selalu dimatikan (proses yatim menahan koneksi DB)
        yield base, c
    finally:
        _stop(proc)


def _cut_patch(base, cookie, path, offset, declared, payload, checksum=None):
    """PATCH mentah lewat socket yang diputus sebelum seluruh body terkirim (seperti Wi-Fi hilang)."""
    host, port = base.replace("http://", "").split(":")
    headers = [f"PATCH {path} HTTP/1.1", f"Host: {host}:{port}", "Tus-Resumable: 1.0.0",
               f"Upload-Offset: {offset}", "Content-Type: application/offset+octet-stream",
               f"Content-Length: {declared}", f"Cookie: {cookie}"]
    if checksum:
        headers.append(f"Upload-Checksum: sha256 {checksum}")
    with socket.create_connection((host, int(port))) as s:
        s.sendall(("\r\n".join(headers) + "\r\n\r\n").encode() + payload)
        time.sleep(0.5)
    time.sleep(0.5)


def test_connection_drop_mid_chunk(live_server, sample_video):
    base, c = live_server
    data = sample_video.read_bytes()
    cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
    r = c.post("/api/uploads", headers={**H, "Upload-Length": str(len(data)), "Upload-Metadata": meta("putus.mp4")})
    url = r.headers["location"]
    # (a) dengan checksum: potongan yang terputus tidak bisa diverifikasi -> dibuang seluruhnya
    chunk = data[:300_000]
    digest = base64.b64encode(hashlib.sha256(chunk).digest()).decode()
    _cut_patch(base, cookie, url, 0, len(chunk), chunk[:120_000], digest)
    assert int(c.head(url, headers=H).headers["upload-offset"]) == 0
    # (b) tanpa checksum (klien tus umum): byte yang sudah diterima disimpan, lanjut dari situ
    _cut_patch(base, cookie, url, 0, len(chunk), chunk[:120_000])
    got = int(c.head(url, headers=H).headers["upload-offset"])
    assert 0 < got <= 120_000
    # (c) lanjutkan dari offset server sampai selesai; isi file identik
    off = got
    while off < len(data):
        body = data[off:off + 250_000]
        r = c.patch(url, content=body, headers={**H, "Upload-Offset": str(off),
                                                "Content-Type": "application/offset+octet-stream"})
        assert r.status_code == 204, r.text
        off = int(r.headers["upload-offset"])
    assert c.get(url).json()["state"] == "selesai"


def test_server_stays_responsive_during_slow_upload(live_server, sample_video):
    """Upload lambat (body menetes) tidak memblokir permintaan lain."""
    base, c = live_server
    data = sample_video.read_bytes()[:400_000]
    r = c.post("/api/uploads", headers={**H, "Upload-Length": str(len(data) + 1000), "Upload-Metadata": meta("pelan.mp4")})
    url = r.headers["location"]

    def drip():
        for i in range(0, len(data), 40_000):
            yield data[i:i + 40_000]
            time.sleep(0.3)

    import httpx
    t = threading.Thread(target=lambda: httpx.patch(base + url, content=drip(), cookies=c.cookies, timeout=60, headers={
        **H, "Upload-Offset": "0", "Content-Type": "application/offset+octet-stream"}))
    t.start()
    time.sleep(0.8)
    lat = []
    for _ in range(5):
        t0 = time.time()
        assert c.get("/api/health").status_code == 200
        lat.append(time.time() - t0)
    t.join(30)
    assert max(lat) < 0.5, lat
    assert int(c.head(url, headers=H).headers["upload-offset"]) == len(data)
    c.delete(url, headers=H)


def _rss_mb(pid):
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


def test_large_upload_server_ram_stays_flat(live_server, padded_video):
    """100 MB lewat server sungguhan: RAM proses server tidak naik sebesar file (streaming 1 MB ke disk)."""
    base, c = live_server
    path = padded_video(100 * MB)
    size = path.stat().st_size
    r = c.post("/api/uploads", headers={**H, "Upload-Length": str(size), "Upload-Metadata": meta("besar.mp4")})
    url = r.headers["location"]
    rss_before, peak = _rss_mb(c.pid), 0.0
    off, step = 0, 32 * MB
    with path.open("rb") as fh:
        while off < size:
            fh.seek(off)
            r = c.patch(url, content=fh.read(step), headers={**H, "Upload-Offset": str(off),
                                                            "Content-Type": "application/offset+octet-stream"})
            assert r.status_code == 204, r.text
            off = int(r.headers["upload-offset"])
            peak = max(peak, _rss_mb(c.pid))
    assert c.get(url).json()["state"] == "selesai"
    assert peak - rss_before < 40, f"RAM server naik {peak - rss_before:.0f} MB untuk potongan 32 MB"


# ------------------------------------------------------------------ 6. lintas workspace & kuota bersamaan

def test_cross_workspace_isolation(unit_users, video_bytes):
    (ca, _ua), (cb, _ub) = unit_users["a"], unit_users["b"]
    url = create(ca, len(video_bytes)).headers["location"]
    assert patch(ca, url, 0, video_bytes[:100_000]).status_code == 204
    for r in (cb.head(url, headers=H), patch(cb, url, 100_000, video_bytes[100_000:200_000]), cb.get(url),
              cb.delete(url, headers=H)):
        assert r.status_code == 404
    assert url.rsplit("/", 1)[1] not in {u["id"] for u in cb.get("/api/uploads").json()["uploads"]}
    assert int(ca.head(url, headers=H).headers["upload-offset"]) == 100_000
    ca.delete(url, headers=H)


def test_concurrent_uploads_quota_per_workspace(unit_users, video_bytes, fake_ai):
    """Unit A: kuota hanya cukup untuk 1 dari 2 upload bersamaan. Unit B tidak terpengaruh."""
    (ca, ua), (cb, ub) = unit_users["a"], unit_users["b"]
    size = 6 * MB
    with db.engine().begin() as c:
        c.execute(update(db.units).where(db.units.c.id == ua["id"]).values(storage_quota_mb=10))
    codes, barrier = [], threading.Barrier(4)

    def go(cl):
        barrier.wait()
        codes.append((cl, create(cl, size).status_code))

    threads = [threading.Thread(target=go, args=(cl,)) for cl in (ca, ca, cb, cb)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    a_codes = sorted(code for cl, code in codes if cl is ca)
    b_codes = sorted(code for cl, code in codes if cl is cb)
    assert a_codes == [201, 507] and b_codes == [201, 201]
    assert lifecycle.unit_usage(ua["id"]) == size  # tepat satu reservasi
    for cl in (ca, cb):
        for u in cl.get("/api/uploads").json()["uploads"]:
            cl.delete(f"/api/uploads/{u['id']}", headers=H)
    assert lifecycle.unit_usage(ua["id"]) == 0


def test_resume_does_not_double_reserve(unit_users, video_bytes, fake_ai):
    (ca, ua) = unit_users["a"]
    url = create(ca, len(video_bytes)).headers["location"]
    assert lifecycle.unit_usage(ua["id"]) == len(video_bytes)
    assert patch(ca, url, 0, video_bytes[:200_000]).status_code == 204
    for _ in range(3):  # beberapa kali "putus & lanjut"
        ca.head(url, headers=H)
    assert lifecycle.unit_usage(ua["id"]) == len(video_bytes)
    send_all(ca, url, video_bytes, start=200_000)
    st = ca.get(url).json()
    assert lifecycle.unit_usage(ua["id"]) == lifecycle.folder_bytes(PROJECTS_DIR / st["project_id"])
    wait_job(ca, st["job"]["id"])


# ------------------------------------------------------------------ 7. disk hampir penuh

def test_disk_full_at_start_and_mid_upload(client, video_bytes, monkeypatch, fake_ai):
    real = lifecycle.disk_status
    monkeypatch.setattr(lifecycle, "disk_status", lambda: {**real(), "usable": 1000})
    r = create(client, len(video_bytes))
    assert r.status_code == 507 and r.json()["storage_full"]
    monkeypatch.setattr(lifecycle, "disk_status", real)
    url = create(client, len(video_bytes)).headers["location"]
    uid = url.rsplit("/", 1)[1]
    assert patch(client, url, 0, video_bytes[:200_000]).status_code == 204
    monkeypatch.setattr(lifecycle, "disk_status", lambda: {**real(), "usable": 0})  # disk penuh di tengah jalan
    r = patch(client, url, 200_000, video_bytes[200_000:400_000])
    assert r.status_code == 507
    assert session(uid)["offset"] == 200_000 and uploads.part_path(session(uid)).stat().st_size == 200_000
    monkeypatch.setattr(lifecycle, "disk_status", real)  # ruang tersedia lagi: lanjut tanpa mengulang
    send_all(client, url, video_bytes, start=200_000)
    st = client.get(url).json()
    assert st["state"] == "selesai"
    wait_job(client, st["job"]["id"])


# ------------------------------------------------------------------ 8-9. batal & cleanup upload terlantar

def test_cancel_releases_everything(unit_users, video_bytes):
    (ca, ua) = unit_users["a"]
    url = create(ca, len(video_bytes)).headers["location"]
    uid = url.rsplit("/", 1)[1]
    row = session(uid)
    assert patch(ca, url, 0, video_bytes[:100_000]).status_code == 204
    assert ca.delete(url, headers=H).status_code == 204
    assert session(uid)["state"] == "batal"
    assert not uploads.part_path(row).exists() and not (PROJECTS_DIR / row["project_id"]).exists()
    assert leases(row["project_id"]) == [] and lifecycle.unit_usage(ua["id"]) == 0
    assert ca.head(url, headers=H).status_code == 410
    assert patch(ca, url, 100_000, video_bytes[100_000:200_000]).status_code == 410


def test_abandoned_upload_cleaned_after_resume_window(client, video_bytes):
    url = create(client, len(video_bytes)).headers["location"]
    uid = url.rsplit("/", 1)[1]
    row = session(uid)
    assert patch(client, url, 0, video_bytes[:100_000]).status_code == 204
    part = uploads.part_path(row)
    old = time.time() - 3 * 3600
    os.utime(part, (old, old))
    lifecycle.sweep()  # masih dalam masa resume: file setengah jadi dilindungi lease
    assert part.exists() and session(uid)["state"] == "aktif"
    res = lifecycle.sweep(now=time.time() + uploads.RESUME_SECONDS + 60)
    assert res["uploads_expired"] >= 1
    assert session(uid)["state"] == "habis"
    assert not part.exists() and not (PROJECTS_DIR / row["project_id"]).exists() and leases(row["project_id"]) == []
    assert client.head(url, headers=H).status_code == 410


# ------------------------------------------------------------------ keamanan & validasi protokol

def test_protocol_validation_and_path_safety(client, video_bytes):
    assert client.post("/api/uploads", headers={"Upload-Length": "10", "Upload-Metadata": meta()}).status_code == 412
    assert create(client, 10, "virus.exe").status_code == 400
    assert create(client, (main.MAX_UPLOAD_MB + 1) * MB).status_code == 413
    assert client.post("/api/uploads", headers={**H, "Upload-Length": "-5"}).status_code == 400
    assert client.post("/api/uploads", headers={**H, "Upload-Length": "10", "Upload-Metadata": "filename %%%"}).status_code == 400
    url = create(client, 1000, "../../../../etc/passwd.mp4").headers["location"]
    row = session(url.rsplit("/", 1)[1])
    assert row["filename"] == "passwd.mp4" and uploads.part_path(row).parent == PROJECTS_DIR / row["project_id"]
    too_much = patch(client, url, 0, b"x" * 1500)  # melewati Upload-Length
    assert too_much.status_code == 413 and session(row["id"])["offset"] == 0
    wrong_type = client.patch(url, content=b"x", headers={**H, "Upload-Offset": "0", "Content-Type": "text/plain"})
    assert wrong_type.status_code == 415
    for bad in ("../x", "0" * 31, "Z" * 32):
        assert client.head(f"/api/uploads/{bad}", headers=H).status_code in (404, 405)
    client.delete(url, headers=H)


def test_active_upload_limit_per_user(client, monkeypatch):
    for u in client.get("/api/uploads").json()["uploads"]:  # sisa sesi dari tes lain milik admin yang sama
        client.delete(f"/api/uploads/{u['id']}", headers=H)
    monkeypatch.setattr(uploads, "MAX_ACTIVE_UPLOADS_PER_USER", 2)
    urls = [create(client, 1000).headers["location"] for _ in range(2)]
    assert create(client, 1000).status_code == 429
    for u in urls:
        client.delete(u, headers=H)
    assert create(client, 1000).status_code == 201
    for u in client.get("/api/uploads").json()["uploads"]:
        client.delete(f"/api/uploads/{u['id']}", headers=H)


def test_partial_upload_protected_from_orphan_cleanup(client, video_bytes):
    url = create(client, len(video_bytes)).headers["location"]
    row = session(url.rsplit("/", 1)[1])
    assert patch(client, url, 0, video_bytes[:100_000]).status_code == 204
    part = uploads.part_path(row)
    old = time.time() - 5 * 3600
    os.utime(part, (old, old))
    lifecycle.clean_orphans()  # ".part" lama, tetapi sesi aktif memegang lease: tidak boleh dihapus
    assert part.exists()
    client.delete(url, headers=H)


def test_legacy_single_request_upload_still_works(client, sample_video, fake_ai):
    with sample_video.open("rb") as fh:
        r = client.post("/api/projects", files={"file": ("lama.mp4", fh, "video/mp4")})
    assert r.status_code == 200
    wait_job(client, r.json()["job"]["id"])


# ------------------------------------------------------------------ P4 final: restart & kedaluwarsa

def test_web_server_restart_mid_upload(tmp_path, sample_video):
    """Server web dimatikan paksa (SIGKILL) di tengah upload lalu dinyalakan ulang: upload lanjut dari posisi
    terakhir yang tercatat, file utuh, job analisis tidak hilang setelah restart berikutnya."""
    data = sample_video.read_bytes()
    proc, base, c = _start_server(tmp_path)
    try:
        url = c.post("/api/uploads", headers={**H, "Upload-Length": str(len(data)),
                                              "Upload-Metadata": meta("restart.mp4")}).headers["location"]
        half = len(data) // 2
        assert c.patch(url, content=data[:half], headers={**H, "Upload-Offset": "0",
                       "Content-Type": "application/offset+octet-stream"}).status_code == 204
    finally:
        _stop(proc, kill=True)
    proc, base, c = _start_server(tmp_path)
    try:
        assert int(c.head(url, headers=H).headers["upload-offset"]) == half
        r = c.patch(url, content=data[half:], headers={**H, "Upload-Offset": str(half),
                                                      "Content-Type": "application/offset+octet-stream"})
        assert r.status_code == 204
        st = c.get(url).json()
        assert st["state"] == "selesai" and st["job"]
        job_id = st["job"]["id"]
    finally:
        _stop(proc)
    proc, base, c = _start_server(tmp_path)  # restart lagi: metadata & job tetap ada
    try:
        assert c.get(url).json()["state"] == "selesai"
        # Job tidak hilang & tidak dibatalkan oleh restart. (Di PostgreSQL worker bawaan sesi tes memakai
        # database yang sama dan bisa sudah mengambilnya, jadi statusnya tidak selalu "antre".)
        job = c.get(f"/api/jobs/{job_id}")
        assert job.status_code == 200 and job.json()["status"] != "batal"
        proj = c.get(f"/api/projects/{st['project_id']}").json()
        assert proj["source"] == "source.mp4" and proj["status"] != "mengunggah"  # metadata utuh setelah restart
    finally:
        _stop(proc)


def test_expired_session_cannot_be_reactivated(unit_users, video_bytes):
    """Lewat batas waktu tetapi pembersihan belum berjalan: PATCH/HEAD ditolak, tidak memperpanjang sesi."""
    (ca, ua) = unit_users["a"]
    url = create(ca, len(video_bytes)).headers["location"]
    uid = url.rsplit("/", 1)[1]
    row = session(uid)
    assert patch(ca, url, 0, video_bytes[:100_000]).status_code == 204
    with db.engine().begin() as c:
        c.execute(update(db.uploads).where(db.uploads.c.id == uid).values(expires_at=time.time() - 1))
    r = patch(ca, url, 100_000, video_bytes[100_000:200_000])
    assert r.status_code == 410
    assert session(uid)["state"] == "habis" and session(uid)["offset"] == 100_000
    assert ca.head(url, headers=H).status_code == 410
    assert uid not in {u["id"] for u in ca.get("/api/uploads").json()["uploads"]}
    assert leases(row["project_id"]) == [] and lifecycle.unit_usage(ua["id"]) == 0
    assert not (PROJECTS_DIR / row["project_id"]).exists()
    # Tidak ada jalan menghidupkannya lagi, termasuk mengubah offset di header.
    assert patch(ca, url, 0, video_bytes[:100_000]).status_code == 410


def test_lost_quota_lease_stops_upload(unit_users, video_bytes):
    """Reservasi kuota sudah lepas (mis. sweep lease): upload tidak boleh lanjut tanpa reservasi."""
    (ca, ua) = unit_users["a"]
    url = create(ca, len(video_bytes)).headers["location"]
    uid = url.rsplit("/", 1)[1]
    assert patch(ca, url, 0, video_bytes[:100_000]).status_code == 204
    with db.engine().begin() as c:
        c.execute(db.media_leases.delete().where(db.media_leases.c.id == session(uid)["lease_id"]))
    assert patch(ca, url, 100_000, video_bytes[100_000:200_000]).status_code == 410
    assert session(uid)["state"] == "habis"


def test_crash_during_finalize_is_recovered(client, video_bytes, fake_ai):
    """Proses mati setelah file di-rename tetapi sebelum status dicatat: status upload dilanjutkan saat dibaca."""
    url = create(client, len(video_bytes)).headers["location"]
    uid = url.rsplit("/", 1)[1]
    off = 0
    while off < len(video_bytes) - 1000:
        off = int(patch(client, url, off, video_bytes[off:min(len(video_bytes) - 1000, off + 200_000)])
                  .headers["upload-offset"])
    row = session(uid)
    # Simulasikan kematian proses di tengah finalisasi: data lengkap, sudah di-rename, status masih "final".
    with uploads.part_path(row).open("ab") as fh:
        fh.write(video_bytes[-1000:])
    os.replace(uploads.part_path(row), PROJECTS_DIR / row["project_id"] / "source.mp4")
    with db.engine().begin() as c:
        c.execute(update(db.uploads).where(db.uploads.c.id == uid).values(
            offset=len(video_bytes), state="final", updated=time.time() - uploads.FINALIZE_STALE - 5))
    st = client.get(url).json()
    assert st["state"] == "selesai" and st["job"]
    assert (PROJECTS_DIR / st["project_id"] / "source.mp4").read_bytes() == video_bytes
    assert leases(st["project_id"]) == []
    assert client.get(url).json()["job"]["id"] == st["job"]["id"]  # dibaca lagi: tidak ada analisis ganda
    analyses = [j for j in jobs.for_project(st["project_id"]) if j["task"] == "analyze"]
    assert len(analyses) == 1
    wait_job(client, st["job"]["id"])


def test_transient_error_during_finalize_keeps_file(client, video_bytes, monkeypatch, fake_ai):
    url = create(client, len(video_bytes)).headers["location"]
    uid = url.rsplit("/", 1)[1]
    calls = {"n": 0}
    real = lifecycle.commit_upload

    def flaky(lid, pid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database sedang restart")
        return real(lid, pid)

    monkeypatch.setattr(lifecycle, "commit_upload", flaky)
    off = send_all(client, url, video_bytes[:-1], step=200_000)
    r = patch(client, url, off, video_bytes[-1:])
    assert r.status_code == 503 and "tidak hilang" in r.json()["detail"]
    row = session(uid)
    assert row["state"] == "aktif" and (PROJECTS_DIR / row["project_id"] / "source.mp4").exists()
    st = client.get(url).json()  # dicoba lagi otomatis saat status dibaca (UI melakukannya)
    assert st["state"] == "selesai"
    wait_job(client, st["job"]["id"])
