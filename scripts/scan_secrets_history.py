"""Pindai SELURUH riwayat git (semua blob yang pernah ada) dengan detect-secrets.

Temuan yang hash-nya sudah ada di .secrets.baseline (diaudit: contoh dokumentasi / kredensial tiruan tes)
diabaikan; temuan lain = gagal (exit 1). Dipakai CI (butuh checkout fetch-depth: 0).

    python scripts/scan_secrets_history.py [--baseline .secrets.baseline]
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from detect_secrets import SecretsCollection
from detect_secrets.settings import default_settings

SKIP = ("tests/schema_snapshots/", "fonts/", "models/", ".secrets.baseline", ".bandit-baseline.json")
BINARY = (".ttf", ".otf", ".onnx", ".png", ".jpg", ".mp4", ".db")


def known_hashes(baseline: Path) -> set[str]:
    data = json.loads(baseline.read_text())
    return {item["hashed_secret"] for items in data["results"].values() for item in items}


def history_blobs() -> dict[str, str]:
    """blob sha -> path (satu path contoh per blob)."""
    out = subprocess.run(["git", "rev-list", "--all", "--objects"], capture_output=True, text=True, check=True).stdout
    blobs = {}
    for line in out.splitlines():
        sha, _, path = line.partition(" ")
        if path and not path.startswith(SKIP) and not path.endswith(BINARY):
            blobs.setdefault(sha, path)
    kinds = subprocess.run(["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"], input="\n".join(blobs),
                           capture_output=True, text=True, check=True).stdout
    keep = {l.split()[0] for l in kinds.splitlines() if l.endswith(" blob")}
    return {s: p for s, p in blobs.items() if s in keep}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default=".secrets.baseline")
    args = ap.parse_args()
    known = known_hashes(Path(args.baseline))
    blobs = history_blobs()
    new = []
    with tempfile.TemporaryDirectory() as tmp, default_settings():
        for sha, path in blobs.items():
            content = subprocess.run(["git", "cat-file", "-p", sha], capture_output=True, check=True).stdout
            f = Path(tmp) / Path(path).name
            f.write_bytes(content)
            found = SecretsCollection()
            found.scan_file(str(f))
            for _name, secret in found:
                if secret.secret_hash not in known:
                    new.append((path, sha[:10], secret.type, secret.line_number))
            f.unlink()
    print(f"Blob riwayat dipindai: {len(blobs)}; temuan baru (belum diaudit): {len(new)}")
    for path, sha, kind, line in new:
        print(f"  {path} (blob {sha}) baris {line}: {kind}")  # isi secret tidak pernah dicetak
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
