"""Alur kerja: unggah klip -> analisis (transkrip, kalimat, adegan) -> rencana cerita -> render."""
import logging
import re
import shutil
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from . import ai, analyze, faces, media, render, storage, story, transcribe
from .config import JOB_WORKERS, MAX_CLIPS, MIN_CLIPS, OUT_H, OUT_W, STORY_PLANNER
from .subtitles import SubtitleOptions, build_ass
from .timeline import build_timeline

log = logging.getLogger("storymaker.pipeline")
_pool = ThreadPoolExecutor(max_workers=max(1, JOB_WORKERS), thread_name_prefix="story-job")
_running: set[str] = set()

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".3gp"}


class JobError(RuntimeError):
    pass


# ---------------------------------------------------------------- antrean

def _set_job(pid: str, stage: str, progress: Optional[float], message: str, error: str = "") -> None:
    storage.update(pid, job={"stage": stage, "progress": None if progress is None else round(progress, 1),
                             "message": message, "error": error, "updated": time.time()})


def submit(pid: str, stage: str, fn: Callable, *args) -> None:
    if pid in _running:
        raise JobError("Proyek ini sedang diproses. Tunggu sampai selesai.")
    _running.add(pid)
    _set_job(pid, stage, 0, "Menunggu giliran...")

    def run():
        try:
            fn(pid, *args)
        except Exception as exc:  # pekerjaan latar: tampilkan error ke pengguna
            log.error("Pekerjaan %s gagal untuk %s:\n%s", stage, pid, traceback.format_exc())
            storage.update(pid, status="gagal")
            _set_job(pid, stage, None, "Gagal", str(exc)[-800:])
        finally:
            _running.discard(pid)

    _pool.submit(run)


def is_running(pid: str) -> bool:
    return pid in _running


# ---------------------------------------------------------------- klip

def add_clip(pid: str, filename: str, src_file) -> dict:
    with storage.lock(pid):
        meta = storage.load(pid)
        if len(meta["clips"]) >= MAX_CLIPS:
            raise JobError(f"Maksimal {MAX_CLIPS} video per proyek.")
        ext = Path(filename).suffix.lower()
        if ext not in VIDEO_EXT:
            raise JobError(f"Format {ext or '(tanpa ekstensi)'} tidak didukung.")
        n = max([int(c["id"][1:]) for c in meta["clips"]] + [0]) + 1
        cid = f"c{n}"
        folder = storage.clip_dir(pid, cid)
        folder.mkdir(parents=True, exist_ok=True)
        dst = folder / f"source{ext}"
        with open(dst, "wb") as out:
            shutil.copyfileobj(src_file, out, length=1024 * 1024)
        try:
            info = media.probe(dst)
        except media.MediaError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise JobError(str(exc)) from exc
        clip = {"id": cid, "name": re.sub(r"[^\w .()-]", "_", filename)[:80], "file": dst.name, "info": info,
                "status": "baru", "order": len(meta["clips"])}
        meta["clips"].append(clip)
        meta["status"] = "baru"
        storage.save(meta)
        return clip


def remove_clip(pid: str, cid: str) -> None:
    with storage.lock(pid):
        meta = storage.load(pid)
        meta["clips"] = [c for c in meta["clips"] if c["id"] != cid]
        for i, c in enumerate(meta["clips"]):
            c["order"] = i
        meta["status"] = "baru"
        storage.save(meta)
    shutil.rmtree(storage.clip_dir(pid, cid), ignore_errors=True)


def clip_source(pid: str, clip: dict) -> Path:
    return storage.clip_dir(pid, clip["id"]) / clip["file"]


# ---------------------------------------------------------------- analisis

def analyze_clip(pid: str, clip: dict, report: Callable[[float, str], None]) -> dict:
    folder = storage.clip_dir(pid, clip["id"])
    cached = storage.read_json(folder / "analysis.json")
    if cached:
        return cached
    src = clip_source(pid, clip)
    info = clip["info"]
    report(0.02, "membuat preview")
    media.make_preview(src, folder / "preview.mp4", info["duration"], info["has_audio"])
    report(0.1, "transkripsi ucapan")
    transcript = {"language": None, "segments": []}
    if info["has_audio"]:
        transcript = transcribe.transcribe(src, info["duration"],
                                           progress=lambda p: report(0.1 + 0.6 * p, "transkripsi ucapan"))
    report(0.72, "menganalisis suara & adegan")
    energy = media.audio_energy(src)
    cuts = media.scene_cuts(src)
    stats = media.frame_stats(src)
    report(0.85, "mendeteksi wajah")
    track = faces.track(src, info["width"], info["height"])
    data = {"transcript": transcript, "energy": energy, "cuts": cuts, "stats": stats, "faces": track}
    storage._write_json(folder / "analysis.json", data)
    return data


def build_catalog(pid: str, meta: dict, analyses: dict[str, dict]) -> dict:
    clips = sorted(meta["clips"], key=lambda c: c["order"])
    old = storage.load_json(pid, "catalog.json", {}) or {}
    old_shots = {s["id"]: s for s in old.get("shots", [])}
    edits = (old.get("edits") or {})
    catalog = {"clips": [], "utterances": [], "shots": [], "edits": edits}
    for order, c in enumerate(clips):
        a = analyses[c["id"]]
        catalog["clips"].append({"id": c["id"], "name": c["name"], "duration": c["info"]["duration"],
                                 "width": c["info"]["width"], "height": c["info"]["height"],
                                 "language": a["transcript"].get("language")})
        utts = analyze.build_utterances(c["id"], a["transcript"], a["energy"], order, len(clips))
        for u in utts:
            if u["id"] in edits:
                apply_text_edit(u, edits[u["id"]])
        shots = analyze.build_shots(c["id"], c["info"]["duration"], a["cuts"], a["stats"], utts)
        for s in shots:  # pertahankan deskripsi AI dari analisis sebelumnya
            prev = old_shots.get(s["id"])
            if prev and prev.get("description") and abs(prev["start"] - s["start"]) < 0.01:
                for k in ("description", "tags", "talking_head", "usable"):
                    s[k] = prev.get(k, s.get(k))
        catalog["utterances"] += utts
        catalog["shots"] += shots
    return catalog


def apply_text_edit(u: dict, text: str) -> None:
    """Koreksi teks subtitle oleh pengguna. Waktu kata dibagi ulang sesuai panjang huruf."""
    toks = text.split()
    if not toks:
        return
    words = u["words"]
    if len(toks) == len(words):
        u["words"] = [dict(w, word=t) for w, t in zip(words, toks)]
    else:
        start, end = u["start"], u["end"]
        total = sum(len(t) + 1 for t in toks)
        t, out = start, []
        for tok in toks:
            d = (end - start) * (len(tok) + 1) / total
            out.append({"start": round(t, 3), "end": round(t + d * 0.92, 3), "word": tok})
            t += d
        u["words"] = out
    u["text"] = " ".join(toks)
    u["edited"] = True


def _thumbs(pid: str, meta: dict, catalog: dict) -> dict[str, Path]:
    out = {}
    clips = {c["id"]: c for c in meta["clips"]}
    for s in catalog["shots"]:
        dst = storage.clip_dir(pid, s["clip_id"]) / "thumbs" / f"{s['id']}.jpg"
        dst.parent.mkdir(exist_ok=True)
        if not dst.exists():
            try:
                media.thumbnail(clip_source(pid, clips[s["clip_id"]]), (s["start"] + s["end"]) / 2, dst)
            except media.MediaError:
                continue
        out[s["id"]] = dst
    return out


def run_analysis(pid: str, planner: str = STORY_PLANNER, api_key: Optional[str] = None) -> None:
    meta = storage.load(pid)
    clips = sorted(meta["clips"], key=lambda c: c["order"])
    if len(clips) < MIN_CLIPS:
        raise JobError(f"Masukkan minimal {MIN_CLIPS} video.")
    storage.update(pid, status="menganalisis")
    analyses = {}
    n = len(clips)
    for i, c in enumerate(clips):
        storage.update_clip(pid, c["id"], status="menganalisis")

        def report(p: float, msg: str, i=i, c=c):
            _set_job(pid, "analyze", 80 * (i + p) / n, f"Video {i + 1}/{n} ({c['name']}): {msg}...")

        analyses[c["id"]] = analyze_clip(pid, c, report)
        storage.update_clip(pid, c["id"], status="siap")

    _set_job(pid, "analyze", 82, "Menyusun katalog kalimat & adegan...")
    catalog = build_catalog(pid, meta, analyses)
    thumbs = _thumbs(pid, meta, catalog)
    use_ai = planner == "claude" or (planner == "auto" and ai.available(api_key))
    if use_ai and any(not s.get("description") for s in catalog["shots"]):
        _set_job(pid, "analyze", 86, "Claude mengenali isi setiap adegan...")
        try:
            desc = ai.describe_shots([s for s in catalog["shots"] if not s.get("description")], thumbs, api_key)
            for s in catalog["shots"]:
                if s["id"] in desc:
                    d = desc[s["id"]]
                    s.update(description=d["description"][:160], tags=[t[:24] for t in d["tags"]][:8],
                             talking_head=bool(d["talking_head"]), usable=bool(d["usable"]))
        except ai.AIError as exc:
            if planner == "claude":
                raise
            log.warning("Deskripsi adegan dilewati: %s", exc)
    storage.save_json(pid, "catalog.json", catalog)
    make_plan(pid, planner, api_key, catalog=catalog, progress_at=92)


def make_plan(pid: str, planner: str = STORY_PLANNER, api_key: Optional[str] = None,
              hook_id: Optional[str] = None, catalog: Optional[dict] = None, progress_at: float = 10) -> dict:
    meta = storage.load(pid)
    catalog = catalog or storage.load_json(pid, "catalog.json")
    if not catalog:
        raise JobError("Analisis belum dijalankan.")
    target = float(meta.get("target") or 60)
    plan = None
    use_ai = planner == "claude" or (planner == "auto" and ai.available(api_key))
    if use_ai:
        _set_job(pid, "plan", progress_at, "Claude membaca seluruh transkrip & menyusun cerita...")
        try:
            raw = ai.plan_story(catalog, target, api_key, meta.get("guidance", ""))
            plan = story.validate_plan(raw, catalog, target)
            if hook_id:
                _replace_hook(plan, hook_id, catalog)
        except ai.AIError as exc:
            if planner == "claude":
                raise
            log.warning("Claude gagal, memakai penyusun heuristik: %s", exc)
    if plan is None:
        plan = story.validate_plan(story.heuristic_plan(catalog, target, hook_id), catalog, target)
        # Tanpa AI, judul diambil dari nama proyek (diisi redaksi) agar tidak mengulang teks hook.
        plan.title = meta.get("name", "")[:80]
    storage.save_json(pid, "plan.json", plan.model_dump())
    storage.update(pid, status="rencana siap")
    _set_job(pid, "plan", 100, "Rencana cerita siap. Periksa lalu render.")
    return plan.model_dump()


def _replace_hook(plan: story.StoryPlan, hook_id: str, catalog: dict) -> None:
    for sec in plan.sections:
        sec.items = [it for it in sec.items if it.ref != hook_id]
    hook = next((s for s in plan.sections if s.role == "hook"), None)
    if hook:
        hook.items = [story.PlanItem(ref=hook_id)]


# ---------------------------------------------------------------- render

def run_render(pid: str, settings: dict) -> None:
    meta = storage.load(pid)
    catalog = storage.load_json(pid, "catalog.json")
    raw = storage.load_json(pid, "plan.json")
    if not catalog or not raw:
        raise JobError("Rencana cerita belum ada.")
    plan = story.validate_plan(raw, catalog, float(meta.get("target") or 60), fill_broll=False)
    storage.update(pid, status="merender")
    transition = settings.get("transition", "fade")
    tl = build_timeline(plan, catalog, transition=0.0 if transition == "none" else story.TRANSITION)

    clips = {c["id"]: c for c in meta["clips"]}
    paths = {cid: clip_source(pid, c) for cid, c in clips.items()}
    infos = {cid: c["info"] for cid, c in clips.items()}
    tracks = {cid: (storage.read_json(storage.clip_dir(pid, cid) / "analysis.json", {}) or {}).get("faces", [])
              for cid in clips}

    subs_ass = None
    if settings.get("subtitles", True):
        sopts = SubtitleOptions(style=settings.get("subtitle_style", "highlight"),
                                uppercase=bool(settings.get("uppercase", False)),
                                size=float(settings.get("subtitle_size", 1.0)),
                                highlight=settings.get("highlight", "#FFD400"))
        title = plan.title if settings.get("show_title", True) else ""
        hook_len = tl.segments[0].duration if tl.segments else 0
        subs_ass = build_ass(tl.words(), OUT_W, OUT_H, sopts, title=title, title_until=min(3.5, hook_len))

    music = None
    if settings.get("music"):
        m = storage.project_dir(pid) / "music" / Path(settings["music"]).name
        music = m if m.exists() else None
    opts = render.RenderOptions(framing=settings.get("framing", "auto"), transition=transition, music=music,
                                music_volume=float(settings.get("music_volume", 0.12)))
    out_dir = storage.project_dir(pid) / "outputs"
    out_dir.mkdir(exist_ok=True)
    name = f"story_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    work = storage.project_dir(pid) / "work"
    render.render(tl, paths, infos, tracks, subs_ass, out_dir / name, work, opts,
                  progress=lambda p, msg: _set_job(pid, "render", 100 * p, msg))
    shutil.rmtree(work, ignore_errors=True)
    with storage.lock(pid):
        meta = storage.load(pid)
        meta["outputs"].insert(0, {"file": name, "duration": tl.duration, "created": time.time(),
                                   "segments": len(tl.segments), "title": plan.title})
        meta["status"] = "selesai"
        storage.save(meta)
    _set_job(pid, "render", 100, "Video selesai dirender.")
