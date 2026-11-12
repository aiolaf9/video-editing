import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("STORY_DATA", tempfile.mkdtemp(prefix="story-test-"))
os.environ["STORY_PLANNER"] = "heuristic"
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

SENTENCES = {
    0: [("Bayangkan, selama sepuluh tahun warga harus berjalan tiga kilometer untuk air bersih.", 0.5),
        ("Dulu sumurnya kering dan anak-anak sering sakit.", 7.0)],
    1: [("Kami mulai membangun pipa bersama warga secara gotong royong.", 0.4),
        ("Prosesnya tiga bulan, dengan bantuan relawan dari kota.", 6.0)],
    2: [("Sekarang air mengalir ke dua ratus rumah, hasilnya anak-anak lebih sehat.", 0.5),
        ("Semoga program ini terus berlanjut untuk generasi berikutnya.", 6.5)],
}


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def fake_transcript(idx: int) -> dict:
    segs = []
    for text, t0 in SENTENCES[idx]:
        words, t = [], t0
        for w in text.split():
            words.append({"start": round(t, 2), "end": round(t + 0.32, 2), "word": w, "p": 0.95})
            t += 0.4
        segs.append({"start": words[0]["start"], "end": words[-1]["end"], "text": text, "words": words})
    return {"language": "id", "segments": segs}


@pytest.fixture(scope="session")
def videos(tmp_path_factory) -> list[Path]:
    """Tiga video berbeda durasi & orientasi (dua horizontal, satu vertikal)."""
    d = tmp_path_factory.mktemp("media")
    specs = [("1280x720", 13, "testsrc2"), ("720x1280", 12, "smptebars"), ("1280x720", 12, "rgbtestsrc")]
    out = []
    for i, (size, dur, src) in enumerate(specs):
        p = d / f"liputan{i + 1}.mp4"
        _ffmpeg("-f", "lavfi", "-i", f"{src}=size={size}:rate=25:duration={dur}",
                "-f", "lavfi", "-i", f"sine=frequency={300 + 100 * i}:duration={dur}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p))
        out.append(p)
    return out


@pytest.fixture
def patch_whisper(monkeypatch):
    from storymaker import transcribe

    def fake(path, duration, language=None, progress=None):
        idx = int(Path(path).parent.name[1:]) - 1
        return fake_transcript(idx % 3)

    monkeypatch.setattr(transcribe, "transcribe", fake)
