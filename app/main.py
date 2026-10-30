"""Server FastAPI: API + UI web statis. Jalankan: uvicorn app.main:app --port 8000"""
import hmac
import json
import logging
import os
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from sqlalchemy import delete, func, insert, select, true, update
from sqlalchemy.exc import IntegrityError, OperationalError

from . import ai, auth, clipper, db, jobs, lifecycle, monitor, plan, storage, uploads
from .config import (ALLOW_URL_IMPORT, DATA_DIR, EMBEDDED_WORKERS, FFMPEG, FFPROBE, FONTS_DIR, JOB_WORKERS,
                     MAX_PENDING_PER_USER, MAX_UPLOAD_MB, MEDIA_LIFECYCLE, MEDIA_MAX_HOURS, MEDIA_RETENTION_HOURS,
                     METRICS_TOKEN, UPLOAD_CHUNK_MB, UPLOAD_RESUME_HOURS, UPLOAD_TMP_DIR,
                     WEB_DIR, WHISPER_MODEL)
from .schemas import (GuidebookIn, LoginRequest, PasswordChange, PlanImport, PlanItem, RangeRequest,
                      RenderRequest, Settings, SuggestRequest, UnitIn, UrlImport, UserCreate, UserUpdate)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("video-editing")

# Upload multipart ditampung Starlette di file sementara: arahkan ke disk data (bukan /tmp sistem)
# supaya ikut terhitung oleh penjaga disk dan dibersihkan pembersih media.
tempfile.tempdir = str(UPLOAD_TMP_DIR)
MEDIA_GONE = ("Media proyek ini sudah dihapus otomatis karena melewati masa simpan "
              f"({MEDIA_RETENTION_HOURS:.0f} jam sejak aktivitas terakhir, maksimal {MEDIA_MAX_HOURS:.0f} jam "
              "sejak upload). Proyek tidak bisa dibuka kembali; unggah ulang videonya untuk mengedit lagi.")

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".3gp", ".ts"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
CurrentUser = Depends(auth.current_user)


# ------------------------------------------------------------------ start-up

def bootstrap() -> None:
    """Siapkan database, admin pertama (dari env), dan data dari versi lama."""
    db.engine()
    email, password = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
    if email and password and auth.count_users() == 0:
        try:
            auth.create_user(email, "Admin", password, "admin", None)
            log.info("Admin pertama dibuat: %s", email)
        except (HTTPException, IntegrityError):
            pass  # proses web lain sudah membuatnya
    try:
        migrate_legacy()
        jobs.migrate_legacy_queues()  # job aktif dari antrean lama (berat/unduh) -> analisis/render/io
    except Exception:
        log.exception("Migrasi data lama gagal (aplikasi tetap berjalan)")


def migrate_legacy() -> None:
    """Proyek & pengaturan AI dari versi satu-pengguna dipindahkan ke unit 'Umum' milik admin pertama."""
    legacy = list(storage.PROJECTS_DIR.glob("*/project.json")) if storage.PROJECTS_DIR.exists() else []
    old_settings = DATA_DIR / "settings.json"
    with db.engine().connect() as c:
        admin = c.execute(select(db.users.c.id).where(db.users.c.role == "admin")
                          .order_by(db.users.c.created).limit(1)).scalar()
    if not admin:
        return
    if legacy:
        unit_id = ensure_unit("Umum")
        n = storage.import_legacy_projects(unit_id, admin)
        log.info("%s proyek lama dipindahkan ke unit 'Umum'", n)
    if old_settings.exists() and db.get_setting("ai") is None:
        try:
            data = json.loads(old_settings.read_text(encoding="utf-8"))
            if "anthropic_api_key" in data:  # format paling lama (khusus Claude)
                data = {"provider": "claude", "providers": {"claude": {
                    "api_key": data.get("anthropic_api_key"), "model": data.get("model")}}}
            db.set_setting("ai", {"provider": data.get("provider", "claude"), "providers": data.get("providers", {})})
            old_settings.rename(old_settings.with_suffix(".json.migrated"))
        except (OSError, ValueError):
            pass


def ensure_unit(name: str) -> str:
    query = select(db.units.c.id).where(db.units.c.name == name)
    with db.engine().connect() as c:
        uid = c.execute(query).scalar()
    if uid:
        return uid
    try:
        with db.engine().begin() as c:
            uid = storage.new_id()
            c.execute(insert(db.units).values(id=uid, name=name, guidebook="", daily_render_quota=0,
                                              daily_ai_quota=0, created=time.time()))
        return uid
    except IntegrityError:  # dibuat proses lain di saat yang sama
        with db.engine().connect() as c:
            return c.execute(query).scalar()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap()
    stop = None
    if EMBEDDED_WORKERS:
        from .worker import start_embedded
        stop = start_embedded(JOB_WORKERS)
    yield
    if stop:
        stop.set()
        from . import jobcontrol
        # Jangan tinggalkan FFmpeg berjalan saat aplikasi ditutup; job dikembalikan ke antrean
        # (bukan dianggap batal) dan dilanjutkan saat aplikasi dinyalakan lagi.
        jobcontrol.cancel_all("shutdown")


app = FastAPI(title="Video Editing AI", version="0.2.0", lifespan=lifespan)


@app.exception_handler(lifecycle.MediaGone)
async def media_gone(_request: Request, _exc: lifecycle.MediaGone):
    return JSONResponse({"detail": MEDIA_GONE, "media_gone": True}, status_code=410)


@app.exception_handler(uploads.UploadError)
async def upload_error(_request: Request, exc: uploads.UploadError):
    headers = {"Tus-Resumable": uploads.TUS_VERSION, "Cache-Control": "no-store"}
    if exc.offset is not None:
        headers["Upload-Offset"] = str(exc.offset)
    return JSONResponse({"detail": str(exc)}, status_code=exc.status, headers=headers)


@app.exception_handler(jobs.QueueFull)
async def queue_full(_request: Request, exc: jobs.QueueFull):
    headers = {"Retry-After": "60"} if exc.status == 503 else {}
    return JSONResponse({"detail": str(exc), "queue_full": True}, status_code=exc.status, headers=headers)


@app.exception_handler(storage.NotFound)
async def project_folder_missing(_request: Request, exc: storage.NotFound):
    # Folder proyek sudah tidak ada (media dihapus) tetapi metadatanya masih ada: 410, bukan error 500.
    try:
        storage.load_project(str(exc))
    except storage.NotFound:
        return JSONResponse({"detail": "Proyek tidak ditemukan."}, status_code=404)
    return JSONResponse({"detail": MEDIA_GONE, "media_gone": True}, status_code=410)


@app.exception_handler(lifecycle.StorageFull)
async def storage_full(_request: Request, exc: lifecycle.StorageFull):
    return JSONResponse({"detail": str(exc), "storage_full": True}, status_code=507)


def _is_upload(request: Request) -> bool:
    path = request.url.path.rstrip("/")
    return request.method == "POST" and (path == "/api/projects" or
                                         (path.startswith("/api/projects/") and path.endswith("/music")))


@app.middleware("http")
async def upload_capacity_guard(request: Request, call_next):
    """Cek disk & kuota SEBELUM isi upload diterima (Starlette menulis seluruh isi ke disk dulu)."""
    if _is_upload(request):
        size = int(request.headers.get("content-length") or 0)
        try:
            lifecycle.ensure_disk(size)
            try:
                user = auth.current_user(request)
            except HTTPException:
                user = None  # biarkan endpoint yang menolak (401)
            if user is not None:
                lifecycle.ensure_quota(_unit(user["unit_id"]), size)
        except lifecycle.StorageFull as exc:
            return JSONResponse({"detail": str(exc), "storage_full": True}, status_code=507)
    return await call_next(request)


@app.exception_handler(OperationalError)
async def db_unavailable(_request: Request, exc: OperationalError):
    # Database sibuk/sedang restart: beri pesan jelas (UI akan mencoba lagi), bukan error 500 mentah.
    log.warning("Database tidak tersedia: %s", str(exc).splitlines()[0][:200])
    return JSONResponse({"detail": "Server sedang sibuk. Coba lagi beberapa detik lagi."}, status_code=503,
                        headers={"Retry-After": "5"})


@app.middleware("http")
async def same_origin_guard(request: Request, call_next):
    """Tolak permintaan pengubah data dari situs lain (lapisan tambahan selain cookie SameSite)."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if origin and host and urlparse(origin).netloc != host:
            return JSONResponse({"detail": "Permintaan lintas situs ditolak."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# ------------------------------------------------------------------ helper

class LeasedFileResponse(FileResponse):
    """FileResponse yang memegang lease media selama dikirim: diperpanjang berkala, dilepas saat selesai
    atau koneksi terputus. Bila proses mati, lease kedaluwarsa sendiri (LEASE_TTL)."""

    def __init__(self, path, lease: str, **kwargs):
        super().__init__(path, **kwargs)
        self.lease = lease

    async def __call__(self, scope, receive, send):
        last = time.time()

        async def send_renewing(message):
            nonlocal last
            if time.time() - last > lifecycle.LEASE_RENEW_EVERY:
                last = time.time()
                await run_in_threadpool(lifecycle.renew_lease, self.lease)
            await send(message)

        try:
            await super().__call__(scope, receive, send_renewing)
        finally:
            await run_in_threadpool(lifecycle.release_lease, self.lease)


def _project_for(pid: str, user: dict) -> dict:
    try:
        meta = storage.load_project(pid)
    except storage.NotFound:
        raise HTTPException(404, "Proyek tidak ditemukan.")
    if not auth.can_view_project(user, meta):
        raise HTTPException(404, "Proyek tidak ditemukan.")  # jangan bocorkan keberadaan proyek unit lain
    return meta


def _upload_size(upload: UploadFile) -> int:
    """Ukuran pasti file yang sudah diterima Starlette (bukan angka dari browser)."""
    if upload.size is not None:
        return int(upload.size)
    pos = upload.file.tell()
    upload.file.seek(0, os.SEEK_END)
    size = upload.file.tell()
    upload.file.seek(pos)
    return size


def _save_upload(upload: UploadFile, dst: Path, request: Request, lease: Optional[str] = None,
                 reserved: Optional[int] = None) -> None:
    limit = MAX_UPLOAD_MB * 1024 * 1024
    declared = int(request.headers.get("content-length") or 0)
    if declared > limit + 1024 * 1024:
        raise HTTPException(413, f"File terlalu besar. Maksimal {MAX_UPLOAD_MB} MB.")
    written = 0
    last_renew = time.time()
    try:
        with dst.open("wb") as fh:
            while chunk := upload.file.read(4 * 1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise HTTPException(413, f"File terlalu besar. Maksimal {MAX_UPLOAD_MB} MB.")
                if reserved is not None and written > reserved:
                    raise lifecycle.StorageFull("Ukuran file melebihi ruang yang dipesan. Unggah ulang.")
                if written % (64 * 1024 * 1024) < len(chunk):
                    lifecycle.ensure_disk()  # disk bisa terisi pekerjaan lain selama upload besar
                if lease and time.time() - last_renew > lifecycle.LEASE_RENEW_EVERY:
                    lifecycle.renew_lease(lease)
                    last_renew = time.time()
                fh.write(chunk)
    except (HTTPException, lifecycle.StorageFull, OSError):
        dst.unlink(missing_ok=True)
        raise


def _project_view(meta: dict) -> dict:
    data = dict(meta)
    data["jobs"] = jobs.view_many(jobs.for_project(meta["id"]))
    data["media"] = lifecycle.info(meta["id"])  # baca saja: tidak memperpanjang masa simpan
    usable = data["media"]["state"] in ("tersedia", "lama")
    folder = storage.PROJECTS_DIR / meta["id"] / "renders"
    # Riwayat render tetap ditampilkan, tetapi hanya yang filenya benar-benar ada yang boleh diunduh.
    data["renders"] = [dict(r, available=usable and (folder / r["file"]).is_file()) for r in meta.get("renders", [])]
    # Preview bisa diputar sebelum analisis (transkrip) selesai.
    data["preview_ready"] = usable and (folder.parent / "preview.mp4").is_file()
    return data


def _check_pending(user: dict) -> None:
    if jobs.pending_for_user(user["id"]) >= MAX_PENDING_PER_USER:
        raise HTTPException(429, f"Anda sudah punya {MAX_PENDING_PER_USER} pekerjaan di antrean. "
                                 "Tunggu sebagian selesai dulu.")


def _unit(unit_id: Optional[str]) -> Optional[dict]:
    if not unit_id:
        return None
    with db.engine().connect() as c:
        return db.row(c.execute(select(db.units).where(db.units.c.id == unit_id)).first())


def _check_quota(user: dict, kind: str) -> None:
    unit = _unit(user["unit_id"])
    if not unit:
        return
    limit = unit["daily_render_quota"] if kind == "render" else unit["daily_ai_quota"]
    if limit and db.usage_count(kind, unit["id"], db.start_of_today()) >= limit:
        what = "render" if kind == "render" else "AI"
        raise HTTPException(429, f"Kuota {what} unit {unit['name']} hari ini ({limit}) sudah habis. "
                                 "Hubungi admin untuk menambah kuota.")


def _queue_job(task: str, kind: str, meta: dict, user: dict, payload: dict, label: str,
               queue: Optional[str] = None) -> dict:
    """Ajukan job atas media proyek: dikunci terhadap pembersihan (lease) dan dihitung sebagai aktivitas."""
    jobs.check_capacity(task, meta.get("unit_id"), queue)  # backpressure: antrean unit/server penuh
    lifecycle.ensure_disk()
    with lifecycle.reserve(meta["id"]) as conn:
        job = jobs.submit(task, kind, meta["id"], payload, label, queue=queue,
                          user_id=user["id"], unit_id=meta.get("unit_id"), conn=conn)
    return jobs.view(job)


# ------------------------------------------------------------------ akun

@app.post("/api/auth/login")
def login(body: LoginRequest, request: Request, response: Response):
    return auth.login(request, response, body.email, body.password)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    auth.logout(request, response)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: dict = CurrentUser):
    data = auth.public_user(user)
    ai_cfg = _ai_settings()
    provider = ai.PROVIDERS[ai_cfg["provider"]]
    cfg = _provider_cfg(ai_cfg, ai_cfg["provider"])
    data["ai"] = {"name": provider["name"], "ready": bool(cfg.get("api_key")) or not provider["needs_key"]}
    data["url_import"] = ALLOW_URL_IMPORT == "all" or (ALLOW_URL_IMPORT == "admins" and user["role"] != "editor")
    data["media_policy"] = {"lifecycle": MEDIA_LIFECYCLE, "retention_hours": MEDIA_RETENTION_HOURS,
                            "max_hours": MEDIA_MAX_HOURS}
    return data


@app.post("/api/auth/password")
def change_password(body: PasswordChange, request: Request, response: Response, user: dict = CurrentUser):
    if not auth.verify_password(body.old_password, user["password_hash"]):
        raise HTTPException(400, "Password lama salah.")
    auth.check_password_rules(body.new_password)
    with db.engine().begin() as c:
        c.execute(update(db.users).where(db.users.c.id == user["id"])
                  .values(password_hash=auth.hash_password(body.new_password)))
    auth.end_user_sessions(user["id"])
    return auth.login(request, response, user["email"], body.new_password)


# ------------------------------------------------------------------ umum

@app.get("/api/health")
def health():
    ok_db = True
    try:
        jobs.ping()
    except Exception:
        ok_db = False
    return {
        "ffmpeg": shutil.which(FFMPEG) is not None or Path(FFMPEG).exists(),
        "ffprobe": shutil.which(FFPROBE) is not None or Path(FFPROBE).exists(),
        "database": ok_db,
        "whisper_model": WHISPER_MODEL,
    }


@app.get("/api/unit")
def my_unit(user: dict = CurrentUser):
    unit = _unit(user["unit_id"])
    if not unit:
        return {"id": None, "name": None, "guidebook": "", "can_edit": False}
    today = db.start_of_today()
    return {**unit, "can_edit": user["role"] in ("admin", "unit_admin"),
            "renders_today": db.usage_count("render", unit["id"], today),
            "ai_today": db.usage_count("ai", unit["id"], today),
            "storage_used": lifecycle.unit_usage(unit["id"]), "storage_quota": lifecycle.unit_quota_bytes(unit)}


@app.put("/api/unit/guidebook")
def set_guidebook(body: GuidebookIn, user: dict = CurrentUser):
    auth.require_manager(user)
    if not user["unit_id"]:
        raise HTTPException(400, "Akun Anda tidak terhubung ke unit. Atur guidebook unit di halaman Admin.")
    with db.engine().begin() as c:
        c.execute(update(db.units).where(db.units.c.id == user["unit_id"]).values(guidebook=body.guidebook))
    return my_unit(user)


# ------------------------------------------------------------------ proyek

@app.get("/api/projects")
def list_projects(user: dict = CurrentUser, unit: Optional[str] = None):
    if user["role"] == "admin":
        items = storage.list_projects(unit_id=unit, all_units=not unit)
    else:
        items = storage.list_projects(unit_id=user["unit_id"])
    media = lifecycle.info_many([p["id"] for p in items])
    for p in items:
        p["media"] = media[p["id"]]
        p["thumb"] = media[p["id"]]["state"] in ("tersedia", "lama") and (storage.PROJECTS_DIR / p["id"] / "thumb.jpg").is_file()
    return items


def _target_unit(user: dict) -> Optional[str]:
    if user["unit_id"]:
        return user["unit_id"]
    if user["role"] == "admin":
        return ensure_unit("Umum")
    raise HTTPException(400, "Akun Anda belum masuk ke unit mana pun. Hubungi admin.")


@app.post("/api/projects")
def upload_project(request: Request, file: UploadFile = File(...), language: Optional[str] = Form(None),
                   user: dict = CurrentUser):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(400, f"Format {ext or '(tanpa ekstensi)'} tidak didukung.")
    _check_pending(user)
    unit_id = _target_unit(user)
    jobs.check_capacity("analyze", unit_id)  # tolak sebelum file disalin bila antrean penuh
    size = _upload_size(file)
    lifecycle.ensure_disk(size)
    meta = storage.create_project(Path(file.filename).stem, unit_id, user["id"])
    pid = meta["id"]
    lease = None
    try:
        # Reservasi kuota atomik dengan ukuran sebenarnya; upload lain di unit yang sama menunggu giliran.
        lease = lifecycle.acquire_lease(pid, "upload", nbytes=size, unit=_unit(unit_id))
        _save_upload(file, storage.project_dir(pid) / f"source{ext}", request, lease, size)
        lifecycle.commit_upload(lease, pid)
    except BaseException:
        if lease:
            lifecycle.release_lease(lease, collect=False)
        storage.delete_project(pid)  # upload gagal/terputus: jangan tinggalkan proyek & file setengah jadi
        raise
    meta = storage.update_project(pid, source=f"source{ext}", status="antre")
    job = _queue_job("analyze", "analisis", meta, user, {"language": language or None}, "Analisis video")
    return {"project": meta, "job": job}


@app.post("/api/projects/from-url")
def import_url(body: UrlImport, user: dict = CurrentUser):
    allowed = ALLOW_URL_IMPORT == "all" or (ALLOW_URL_IMPORT == "admins" and user["role"] != "editor")
    if not allowed:
        raise HTTPException(403, "Ambil video dari link dinonaktifkan. Unggah file video Anda.")
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "Link harus diawali http:// atau https://")
    _check_pending(user)
    unit_id = _target_unit(user)
    jobs.check_capacity("download", unit_id)
    lifecycle.ensure_disk()
    lifecycle.ensure_quota(_unit(unit_id))
    meta = storage.create_project("Mengunduh...", unit_id, user["id"])
    meta = storage.update_project(meta["id"], status="antre", url=body.url)
    job = _queue_job("download", "analisis", meta, user, {"url": body.url, "language": body.language},
                     "Unduh video")
    return {"project": meta, "job": job}


# ------------------------------------------------------------------ upload bertahap (tus 1.0.0, P4)

TUS = {"Tus-Resumable": uploads.TUS_VERSION}
READ_PIECE = 1024 * 1024  # tulis ke disk per 1 MB: RAM per upload tetap kecil


def _tus_request(request: Request) -> None:
    if request.headers.get("tus-resumable") != uploads.TUS_VERSION:
        raise uploads.UploadError(412, "Versi protokol upload tidak didukung (butuh Tus-Resumable: 1.0.0).")


def _int_header(request: Request, name: str, required: bool = True) -> Optional[int]:
    raw = request.headers.get(name)
    if raw is None:
        if required:
            raise uploads.UploadError(400, f"Header {name} wajib.")
        return None
    if not raw.isdigit():
        raise uploads.UploadError(400, f"Header {name} tidak valid.")
    return int(raw)


@app.options("/api/uploads")
@app.options("/api/uploads/{uid}")
def upload_options(uid: Optional[str] = None):
    return Response(status_code=204, headers={**TUS, "Tus-Version": uploads.TUS_VERSION,
                                              "Tus-Extension": uploads.TUS_EXTENSIONS,
                                              "Tus-Max-Size": str(uploads.max_size()),
                                              "Tus-Checksum-Algorithm": ",".join(uploads.CHECKSUMS)})


@app.get("/api/uploads")
def upload_list(user: dict = CurrentUser):
    """Upload milik pengguna yang belum selesai (untuk dilanjutkan setelah refresh), beserta batasnya."""
    return {"uploads": uploads.list_active(user), "max_mb": MAX_UPLOAD_MB, "chunk_mb": UPLOAD_CHUNK_MB,
            "resume_hours": UPLOAD_RESUME_HOURS}


@app.post("/api/uploads")
def upload_create(request: Request, user: dict = CurrentUser):
    _tus_request(request)
    if request.headers.get("upload-defer-length"):
        raise uploads.UploadError(400, "Ukuran file harus diketahui di awal.")
    size = _int_header(request, "upload-length")
    meta = uploads.parse_metadata(request.headers.get("upload-metadata"))
    _check_pending(user)
    unit_id = _target_unit(user)
    jobs.check_capacity("analyze", unit_id)  # antrean penuh: tolak sebelum file dikirim
    row = uploads.create(user, unit_id, size, meta, VIDEO_EXTS)
    return Response(status_code=201, headers={**TUS, "Location": f"/api/uploads/{row['id']}", "Upload-Offset": "0"})


@app.head("/api/uploads/{uid}")
def upload_head(uid: str, user: dict = CurrentUser):
    row = uploads.recover(uploads.check_alive(uploads.get(uid, user)), lambda r: _start_analysis(r, user))
    if row["state"] in ("batal", "habis", "gagal"):
        raise uploads.UploadError(410, row["error"] or "Upload ini sudah tidak aktif.")
    return Response(status_code=200, headers={**TUS, "Upload-Offset": str(row["offset"]),
                                              "Upload-Length": str(row["size"]), "Cache-Control": "no-store"})


@app.get("/api/uploads/{uid}")
def upload_status(uid: str, user: dict = CurrentUser):
    row = uploads.get(uid, user)
    try:
        row = uploads.recover(uploads.check_alive(row), lambda r: _start_analysis(r, user))
    except uploads.UploadError:
        row = uploads.get(uid, user)  # status terbaru (gagal/habis) tetap dilaporkan sebagai data
    return uploads.view(row)


def _start_analysis(row: dict, user: dict) -> dict:
    existing = [j for j in jobs.for_project(row["project_id"]) if j["task"] == "analyze"]
    if existing:  # finalisasi yang dilanjutkan setelah crash: jangan menggandakan analisis
        return jobs.view(existing[-1])
    meta = storage.load_project(row["project_id"])
    language = json.loads(row.get("meta") or "{}").get("language")
    return _queue_job("analyze", "analisis", meta, user, {"language": language}, "Analisis video")


@app.patch("/api/uploads/{uid}")
async def upload_patch(uid: str, request: Request, user: dict = CurrentUser):
    """Satu potongan. Dibaca bertahap dari jaringan & ditulis per 1 MB di thread terpisah: upload besar tidak
    memblokir server dan tidak pernah dimuat utuh ke RAM."""
    _tus_request(request)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/offset+octet-stream":
        raise uploads.UploadError(415, "Content-Type harus application/offset+octet-stream.")
    offset = _int_header(request, "upload-offset")
    length = _int_header(request, "content-length", required=False)
    row = await run_in_threadpool(uploads.get, uid, user)
    writer = await run_in_threadpool(uploads.ChunkWriter, row, offset, length, request.headers.get("upload-checksum"))
    try:
        buf = bytearray()
        try:
            async for piece in request.stream():
                buf += piece
                if len(buf) >= READ_PIECE:
                    await run_in_threadpool(writer.write, bytes(buf))
                    buf.clear()
            if buf:
                await run_in_threadpool(writer.write, bytes(buf))
        except ClientDisconnect:
            # Koneksi putus di tengah potongan. Tanpa checksum, byte yang sudah diterima disimpan (tus);
            # dengan checksum, potongan tidak bisa diverifikasi sehingga dibuang & dikirim ulang klien.
            if writer.hasher is None and (writer.written or buf):
                if buf:
                    await run_in_threadpool(writer.write, bytes(buf))
                await run_in_threadpool(writer.commit, None)
            else:
                await run_in_threadpool(writer.rollback)
            return Response(status_code=400)
        row = await run_in_threadpool(writer.commit, lambda r: _start_analysis(r, user))
    except BaseException:
        if not writer.committed:
            await run_in_threadpool(writer.rollback)
        raise
    finally:
        await run_in_threadpool(writer.close)
    return Response(status_code=204, headers={**TUS, "Upload-Offset": str(row["offset"])})


@app.delete("/api/uploads/{uid}")
def upload_delete(uid: str, user: dict = CurrentUser):
    row = uploads.get(uid, user)
    uploads.abort(row, "batal")
    return Response(status_code=204, headers=TUS)


@app.get("/api/projects/{pid}")
def get_project(pid: str, user: dict = CurrentUser):
    meta = _project_for(pid, user)
    data = _project_view(meta)
    data["can_delete"] = auth.can_delete_project(user, meta)
    return data


@app.delete("/api/projects/{pid}")
def delete_project(pid: str, user: dict = CurrentUser):
    meta = _project_for(pid, user)
    if not auth.can_delete_project(user, meta):
        raise HTTPException(403, "Hanya pembuat proyek atau Admin Unit yang boleh menghapus.")
    if any(j["status"] in jobs.ACTIVE for j in jobs.for_project(pid)):
        raise HTTPException(409, "Proyek masih diproses. Batalkan pekerjaannya dulu atau tunggu sampai selesai.")
    storage.delete_project(pid)
    return {"ok": True}


@app.post("/api/projects/{pid}/reanalyze")
def reanalyze(pid: str, language: Optional[str] = None, user: dict = CurrentUser):
    meta = _project_for(pid, user)
    if any(j["status"] in jobs.ACTIVE and j["kind"] == "analisis" for j in jobs.for_project(pid)):
        raise HTTPException(409, "Analisis sedang berjalan.")
    if not meta.get("source"):
        raise HTTPException(409, "Video sumber tidak ada. Unggah ulang videonya.")
    _check_pending(user)
    return _queue_job("analyze", "analisis", meta, user, {"language": language, "reanalyze": True}, "Analisis ulang")


@app.get("/api/projects/{pid}/transcript")
def get_transcript(pid: str, user: dict = CurrentUser):
    _project_for(pid, user)
    return storage.read_data(pid, "transcript", {"language": None, "segments": []})


@app.get("/api/projects/{pid}/clips")
def get_clips(pid: str, user: dict = CurrentUser):
    _project_for(pid, user)
    return storage.read_data(pid, "clips", [])


@app.post("/api/projects/{pid}/clips/suggest")
def suggest_clips(pid: str, body: SuggestRequest, user: dict = CurrentUser):
    meta = _project_for(pid, user)
    transcript = storage.read_data(pid, "transcript")
    if transcript is None:
        raise HTTPException(409, "Video belum selesai dianalisis.")
    lifecycle.touch(pid)
    clips = clipper.suggest(transcript, storage.read_data(pid, "energy", []),
                            meta.get("duration") or 0, body.target, body.top_n)
    storage.write_data(pid, "clips", clips)
    return clips


@app.get("/api/projects/{pid}/preview")
def get_preview(pid: str, user: dict = CurrentUser):
    _project_for(pid, user)
    lifecycle.require_available(pid)  # memutar preview tidak memperpanjang masa simpan
    path = storage.project_dir(pid) / "preview.mp4"
    if not path.exists():
        raise HTTPException(404, "Preview belum siap.")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/projects/{pid}/thumb")
def get_thumb(pid: str, user: dict = CurrentUser):
    _project_for(pid, user)
    lifecycle.require_available(pid)
    path = storage.project_dir(pid) / "thumb.jpg"
    if not path.exists():
        raise HTTPException(404, "Belum ada gambar kecil.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/projects/{pid}/music")
def upload_music(pid: str, request: Request, file: UploadFile = File(...), user: dict = CurrentUser):
    meta = _project_for(pid, user)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXTS:
        raise HTTPException(400, "Format musik harus mp3, wav, m4a, aac, ogg, flac, atau opus.")
    lifecycle.touch(pid)
    name = storage.safe_filename(file.filename, f"musik{ext}")
    size = _upload_size(file)
    lifecycle.ensure_disk(size)
    dst = storage.project_dir(pid) / "music" / name
    lease = lifecycle.acquire_lease(pid, "upload", nbytes=size, unit=_unit(meta["unit_id"]))
    try:
        _save_upload(file, dst, request, lease, size)
        storage.add_music(pid, name)
        lifecycle.commit_upload(lease, pid)
    except BaseException:
        lifecycle.release_lease(lease, collect=False)
        raise
    return {"file": name}


@app.post("/api/projects/{pid}/render")
def render_clip(pid: str, body: RenderRequest, user: dict = CurrentUser):
    meta = _project_for(pid, user)
    if meta.get("status") != "siap":
        raise HTTPException(409, "Video belum selesai dianalisis.")
    if body.start >= (meta.get("duration") or 0):
        raise HTTPException(400, "Titik awal klip melebihi durasi video.")
    _check_pending(user)
    _check_quota(user, "render")
    lifecycle.ensure_quota(_unit(meta.get("unit_id")))  # kuota media penuh: render baru ditunda
    label = body.title or f"Klip {body.start:.0f}s-{body.end:.0f}s"
    return _queue_job("render", "render", meta, user, {"request": body.model_dump()}, label)


@app.get("/api/projects/{pid}/renders/{file}")
def download_render(pid: str, file: str, download: bool = False, user: dict = CurrentUser):
    meta = _project_for(pid, user)
    item = next((r for r in meta.get("renders", []) if r["file"] == file), None)
    if item is None:
        raise HTTPException(404, "Hasil render tidak ditemukan.")
    if download:
        lifecycle.touch(pid)  # mengunduh = aktivitas pengguna (memutar di halaman tidak)
    else:
        lifecycle.require_available(pid)
    path = storage.project_dir(pid) / "renders" / item["file"]
    if not path.exists():
        raise HTTPException(410, "File hasil render ini sudah tidak tersedia.")
    name = storage.safe_filename(item["title"], "klip") + ".mp4"
    # Lease selama file dikirim: pembersihan tidak menghapus media yang sedang diunduh.
    lease = lifecycle.acquire_lease(pid, "download")
    return LeasedFileResponse(path, lease, media_type="video/mp4", filename=name if download else None)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, user: dict = CurrentUser):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Pekerjaan tidak ditemukan.")
    _project_for(job["project_id"], user)
    return jobs.view(job)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user: dict = CurrentUser):
    """Batalkan job: yang masih antre langsung batal, yang sedang berjalan dihentikan worker (±2 detik)."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Pekerjaan tidak ditemukan.")
    meta = _project_for(job["project_id"], user)
    if job["user_id"] != user["id"] and not auth.can_delete_project(user, meta):
        raise HTTPException(403, "Hanya pembuat pekerjaan atau Admin Unit yang boleh membatalkan.")
    if jobs.request_cancel(job_id) is None:
        raise HTTPException(409, "Pekerjaan sudah selesai, tidak bisa dibatalkan.")
    if job["status"] == "antre" and job["kind"] == "analisis":
        # Belum sempat diproses: proyek tidak boleh tertinggal di status "antre".
        storage.update_project(meta["id"], status="siap" if meta.get("status") == "siap" else "batal")
    return jobs.view(jobs.get(job_id))


# ------------------------------------------------------------------ rencana animasi

ENV_KEYS = {"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}


def _ai_settings() -> dict:
    """{"provider", "providers": {nama: {"api_key", "model", "base_url"}}} (pengaturan bersama, diatur admin)."""
    data = db.get_setting("ai") or {}
    data.setdefault("providers", {})
    if data.get("provider") not in ai.PROVIDERS:
        data["provider"] = "claude"
    return data


def _provider_cfg(settings: dict, provider: str) -> dict:
    cfg = dict(settings["providers"].get(provider) or {})
    if not cfg.get("api_key") and provider in ENV_KEYS:
        cfg["api_key"] = os.environ.get(ENV_KEYS[provider])
    return cfg


def _plan_context(pid: str, start: float, end: float, user: dict) -> dict:
    meta = _project_for(pid, user)
    if meta.get("status") != "siap":
        raise HTTPException(409, "Video belum selesai dianalisis.")
    lifecycle.touch(pid)  # membuat/menyunting animasi = aktivitas pengguna
    end = min(end, meta.get("duration") or end)
    if end - start < 1:
        raise HTTPException(400, "Rentang klip tidak valid.")
    unit = _unit(meta.get("unit_id"))
    return {"transcript": storage.read_data(pid, "transcript", {}) or {}, "start": start, "end": end,
            "guidebook": unit["guidebook"] if unit else ""}


def _guidebook(ctx: dict, body: RangeRequest) -> str:
    # Guidebook unit selalu dipakai agar gaya seragam; catatan tambahan dari editor ikut dikirim.
    extra = body.guidebook.strip()
    base = ctx["guidebook"].strip()
    return base if not extra or extra == base else (f"{base}\n{extra}" if base else extra)


def _save_range(pid: str, items: list[dict], start: float, end: float) -> list[dict]:
    new = plan.normalize(items, start, end)
    merged = plan.merge(storage.read_data(pid, "plan", []) or [], new, start, end)
    storage.write_data(pid, "plan", merged)
    return merged


@app.get("/api/settings")
def get_settings(user: dict = CurrentUser):
    """Pengaturan AI tanpa membocorkan API key (hanya 4 karakter terakhir)."""
    auth.require_admin(user)
    data = _ai_settings()
    providers = {}
    for name, info in ai.PROVIDERS.items():
        cfg = _provider_cfg(data, name)
        key = cfg.get("api_key") or ""
        providers[name] = {
            "label": info["name"], "needs_key": info["needs_key"], "key_url": info["key_url"],
            "default_base_url": info["base_url"], "base_url": cfg.get("base_url") or "",
            "model": cfg.get("model") or "", "default_model": info.get("default_model", ""),
            "has_key": bool(key), "key_hint": f"...{key[-4:]}" if key else "",
        }
    return {"provider": data["provider"], "providers": providers}


@app.put("/api/settings")
def put_settings(body: Settings, user: dict = CurrentUser):
    auth.require_admin(user)
    if body.provider not in ai.PROVIDERS:
        raise HTTPException(400, "Penyedia AI tidak dikenal.")
    data = _ai_settings()
    data["provider"] = body.provider
    cfg = data["providers"].setdefault(body.provider, {})
    if body.api_key:
        cfg["api_key"] = body.api_key.strip()
    elif body.clear_key:
        cfg.pop("api_key", None)
    cfg["model"] = (body.model or "").strip()
    cfg["base_url"] = (body.base_url or "").strip()
    db.set_setting("ai", data)
    return get_settings(user)


@app.get("/api/ai/models")
def ai_models(user: dict = CurrentUser):
    auth.require_admin(user)
    data = _ai_settings()
    try:
        return ai.list_models(data["provider"], _provider_cfg(data, data["provider"]))
    except Exception as exc:
        raise HTTPException(502, f"Gagal memuat daftar model: {exc}")


@app.get("/api/projects/{pid}/faces")
def get_faces(pid: str, user: dict = CurrentUser):
    _project_for(pid, user)
    return storage.read_data(pid, "faces", []) or []


@app.get("/api/projects/{pid}/plan")
def get_plan(pid: str, user: dict = CurrentUser):
    _project_for(pid, user)
    return storage.read_data(pid, "plan", []) or []


@app.put("/api/projects/{pid}/plan")
def put_plan(pid: str, items: list[PlanItem], user: dict = CurrentUser):
    meta = _project_for(pid, user)
    lifecycle.touch(pid)
    data = plan.normalize([i.model_dump() for i in items], 0, meta.get("duration") or 1e9)
    storage.write_data(pid, "plan", data)
    return data


@app.post("/api/projects/{pid}/plan/auto")
def plan_auto(pid: str, body: RangeRequest, user: dict = CurrentUser):
    ctx = _plan_context(pid, body.start, body.end, user)
    items = plan.heuristic(ctx["transcript"], storage.read_data(pid, "energy", []) or [], ctx["start"], ctx["end"])
    return _save_range(pid, items, ctx["start"], ctx["end"])


@app.post("/api/projects/{pid}/plan/prompt")
def plan_prompt(pid: str, body: RangeRequest, user: dict = CurrentUser):
    ctx = _plan_context(pid, body.start, body.end, user)
    return {"prompt": plan.build_prompt(ctx["transcript"], ctx["start"], ctx["end"], _guidebook(ctx, body))}


@app.post("/api/projects/{pid}/plan/import")
def plan_import(pid: str, body: PlanImport, user: dict = CurrentUser):
    ctx = _plan_context(pid, body.start, body.end, user)
    try:
        items = plan.parse_reply(body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    saved = _save_range(pid, items, ctx["start"], ctx["end"])
    if not any(ctx["start"] <= i["start"] < ctx["end"] for i in saved):
        raise HTTPException(400, "JSON terbaca, tetapi tidak ada elemen yang waktunya di dalam rentang klip ini.")
    return saved


@app.post("/api/projects/{pid}/plan/ai")
def plan_ai(pid: str, body: RangeRequest, user: dict = CurrentUser):
    ctx = _plan_context(pid, body.start, body.end, user)
    settings = _ai_settings()
    provider = settings["provider"]
    label = ai.PROVIDERS[provider]["name"]
    cfg = _provider_cfg(settings, provider)
    if ai.PROVIDERS[provider]["needs_key"] and not cfg.get("api_key"):
        raise HTTPException(400, f"API key {label} belum diatur admin. Pakai mode salin-tempel dulu.")
    _check_quota(user, "ai")
    try:
        items, model = ai.generate_plan(provider, cfg, ctx["transcript"], ctx["start"], ctx["end"],
                                        _guidebook(ctx, body))
    except ImportError:
        raise HTTPException(500, "Library belum lengkap. Jalankan: pip install -r requirements.txt")
    except Exception as exc:  # error API (kunci salah, kuota, jaringan) ditampilkan apa adanya
        log.exception("%s API gagal", label)
        raise HTTPException(502, f"{label} API gagal: {exc}")
    db.record_usage("ai", user["unit_id"], user["id"])
    if not cfg.get("model"):  # simpan model yang dipilih otomatis agar terlihat di halaman admin
        settings["providers"].setdefault(provider, {})["model"] = model
        db.set_setting("ai", settings)
    return _save_range(pid, items, ctx["start"], ctx["end"])


# ------------------------------------------------------------------ admin

@app.get("/api/admin/units")
def admin_units(user: dict = CurrentUser):
    auth.require_manager(user)
    q = select(db.units).order_by(db.units.c.name)
    if user["role"] != "admin":
        q = q.where(db.units.c.id == user["unit_id"])
    today = db.start_of_today()
    with db.engine().connect() as c:
        units = [db.row(r) for r in c.execute(q)]
        members = dict(c.execute(select(db.users.c.unit_id, func.count()).group_by(db.users.c.unit_id)).all())
    for u in units:
        u["members"] = members.get(u["id"], 0)
        u["renders_today"] = db.usage_count("render", u["id"], today)
        u["ai_today"] = db.usage_count("ai", u["id"], today)
        u["storage_used"] = lifecycle.unit_usage(u["id"])
        u["storage_quota"] = lifecycle.unit_quota_bytes(u)  # 0 = tanpa batas
    return units


@app.post("/api/admin/units")
def create_unit(body: UnitIn, user: dict = CurrentUser):
    auth.require_admin(user)
    with db.engine().begin() as c:
        if c.execute(select(db.units.c.id).where(db.units.c.name == body.name.strip())).first():
            raise HTTPException(409, "Nama unit sudah dipakai.")
        uid = storage.new_id()
        c.execute(insert(db.units).values(id=uid, name=body.name.strip(), guidebook=body.guidebook,
                                          daily_render_quota=body.daily_render_quota,
                                          daily_ai_quota=body.daily_ai_quota, created=time.time(),
                                          storage_quota_mb=body.storage_quota_mb))
    return _unit(uid)


@app.put("/api/admin/units/{unit_id}")
def edit_unit(unit_id: str, body: UnitIn, user: dict = CurrentUser):
    auth.require_manager(user)
    if not _unit(unit_id):
        raise HTTPException(404, "Unit tidak ditemukan.")
    values = {"guidebook": body.guidebook}
    if user["role"] == "admin":  # nama & kuota hanya diatur admin pusat
        values.update(name=body.name.strip(), daily_render_quota=body.daily_render_quota,
                      daily_ai_quota=body.daily_ai_quota)
        if "storage_quota_mb" in body.model_fields_set:
            values["storage_quota_mb"] = body.storage_quota_mb
    elif unit_id != user["unit_id"]:
        raise HTTPException(403, "Anda hanya bisa mengatur unit Anda sendiri.")
    with db.engine().begin() as c:
        c.execute(update(db.units).where(db.units.c.id == unit_id).values(**values))
    return _unit(unit_id)


@app.delete("/api/admin/units/{unit_id}")
def remove_unit(unit_id: str, user: dict = CurrentUser):
    auth.require_admin(user)
    with db.engine().connect() as c:
        used = c.execute(select(func.count()).select_from(db.users).where(db.users.c.unit_id == unit_id)).scalar() \
            + c.execute(select(func.count()).select_from(db.projects).where(db.projects.c.unit_id == unit_id)).scalar()
    if used:
        raise HTTPException(409, "Unit masih punya pengguna atau proyek. Pindahkan/hapus dulu.")
    with db.engine().begin() as c:
        c.execute(delete(db.units).where(db.units.c.id == unit_id))
    return {"ok": True}


@app.get("/api/admin/users")
def admin_users(user: dict = CurrentUser):
    auth.require_manager(user)
    q = (select(db.users, db.units.c.name.label("unit_name"))
         .select_from(db.users.outerjoin(db.units, db.units.c.id == db.users.c.unit_id)).order_by(db.users.c.name))
    if user["role"] != "admin":
        q = q.where(db.users.c.unit_id == user["unit_id"])
    with db.engine().connect() as c:
        return [auth.public_user(db.row(r)) for r in c.execute(q)]


@app.post("/api/admin/users")
def admin_create_user(body: UserCreate, user: dict = CurrentUser):
    auth.require_manager(user)
    unit_id = body.unit_id
    if user["role"] != "admin":
        if body.role == "admin":
            raise HTTPException(403, "Admin Unit tidak bisa membuat Admin.")
        unit_id = user["unit_id"]
    if unit_id and not _unit(unit_id):
        raise HTTPException(400, "Unit tidak ditemukan.")
    return auth.public_user(auth.create_user(body.email, body.name, body.password, body.role, unit_id))


@app.put("/api/admin/users/{user_id}")
def admin_update_user(user_id: str, body: UserUpdate, user: dict = CurrentUser):
    auth.require_manager(user)
    target = auth.get_user(user_id)
    if not target or not auth.can_manage_user(user, target):
        raise HTTPException(404, "Pengguna tidak ditemukan.")
    values: dict = {}
    if body.name is not None:
        values["name"] = body.name.strip()[:120] or target["name"]
    if body.role is not None:
        if user["role"] != "admin" and body.role == "admin":
            raise HTTPException(403, "Admin Unit tidak bisa menjadikan seseorang Admin.")
        values["role"] = body.role
    if body.unit_id is not None and user["role"] == "admin":
        if body.unit_id and not _unit(body.unit_id):
            raise HTTPException(400, "Unit tidak ditemukan.")
        values["unit_id"] = body.unit_id or None
    if body.active is not None:
        if user_id == user["id"] and not body.active:
            raise HTTPException(400, "Anda tidak bisa menonaktifkan akun sendiri.")
        values["active"] = body.active
    if body.password:
        auth.check_password_rules(body.password)
        values["password_hash"] = auth.hash_password(body.password)
    role, unit_id = values.get("role", target["role"]), values.get("unit_id", target["unit_id"])
    if role != "admin" and not unit_id:
        raise HTTPException(400, "Pengguna selain Admin harus masuk ke salah satu unit.")
    if user_id == user["id"] and role != user["role"]:
        raise HTTPException(400, "Anda tidak bisa mengubah peran akun sendiri.")
    if values:
        with db.engine().begin() as c:
            c.execute(update(db.users).where(db.users.c.id == user_id).values(**values))
    if body.password or body.active is False:
        auth.end_user_sessions(user_id)  # paksa login ulang
    return auth.public_user(auth.get_user(user_id))


@app.get("/api/admin/metrics")
def admin_metrics(user: dict = CurrentUser):
    """Monitoring lengkap (admin pusat): sistem, disk, worker, antrean, waktu tunggu & durasi."""
    if user["role"] != "admin":
        raise HTTPException(403, "Hanya admin.")
    return monitor.snapshot()


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request):
    """Untuk Prometheus/Grafana. Aktif bila METRICS_TOKEN diisi; kirim header Authorization: Bearer <token>."""
    if not METRICS_TOKEN:
        raise HTTPException(404)
    if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {METRICS_TOKEN}"):
        raise HTTPException(401)
    return Response(monitor.prometheus(monitor.snapshot()), media_type="text/plain; version=0.0.4")


@app.get("/api/admin/stats")
def admin_stats(user: dict = CurrentUser):
    auth.require_manager(user)
    since = time.time() - 30 * 86400
    with db.engine().connect() as c:
        q = select(db.usage.c.unit_id, db.usage.c.kind, func.count()).where(db.usage.c.at >= since)
        if user["role"] != "admin":
            q = q.where(db.usage.c.unit_id == user["unit_id"])
        rows = c.execute(q.group_by(db.usage.c.unit_id, db.usage.c.kind)).all()
    per_unit: dict = {}
    for unit_id, kind, n in rows:
        per_unit.setdefault(unit_id or "-", {})[kind] = n
    pm, pr = db.project_media.c, db.projects.c
    scope = db.project_media.join(db.projects, pr.id == pm.project_id)
    unit_filter = pr.unit_id == user["unit_id"] if user["role"] != "admin" else true()  # Admin Unit: unitnya saja
    with db.engine().connect() as c:
        by_state = dict(c.execute(select(pm.state, func.count()).select_from(scope).where(unit_filter)
                                  .group_by(pm.state)).all())
        media_bytes = c.execute(select(func.coalesce(func.sum(pm.bytes), 0)).select_from(scope).where(
            unit_filter, pm.state.in_(("tersedia", "menghapus")))).scalar() or 0
    storage_info = {"media_bytes": int(media_bytes), "projects_by_state": by_state,
                    "retention_hours": MEDIA_RETENTION_HOURS, "max_hours": MEDIA_MAX_HOURS}
    if user["role"] == "admin":  # info disk & proyek lama hanya untuk admin pusat
        storage_info["disk"] = lifecycle.disk_status()
        storage_info["legacy_projects"] = lifecycle.legacy_count()
    return {"queues": jobs.queue_stats(), "last_30_days": per_unit, "storage": storage_info}


app.mount("/fonts", StaticFiles(directory=FONTS_DIR), name="fonts")
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
