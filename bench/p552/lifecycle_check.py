"""P5.5.2-C: validasi upload besar & siklus media dengan server uvicorn sungguhan dan berkas video nyata.

Skenario (masing-masing dengan bukti):
  1. upload tus berkas besar: socket diputus di 30 %, server di-SIGKILL di 60 % lalu dinyalakan ulang (data sama),
     upload dilanjutkan dari offset server; sha256 sumber tersimpan = berkas asli; ffprobe membaca durasi
  2. batal (DELETE) di tengah upload: berkas parsial terhapus, sesi tidak bisa dilanjutkan
  3. kuota unit: upload melebihi kuota ditolak saat dibuat (sebelum data dikirim)
  4. lease/job aktif menahan pembersihan walau masa simpan lewat; setelah job selesai media terhapus (TTL)
  5. folder yatim (tanpa proyek) yang cukup tua dibersihkan
TTL diuji dengan menjalankan lifecycle.sweep(now=masa depan) pada data server yang sama (logika asli, tanpa menunggu).

    python bench/p552/lifecycle_check.py <berkas-video> <workdir> [--out hasil.json]
"""
import argparse
import base64
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))
from upload_bench import H, MB, Server  # noqa: E402

CHUNK = 16 * MB


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(MB), b""):
            h.update(chunk)
    return h.hexdigest()


def create(srv, path: Path):
    meta = "filename " + base64.b64encode(path.name.encode()).decode()
    return srv.c.post("/api/uploads", headers={**H, "Upload-Length": str(path.stat().st_size),
                                               "Upload-Metadata": meta})


def patch(srv, url, path: Path, off: int, n: int):
    with path.open("rb") as f:
        f.seek(off)
        data = f.read(n)
    return srv.c.patch(url, content=data, headers={**H, "Upload-Offset": str(off),
                                                   "Content-Type": "application/offset+octet-stream"})


def offset(srv, url) -> int:
    return int(srv.c.head(url, headers=H).headers["upload-offset"])


def cut_socket(srv, url, path: Path, off: int, nbytes: int):
    """Kirim sebagian potongan lalu tutup koneksi (klien terputus di tengah PATCH)."""
    import socket
    with path.open("rb") as f:
        f.seek(off)
        part = f.read(nbytes)
    host, port = srv.base.replace("http://", "").split(":")
    cookie = "; ".join(f"{k}={v}" for k, v in srv.c.cookies.items())
    head = (f"PATCH {url} HTTP/1.1\r\nHost: {host}:{port}\r\nTus-Resumable: 1.0.0\r\nUpload-Offset: {off}\r\n"
            f"Content-Type: application/offset+octet-stream\r\nContent-Length: {CHUNK}\r\n"
            f"Cookie: {cookie}\r\n\r\n").encode()
    with socket.create_connection((host, int(port))) as s:
        s.sendall(head + part)
    time.sleep(1)


def sweep(data: Path, now_offset_h: float) -> dict:
    code = ("import json,time; from app import lifecycle; "
            f"print(json.dumps(lifecycle.sweep(now=time.time()+{now_offset_h}*3600)))")
    env = dict(os.environ, VIDEO_APP_DATA=str(data))
    env.pop("DATABASE_URL", None)
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("workdir")
    ap.add_argument("--out")
    a = ap.parse_args()
    video, work = Path(a.video), Path(a.workdir)
    work.mkdir(parents=True, exist_ok=True)
    size, src_sha = video.stat().st_size, sha(video)
    res = {"file": video.name, "bytes": size, "sha256": src_sha}

    # 1. putus di 30 %, SIGKILL server di 60 %, lanjut setelah restart
    srv = Server(work)
    t0 = time.time()
    r = create(srv, video)
    url = r.headers["location"]
    off, sent, events = 0, 0, []
    while off < size:
        if not any(e["event"] == "socket putus" for e in events) and off + CHUNK > size * 0.3:
            cut_socket(srv, url, video, off, CHUNK // 2)
            sent += CHUNK // 2
            new = offset(srv, url)
            events.append({"event": "socket putus", "offset_before": off, "offset_server_after": new})
            off = new
            continue
        if not any(e["event"] == "SIGKILL server" for e in events) and off > size * 0.6:
            os.kill(srv.proc.pid, signal.SIGKILL)
            srv.proc.wait()
            srv = Server(work)                       # data & database sama
            new = offset(srv, url)
            events.append({"event": "SIGKILL server", "offset_before": off, "offset_server_after": new})
            off = new
            continue
        rr = patch(srv, url, video, off, min(CHUNK, size - off))
        assert rr.status_code == 204, (rr.status_code, rr.text[:200])
        sent += min(CHUNK, size - off)
        off = int(rr.headers["upload-offset"])
    info = srv.c.get(url).json()
    pid = info.get("project_id") or (info.get("project") or {}).get("id")
    for _ in range(60):
        if info.get("state") == "selesai" and pid:
            break
        time.sleep(1)
        info = srv.c.get(url).json()
        pid = info.get("project_id") or (info.get("project") or {}).get("id")
    wall = time.time() - t0
    stored = next((p for p in (work / "data" / "projects" / str(pid)).glob("source.*")), None) if pid else None
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(stored)], capture_output=True, text=True).stdout.strip() if stored else ""
    res["resume"] = {"events": events, "state": info.get("state"), "project": pid, "seconds": round(wall, 1),
                     "mb_per_s": round(size / MB / wall, 1), "sent_mb": round(sent / MB, 1),
                     "resent_mb": round((sent - size) / MB, 1),
                     "stored_sha256_equal": bool(stored) and sha(stored) == src_sha, "ffprobe_duration": probe}

    # 2. batal di tengah upload
    r = create(srv, video)
    url2 = r.headers["location"]
    patch(srv, url2, video, 0, CHUNK)
    parts_before = sorted(str(p) for p in (work / "data").rglob("*.part"))
    d = srv.c.delete(url2, headers=H)
    after = srv.c.head(url2, headers=H)
    parts_after = sorted(str(p) for p in (work / "data").rglob("*.part"))
    res["cancel"] = {"delete_status": d.status_code, "head_after": after.status_code,
                     "part_files_before": len(parts_before), "part_files_after": len(parts_after)}
    srv.stop()

    # 3. kuota unit (server terpisah, kuota bawaan 100 MB)
    qwork = work / "quota"
    os.environ["STORAGE_QUOTA_MB_DEFAULT"] = "100"
    try:
        qsrv = Server(qwork)
        r = create(qsrv, video)
        res["quota"] = {"status": r.status_code, "message": (r.json().get("detail") if r.headers.get(
            "content-type", "").startswith("application/json") else r.text)[:200]}
        qsrv.stop()
    finally:
        os.environ.pop("STORAGE_QUOTA_MB_DEFAULT")

    # 4. lease/job aktif menahan TTL; 5. yatim
    data = work / "data"
    orphan = data / "projects" / "yatim000000"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "sisa.bin").write_bytes(os.urandom(MB))
    old = time.time() - 7 * 86400
    os.utime(orphan / "sisa.bin", (old, old))
    os.utime(orphan, (old, old))
    proj_dir = data / "projects" / str(pid)
    first = sweep(data, 24 * 30)                           # sebulan kemudian, job analisis masih antre
    present_with_job = proj_dir.exists() and any(proj_dir.glob("source.*"))
    env = dict(os.environ, VIDEO_APP_DATA=str(data))
    env.pop("DATABASE_URL", None)
    subprocess.run([sys.executable, "-c", (
        "from app import db; from sqlalchemy import update; import time\n"
        "with db.engine().begin() as c:\n"
        f"    c.execute(update(db.jobs).where(db.jobs.c.project_id=='{pid}', db.jobs.c.status.in_(['antre','jalan']))"
        ".values(status='selesai', finished=time.time()))")], cwd=ROOT, env=env, check=True)
    second = sweep(data, 24 * 30)
    res["lifecycle"] = {"sweep_with_active_job": first, "media_kept_while_job_active": present_with_job,
                        "sweep_after_job_done": second,
                        "media_deleted_after_ttl": not any(proj_dir.glob("source.*")) if proj_dir.exists() else True,
                        "orphan_removed": not orphan.exists()}
    print(json.dumps(res, indent=1, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as exc:
        sys.exit(f"HTTP gagal: {exc}")
