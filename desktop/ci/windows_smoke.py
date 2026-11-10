"""CI Windows (cepat, sebelum unduhan besar): modul khusus OS bekerja di Windows dan app dapat diimpor."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
tmp = Path(tempfile.mkdtemp())
os.environ.update(VIDEO_APP_DATA=str(tmp / "data"), DESKTOP_MODE="1", MEDIA_LIFECYCLE="0", HF_HUB_OFFLINE="1")

from app import oslock, resources  # noqa: E402

path = tmp / "kunci.part"
path.write_bytes(b"x" * 4096)
with open(path, "r+b") as a, open(path, "r+b") as b:
    assert oslock.try_lock(a), "kunci pertama gagal"
    assert not oslock.try_lock(b), "kunci tidak eksklusif antar handle"
    a.seek(0)
    a.write(b"yy")                       # pemegang kunci tetap bisa menulis isi berkas
    b.seek(100)
    assert b.read(4) == b"xxxx"          # handle lain tetap bisa membaca isi (kunci di luar isi berkas)
    oslock.unlock(a)
    assert oslock.try_lock(b), "kunci tidak terlepas"
    oslock.unlock(b)
mem = resources.memory()
assert mem and mem["total"] > 2**30 and 0 < mem["available"] <= mem["total"], mem
print("RAM", round(mem["total"] / 2**30, 1), "GB; tersedia", round(mem["available"] / 2**30, 1), "GB")

from app import main  # noqa: E402,F401  (impor penuh, termasuk uploads & migrate)

print("windows smoke OK")
