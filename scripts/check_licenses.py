"""Periksa lisensi dependensi Python yang TERPASANG di lingkungan ini (jalankan di venv berisi requirements.txt saja).

Kategori
- izin      : permisif (MIT, BSD, Apache-2.0, ISC, PSF, ...) — aman untuk SaaS komersial.
- catatan   : copyleft lemah (MPL-2.0, LGPL) — boleh dipakai tanpa modifikasi; kewajiban bila didistribusikan.
- tolak     : copyleft kuat (GPL, AGPL, SSPL, ...) — CI gagal kecuali sudah ditinjau di license_review.json.
- manual    : metadata tidak jelas — CI gagal kecuali sudah ditinjau di license_review.json.
- keputusan : sudah ditinjau, menunggu keputusan pemilik (dicatat di license_review.json): peringatan, tidak gagal.

Hasil "terverifikasi" = dari metadata paket (License-Expression / License / classifier) atau file LICENSE yang
ikut terpasang; "manual" = keputusan manusia yang dicatat di scripts/license_review.json (beserta buktinya).

    python scripts/check_licenses.py [--markdown docs/licenses-python.md]
"""
import argparse
import json
import re
import sys
from importlib import metadata
from pathlib import Path

PERMISSIVE = ["MIT", "BSD", "APACHE", "ISC", "PSF", "PYTHON SOFTWARE FOUNDATION", "UNLICENSE", "0BSD", "ZLIB", "HPND",
              "PUBLIC DOMAIN", "CNRI", "ZPL", "MIT-CMU"]
WEAK = ["MPL", "MOZILLA PUBLIC LICENSE", "LGPL", "LESSER GENERAL PUBLIC"]
STRONG = ["AGPL", "AFFERO", "GPL", "GENERAL PUBLIC LICENSE", "SSPL", "EUPL", "CC-BY-NC", "NON-COMMERCIAL"]
REVIEW = Path(__file__).with_name("license_review.json")


def describe(dist) -> str:
    md = dist.metadata
    expr = md.get("License-Expression")
    if expr:
        return expr.strip()
    classifiers = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    classifiers = [c for c in classifiers if c not in ("OSI Approved",)]
    lic = (md.get("License") or "").strip()
    if classifiers:
        return "; ".join(classifiers)
    if lic and len(lic) < 120 and "\n" not in lic:
        return lic
    # Teks lisensi lengkap di field License / file LICENSE yang ikut terpasang: ambil baris pengenalnya.
    text = lic
    for f in dist.files or []:
        if re.search(r"(LICEN[CS]E|COPYING)", f.name, re.I):
            try:
                text = (dist.locate_file(f)).read_text(errors="ignore")
                break
            except OSError:
                continue
    for name, pattern in (("MIT", r"Permission is hereby granted, free of charge"),
                          ("Apache-2.0", r"Apache License,?\s+Version 2\.0"),
                          ("BSD-3-Clause", r"Neither the name of"), ("BSD-2-Clause", r"Redistribution and use in source"),
                          ("MPL-2.0", r"Mozilla Public License,? v(ersion)?\.? ?2\.0"), ("GPL", r"GNU GENERAL PUBLIC LICENSE"),
                          ("LGPL", r"GNU LESSER GENERAL PUBLIC LICENSE"), ("Unlicense", r"This is free and unencumbered")):
        if re.search(pattern, text or "", re.I):
            return f"{name} (dari file lisensi)"
    return "UNKNOWN"


def category(lic: str) -> str:
    up = lic.upper()
    if any(k in up for k in ("AGPL", "AFFERO", "SSPL", "EUPL", "NON-COMMERCIAL", "CC-BY-NC")):
        return "tolak"
    if any(k in up for k in WEAK):
        return "catatan" if not re.search(r"(?<!L)GPL(?!.*LGPL)", up.replace("LGPL", "")) else "tolak"
    if re.search(r"\bGPL\b|GENERAL PUBLIC LICENSE", up):
        return "tolak"
    if any(k in up for k in PERMISSIVE):
        return "izin"
    return "manual"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown")
    args = ap.parse_args()
    review = json.loads(REVIEW.read_text()) if REVIEW.exists() else {}
    rows, failed = [], []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        if name.lower() in ("pip", "setuptools", "wheel"):
            continue
        lic = describe(dist)
        cat = category(lic)
        rv = review.get(name.lower())
        source = "metadata"
        if rv:
            lic, cat, source = rv["license"], rv["category"], f"tinjauan manual: {rv['evidence']}"
        elif cat in ("tolak", "manual"):
            failed.append(name)
        rows.append((name, dist.version, lic, cat, source, (rv or {}).get("note", "")))
    lines = ["| Paket | Versi | Lisensi | Kategori | Sumber | Catatan SaaS |", "|---|---|---|---|---|---|"]
    lines += [f"| {n} | {v} | {l} | {c} | {s} | {note} |" for n, v, l, c, s, note in rows]
    counts = {c: sum(r[3] == c for r in rows) for c in ("izin", "catatan", "keputusan", "manual", "tolak")}
    summary = f"{len(rows)} paket: " + ", ".join(f"{k} {v}" for k, v in counts.items())
    print(summary)
    if args.markdown:
        Path(args.markdown).write_text("<!-- dibuat oleh scripts/check_licenses.py -->\n" + summary + "\n\n" + "\n".join(lines) + "\n")
    for r in rows:
        if r[3] == "keputusan":
            print(f"PERINGATAN (butuh keputusan pemilik): {r[0]} {r[1]} {r[2]} — {r[5]}")
    for n in failed:
        print(f"PERLU TINJAUAN: {n} ({next(r[2] for r in rows if r[0] == n)}) belum ada di license_review.json")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
