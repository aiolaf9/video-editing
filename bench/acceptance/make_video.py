"""Video uji acceptance MVP dari korpus FLEURS (ucapan manusia nyata, lisensi CC-BY 4.0) dan video panjang > 15 menit.

python bench/acceptance/make_video.py <korpus> <set> <folder_keluar>
  -> <folder>/accept.mp4 (testsrc2 720p + audio ucapan set), <folder>/reference.json, <folder>/panjang_16m.mp4
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "p552"))


def main(corpus: Path, name: str, out: Path) -> None:
    from w5_validate import utterance_timeline
    out.mkdir(parents=True, exist_ok=True)
    sets = json.loads((corpus / "corpus.json").read_text())["sets"]
    joined, tl, _ = utterance_timeline(name, sets[name], Path(tempfile.mkdtemp()))
    dur = tl[-1]["end"] + 0.4
    ff = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    subprocess.run(ff + ["-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=25:duration={dur:.2f}", "-i", str(joined),
                         "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                         "-c:a", "aac", "-b:a", "96k", str(out / "accept.mp4")], check=True)
    subprocess.run(ff + ["-f", "lavfi", "-i", "color=c=gray:size=160x90:rate=1:duration=960", "-f", "lavfi",
                         "-i", "anullsrc=r=16000:cl=mono", "-t", "960", "-c:v", "libx264", "-preset", "ultrafast",
                         "-c:a", "aac", "-b:a", "16k", str(out / "panjang_16m.mp4")], check=True)
    ref = {"set": name, "duration": round(dur, 2), "language": sets[name].get("lang"),
           "text": " ".join(u["text"] for u in tl),
           "utterances": [{"idx": u["idx"], "start": round(u["start"], 2), "end": round(u["end"], 2), "text": u["text"]}
                          for u in tl]}
    (out / "reference.json").write_text(json.dumps(ref, ensure_ascii=False, indent=1))
    print(json.dumps({"set": name, "utterances": len(tl), "duration_s": ref["duration"]}))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
