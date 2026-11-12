"""Penyimpanan proyek di disk: data/projects/<pid>/project.json + folder per klip."""
import json
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from .config import PROJECTS_DIR

_locks: dict[str, threading.RLock] = {}
_glock = threading.Lock()
_ID = re.compile(r"^[a-z0-9]{6,32}$")


def new_id(n: int = 10) -> str:
    return secrets.token_hex(n // 2 + 1)[:n]


def lock(pid: str) -> threading.RLock:
    with _glock:
        return _locks.setdefault(pid, threading.RLock())


def project_dir(pid: str) -> Path:
    if not _ID.match(pid or ""):
        raise FileNotFoundError("ID proyek tidak valid.")
    return PROJECTS_DIR / pid


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def create_project(name: str, target: float) -> dict:
    pid = new_id()
    folder = project_dir(pid)
    (folder / "clips").mkdir(parents=True)
    meta = {"id": pid, "name": name or "Cerita baru", "created": time.time(), "target": target,
            "status": "baru", "clips": [], "job": None, "outputs": [], "guidance": ""}
    _write_json(folder / "project.json", meta)
    return meta


def load(pid: str) -> dict:
    meta = read_json(project_dir(pid) / "project.json")
    if meta is None:
        raise FileNotFoundError("Proyek tidak ditemukan.")
    return meta


def save(meta: dict) -> None:
    with lock(meta["id"]):
        _write_json(project_dir(meta["id"]) / "project.json", meta)


def update(pid: str, **fields) -> dict:
    with lock(pid):
        meta = load(pid)
        meta.update(fields)
        save(meta)
        return meta


def update_clip(pid: str, cid: str, **fields) -> None:
    with lock(pid):
        meta = load(pid)
        for c in meta["clips"]:
            if c["id"] == cid:
                c.update(fields)
        save(meta)


def list_projects() -> list[dict]:
    out = []
    for f in PROJECTS_DIR.glob("*/project.json"):
        meta = read_json(f)
        if meta:
            out.append({k: meta.get(k) for k in ("id", "name", "created", "status", "target")}
                       | {"clips": len(meta.get("clips", []))})
    return sorted(out, key=lambda m: -m["created"])


def delete_project(pid: str) -> None:
    shutil.rmtree(project_dir(pid), ignore_errors=True)


def clip_dir(pid: str, cid: str) -> Path:
    if not _ID.match(cid or "") and not re.match(r"^c\d{1,3}$", cid or ""):
        raise FileNotFoundError("ID klip tidak valid.")
    return project_dir(pid) / "clips" / cid


def save_json(pid: str, name: str, data: Any) -> None:
    with lock(pid):
        _write_json(project_dir(pid) / name, data)


def load_json(pid: str, name: str, default: Any = None) -> Any:
    return read_json(project_dir(pid) / name, default)
