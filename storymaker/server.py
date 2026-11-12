"""API FastAPI + UI web NH StoryMaker AI.

Jalankan:  python -m storymaker   (lalu buka http://localhost:8100)
"""
import logging
import mimetypes
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, pipeline, storage, story
from .config import MAX_CLIPS, MIN_CLIPS, STORY_PLANNER, WEB_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="NH StoryMaker AI")


def _meta(pid: str) -> dict:
    try:
        return storage.load(pid)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


class NewProject(BaseModel):
    name: str = Field("Cerita baru", max_length=120)
    target: float = Field(60, ge=20, le=180)


class ProjectPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=120)
    target: Optional[float] = Field(None, ge=20, le=180)
    guidance: Optional[str] = Field(None, max_length=2000)
    clip_order: Optional[list[str]] = None


class PlanRequest(BaseModel):
    planner: Literal["auto", "claude", "heuristic"] = STORY_PLANNER  # type: ignore[assignment]
    hook_id: Optional[str] = None


class PasteRequest(BaseModel):
    text: str = Field(max_length=200_000)


class TextEdit(BaseModel):
    text: str = Field(min_length=1, max_length=600)


class RenderSettings(BaseModel):
    framing: Literal["auto", "crop", "blur"] = "auto"
    transition: Literal["fade", "dissolve", "fadeblack", "slide", "smooth", "none"] = "fade"
    subtitles: bool = True
    subtitle_style: Literal["highlight", "simple"] = "highlight"
    subtitle_size: float = Field(1.0, ge=0.6, le=1.6)
    uppercase: bool = False
    highlight: str = Field("#FFD400", pattern=r"^#[0-9A-Fa-f]{6}$")
    show_title: bool = True
    music: Optional[str] = None
    music_volume: float = Field(0.12, ge=0, le=1)


@app.get("/api/config")
def config():
    return {"min_clips": MIN_CLIPS, "max_clips": MAX_CLIPS, "claude": ai.available(), "planner": STORY_PLANNER}


@app.get("/api/projects")
def projects():
    return storage.list_projects()


@app.post("/api/projects")
def create(body: NewProject):
    return storage.create_project(body.name, body.target)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    meta = _meta(pid)
    catalog = storage.load_json(pid, "catalog.json")
    raw = storage.load_json(pid, "plan.json")
    plan = None
    if catalog and raw:
        try:
            plan = story.plan_summary(story.StoryPlan.model_validate(raw), catalog)
        except (KeyError, ValueError):
            plan = None
    meta["running"] = pipeline.is_running(pid)
    meta["plan"] = plan
    meta["hooks"] = story.hook_candidates(catalog) if catalog else []
    return meta


@app.patch("/api/projects/{pid}")
def patch_project(pid: str, body: ProjectPatch):
    with storage.lock(pid):
        meta = _meta(pid)
        for k in ("name", "target", "guidance"):
            v = getattr(body, k)
            if v is not None:
                meta[k] = v
        if body.clip_order:
            order = {cid: i for i, cid in enumerate(body.clip_order)}
            for c in meta["clips"]:
                c["order"] = order.get(c["id"], len(order) + c["order"])
        storage.save(meta)
    return meta


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    _meta(pid)
    if pipeline.is_running(pid):
        raise HTTPException(409, "Proyek sedang diproses.")
    storage.delete_project(pid)
    return {"ok": True}


@app.post("/api/projects/{pid}/clips")
def upload(pid: str, files: list[UploadFile] = File(...)):
    _meta(pid)
    if pipeline.is_running(pid):
        raise HTTPException(409, "Proyek sedang diproses.")
    added, errors = [], []
    for f in files:
        try:
            added.append(pipeline.add_clip(pid, f.filename or "video.mp4", f.file))
        except pipeline.JobError as exc:
            errors.append(f"{f.filename}: {exc}")
    return {"added": added, "errors": errors}


@app.delete("/api/projects/{pid}/clips/{cid}")
def delete_clip(pid: str, cid: str):
    _meta(pid)
    if pipeline.is_running(pid):
        raise HTTPException(409, "Proyek sedang diproses.")
    pipeline.remove_clip(pid, cid)
    return {"ok": True}


def _submit(pid: str, stage: str, fn, *args):
    try:
        pipeline.submit(pid, stage, fn, *args)
    except pipeline.JobError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@app.post("/api/projects/{pid}/analyze")
def analyze(pid: str, body: PlanRequest = PlanRequest()):
    meta = _meta(pid)
    if len(meta["clips"]) < MIN_CLIPS:
        raise HTTPException(400, f"Masukkan minimal {MIN_CLIPS} video (saat ini {len(meta['clips'])}).")
    return _submit(pid, "analyze", pipeline.run_analysis, body.planner)


@app.post("/api/projects/{pid}/plan")
def regenerate_plan(pid: str, body: PlanRequest):
    _meta(pid)
    return _submit(pid, "plan", lambda p: pipeline.make_plan(p, body.planner, hook_id=body.hook_id))


@app.put("/api/projects/{pid}/plan")
def save_plan(pid: str, plan: dict):
    """Rencana hasil edit pengguna (urutan, hapus, ganti hook/B-roll)."""
    meta = _meta(pid)
    catalog = storage.load_json(pid, "catalog.json")
    if not catalog:
        raise HTTPException(400, "Analisis belum dijalankan.")
    try:
        p = story.validate_plan(plan, catalog, float(meta["target"]), fill_broll=False)
    except ValueError as exc:
        raise HTTPException(400, f"Rencana tidak valid: {exc}") from exc
    p.source = "manual" if p.source == "heuristic" else p.source
    storage.save_json(pid, "plan.json", p.model_dump())
    return story.plan_summary(p, catalog)


@app.get("/api/projects/{pid}/prompt")
def prompt(pid: str):
    meta = _meta(pid)
    catalog = storage.load_json(pid, "catalog.json")
    if not catalog:
        raise HTTPException(400, "Analisis belum dijalankan.")
    return {"prompt": ai.manual_prompt(catalog, float(meta["target"]), meta.get("guidance", ""))}


@app.post("/api/projects/{pid}/plan/paste")
def paste_plan(pid: str, body: PasteRequest):
    meta = _meta(pid)
    catalog = storage.load_json(pid, "catalog.json")
    if not catalog:
        raise HTTPException(400, "Analisis belum dijalankan.")
    try:
        raw = story.parse_plan_text(body.text)
        raw["source"] = "claude"
        p = story.validate_plan(raw, catalog, float(meta["target"]))
    except ValueError as exc:
        raise HTTPException(400, f"Jawaban tidak bisa dibaca: {exc}") from exc
    storage.save_json(pid, "plan.json", p.model_dump())
    return story.plan_summary(p, catalog)


@app.get("/api/projects/{pid}/catalog")
def catalog(pid: str):
    _meta(pid)
    return storage.load_json(pid, "catalog.json") or {}


@app.put("/api/projects/{pid}/utterances/{uid}")
def edit_text(pid: str, uid: str, body: TextEdit):
    """Koreksi salah dengar transkrip (mis. nama orang/tempat) sebelum dibakar sebagai subtitle."""
    _meta(pid)
    with storage.lock(pid):
        cat = storage.load_json(pid, "catalog.json")
        u = next((x for x in (cat or {}).get("utterances", []) if x["id"] == uid), None)
        if not u:
            raise HTTPException(404, "Kalimat tidak ditemukan.")
        pipeline.apply_text_edit(u, body.text)
        cat.setdefault("edits", {})[uid] = u["text"]
        storage.save_json(pid, "catalog.json", cat)
    return {"id": uid, "text": u["text"]}


@app.post("/api/projects/{pid}/music")
def upload_music(pid: str, file: UploadFile = File(...)):
    _meta(pid)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}:
        raise HTTPException(400, "Format musik tidak didukung.")
    folder = storage.project_dir(pid) / "music"
    folder.mkdir(exist_ok=True)
    name = f"music{ext}"
    with open(folder / name, "wb") as out:
        out.write(file.file.read())
    return {"music": name}


@app.post("/api/projects/{pid}/render")
def start_render(pid: str, body: RenderSettings):
    _meta(pid)
    if not storage.load_json(pid, "plan.json"):
        raise HTTPException(400, "Rencana cerita belum ada.")
    return _submit(pid, "render", pipeline.run_render, body.model_dump())


@app.get("/api/projects/{pid}/files/{path:path}")
def files(pid: str, path: str):
    root = storage.project_dir(pid).resolve()
    target = (root / path).resolve()
    allowed = ("outputs/", "clips/")
    if not str(target).startswith(str(root)) or not path.startswith(allowed) or not target.is_file():
        raise HTTPException(404, "File tidak ditemukan.")
    if path.startswith("clips/") and target.suffix not in {".mp4", ".jpg"}:
        raise HTTPException(404, "File tidak ditemukan.")
    return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream")


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
