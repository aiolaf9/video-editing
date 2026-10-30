"""P5.5.1: media dari pengguna tidak tepercaya tidak boleh membuat FFmpeg/FFprobe/Whisper membuka jaringan.

Berkas "video" yang sebenarnya playlist HLS/concat menunjuk ke server penjebak; server itu harus menerima 0
permintaan, apa pun ekstensi berkasnya dan apa pun bawaan versi FFmpeg (pembatasan eksplisit -protocol_whitelist)."""
import http.server
import threading

import numpy as np
import pytest

from app import media, transcribe


class _Trap(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        _Trap.hits.append(self.path)
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def trap():
    _Trap.hits = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Trap)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def _disguised(tmp_path, trap):
    hls = f"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n{trap}/segmen.ts\n#EXT-X-ENDLIST\n"
    files = [tmp_path / name for name in ("a.mp4", "b.m3u8", "c.ts", "d.mkv")]
    for f in files:
        f.write_text(hls)
    concat = tmp_path / "e.mp4"
    concat.write_text(f"ffconcat version 1.0\nfile {trap}/concat.mp4\nfile /etc/hostname\n")
    return [*files, concat]


def test_ffmpeg_and_ffprobe_never_fetch_from_untrusted_files(tmp_path, trap):
    for path in _disguised(tmp_path, trap):
        with pytest.raises(media.MediaError):
            media.probe(path)
        with pytest.raises(media.MediaError):
            media.decode_audio(path)
        assert media.audio_energy(path) == []
        media.make_thumbnail(path, tmp_path / f"{path.stem}.jpg", 1.0)
        with pytest.raises(Exception):
            media.run_ffmpeg(["-i", str(path), "-f", "null", "-"], duration=1, stall_timeout=30)
    assert _Trap.hits == []


def test_every_ffmpeg_input_is_restricted_to_local_protocols():
    args = ["-ss", "1", "-i", "a.mp4", "-stream_loop", "-1", "-i", "musik.mp3", "-loop", "1", "-i", "o.png", "out.mp4"]
    guarded = media.local_inputs(args)
    assert guarded.count("-protocol_whitelist") == 3
    for i, arg in enumerate(guarded):
        if arg == "-i":
            assert guarded[i - 2:i] == media.INPUT_PROTOCOLS
    assert media.local_inputs(guarded) == guarded          # idempoten


def test_whisper_receives_decoded_samples_not_a_path(monkeypatch, tmp_path):
    """faster-whisper/PyAV tidak pernah membuka berkas pengguna: audio di-decode FFmpeg (dibatasi) lalu diberikan
    sebagai array."""
    seen = {}

    class FakeModel:
        def transcribe(self, audio, **kw):
            seen["audio"] = audio
            return iter([]), type("Info", (), {"language": "id"})()

    monkeypatch.setattr(media, "decode_audio", lambda path: np.zeros(16000, dtype=np.float32) + 0.1)
    monkeypatch.setattr(transcribe, "_get_model", lambda: FakeModel())
    transcribe.transcribe(tmp_path / "berkas-pengguna.mp4", 1.0, language="id")
    assert isinstance(seen["audio"], np.ndarray)
