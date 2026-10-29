"""Benchmark Whisper SUNGGUHAN (faster-whisper) untuk server produksi.

Hasil benchmark antrean (queue_bench.py) memakai beban CPU pengganti; angka itu TIDAK boleh dianggap
sebagai performa Whisper. Skrip ini mengukur Whisper asli: waktu muat model, real-time factor (RTF =
waktu proses / durasi audio), RAM puncak, dan efek jumlah thread & proses paralel.

Aman secara default:
- Tidak mengunduh model kecuali --allow-download, dan itu pun hanya bila disk & RAM cukup.
- Pemeriksaan awal (--check) hanya membaca: paket terpasang, model ada di cache, disk, RAM, GPU.

Contoh
  python bench/whisper_bench.py --check --model small
  python bench/whisper_bench.py --model small --media rapat.mp4 --threads 2,4 --parallel 1,2 --out whisper.json
  # di server tanpa model: tambah --allow-download (unduh ±0,5 GB untuk small) setelah --check lolos

Gunakan rekaman UCAPAN sungguhan (mis. potongan video kajian/rapat 2-5 menit). Nada/sinyal sintetis tidak
mewakili beban Whisper (VAD melewati bagian tanpa ucapan) sehingga hasilnya ditandai tidak valid.
"""
import argparse
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Perkiraan ukuran unduhan (MB) model CTranslate2 faster-whisper.
MODEL_DISK_MB = {"tiny": 80, "base": 150, "small": 490, "medium": 1530, "large-v2": 3100, "large-v3": 3100}


def preflight(model: str) -> dict:
    """Hanya membaca: apa yang tersedia & apakah kapasitas cukup."""
    from app import resources
    from app.config import TASK_MEM_MB, _WHISPER_MEM

    out = {"model": model, "cpus": resources.effective_cpus(), "gpus": resources.gpus()}
    try:
        import faster_whisper  # noqa: F401
        import ctranslate2
        out["faster_whisper"] = True
        out["ctranslate2"] = ctranslate2.__version__
    except ImportError as exc:
        out["faster_whisper"] = False
        out["error"] = f"faster-whisper belum terpasang: {exc}"
    out["model_cached"] = model_path(model) is not None
    mem = resources.memory()
    need_ram = _WHISPER_MEM.get(model, TASK_MEM_MB["analisis"])
    out["ram_available_mb"] = mem and round(mem["available"] / 2**20)
    out["ram_needed_mb"] = need_ram
    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    cache.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(cache).free // 2**20
    out["disk_free_mb"], out["disk_needed_mb"] = free, MODEL_DISK_MB.get(model, 3100) * 2 + 1024
    out["ok_ram"] = mem is None or mem["available"] / 2**20 >= need_ram + 512
    out["ok_disk"] = out["model_cached"] or free >= out["disk_needed_mb"]
    return out


def model_path(model: str):
    try:
        from faster_whisper.utils import download_model
        return download_model(model, local_files_only=True)
    except Exception:
        return None


def media_duration(path: Path) -> float:
    from app import media
    return float(media.probe(path)["duration"])


def run_once(model: str, media_path: str, threads: int, device: str) -> dict:
    """Satu proses: muat model + transkripsi. Dipanggil sebagai subprocess agar RAM puncak terukur bersih."""
    from faster_whisper import WhisperModel

    from app import media

    t0 = time.time()
    m = WhisperModel(model, device=device, compute_type="int8" if device == "cpu" else "default",
                     cpu_threads=threads, local_files_only=True)
    load_s = time.time() - t0
    audio = media.decode_audio(Path(media_path))
    t1 = time.time()
    segments, info = m.transcribe(audio, language=None, vad_filter=True, beam_size=5)
    seg = list(segments)
    proc_s = time.time() - t1
    return {"threads": threads, "load_s": round(load_s, 2), "transcribe_s": round(proc_s, 2),
            "segments": len(seg), "language": info.language, "speech_s": round(sum(s.end - s.start for s in seg), 1),
            "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("WHISPER_MODEL", "small"))
    ap.add_argument("--media", help="file video/audio berisi ucapan sungguhan")
    ap.add_argument("--threads", default="", help="mis. 2,4 (bawaan: jatah adil & semua core)")
    ap.add_argument("--parallel", default="1", help="jumlah proses Whisper bersamaan, mis. 1,2")
    ap.add_argument("--device", default="cpu", choices=("cpu", "cuda"))
    ap.add_argument("--check", action="store_true", help="hanya pemeriksaan kapasitas (tidak menjalankan apa pun)")
    ap.add_argument("--allow-download", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--_child", nargs=2, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args._child:
        print(json.dumps(run_once(args.model, args.media, int(args._child[0]), args.device)))
        return

    pre = preflight(args.model)
    print(json.dumps(pre, indent=2))
    if args.check:
        return
    if not pre["faster_whisper"]:
        sys.exit("Berhenti: faster-whisper tidak tersedia.")
    if not pre["ok_ram"]:
        sys.exit(f"Berhenti: RAM tersedia {pre['ram_available_mb']} MB kurang dari kebutuhan model.")
    if not pre["model_cached"]:
        if not args.allow_download:
            sys.exit("Berhenti: model belum ada di cache. Jalankan ulang dengan --allow-download setelah "
                     "memastikan disk & kuota jaringan cukup.")
        if not pre["ok_disk"]:
            sys.exit(f"Berhenti: disk kosong {pre['disk_free_mb']} MB < {pre['disk_needed_mb']} MB.")
        from faster_whisper.utils import download_model
        t = time.time()
        download_model(args.model)
        print(f"Model diunduh dalam {time.time() - t:.0f} dtk")
    if not args.media:
        sys.exit("Berhenti: berikan --media berisi ucapan sungguhan.")

    from app import resources

    duration = media_duration(Path(args.media))
    thread_list = [int(x) for x in args.threads.split(",") if x] or sorted({resources.fair_share(),
                                                                          resources.effective_cpus()})
    results = []
    for par in [int(x) for x in args.parallel.split(",")]:
        for threads in thread_list:
            if par * threads > resources.effective_cpus() * 2:
                continue  # oversubscription > 2x: tidak relevan untuk produksi
            t = time.time()
            procs = [subprocess.Popen([sys.executable, __file__, "--model", args.model, "--media", args.media,
                                       "--device", args.device, "--_child", str(threads), "x"],
                                      stdout=subprocess.PIPE, text=True) for _ in range(par)]
            outs = [json.loads(p.communicate()[0].strip().splitlines()[-1]) for p in procs]
            wall = time.time() - t
            results.append({
                "parallel": par, "threads_each": threads, "wall_s": round(wall, 2),
                "rtf_mean": round(sum(o["transcribe_s"] for o in outs) / len(outs) / duration, 3),
                "audio_min_per_wall_min": round(par * duration / wall, 2),
                "load_s": max(o["load_s"] for o in outs), "peak_rss_mb_each": max(o["peak_rss_mb"] for o in outs),
                "speech_s": outs[0]["speech_s"], "language": outs[0]["language"]})
            print(json.dumps(results[-1]))
    valid = bool(results) and results[0]["speech_s"] > 0.3 * duration
    report = {"preflight": pre, "media": args.media, "duration_s": duration, "valid_speech_media": valid,
              "results": results}
    if not valid:
        print("PERINGATAN: media hampir tanpa ucapan; hasil tidak mewakili beban Whisper nyata.")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
