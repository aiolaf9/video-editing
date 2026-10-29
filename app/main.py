"""Server FastAPI: API + UI web statis. Jalankan: uvicorn app.main:app --port 8000"""
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import clipper, pipeline, plan, storage
from .config import DATA_DIR, FFMPEG, FONTS_DIR, FFPROBE, JOB_WORKERS, WEB_DIR, WHISPER_MODEL
from .jobs import JobManager
from .schemas import PlanImport, PlanItem, RangeRequest, RenderRequest, Settings, SuggestRequest, UrlImport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".3gp", ".ts"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}

app = FastAPI(title="Video Editing AI", version="0.1.0")
jobs = JobManager(JOB_WORKERS)


def _project_or_404(pid: str) -> dict:
    try:
        return storage.load_project(pid)
    except storage.NotFound:
        raise HTTPException(404, "Proyek tidak ditemukan.")


def _save_upload(upload: UploadFile, dst: Path) -> None:
    with dst.open("wb") as fh:
        shutil.copyfileobj(upload.file, fh, length=4 * 1024 * 1024)


def _project_view(meta: dict) -> dict:
    data = dict(meta)
    data["jobs"] = [jobs.view(j) for j in jobs.for_project(meta["id"])]
    return data


@app.get("/api/health")
def health():
    return {
        "ffmpeg": shutil.which(FFMPEG) is not None or Path(FFMPEG).exists(),
        "ffprobe": shutil.which(FFPROBE) is not None or Path(FFPROBE).exists(),
        "whisper_model": WHISPER_MODEL,
    }


@app.get("/api/projects")
def list_projects():
    return storage.list_projects()


@app.post("/api/projects")
def upload_project(file: UploadFile = File(...), language: Optional[str] = Form(None)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(400, f"Format {ext or '(tanpa ekstensi)'} tidak didukung.")
    meta = storage.create_project(Path(file.filename).stem)
    pid = meta["id"]
    _save_upload(file, storage.project_dir(pid) / f"source{ext}")
    storage.update_project(pid, source=f"source{ext}", status="antre")
    job = jobs.submit("analisis", pid, lambda j: pipeline.safe_analyze(j, pid, language or None), "Analisis video")
    return {"project": storage.load_project(pid), "job": jobs.view(job)}


@app.post("/api/projects/from-url")
def import_url(body: UrlImport):
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "Link harus diawali http:// atau https://")
    meta = storage.create_project("Mengunduh...")
    pid = meta["id"]
    storage.update_project(pid, status="antre", url=body.url)

    def run(job):
        pipeline.download(job, pid, body.url)
        jobs.submit("analisis", pid, lambda j: pipeline.safe_analyze(j, pid, body.language), "Analisis video")

    job = jobs.submit("analisis", pid, run, "Unduh video", pool="unduh")
    return {"project": storage.load_project(pid), "job": jobs.view(job)}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _project_view(_project_or_404(pid))


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    _project_or_404(pid)
    if any(j.status in ("antre", "jalan") for j in jobs.for_project(pid)):
        raise HTTPException(409, "Proyek masih diproses. Tunggu sampai selesai.")
    storage.delete_project(pid)
    return {"ok": True}


@app.post("/api/projects/{pid}/reanalyze")
def reanalyze(pid: str, language: Optional[str] = None):
    _project_or_404(pid)
    job = jobs.submit("analisis", pid, lambda j: pipeline.safe_analyze(j, pid, language), "Analisis ulang")
    return jobs.view(job)


@app.get("/api/projects/{pid}/transcript")
def get_transcript(pid: str):
    _project_or_404(pid)
    return storage.read_data(pid, "transcript", {"language": None, "segments": []})


@app.get("/api/projects/{pid}/clips")
def get_clips(pid: str):
    _project_or_404(pid)
    return storage.read_data(pid, "clips", [])


@app.post("/api/projects/{pid}/clips/suggest")
def suggest_clips(pid: str, body: SuggestRequest):
    meta = _project_or_404(pid)
    transcript = storage.read_data(pid, "transcript")
    if transcript is None:
        raise HTTPException(409, "Video belum selesai dianalisis.")
    clips = clipper.suggest(transcript, storage.read_data(pid, "energy", []),
                            meta.get("duration") or 0, body.target, body.top_n)
    storage.write_data(pid, "clips", clips)
    return clips


@app.get("/api/projects/{pid}/preview")
def get_preview(pid: str):
    _project_or_404(pid)
    path = storage.project_dir(pid) / "preview.mp4"
    if not path.exists():
        raise HTTPException(404, "Preview belum siap.")
    return FileResponse(path, media_type="video/mp4")


@app.post("/api/projects/{pid}/music")
def upload_music(pid: str, file: UploadFile = File(...)):
    _project_or_404(pid)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXTS:
        raise HTTPException(400, "Format musik harus mp3, wav, m4a, aac, ogg, flac, atau opus.")
    name = storage.safe_filename(file.filename, f"musik{ext}")
    _save_upload(file, storage.project_dir(pid) / "music" / name)
    meta = storage.load_project(pid)
    if name not in meta.get("music", []):
        storage.update_project(pid, music=[*meta.get("music", []), name])
    return {"file": name}


@app.post("/api/projects/{pid}/render")
def render_clip(pid: str, body: RenderRequest):
    meta = _project_or_404(pid)
    if meta.get("status") != "siap":
        raise HTTPException(409, "Video belum selesai dianalisis.")
    if body.start >= (meta.get("duration") or 0):
        raise HTTPException(400, "Titik awal klip melebihi durasi video.")
    label = body.title or f"Klip {body.start:.0f}s-{body.end:.0f}s"
    job = jobs.submit("render", pid, lambda j: pipeline.render(j, pid, body), label)
    return jobs.view(job)


@app.get("/api/projects/{pid}/renders/{file}")
def download_render(pid: str, file: str, download: bool = False):
    meta = _project_or_404(pid)
    item = next((r for r in meta.get("renders", []) if r["file"] == file), None)
    if item is None:
        raise HTTPException(404, "Hasil render tidak ditemukan.")
    path = storage.project_dir(pid) / "renders" / item["file"]
    name = storage.safe_filename(item["title"], "klip") + ".mp4"
    return FileResponse(path, media_type="video/mp4", filename=name if download else None)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Pekerjaan tidak ditemukan.")
    return jobs.view(job)


# ------------------------------------------------------------------ rencana animasi

def _load_settings() -> dict:
    path = DATA_DIR / "settings.json"
    data = Settings().model_dump()
    if path.exists():
        try:
            data.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return data


def _plan_context(pid: str, start: float, end: float) -> dict:
    meta = _project_or_404(pid)
    if meta.get("status") != "siap":
        raise HTTPException(409, "Video belum selesai dianalisis.")
    end = min(end, meta.get("duration") or end)
    if end - start < 1:
        raise HTTPException(400, "Rentang klip tidak valid.")
    return {"transcript": storage.read_data(pid, "transcript", {}) or {}, "start": start, "end": end}


def _save_range(pid: str, items: list[dict], start: float, end: float) -> list[dict]:
    new = plan.normalize(items, start, end)
    merged = plan.merge(storage.read_data(pid, "plan", []) or [], new, start, end)
    storage.write_data(pid, "plan", merged)
    return merged


@app.get("/api/settings")
def get_settings():
    data = _load_settings()
    key = data.pop("anthropic_api_key", None) or os.environ.get("ANTHROPIC_API_KEY")
    data["has_api_key"] = bool(key)
    data["api_key_hint"] = f"...{key[-4:]}" if key else ""
    return data


@app.put("/api/settings")
def put_settings(body: Settings):
    data = _load_settings()
    update = body.model_dump()
    if not update.get("anthropic_api_key"):
        update.pop("anthropic_api_key")  # kosong = tetap pakai kunci lama
    data.update(update)
    path = DATA_DIR / "settings.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return get_settings()


@app.get("/api/projects/{pid}/faces")
def get_faces(pid: str):
    _project_or_404(pid)
    return storage.read_data(pid, "faces", []) or []


@app.get("/api/projects/{pid}/plan")
def get_plan(pid: str):
    _project_or_404(pid)
    return storage.read_data(pid, "plan", []) or []


@app.put("/api/projects/{pid}/plan")
def put_plan(pid: str, items: list[PlanItem]):
    meta = _project_or_404(pid)
    data = plan.normalize([i.model_dump() for i in items], 0, meta.get("duration") or 1e9)
    storage.write_data(pid, "plan", data)
    return data


@app.post("/api/projects/{pid}/plan/auto")
def plan_auto(pid: str, body: RangeRequest):
    ctx = _plan_context(pid, body.start, body.end)
    items = plan.heuristic(ctx["transcript"], storage.read_data(pid, "energy", []) or [], ctx["start"], ctx["end"])
    return _save_range(pid, items, ctx["start"], ctx["end"])


@app.post("/api/projects/{pid}/plan/prompt")
def plan_prompt(pid: str, body: RangeRequest):
    ctx = _plan_context(pid, body.start, body.end)
    return {"prompt": plan.build_prompt(ctx["transcript"], ctx["start"], ctx["end"], body.guidebook)}


@app.post("/api/projects/{pid}/plan/import")
def plan_import(pid: str, body: PlanImport):
    ctx = _plan_context(pid, body.start, body.end)
    try:
        items = plan.parse_reply(body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    saved = _save_range(pid, items, ctx["start"], ctx["end"])
    if not any(ctx["start"] <= i["start"] < ctx["end"] for i in saved):
        raise HTTPException(400, "JSON terbaca, tetapi tidak ada elemen yang waktunya di dalam rentang klip ini.")
    return saved


@app.post("/api/projects/{pid}/plan/ai")
def plan_ai(pid: str, body: RangeRequest):
    ctx = _plan_context(pid, body.start, body.end)
    settings = _load_settings()
    key = settings.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise HTTPException(400, "API key Claude belum diisi. Isi di panel Animasi, atau pakai mode salin-tempel.")
    try:
        items = plan.ask_claude(ctx["transcript"], ctx["start"], ctx["end"], body.guidebook,
                                key, settings.get("model") or "claude-opus-5-5")
    except ImportError:
        raise HTTPException(500, "Library 'anthropic' belum terpasang. Jalankan: pip install -r requirements.txt")
    except Exception as exc:  # error API (kunci salah, kuota, jaringan) ditampilkan apa adanya
        logging.getLogger("video-editing.plan").exception("Claude API gagal")
        raise HTTPException(502, f"Claude API gagal: {exc}")
    return _save_range(pid, items, ctx["start"], ctx["end"])


app.mount("/fonts", StaticFiles(directory=FONTS_DIR), name="fonts")
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
