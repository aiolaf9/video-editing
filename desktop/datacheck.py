"""Pemeriksaan data pengguna setelah pembaruan aplikasi (M1 -> M1.1): proyek, transkrip, resep editing, dan hasil
render harus tetap ada dan terbaca oleh versi baru. Tidak mengubah apa pun.

    NH-AI-Clip-Studio.exe --check-data [--report hasil.json]          (pemeriksaan lewat aplikasi, folder data bawaan)
    python -m desktop.datacheck snapshot <folder-data> <keluaran.json>  (CI: sidik berkas sebelum pembaruan)
    python -m desktop.datacheck compare <sebelum.json> <sesudah.json>   (CI: tidak ada berkas pengguna yang hilang/berubah)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import urllib.parse
from pathlib import Path

# Berkas yang memang berubah karena aplikasi berjalan (bukan data proyek pengguna).
VOLATILE = ("logs/", "app.lock", "app.db", "app.db-wal", "app.db-shm", "app.db-journal", "tmp/", "uploads/")
TABLES = ("projects", "project_recipes", "renders", "project_media", "edit_templates", "settings")


def check(base: str, key: str) -> dict:
    """Muat setiap proyek lewat API versi ini: proyek, transkrip, resep, klip, tinjauan, dan setiap berkas render."""
    from desktop.selftest import Client
    c = Client(base)
    c.get(f"/api/desktop/enter?key={urllib.parse.quote(key)}")
    status, items = c.get("/api/projects")
    if status != 200 or not isinstance(items, list):
        return {"ok": False, "error": f"daftar proyek: {status} {items}"}
    rows, problems = [], []
    for item in items:
        pid = item["id"]
        row = {"id": pid, "name": item.get("name")}
        s, proj = c.get(f"/api/projects/{pid}")
        s_t, tr = c.get(f"/api/projects/{pid}/transcript")
        s_r, rec = c.get(f"/api/projects/{pid}/recipe")
        s_c, clips = c.get(f"/api/projects/{pid}/clips")
        s_v, review = c.get(f"/api/projects/{pid}/transcript/review")
        row.update(project=s, transcript=s_t, recipe=s_r, clips=s_c, review=s_v,
                   segments=len(tr.get("segments", [])) if isinstance(tr, dict) else None,
                   recipe_version=rec.get("version") if isinstance(rec, dict) else None)
        renders = proj.get("renders", []) if isinstance(proj, dict) else []
        ok_files = 0
        for r in renders:
            if not r.get("available"):
                continue
            s_f, body = c.get(f"/api/projects/{pid}/renders/{urllib.parse.quote(r['file'])}")
            ok_files += s_f == 200 and isinstance(body, bytes) and len(body) > 1000
        row.update(renders=len(renders), renders_available=sum(bool(r.get("available")) for r in renders),
                   renders_readable=ok_files)
        bad = [k for k in ("project", "transcript", "recipe", "clips", "review") if row[k] != 200]
        if bad or ok_files != row["renders_available"]:
            problems.append(f"{pid}: gagal {bad or ''} render terbaca {ok_files}/{row['renders_available']}")
        rows.append(row)
    return {"ok": not problems, "projects": len(rows), "items": rows, "problems": problems}


def snapshot(data_dir: Path) -> dict:
    data_dir = Path(data_dir)
    files = {}
    for p in sorted(data_dir.rglob("*")):
        rel = p.relative_to(data_dir).as_posix()
        if not p.is_file() or rel.startswith(VOLATILE) or rel in VOLATILE:
            continue
        files[rel] = {"size": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    tables, ids = {}, {}
    db = data_dir / "app.db"
    if db.exists():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            have = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
            for t in TABLES:
                if t in have:
                    tables[t] = con.execute(f"select count(*) from {t}").fetchone()[0]  # nosec B608 - nama tetap
            if "projects" in have:
                ids["projects"] = sorted(r[0] for r in con.execute("select id from projects"))
            if "project_recipes" in have:
                ids["recipes"] = {r[0]: hashlib.sha256(r[1].encode()).hexdigest()
                                  for r in con.execute("select project_id, data from project_recipes")}
        finally:
            con.close()
    return {"files": files, "tables": tables, "ids": ids}


def compare(before: dict, after: dict) -> dict:
    lost = [f for f in before["files"] if f not in after["files"]]
    diff = [f for f, v in before["files"].items() if f in after["files"] and after["files"][f]["sha256"] != v["sha256"]]
    # Metadata JSON boleh ditulis ulang oleh aplikasi (isi diperiksa lewat API oleh check()); video, transkrip mentah,
    # thumbnail, dan hasil render tidak boleh berubah.
    meta_changed = [f for f in diff if f.endswith("meta.json")]
    changed = [f for f in diff if f not in meta_changed]
    shrunk = {t: (n, after["tables"].get(t, 0)) for t, n in before["tables"].items() if after["tables"].get(t, 0) < n}
    lost_projects = sorted(set(before["ids"].get("projects", [])) - set(after["ids"].get("projects", [])))
    recipes_changed = sorted(p for p, h in before["ids"].get("recipes", {}).items()
                             if after["ids"].get("recipes", {}).get(p) != h)
    ok = not (lost or changed or shrunk or lost_projects or recipes_changed)
    return {"ok": ok, "files_before": len(before["files"]), "files_after": len(after["files"]),
            "tables_before": before["tables"], "tables_after": after["tables"], "lost_files": lost[:50],
            "changed_files": changed[:50], "meta_rewritten": meta_changed[:50], "shrunk_tables": shrunk, "lost_projects": lost_projects,
            "recipes_changed": recipes_changed}


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) == 3 and a[0] == "snapshot":
        Path(a[2]).write_text(json.dumps(snapshot(Path(a[1])), indent=1), encoding="utf-8")
        return 0
    if len(a) == 3 and a[0] == "compare":
        res = compare(*(json.loads(Path(p).read_text(encoding="utf-8")) for p in a[1:]))
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res["ok"] else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
