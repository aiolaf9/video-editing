"""CI: unduh model Whisper base (CTranslate2) untuk dibundel installer; revisi dicatat, lisensi harus MIT."""
import json
import re
import sys
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

repo, dst = sys.argv[1], Path(sys.argv[2])
info = HfApi().model_info(repo)
snapshot_download(repo, revision=info.sha, local_dir=dst,  # nosec B615 - revisi dikunci ke sha yang dicatat
                  allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.*", "README.md",
                                  "preprocessor_config.json"])  # v3/turbo: 128 mel (tanpa ini salah fitur)
card = (dst / "README.md").read_text(encoding="utf-8")
if not re.search(r"^license:\s*mit\s*$", card, re.M | re.I):
    raise SystemExit("Lisensi model bukan MIT: periksa sebelum dibundel.")
(dst / "SOURCE.json").write_text(json.dumps({"repo": repo, "revision": info.sha, "license": "MIT"}))
print("Whisper", repo, info.sha, sorted(p.name for p in dst.iterdir()))
