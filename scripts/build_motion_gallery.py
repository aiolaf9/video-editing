"""Render galeri Motion Library (60 klip + thumbnail) ke folder bundel installer, memakai FFmpeg yang akan dibundel.

    python scripts/build_motion_gallery.py app/data/motion-gallery

Nama berkas memakai kunci yang sama dengan cache aplikasi (definisi preset + ENGINE_VERSION), sehingga aplikasi
terpasang langsung memakainya; bila preset/engine berubah, aplikasi merender ulang otomatis.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    from app import motion_gallery, motionlib
    motion_gallery.CACHE = out
    motion_gallery.BUNDLED = out / "_tidak_ada"
    t0, bad = time.time(), []
    for p in motionlib.presets():
        try:
            motion_gallery.render(p["id"])
        except Exception as e:  # noqa: BLE001
            bad.append((p["id"], str(e)[:200]))
    for f in out.glob("scene-*"):
        f.unlink()
    files = sorted(out.glob("*"))
    size = sum(f.stat().st_size for f in files)
    print(f"{len(files)} berkas, {size / 2**20:.1f} MB, {time.time() - t0:.0f} dtk; gagal: {bad}")
    return 1 if bad or len(files) != 2 * len(motionlib.presets()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
