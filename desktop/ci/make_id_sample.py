"""CI Windows: video uji berbahasa Indonesia ASLI + transkrip rujukan, dari Google FLEURS id_id (CC-BY 4.0).

Kalimat unik (tanpa rekaman ganda) diambil berurutan sampai ±`--seconds`, digabung dengan jeda 0,4 dtk, lalu
dijadikan MP4 1280x720 (gambar uji) + audio. Rujukan = gabungan transkripsi FLEURS (untuk WER/CER selftest).
Ini ucapan baca (bukan ceramah/istilah Arab): menguji jalur bahasa Indonesia, bukan kosakata dakwah.

    python desktop/ci/make_id_sample.py <ffmpeg.exe> <keluaran.mp4> [--seconds 150]
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench" / "p552"))
from fleurs_corpus import fetch  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ffmpeg")
    ap.add_argument("out")
    ap.add_argument("--seconds", type=float, default=150)
    a = ap.parse_args()
    out = Path(a.out)
    work = Path(tempfile.mkdtemp(prefix="fleurs-id-"))
    utts, revision = fetch("id_id", work)
    picked, seen, total = [], set(), 0.0
    for u in utts:
        key = " ".join(re.sub(r"[^\w\s]", " ", u["text"].lower()).split())
        if key in seen:
            continue
        seen.add(key)
        dur = int(Path(u["file"]).stat().st_size - 44) / (16000 * 2)    # wav PCM16 mono 16 kHz (FLEURS)
        picked.append(u)
        total += dur + 0.4
        if total >= a.seconds:
            break
    inputs, filters = [], []
    for k, u in enumerate(picked):
        inputs += ["-i", u["file"]]
        filters.append(f"[{k}:a]aresample=16000,aformat=channel_layouts=mono,apad=pad_dur=0.4[a{k}]")
    concat = "".join(f"[a{k}]" for k in range(len(picked))) + f"concat=n={len(picked)}:v=0:a=1[aout]"
    wav = work / "id.wav"
    subprocess.run([a.ffmpeg, "-v", "error", "-y", *inputs, "-filter_complex", ";".join(filters + [concat]),
                    "-map", "[aout]", str(wav)], check=True)
    subprocess.run([a.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25", "-i", str(wav),
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)], check=True)
    out.with_suffix(".txt").write_text(" ".join(u["text"] for u in picked), encoding="utf-8")
    print(f"{out}: {len(picked)} kalimat, ±{total:.0f} dtk, FLEURS revisi {revision}")


if __name__ == "__main__":
    main()
