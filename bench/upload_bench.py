"""Benchmark upload (P4): upload lama (satu request multipart) vs upload bertahap (tus) ke server uvicorn sungguhan.

    python bench/upload_bench.py --workdir /tmp/ub --sizes 100,500,2048 --out upload.json
Diukur di proses SERVER: waktu, throughput, RAM puncak (VmHWM), CPU, dan byte yang ditulis ke disk
(/proc/<pid>/io write_bytes) -- upload lama menulis file dua kali (spool multipart + salinan).
Skenario putus: upload diputus di ±50% (socket ditutup di tengah potongan) lalu dilanjutkan; dihitung byte
yang harus dikirim ulang. Upload lama harus mengulang dari nol.
Server memakai EMBEDDED_WORKERS=0 (analisis tidak berjalan) agar yang terukur hanya upload.
"""
import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
MB = 1024 * 1024
H = {"Tus-Resumable": "1.0.0"}


def proc_stats(pid):
    out = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith(("VmHWM:", "VmRSS:")):
            out[line.split(":")[0]] = int(line.split()[1]) / 1024
    io = dict(l.split(": ") for l in Path(f"/proc/{pid}/io").read_text().splitlines())
    ticks = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    out["write_mb"] = int(io["write_bytes"]) / MB
    out["cpu_s"] = (int(ticks[11]) + int(ticks[12])) / os.sysconf("SC_CLK_TCK")
    return out


class Server:
    def __init__(self, work: Path):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        env = dict(os.environ, VIDEO_APP_DATA=str(work / "data"), EMBEDDED_WORKERS="0", ADMIN_EMAIL="bench@ub.local",
                   ADMIN_PASSWORD="rahasia-bench-123", MAX_UPLOAD_MB="4096", COOKIE_SECURE="0")
        env.pop("DATABASE_URL", None)
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(self.port)],
                                     cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(100):
            try:
                if httpx.get(self.base + "/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        self.c = httpx.Client(base_url=self.base, timeout=600)
        self.c.post("/api/auth/login", json={"email": "bench@ub.local", "password": "rahasia-bench-123"})

    def stop(self):
        self.proc.terminate()
        self.proc.wait(30)


def make_file(work: Path, size_mb: int) -> Path:
    """Video valid kecil + padding (ffprobe tetap membacanya) sebesar size_mb, isi acak sebagian agar tidak sparse."""
    path = work / f"upload-{size_mb}.mp4"
    if path.exists() and path.stat().st_size == size_mb * MB:
        return path
    sample = work / "sample.mp4"
    if not sample.exists():
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=1280x720:rate=30:duration=10", "-f", "lavfi", "-i", "sine=duration=10",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(sample)], check=True)
    block = os.urandom(MB)
    with path.open("wb") as fh:
        fh.write(sample.read_bytes())
        while fh.tell() < size_mb * MB:
            fh.write(block[: min(MB, size_mb * MB - fh.tell())])
    return path


def legacy_upload(srv: Server, path: Path) -> dict:
    with path.open("rb") as fh:
        r = srv.c.post("/api/projects", files={"file": (path.name, fh, "video/mp4")})
    return {"status": r.status_code}


def tus_upload(srv: Server, path: Path, chunk=32 * MB, cut_at=None) -> dict:
    size = path.stat().st_size
    meta = "filename " + base64.b64encode(path.name.encode()).decode()
    url = srv.c.post("/api/uploads", headers={**H, "Upload-Length": str(size), "Upload-Metadata": meta}).headers["location"]
    sent, off, cut_done = 0, 0, False
    with path.open("rb") as fh:
        while off < size:
            fh.seek(off)
            data = fh.read(chunk)
            if cut_at is not None and not cut_done and off + len(data) > cut_at:
                # putus di tengah potongan: kirim sebagian lalu tutup socket (tanpa checksum = klien tus umum)
                part = data[: max(1, cut_at - off)]
                host, port = srv.base.replace("http://", "").split(":")
                cookie = "; ".join(f"{k}={v}" for k, v in srv.c.cookies.items())
                head = (f"PATCH {url} HTTP/1.1\r\nHost: {host}:{port}\r\nTus-Resumable: 1.0.0\r\nUpload-Offset: {off}\r\n"
                        f"Content-Type: application/offset+octet-stream\r\nContent-Length: {len(data)}\r\n"
                        f"Cookie: {cookie}\r\n\r\n").encode()
                with socket.create_connection((host, int(port))) as s:
                    s.sendall(head + part)
                sent += len(part)
                cut_done = True
                time.sleep(1)
                off = int(srv.c.head(url, headers=H).headers["upload-offset"])  # lanjut dari posisi server
                continue
            r = srv.c.patch(url, content=data, headers={**H, "Upload-Offset": str(off),
                                                        "Content-Type": "application/offset+octet-stream"})
            assert r.status_code == 204, r.text
            sent += len(data)
            off = int(r.headers["upload-offset"])
    state = srv.c.get(url).json()["state"]
    return {"status": state, "bytes_sent_mb": round(sent / MB, 1), "resent_mb": round((sent - size) / MB, 2)}


def run(srv, label, fn, size_mb):
    before = proc_stats(srv.proc.pid)
    t = time.time()
    extra = fn()
    wall = time.time() - t
    after = proc_stats(srv.proc.pid)
    return {"method": label, "size_mb": size_mb, "seconds": round(wall, 2), "mb_per_s": round(size_mb / wall, 1),
            "server_write_mb": round(after["write_mb"] - before["write_mb"]),
            "server_cpu_s": round(after["cpu_s"] - before["cpu_s"], 1),
            "server_rss_peak_mb": round(after["VmHWM"]), "server_rss_now_mb": round(after["VmRSS"]), **extra}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--sizes", default="100,500,2048")
    ap.add_argument("--out")
    args = ap.parse_args()
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    rows = []
    for size_mb in [int(x) for x in args.sizes.split(",")]:
        path = make_file(work, size_mb)
        for label, fn in (("lama (multipart)", lambda s: legacy_upload(s, path)),
                          ("tus", lambda s: tus_upload(s, path)),
                          ("tus, putus di 50% lalu lanjut", lambda s: tus_upload(s, path, cut_at=path.stat().st_size // 2))):
            data_root = Path(tempfile.mkdtemp(dir=work))
            srv = Server(data_root)  # server baru per run: RAM puncak tidak tercampur
            try:
                row = run(srv, label, lambda: fn(srv), size_mb)
            finally:
                srv.stop()
                shutil.rmtree(data_root, ignore_errors=True)  # hemat disk untuk ukuran berikutnya
            if label.startswith("lama"):
                row["resent_if_cut_mb"] = round(size_mb / 2, 1)  # upload lama: putus = ulang dari nol
            print(json.dumps(row))
            rows.append(row)
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
