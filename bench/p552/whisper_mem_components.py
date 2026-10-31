"""P5.5.2 W5: komponen memori transkripsi yang BERGANTUNG DURASI, diukur tanpa model Whisper.

Jalur aplikasi (app.transcribe -> WhisperModel.transcribe, faster-whisper 1.2.1, vad_filter=True):
  1. media.decode_audio: stdout FFmpeg (bytes) + salinan float32 numpy
  2. VAD Silero (onnx) atas SELURUH audio (dipad)
  3. collect_chunks + np.concatenate: salinan audio ucapan
  4. FeatureExtractor atas SELURUH audio ucapan sekaligus (STFT + mel)
Model & decoder (tetap per model) tidak termasuk di sini; diukur terpisah di runner.
Kasus terburuk: seluruh audio dianggap ucapan pada langkah 3-4 (VAD tetap dijalankan atas audio penuh).

    python bench/p552/whisper_mem_components.py --minutes 1,10,30,60 [--n-mels 80]
Setiap durasi diukur di subprocess terpisah; puncak = VmHWM - VmRSS awal (setelah impor).
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def rss_mb(key="VmRSS"):
    return int(Path("/proc/self/status").read_text().split(f"{key}:")[1].split()[0]) / 1024


def child(minutes: float, n_mels: int, step: str):
    import numpy as np  # noqa: F401
    from faster_whisper.feature_extractor import FeatureExtractor
    from faster_whisper.vad import VadOptions, collect_chunks, get_speech_timestamps

    from app import media

    tmp = Path(tempfile.mkdtemp())
    wav = tmp / "a.wav"
    # Ucapan tiruan berenergi (noise berwarna) agar VAD bekerja atas seluruh sinyal; hanya untuk memori, bukan kualitas.
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"anoisesrc=c=pink:r=16000:a=0.3:d={minutes * 60}", "-ac", "1", str(wav)], check=True)
    fe = FeatureExtractor(feature_size=n_mels)
    base = rss_mb()
    out = {"minutes": minutes, "n_mels": n_mels, "step": step, "baseline_mb": round(base, 1)}
    audio = media.decode_audio(wav)
    out["audio_mb"] = round(audio.nbytes / 2**20, 1)
    out["after_decode_peak_mb"] = round(rss_mb("VmHWM") - base, 1)
    if step in ("vad", "all"):
        ts = get_speech_timestamps(audio, VadOptions())
        out["vad_segments"] = len(ts)
        out["after_vad_peak_mb"] = round(rss_mb("VmHWM") - base, 1)
    if step in ("features", "all"):
        chunks, _ = collect_chunks(audio, [{"start": 0, "end": len(audio)}])   # terburuk: semua ucapan
        speech = np.concatenate(chunks, axis=0)
        feats = fe(speech)
        out["features_mb"] = round(feats.nbytes / 2**20, 1)
        out["after_features_peak_mb"] = round(rss_mb("VmHWM") - base, 1)
    out["peak_total_over_baseline_mb"] = round(rss_mb("VmHWM") - base, 1)
    print(json.dumps(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", default="1,10,30,60")
    ap.add_argument("--n-mels", type=int, default=80)
    ap.add_argument("--step", default="all", choices=("decode", "vad", "features", "all"))
    ap.add_argument("--child", nargs=3, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        child(float(a.child[0]), int(a.child[1]), a.child[2])
        return
    for m in a.minutes.split(","):
        r = subprocess.run([sys.executable, __file__, "--child", m, str(a.n_mels), a.step],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip()[-500:], flush=True)


if __name__ == "__main__":
    main()
