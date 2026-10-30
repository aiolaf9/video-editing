"""Server fixture yang DIKENDALIKAN untuk uji staging (bukti utama; layanan internet pihak ketiga hanya pelengkap).

    python fixture_server.py public    # di jaringan fx-public (subnet yang dianggap publik oleh proxy)
    python fixture_server.py internal  # di jaringan fx-private (10.250.0.0/24): tidak boleh pernah dihubungi

Mode public:
  /media/ok.mp4          video 6 dtk + audio, dibuat saat start; /media/ok.mp4.sha256 = hash isinya
  /media/slow.mp4        video 20 dtk dikirim lambat (untuk uji restart/crash di tengah unduhan); .sha256 juga
  /redir/internal        302 -> http://internal-target/secret-p551   (nama DNS -> IP privat)
  /redir/metadata        302 -> http://169.254.169.254/latest/meta-data/fixture-p551
  /redir/loopback        302 -> http://127.0.0.1/secret-p551
  /hits                  JSON daftar permintaan yang diterima (path + alamat peminta)
Mode internal: semua path dicatat di /hits (yang dipanggil dari runner lewat docker exec, bukan dari proxy).
"""
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

MODE = sys.argv[1] if len(sys.argv) > 1 else "public"
WORK = Path(os.environ.get("FIXTURE_DIR", "/tmp/fixture"))
SLOW_BPS = int(os.environ.get("FIXTURE_SLOW_BPS", "48000"))
PORT = int(os.environ.get("FIXTURE_PORT", "80"))   # port 80: satu-satunya port http yang diizinkan proxy
hits: list = []
lock = threading.Lock()


def make(name: str, seconds: int) -> str:
    path = WORK / name
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(path)], check=True)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/plain", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self.path != "/hits":
            with lock:
                hits.append({"path": self.path, "dari": self.client_address[0], "t": round(time.time(), 1)})
        if self.path == "/hits":
            return self._send(200, json.dumps(hits).encode(), "application/json")
        if MODE == "internal":
            return self._send(200, b"RAHASIA-INTERNAL-P551")
        redirects = {"/redir/internal": "http://internal-target/secret-p551",
                     "/redir/metadata": "http://169.254.169.254/latest/meta-data/fixture-p551",
                     "/redir/loopback": "http://127.0.0.1/secret-p551"}
        if self.path in redirects:
            return self._send(302, headers={"Location": redirects[self.path]})
        name = self.path.removeprefix("/media/")
        if name.endswith(".sha256") and (WORK / name[:-7]).exists():
            return self._send(200, SHAS[name[:-7]].encode())
        if name in SHAS:
            data = (WORK / name).read_bytes()
            if name != "slow.mp4" or self.command == "HEAD":
                return self._send(200, data, "video/mp4")
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            step = max(1, SLOW_BPS // 4)
            try:
                for i in range(0, len(data), step):
                    self.wfile.write(data[i:i + step])
                    self.wfile.flush()
                    time.sleep(0.25)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        return self._send(404, b"tidak ada")


WORK.mkdir(parents=True, exist_ok=True)
SHAS = {"ok.mp4": make("ok.mp4", 6), "slow.mp4": make("slow.mp4", 20)} if MODE == "public" else {}
print(f"fixture {MODE} siap: {SHAS}", flush=True)
http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
