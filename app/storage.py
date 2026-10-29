"""Penyimpanan proyek: data ringkas di database, file besar (video, transkrip, rencana) di folder.

Folder per proyek: DATA_DIR/projects/<id>/ berisi source.*, preview.mp4, renders/, music/,
serta dokumen JSON (transcript, clips, energy, faces, plan) yang dibaca lewat read_data/write_data.
"""
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete, insert, select, update

from . import db
from .config import PROJECTS_DIR

_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_COLUMNS = {c.name for c in db.projects.columns} - {"id", "created"}


class NotFound(Exception):
    pass


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def project_dir(pid: str) -> Path:
    if not _ID_RE.match(pid or ""):
        raise NotFound(pid)
    path = PROJECTS_DIR / pid
    if not path.is_dir():
        raise NotFound(pid)
    return path


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def create_project(name: str, unit_id: Optional[str] = None, owner_id: Optional[str] = None) -> dict:
    pid = new_id()
    path = PROJECTS_DIR / pid
    (path / "renders").mkdir(parents=True)
    (path / "music").mkdir()
    with db.engine().begin() as c:
        c.execute(insert(db.projects).values(id=pid, unit_id=unit_id, owner_id=owner_id,
                                             name=(name or "Video tanpa judul")[:200], status="baru",
                                             created=time.time()))
    return load_project(pid)


def _with_children(c, meta: dict) -> dict:
    meta["renders"] = [db.row(r) for r in c.execute(
        select(db.renders).where(db.renders.c.project_id == meta["id"]).order_by(db.renders.c.created))]
    meta["music"] = [r[0] for r in c.execute(
        select(db.music.c.name).where(db.music.c.project_id == meta["id"]).order_by(db.music.c.name))]
    return meta


def load_project(pid: str) -> dict:
    if not _ID_RE.match(pid or ""):
        raise NotFound(pid)
    with db.engine().connect() as c:
        meta = db.row(c.execute(select(db.projects).where(db.projects.c.id == pid)).first())
        if meta is None:
            raise NotFound(pid)
        return _with_children(c, meta)


def update_project(pid: str, **fields: Any) -> dict:
    values = {k: v for k, v in fields.items() if k in _COLUMNS}
    if values:
        with db.engine().begin() as c:
            c.execute(update(db.projects).where(db.projects.c.id == pid).values(**values))
    return load_project(pid)


def add_render(pid: str, item: dict) -> None:
    cols = {c.name for c in db.renders.columns}
    with db.engine().begin() as c:
        c.execute(insert(db.renders).values(project_id=pid, **{k: v for k, v in item.items() if k in cols}))


def append_to_list(pid: str, key: str, item: dict) -> dict:
    """Kompatibilitas dengan kode lama: hanya daftar 'renders' yang didukung."""
    if key != "renders":
        raise ValueError(key)
    add_render(pid, item)
    return load_project(pid)


def add_music(pid: str, name: str) -> None:
    with db.engine().begin() as c:
        exists = c.execute(select(db.music).where(db.music.c.project_id == pid, db.music.c.name == name)).first()
        if not exists:
            c.execute(insert(db.music).values(project_id=pid, name=name))


def list_projects(unit_id: Optional[str] = None, all_units: bool = False, limit: int = 300) -> list[dict]:
    """Proyek terbaru lebih dulu. Tanpa all_units hanya proyek milik unit tersebut."""
    q = (select(db.projects, db.users.c.name.label("owner_name"), db.units.c.name.label("unit_name"))
         .select_from(db.projects.outerjoin(db.users, db.users.c.id == db.projects.c.owner_id)
                      .outerjoin(db.units, db.units.c.id == db.projects.c.unit_id))
         .order_by(db.projects.c.created.desc()).limit(limit))
    if not all_units:
        q = q.where(db.projects.c.unit_id == unit_id) if unit_id else q.where(db.projects.c.unit_id.is_(None))
    with db.engine().connect() as c:
        items = [db.row(r) for r in c.execute(q)]
        counts = dict(c.execute(select(db.renders.c.project_id, db.func.count())
                                .where(db.renders.c.project_id.in_([i["id"] for i in items]))
                                .group_by(db.renders.c.project_id)).all()) if items else {}
    for i in items:
        i["render_count"] = counts.get(i["id"], 0)
    return items


def delete_project(pid: str) -> None:
    with db.engine().begin() as c:
        c.execute(delete(db.renders).where(db.renders.c.project_id == pid))
        c.execute(delete(db.music).where(db.music.c.project_id == pid))
        c.execute(delete(db.jobs).where(db.jobs.c.project_id == pid))
        c.execute(delete(db.projects).where(db.projects.c.id == pid))
    shutil.rmtree(PROJECTS_DIR / pid, ignore_errors=True)


def read_data(pid: str, name: str, default: Any = None) -> Any:
    path = project_dir(pid) / f"{name}.json"
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_data(pid: str, name: str, data: Any) -> None:
    _write_json(project_dir(pid) / f"{name}.json", data)


def safe_filename(name: str, fallback: str = "file") -> str:
    base = Path(name or "").name
    base = re.sub(r"[^\w.\- ]+", "_", base).strip(" .")
    return base[:120] or fallback


def import_legacy_projects(unit_id: Optional[str], owner_id: Optional[str]) -> int:
    """Pindahkan proyek dari versi lama (project.json per folder) ke database. Aman dijalankan berulang."""
    count = 0
    for folder in sorted(PROJECTS_DIR.iterdir()) if PROJECTS_DIR.exists() else []:
        meta_file = folder / "project.json"
        if not (_ID_RE.match(folder.name) and meta_file.exists()):
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            with db.engine().begin() as c:
                if c.execute(select(db.projects.c.id).where(db.projects.c.id == folder.name)).first():
                    continue
                status = meta.get("status") if meta.get("status") in ("siap", "gagal") else "gagal"
                values = {k: v for k, v in meta.items() if k in _COLUMNS}
                values.update(id=folder.name, unit_id=unit_id, owner_id=owner_id, status=status,
                              name=(meta.get("name") or "Video")[:200], created=meta.get("created") or time.time())
                c.execute(insert(db.projects).values(**values))
                for r in meta.get("renders", []):
                    if (folder / "renders" / r.get("file", "")).exists():
                        c.execute(insert(db.renders).values(
                            id=r["id"], project_id=folder.name, file=r["file"], title=r.get("title", "Klip"),
                            start=r.get("start", 0), end=r.get("end", 0), aspect=r.get("aspect", "9:16"),
                            size=r.get("size", 0), created=r.get("created", time.time())))
                for m in meta.get("music", []):
                    c.execute(insert(db.music).values(project_id=folder.name, name=m))
            meta_file.rename(folder / "project.json.migrated")
            count += 1
        except Exception:  # proyek rusak dilewati, tidak menghentikan aplikasi
            continue
    return count
