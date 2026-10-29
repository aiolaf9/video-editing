import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Data uji disimpan di folder sementara, bukan di ./data
os.environ.setdefault("VIDEO_APP_DATA", tempfile.mkdtemp(prefix="ve-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    """Video 1280x720 berdurasi 8 detik dengan nada sinus sebagai 'suara'."""
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=8",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path))
    return path


@pytest.fixture(scope="session")
def sample_music(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("media") / "music.mp3"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=3", "-c:a", "libmp3lame", str(path))
    return path


@pytest.fixture
def fake_transcript() -> dict:
    words = ["Kenapa", "video", "ini", "viral?", "Karena", "editnya", "rapi", "sekali.", "Ayo", "coba!"]
    out, t = [], 0.5
    for w in words:
        out.append({"start": round(t, 2), "end": round(t + 0.5, 2), "word": w})
        t += 0.65
    return {"language": "id", "segments": [
        {"start": out[0]["start"], "end": out[3]["end"], "text": " ".join(w["word"] for w in out[:4]), "words": out[:4]},
        {"start": out[4]["start"], "end": out[7]["end"], "text": " ".join(w["word"] for w in out[4:8]), "words": out[4:8]},
        {"start": out[8]["start"], "end": out[9]["end"], "text": " ".join(w["word"] for w in out[8:]), "words": out[8:]},
    ]}
