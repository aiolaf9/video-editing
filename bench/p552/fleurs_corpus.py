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
# Urutan kolom TSV FLEURS (tanpa baris header), sesuai skrip dataset google/fleurs.
FLEURS_COLUMNS = ["id", "file_name", "raw_transcription", "transcription", "phonemes", "num_samples", "gender"]
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
        wav = next((c for c in r if c.strip().endswith(".wav")), None)
        if wav and (audio / wav.strip()).exists():
            i = r.index(wav)
            gender = next((c.strip().upper() for c in r if c.strip().upper() in ("MALE", "FEMALE")), "")
            utts.append({"file": str(audio / wav.strip()), "text": r[i + 1], "gender": gender})
    # Diagnostik (run CI #17 gagal karena daftar pria/wanita kosong; isi TSV tidak dapat dilihat dari sandbox).
    # TSV FLEURS tidak berheader: nama kolom dipetakan dari urutan terdokumentasi bila jumlah kolom = 7. Teks ucapan
    # tidak dicetak (hanya panjangnya); nilai lain dipotong 24 karakter. Data publik (CC-BY 4.0), tanpa data pribadi.
    cols = sorted({len(r) for r in rows})
    names = FLEURS_COLUMNS if cols == [len(FLEURS_COLUMNS)] else [f"kolom{i}" for i in range(max(cols or [0]))]
    sample = {}
    if rows:
        for name, value in zip(names, rows[0]):
            sample[name] = f"<{len(value)} karakter>" if "transcription" in name else value[:24]
    gender_col = {}
    if cols == [len(FLEURS_COLUMNS)]:
        for r in rows:
            gender_col[r[-1].strip() or "(kosong)"] = gender_col.get(r[-1].strip() or "(kosong)", 0) + 1
    genders = {g or "(kosong)": sum(u["gender"] == g for u in utts) for g in {u["gender"] for u in utts}}
    print("DIAGNOSTIK " + json.dumps({"lang": lang, "tsv_rows": len(rows), "kolom_per_baris": cols, "kolom": names,
                                      "utts_cocok_audio": len(utts), "gender_terbaca": genders,
                                      "isi_kolom_gender_mentah": gender_col, "contoh_baris": sample},
                                     ensure_ascii=False), flush=True)
    if not utts:
        raise SystemExit(f"FLEURS {lang}: tidak ada ucapan yang cocok antara TSV & audio")
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
    # Hening di akhir berkas: FFmpeg mencetak silence_start lalu silence_end tepat di akhir berkas (EOF). Dulu kasus ini
    # dibaca sebagai "tidak ada hening akhir" sehingga akhir ucapan = akhir berkas (salah, P5.5.2 exp gt).
    trailing = starts and (not ends or starts[-1] > ends[-1] or abs(ends[-1] - d) < 0.05)
    end = starts[-1] if trailing else d
    if end <= begin:          # berkas tanpa suara terdeteksi: tidak ada batas yang bermakna, pakai seluruh berkas
        begin, end = 0.0, d
    return round(begin, 3), round(end, 3)


def rms_db(p) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(p), "-af", "astats=measure_perchannel=0",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    found = re.findall(r"RMS level dB: (-?inf|-?[0-9.]+)", err)
    return float(found[-1]) if found else float("-inf")      # audio senyap total: astats mencetak -inf


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
        if len(male) >= 12 and len(female) >= 12:
            dialog, speaker_source = [u for pair in zip(male[:12], female[:12]) for u in pair], "fleurs_gender"
        else:
            # Gender tidak terbaca: ucapan berurutan, HANYA untuk akurasi transkripsi gabungan. Bukan percakapan dua
            # pembicara terverifikasi; evaluasi pergantian pembicara/diarization tetap NOT VERIFIED.
            dialog, speaker_source = utts[a.clean:a.clean + 24], "unknown"
            print(f"PERINGATAN: gender pria/wanita {len(male)}/{len(female)} — fallback ucapan berurutan, "
                  "speaker_source=unknown", flush=True)
        if len(dialog) < 2:
            raise SystemExit("FLEURS id: ucapan tidak cukup untuk set gabungan")
        b = concat(dialog, out / "id_dialog.wav")
        dialog_dur = duration(out / "id_dialog.wav")
        if dialog_dur < 1 or rms_db(out / "id_dialog.wav") < -60:
            raise SystemExit(f"FLEURS id: audio gabungan kosong/senyap ({dialog_dur:.1f} dtk)")
        sets["id_dialog"] = {"kind": "timeline", "lang": "id", "file": str(out / "id_dialog.wav"), "bounds": b,
                             "speaker_source": speaker_source, "two_speakers_verified": speaker_source != "unknown",
                             "utterances": len(dialog)}
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
                                  "bounds": b, "snr_db": 10, "speaker_source": speaker_source,
                                  "two_speakers_verified": speaker_source != "unknown", "utterances": len(dialog)}
        pool, total, long_utts = utts[a.clean + 24:] + utts[a.clean:a.clean + 24], 0.0, []
        for u in pool:
            if total >= a.long_seconds:
                break
            long_utts.append(u)
            total += duration(u["file"]) + 0.4
        sets["id_long"] = {"kind": "timeline", "lang": "id", "file": str(out / "id_long.wav"),
                           "bounds": concat(long_utts, out / "id_long.wav"), "utterances": len(long_utts)}
    for s in sets.values():
        if s["kind"] == "timeline":
            s["duration"] = duration(s["file"])
    (out / "corpus.json").write_text(json.dumps({"source": f"huggingface.co/datasets/{REPO}", "revision": rev,
                                                 "license": "CC-BY-4.0", "sets": sets}, ensure_ascii=False, indent=1))
    print("KORPUS " + json.dumps({k: {"ucapan": v.get("utterances") or len(v.get("items", [])),
                                      "durasi_dtk": round(v.get("duration") or sum(i["duration"] for i in v["items"]), 1),
                                      "speaker_source": v.get("speaker_source")} for k, v in sets.items()},
                                 ensure_ascii=False))


if __name__ == "__main__":
    main()
