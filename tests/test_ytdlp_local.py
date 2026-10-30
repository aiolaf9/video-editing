"""yt-dlp ASLI (tanpa mock) mengunduh dari server HTTP lokal: file tunggal dan DASH video+audio terpisah yang
harus digabung FFmpeg. Membuktikan fitur "ambil dari link" berjalan dengan dependensi yt-dlp yang terpasang
(tanpa mutagen, lihat docs/LICENSES.md). Tidak memakai internet."""
import functools
import http.server
import json
import subprocess
import threading
from importlib import metadata

import pytest

from conftest import _ffmpeg


class _Job:
    def update(self, progress=None, message=None):
        pass


@pytest.fixture(scope="module")
def media_server(tmp_path_factory, sample_video):
    root = tmp_path_factory.mktemp("ytsrv")
    _ffmpeg("-i", str(sample_video), "-t", "3", "-c", "copy", str(root / "single.mp4"))
    _ffmpeg("-i", str(root / "single.mp4"), "-map", "0:v", "-map", "0:a", "-c", "copy", "-f", "dash",
            "-seg_duration", "1", "-adaptation_sets", "id=0,streams=v id=1,streams=a", str(root / "manifest.mpd"))
    handler = functools.partial(_QuietHandler, directory=str(root))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _streams(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return sorted(s["codec_type"] for s in json.loads(out)["streams"])


@pytest.mark.parametrize("name", ["single.mp4", "manifest.mpd"])
def test_real_ytdlp_download(client, media_server, trust_loopback, name):
    """Server lokal = sumber internal tepercaya (impor admin). Jalur IP publik: tests/test_p54_ssrf.py."""
    from app import pipeline, storage

    pid = storage.create_project("uji yt-dlp")["id"]
    source = pipeline.download(_Job(), pid, f"{media_server}/{name}", trusted=True)
    path = storage.project_dir(pid) / source
    assert path.suffix == ".mp4" and path.stat().st_size > 0
    assert _streams(path) == ["audio", "video"]       # DASH: video & audio terpisah digabung
    assert storage.load_project(pid)["status"] == "antre"


def test_ytdlp_dependencies_without_mutagen():
    """Dependensi opsional yt-dlp yang dipakai tetap terpasang; versi yt-dlp-ejs cocok dengan yt-dlp
    (bila tidak cocok, yt-dlp diam-diam melewati solver JS YouTube dari paket ini)."""
    from yt_dlp.extractor.youtube.jsc._builtin import vendor

    from yt_dlp import dependencies as deps

    for mod in ("brotli", "certifi", "requests", "urllib3", "websockets", "yt_dlp_ejs"):
        assert getattr(deps, mod, None) is not None, f"dependensi yt-dlp hilang: {mod}"
    assert deps.Cryptodome.AES is not None, "dependensi yt-dlp hilang: pycryptodomex"
    ejs = metadata.version("yt-dlp-ejs").split(".")[:2]
    assert ejs == vendor.VERSION.split(".")[:2], f"yt-dlp-ejs {ejs} tidak cocok dengan yt-dlp (butuh {vendor.VERSION})"
