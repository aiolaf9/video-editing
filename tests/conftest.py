import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Data uji disimpan di folder sementara, bukan di ./data, dan dihapus lagi di akhir sesi (pytest_unconfigure).
_OWN_TMP: list[str] = []
if "VIDEO_APP_DATA" not in os.environ:
    os.environ["VIDEO_APP_DATA"] = tempfile.mkdtemp(prefix="ve-test-")
    _OWN_TMP.append(os.environ["VIDEO_APP_DATA"])
os.environ.setdefault("EMBEDDED_WORKERS", "1")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("ADMIN_PASSWORD", "rahasia-admin-123")
os.environ.setdefault("MAX_PENDING_PER_USER", "50")
# Database uji selalu baru: skema dibuat lewat migrasi Alembic (juga di PostgreSQL).
os.environ.setdefault("AUTO_MIGRATE", "1")
# Tidak pernah mengunduh model AI dalam regresi (CI & lokal); tes -m heavy mengizinkannya sendiri.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
# Pembersihan media dijalankan eksplisit oleh tes (lifecycle.sweep(now=...)), bukan oleh worker bawaan.
os.environ.setdefault("CLEANUP_INTERVAL", "1000000")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_configure(config):
    # Folder sementara pytest di luar DATA_DIR: aplikasi mengarahkan tempfile ke DATA_DIR/tmp dan
    # tes pembersihan (sweep dengan waktu di masa depan) akan menganggapnya file tertinggal.
    if not config.option.basetemp:
        config.option.basetemp = tempfile.mkdtemp(prefix="ve-pytest-")
        _OWN_TMP.append(str(config.option.basetemp))


def pytest_unconfigure(config):
    # Tanpa ini setiap sesi tes meninggalkan ratusan MB (video uji, preview, render) di /tmp.
    import shutil

    for path in _OWN_TMP:
        shutil.rmtree(path, ignore_errors=True)


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


@pytest.fixture(scope="session")
def app_client():
    """Klien tanpa login; lifespan aktif sehingga worker bawaan berjalan."""
    from fastapi.testclient import TestClient

    from app import main

    with TestClient(main.app) as c:
        yield c


def login_client(email: str, password: str):
    from fastapi.testclient import TestClient

    from app import main

    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture(scope="session")
def client(app_client):
    """Klien yang sudah login sebagai admin."""
    return login_client(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])
