"""Penyimpanan proyek berbasis folder + file JSON (tanpa database)."""
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import PROJECTS_DIR

_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_lock = threading.RLock()


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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def create_project(name: str) -> dict:
    pid = new_id()
    path = PROJECTS_DIR / pid
    (path / "renders").mkdir(parents=True)
    (path / "music").mkdir()
    meta = {
        "id": pid,
        "name": name or "Video tanpa judul",
        "created": time.time(),
        "status": "baru",
        "source": None,
        "renders": [],
        "music": [],
    }
    _write_json(path / "project.json", meta)
    return meta


def load_project(pid: str) -> dict:
    path = project_dir(pid) / "project.json"
    return json.loads(path.read_text(encoding="utf-8"))


def update_project(pid: str, **fields: Any) -> dict:
    with _lock:
        meta = load_project(pid)
        meta.update(fields)
        _write_json(project_dir(pid) / "project.json", meta)
        return meta


def append_to_list(pid: str, key: str, item: dict) -> dict:
    with _lock:
        meta = load_project(pid)
        meta.setdefault(key, []).append(item)
        _write_json(project_dir(pid) / "project.json", meta)
        return meta


def list_projects() -> list[dict]:
    items = []
    for child in PROJECTS_DIR.iterdir():
        if _ID_RE.match(child.name) and (child / "project.json").exists():
            try:
                items.append(load_project(child.name))
            except (OSError, json.JSONDecodeError):
                continue
    return sorted(items, key=lambda m: m.get("created", 0), reverse=True)


def delete_project(pid: str) -> None:
    shutil.rmtree(project_dir(pid))


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
