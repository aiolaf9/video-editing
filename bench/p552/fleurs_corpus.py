"""P5.5.2-A: korpus ucapan ber-ground-truth dari Google FLEURS (CC-BY 4.0), untuk benchmark Whisper asli.

Mengunduh split `test` (Indonesia `id_id`, Inggris `en_us`) dari Hugging Face dan menyusun:
  <lang>_clean/NNN.wav  : ucapan tunggal + transkrip (WER per ucapan)
  id_dialog.wav        : ucapan pria & wanita bergantian, jeda 0,4 dtk ("podcast dua pembicara" yang DISUSUN dari
                         ucapan terpisah, bukan percakapan alami)
  id_noisy_snr10.wav   : id_dialog + noise pink, SNR 10 dB
  id_long.wav          : ±10 menit ucapan berurutan
Setiap gabungan disertai batas ucapan (awal/akhir suara terdeteksi dengan silencedetect) sebagai acuan timestamp.
Butuh akses ke huggingface.co (runner GitHub; sandbox pengembangan memblokirnya).

    python bench/p552/fleurs_corpus.py <folder> [--clean 20] [--long-seconds 600]
"""
import argparse
import csv
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

REPO = "google/fleurs"
RATE = 16000


def fetch(lang: str, dest: Path):
    from huggingface_hub import HfApi, hf_hub_download

    files = HfApi().list_repo_files(REPO, repo_type="dataset")
    tsv = next(f for f in files if re.fullmatch(rf"data/{lang}/test\.tsv", f))
    tar = next(f for f in files if re.fullmatch(rf"data/{lang}/audio/test\.tar\.gz", f))
    info = HfApi().dataset_info(REPO)
    rows = list(csv.reader(io.open(hf_hub_download(REPO, tsv, repo_type="dataset"), encoding="utf-8"),
                           delimiter="\t", quoting=csv.QUOTE_NONE))
    audio = dest / f"raw_{lang}"
    audio.mkdir(parents=True, exist_ok=True)
    with tarfile.open(hf_hub_download(REPO, tar, repo_type="dataset")) as t:
        for m in t.getmembers():
            if m.isfile() and m.name.endswith(".wav"):
                (audio / Path(m.name).name).write_bytes(t.extractfile(m).read())
    utts = []
    for r in rows:
        wav = next((c for c in r if c.endswith(".wav")), None)
        if wav and (audio / wav).exists():
            i = r.index(wav)
            gender = next((c for c in r if c.upper() in ("MALE", "FEMALE")), "")
            utts.append({"file": str(audio / wav), "text": r[i + 1], "gender": gender.upper()})
    return utts, info.sha


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], check=True)


def duration(p) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def speech_bounds(p) -> tuple[float, float]:
    """Awal & akhir suara dalam satu ucapan (silencedetect -35 dB, 0,15 dtk)."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(p), "-af", "silencedetect=n=-35dB:d=0.15",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    d = duration(p)
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", err)]
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", err)]
    begin = ends[0] if starts and starts[0] < 0.05 and ends else 0.0
    end = starts[-1] if starts and (not ends or starts[-1] > ends[-1]) else d
    return round(begin, 3), round(end, 3)


def rms_db(p) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(p), "-af", "astats=measure_perchannel=0",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(re.findall(r"RMS level dB: (-?[0-9.]+)", err)[-1])


def concat(utts, out: Path, gap=0.4):
    """Gabung ucapan (mono 16 kHz) dengan jeda; kembalikan batas tiap ucapan pada timeline gabungan."""
    lst, t, bounds = out.with_suffix(".txt"), 0.0, []
    silence = out.parent / "gap.wav"
    ffmpeg("-f", "lavfi", "-i", f"anullsrc=r={RATE}:cl=mono", "-t", str(gap), str(silence))
    lines = []
    for u in utts:
        d = duration(u["file"])
        b, e = speech_bounds(u["file"])
        bounds.append({"start": round(t + b, 3), "end": round(t + e, 3), "text": u["text"], "gender": u["gender"]})
        lines += [f"file '{u['file']}'", f"file '{silence}'"]
        t += d + gap
    lst.write_text("\n".join(lines))
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-ar", str(RATE), "-ac", "1", str(out))
    return bounds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--clean", type=int, default=20)
    ap.add_argument("--long-seconds", type=int, default=600)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sets, rev = {}, None
    for lang in ("id_id", "en_us"):
        utts, rev = fetch(lang, out)
        short = lang[:2]
        clean = out / f"{short}_clean"
        clean.mkdir(exist_ok=True)
        items = []
        for k, u in enumerate(utts[: a.clean]):
            dst = clean / f"{k:03d}.wav"
            ffmpeg("-i", u["file"], "-ar", str(RATE), "-ac", "1", str(dst))
            items.append({"file": str(dst), "text": u["text"], "duration": duration(dst)})
        sets[f"{short}_clean"] = {"kind": "utterances", "lang": short, "items": items}
        if short != "id":
            continue
        male = [u for u in utts[a.clean:] if u["gender"] == "MALE"]
        female = [u for u in utts[a.clean:] if u["gender"] == "FEMALE"]
        dialog = [u for pair in zip(male[:12], female[:12]) for u in pair]
        b = concat(dialog, out / "id_dialog.wav")
        sets["id_dialog"] = {"kind": "timeline", "lang": "id", "file": str(out / "id_dialog.wav"), "bounds": b}
        speech = out / "id_dialog.wav"
        d = duration(speech)
        # Noise pink diskalakan agar RMS-nya tepat 10 dB di bawah RMS ucapan (keseluruhan berkas).
        noise = out / "pink.wav"
        ffmpeg("-f", "lavfi", "-i", f"anoisesrc=c=pink:r={RATE}:a=0.5:d={d}", "-ac", "1", str(noise))
        gain = rms_db(speech) - 10 - rms_db(noise)
        ffmpeg("-i", str(speech), "-i", str(noise), "-filter_complex",
               f"[1]volume={gain:.2f}dB[n];[0][n]amix=inputs=2:normalize=0:duration=first",
               "-ar", str(RATE), "-ac", "1", str(out / "id_noisy_snr10.wav"))
        sets["id_noisy_snr10"] = {"kind": "timeline", "lang": "id", "file": str(out / "id_noisy_snr10.wav"),
                                  "bounds": b, "snr_db": 10}
        pool, total, long_utts = utts[a.clean + 24:] + utts[a.clean:a.clean + 24], 0.0, []
        for u in pool:
            if total >= a.long_seconds:
                break
            long_utts.append(u)
            total += duration(u["file"]) + 0.4
        sets["id_long"] = {"kind": "timeline", "lang": "id", "file": str(out / "id_long.wav"),
                           "bounds": concat(long_utts, out / "id_long.wav")}
    for s in sets.values():
        if s["kind"] == "timeline":
            s["duration"] = duration(s["file"])
    (out / "corpus.json").write_text(json.dumps({"source": f"huggingface.co/datasets/{REPO}", "revision": rev,
                                                 "license": "CC-BY-4.0", "sets": sets}, ensure_ascii=False, indent=1))
    print(json.dumps({k: (len(v.get("items", [])) or round(v["duration"])) for k, v in sets.items()}))


if __name__ == "__main__":
    main()
