"""Uji di DALAM image produksi: FFmpeg/FFprobe image itu sendiri tidak membuka jaringan dari media tidak tepercaya
(playlist HLS / concat yang menyamar sebagai video). Server penjebak lokal harus menerima 0 permintaan.

    docker run --rm -v "$PWD/deploy/checks:/c:ro" --entrypoint python video-editing:latest /c/media_trap.py
"""
import http.server
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, "/app")
from app import media  # noqa: E402

hits = []


class Trap(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        hits.append(self.path)
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Trap)
threading.Thread(target=srv.serve_forever, daemon=True).start()
trap = f"http://127.0.0.1:{srv.server_address[1]}"
work = Path(tempfile.mkdtemp())
hls = f"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n{trap}/segmen.ts\n#EXT-X-ENDLIST\n"
files = []
for name in ("a.mp4", "b.m3u8", "c.ts", "d.mkv"):
    (work / name).write_text(hls)
    files.append(work / name)
(work / "e.mp4").write_text(f"ffconcat version 1.0\nfile {trap}/concat.mp4\n")
files.append(work / "e.mp4")

for path in files:
    for fn in (media.probe, media.decode_audio, media.audio_energy):
        try:
            fn(path)
        except Exception:
            pass
    # FFprobe/FFmpeg tanpa pembatasan aplikasi: menguji bawaan versi FFmpeg di image ini (informasi).
    subprocess.run(["ffprobe", "-v", "error", "-show_format", str(path)], capture_output=True, timeout=60)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, timeout=60)

problems = []

# Positif: MP4 normal dengan audio tetap dapat dibaca lewat pembatasan file,pipe.
normal = work / "normal.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=3", "-f", "lavfi",
                "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", str(normal)], check=True, timeout=120)
info = media.probe(normal)
samples = media.decode_audio(normal)
if not (info["has_audio"] and info["width"] == 640 and samples.size > 16000):
    problems.append(f"MP4 normal tidak terbaca dengan benar: {info} sampel={samples.size}")

# Negatif: playlist/concat yang mencoba membaca berkas lokal lain (bukan media).
for name, body in {"lfi1.mp4": "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nfile:///etc/passwd\n#EXT-X-ENDLIST\n",
                   "lfi2.m3u8": "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n/app/app/config.py\n#EXT-X-ENDLIST\n",
                   "lfi3.mp4": "ffconcat version 1.0\nfile /etc/passwd\n",
                   "lfi4.mp4": "ffconcat version 1.0\nfile ../../../../etc/hostname\n"}.items():
    (work / name).write_text(body)
    for fn in (media.probe, media.decode_audio):
        try:
            fn(work / name)
            problems.append(f"{name}: {fn.__name__} tidak menolak")
        except media.MediaError:
            pass

version = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
print(f"{version}\npermintaan ke server penjebak: {len(hits)} {hits}\nMP4 normal: {info['width']}x{info['height']} "
      f"audio={info['has_audio']} sampel={samples.size}\nmasalah: {problems or 'tidak ada'}")
sys.exit(1 if hits or problems else 0)
