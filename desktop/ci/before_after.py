"""Bukti sebelum/sesudah M1.1 dengan aplikasi sungguhan (server lokal + worker, mode desktop).

Video yang sama diproses dua kali, lalu rentang yang SAMA dirender 9:16 dengan subtitle (tanpa koreksi manual):
  sebelum = perilaku rilis M1: model Cepat (base), bahasa "Deteksi otomatis" dari 30 dtk pertama, conditioning lama
  sesudah = M1.1 mode Akurat (small), bahasa Indonesia, pengaturan decoding bawaan M1.1
Keluaran: <out>/sebelum.mp4, <out>/sesudah.mp4, transkrip masing-masing, dan ringkasan.json (WER/CER vs rujukan).

    python desktop/ci/before_after.py <video> <rujukan.txt> <folder-keluaran> [--start 0 --end 30]
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from desktop import launcher, selftest  # noqa: E402

M1_DECODE = {"detect_windows": 1, "condition_on_previous_text": True, "use_hotwords": False}   # M1: tanpa kosakata


def process(c, video: Path, language: str, quality: str, decode: dict, rng: tuple, out: Path, tag: str,
            reference: str) -> dict:
    if decode is None:
        os.environ.pop("WHISPER_DECODE", None)
    else:
        os.environ["WHISPER_DECODE"] = json.dumps(decode)
    status, res = c.upload(video, language, quality)
    assert status == 200, res
    pid = res["project"]["id"]
    job = selftest._wait(c, res["job"]["id"], 1800)
    assert job["status"] == "selesai", job
    status, tr = c.get(f"/api/projects/{pid}/transcript")
    words = [w for s in tr["segments"] for w in s.get("words", [])]
    in_range = " ".join(w["word"] for w in words if rng[0] <= w["start"] < rng[1])
    (out / f"{tag}.txt").write_text(" ".join(w["word"] for w in words), encoding="utf-8")
    status, state = c.get(f"/api/projects/{pid}/recipe")
    rec = state["recipe"]
    rec["subtitles"].update(enabled=True, style="karaoke", font="Poppins", uppercase=False, words_per_line=4)
    status, saved = c.json("PUT", f"/api/projects/{pid}/recipe", {"version": state["version"], "recipe": rec})
    status, job = c.json("POST", f"/api/projects/{pid}/render-recipe",
                         {"version": saved["version"], "start": rng[0], "end": rng[1], "title": tag})
    assert status == 200, job
    done = selftest._wait(c, job["id"], 1800)
    assert done["status"] == "selesai", done
    status, project = c.get(f"/api/projects/{pid}")
    item = project["renders"][-1]
    src = Path(os.environ["VIDEO_APP_DATA"]) / "projects" / pid / "renders" / item["file"]
    shutil.copy(src, out / f"{tag}.mp4")
    return {"model": tr.get("model"), "quality": tr.get("quality"), "language": tr.get("language"),
            "decode": decode, "words": len(words), "accuracy_full": selftest.wer_cer(reference, " ".join(
                w["word"] for w in words)), "subtitle_in_mp4": in_range}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("reference")
    ap.add_argument("out")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=30.0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ba-"))
    key = launcher.configure_env(work / "data", work / "export")
    launcher.setup_logging(work / "data")
    server = launcher.LocalServer(launcher.free_port())
    server.start()
    reference = Path(a.reference).read_text(encoding="utf-8")
    try:
        c = selftest.Client(server.base)
        c.get(f"/api/desktop/enter?key={urllib.parse.quote(key)}")
        rng = (a.start, a.end)
        summary = {
            "range": rng,
            "sebelum": process(c, Path(a.video), "", "cepat", M1_DECODE, rng, out, "sebelum", reference),
            "sesudah": process(c, Path(a.video), "id", "akurat", None, rng, out, "sesudah", reference),
            "catatan": "Tanpa koreksi manual. Sampel FLEURS id_id (ucapan baca, tanpa istilah Arab).",
        }
    finally:
        server.stop()
    (out / "ringkasan.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
