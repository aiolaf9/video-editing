"""CI M2: transkrip Whisper (jalur aplikasi, mode Standar) sebuah video -> JSON untuk bench/m2/studio_demo.py.

    python bench/m2/transcribe_json.py video.mp4 keluar.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    from app import media, transcribe
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    r = transcribe.transcribe(src, media.probe(src)["duration"], "id", quality="akurat")
    out.write_text(json.dumps({"language": r.get("language"), "segments": r["segments"]}, ensure_ascii=False),
                   encoding="utf-8")
    print(f"{sum(len(s.get('words') or []) for s in r['segments'])} kata, model {r.get('model')}")


if __name__ == "__main__":
    main()
