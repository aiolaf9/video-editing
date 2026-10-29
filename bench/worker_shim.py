"""Worker untuk benchmark: worker asli, hanya Whisper diganti beban CPU yang setara & dapat diulang.

Model Whisper tidak selalu bisa diunduh di lingkungan uji, dan durasinya sulit dibandingkan antar-run.
Pengganti: encode x264 dengan jumlah frame tetap per detik video (kerja tetap), memakai jumlah thread
yang SAMA dengan yang akan dipakai Whisper oleh kode yang diuji (0 = semua core, seperti ctranslate2).

    python bench/worker_shim.py <repo_kode> --queues ...
"""
import os
import sys

code = sys.argv.pop(1)
sys.path.insert(0, code)

from app import media, transcribe, worker  # noqa: E402

BURN_FRAMES_PER_SEC = float(os.environ.get("BENCH_BURN_FRAMES_PER_SEC", "8"))


def _whisper_threads() -> int:
    try:
        from app import resources  # kode baru (P3): sama dengan transcribe._load
        return transcribe.WHISPER_THREADS or resources.effective_cpus()
    except ImportError:
        return transcribe.WHISPER_THREADS  # kode lama: 0 = otomatis (semua core)


def fake_transcribe(path, duration, language=None, progress=None):
    frames = max(10, int(duration * BURN_FRAMES_PER_SEC))
    args = ["-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=25", "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "medium", "-threads", str(_whisper_threads()), "-f", "null", "-"]
    media.run_ffmpeg(args, frames / 25.0, (lambda f: progress(f)) if progress else None)
    return {"language": language or "id", "segments": [
        {"start": 0.5, "end": 3.0, "text": "Halo semua, ini uji beban.", "words": []}]}


transcribe.transcribe = fake_transcribe
sys.argv = [sys.argv[0]] + sys.argv[1:]
worker.main()
