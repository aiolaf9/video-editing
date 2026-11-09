"""Resep editing per proyek (P1): satu dokumen JSON tervalidasi di database yang menjadi sumber tunggal untuk
pratinjau di browser DAN render FFmpeg.

Isi: rentang klip & klip yang dicentang, rasio, crop, warna/filter, kecepatan/zoom/fade, gaya subtitle, koreksi
teks (per segmen transkrip), overlay animasi, audio, template yang dipakai.

Konkurensi (dua perangkat): setiap simpan menaikkan `version`. Klien mengirim versi yang ia ubah; bila versi di
server sudah berbeda, simpanan DITOLAK (409) beserta isi terbaru, sehingga pengguna memilih memuat versi terbaru
atau menimpa secara sadar. Perubahan dari server sendiri (koreksi teks, rencana animasi otomatis/AI) memakai
`server_update`: baca-ubah-tulis bersyarat versi, diulang bila bertabrakan, jadi tidak pernah menimpa perubahan lain.

Kompatibilitas: proyek tanpa baris resep memakai nilai bawaan; overlay `None` = belum dipindahkan, dibaca dari
plan.json proyek lama; transkrip mentah di folder media tidak diubah (koreksi diterapkan saat dibaca).
"""
from __future__ import annotations

import copy
import json
import time
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from . import db
from .schemas import MAX_OVERLAYS, MAX_TEXT_FIXES, MusicSettings, PlanItem, RenderRequest, SubtitleSettings

SCHEMA_VERSION = 1
MAX_RECIPE_BYTES = 256 * 1024          # batas ukuran JSON resep tersimpan
MAX_TEXT_FIX_CHARS = 2000
MAX_TEXT_FIX_TOTAL = 100_000
FONT_PATTERN = r"^[A-Za-z0-9 _-]{1,60}$"   # nama font masuk ke berkas ASS: tanpa koma/kurung kurawal
MUSIC_PATTERN = r"^[^/\\\x00]{1,150}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Range(_Strict):
    start: float = Field(ge=0, le=86400)
    end: float = Field(gt=0, le=86400)

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError("Akhir rentang harus setelah awal.")
        return self


class RecipeSubtitles(SubtitleSettings):
    model_config = ConfigDict(extra="forbid")
    font: str = Field("Arial", pattern=FONT_PATTERN)


class Crop(_Strict):
    x: float = Field(0.5, ge=0, le=1)
    y: float = Field(0.5, ge=0, le=1)
    reframe: Literal["manual", "auto"] = "auto"


class Look(_Strict):
    color: Literal["none", "vivid", "cinematic", "warm", "cool", "bw", "vintage"] = "none"
    brightness: float = Field(0.0, ge=-0.3, le=0.3)
    contrast: float = Field(1.0, ge=0.5, le=1.8)
    saturation: float = Field(1.0, ge=0.0, le=2.0)


class Motion(_Strict):
    speed: float = Field(1.0, ge=0.5, le=2.0)
    zoom: Literal["none", "slow"] = "none"
    fade: bool = True


class Audio(_Strict):
    music: Optional[str] = Field(None, pattern=MUSIC_PATTERN)
    volume: float = Field(0.25, ge=0.0, le=1.5)
    ducking: bool = True
    normalize: bool = True


class Recipe(_Strict):
    v: Literal[1] = SCHEMA_VERSION
    selection: Optional[Range] = None
    target: int = Field(30, ge=5, le=180)            # durasi target kandidat klip
    checked: list[Range] = Field(default_factory=list, max_length=30)   # klip dicentang untuk render massal
    aspect: Literal["9:16", "1:1", "4:5", "16:9", "original"] = "9:16"
    crop: Crop = Crop()
    look: Look = Look()
    motion: Motion = Motion()
    subtitles: RecipeSubtitles = RecipeSubtitles()
    audio: Audio = Audio()
    animations: bool = True
    overlays: Optional[list[PlanItem]] = Field(None, max_length=MAX_OVERLAYS)
    text_fixes: dict[str, str] = Field(default_factory=dict, max_length=MAX_TEXT_FIXES)
    template_id: Optional[str] = Field(None, max_length=40, pattern=r"^[A-Za-z0-9_:-]+$")
    encoder: Literal["cpu", "nvidia", "intel", "amd", "mac"] = "cpu"
    language: Optional[str] = Field(None, max_length=10, pattern=r"^[a-z]{2,3}$")

    @field_validator("text_fixes")
    @classmethod
    def _fixes(cls, value: dict[str, str]) -> dict[str, str]:
        total = 0
        out = {}
        for key, text in value.items():
            if not key.isdigit() or int(key) > 100_000:
                raise ValueError("Kunci koreksi teks harus nomor segmen.")
            text = " ".join(str(text).split())
            if len(text) > MAX_TEXT_FIX_CHARS:
                raise ValueError("Teks koreksi terlalu panjang.")
            total += len(text)
            out[str(int(key))] = text
        if total > MAX_TEXT_FIX_TOTAL:
            raise ValueError("Total koreksi teks terlalu besar.")
        return out


class Conflict(Exception):
    """Versi resep di server sudah berubah sejak klien memuatnya."""

    def __init__(self, current: dict):
        super().__init__("Resep diubah di perangkat lain.")
        self.current = current


class TooLarge(ValueError):
    pass


def default() -> Recipe:
    return Recipe()


def _dump(recipe: Recipe) -> str:
    text = json.dumps(recipe.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_RECIPE_BYTES:
        raise TooLarge("Resep editing terlalu besar.")
    return text


def _parse(text: str) -> Recipe:
    try:
        return Recipe.model_validate(json.loads(text))
    except (ValidationError, ValueError):
        return default()     # baris rusak/versi lama tak dikenal: jangan kunci proyek, mulai dari bawaan


def _row(conn, pid: str) -> Optional[dict]:
    return db.row(conn.execute(select(db.project_recipes).where(db.project_recipes.c.project_id == pid)).first())


def load(pid: str) -> dict:
    """{"version", "recipe" (Recipe), "updated", "updated_by"}; version 0 = belum pernah disimpan."""
    with db.engine().connect() as c:
        row = _row(c, pid)
    if row is None:
        return {"version": 0, "recipe": default(), "updated": None, "updated_by": None}
    return {"version": row["version"], "recipe": _parse(row["data"]), "updated": row["updated"],
            "updated_by": row["updated_by"]}


def save(pid: str, recipe: Recipe, base_version: int, user_id: Optional[str]) -> dict:
    """Simpan bila versi di server masih `base_version`; selain itu Conflict(isi terbaru)."""
    data, now = _dump(recipe), time.time()
    t = db.project_recipes.c
    if base_version == 0:
        try:
            with db.engine().begin() as c:
                c.execute(insert(db.project_recipes).values(project_id=pid, version=1, data=data, updated=now,
                                                            updated_by=user_id))
            return {"version": 1, "recipe": recipe, "updated": now, "updated_by": user_id}
        except IntegrityError:
            raise Conflict(load(pid))   # perangkat lain lebih dulu membuat baris resep
    with db.engine().begin() as c:
        done = c.execute(update(db.project_recipes).where(t.project_id == pid, t.version == base_version).values(
            version=base_version + 1, data=data, updated=now, updated_by=user_id)).rowcount
    if done:
        return {"version": base_version + 1, "recipe": recipe, "updated": now, "updated_by": user_id}
    raise Conflict(load(pid))


def server_update(pid: str, change: Callable[[Recipe], Recipe], user_id: Optional[str], attempts: int = 8) -> dict:
    """Ubah resep dari sisi server tanpa menimpa perubahan lain (dicoba ulang bila versi bertabrakan)."""
    for _ in range(attempts):
        cur = load(pid)
        new = change(cur["recipe"].model_copy(deep=True))
        try:
            return save(pid, Recipe.model_validate(new.model_dump()), cur["version"], user_id)
        except Conflict:
            continue
    raise Conflict(load(pid))


# ------------------------------------------------------------------ isi efektif (dipakai pratinjau & render)

def effective_overlays(pid: str, recipe: Optional[Recipe] = None) -> list[dict]:
    from . import storage
    recipe = recipe or load(pid)["recipe"]
    if recipe.overlays is not None:
        return [i.model_dump() for i in recipe.overlays]
    return storage.read_data(pid, "plan", []) or []      # proyek lama: rencana masih di plan.json


def apply_text_fixes(transcript: Optional[dict], fixes: dict[str, str]) -> Optional[dict]:
    """Transkrip dengan koreksi pengguna. Transkrip mentah tidak diubah; koreksi untuk segmen yang tidak ada
    (mis. setelah analisis ulang) diabaikan."""
    from . import transcript_edit
    if not transcript or not fixes:
        return transcript
    out = copy.deepcopy(transcript)
    n = len(out.get("segments") or [])
    edits = [{"index": int(k), "text": v} for k, v in sorted(fixes.items(), key=lambda kv: int(kv[0])) if int(k) < n]
    try:
        return transcript_edit.apply_edits(out, edits)
    except transcript_edit.EditError:
        return out


def effective_transcript(pid: str, recipe: Optional[Recipe] = None, fixes: Optional[dict] = None) -> Optional[dict]:
    from . import storage
    raw = storage.read_data(pid, "transcript")
    if fixes is None:
        fixes = (recipe or load(pid)["recipe"]).text_fixes
    return apply_text_fixes(raw, fixes)


def to_render_request(pid: str, recipe: Recipe, start: float, end: float, title: str = "") -> RenderRequest:
    """Permintaan render dari resep tersimpan, dengan snapshot overlay & koreksi teks saat diajukan: render yang
    menunggu di antrean tidak berubah bila resep diedit lagi."""
    return RenderRequest(
        start=start, end=end, title=title, aspect=recipe.aspect, reframe=recipe.crop.reframe,
        animations=recipe.animations, crop_x=recipe.crop.x, crop_y=recipe.crop.y,
        color=recipe.look.color, brightness=recipe.look.brightness, contrast=recipe.look.contrast,
        saturation=recipe.look.saturation, speed=recipe.motion.speed, zoom=recipe.motion.zoom,
        fade=recipe.motion.fade, normalize_audio=recipe.audio.normalize, encoder=recipe.encoder,
        subtitles=SubtitleSettings(**recipe.subtitles.model_dump()),
        music=MusicSettings(file=recipe.audio.music, volume=recipe.audio.volume, ducking=recipe.audio.ducking),
        overlays=[PlanItem(**i) for i in effective_overlays(pid, recipe)],
        text_fixes=dict(recipe.text_fixes),
    )


def public(state: dict, users: Optional[dict] = None) -> dict:
    """Bentuk JSON untuk API."""
    uid = state.get("updated_by")
    return {"version": state["version"], "recipe": state["recipe"].model_dump(mode="json"),
            "updated": state.get("updated"), "updated_by": uid,
            "updated_by_name": (users or {}).get(uid) if uid else None}


class SaveBody(_Strict):
    version: int = Field(ge=0)          # versi yang sedang diubah klien (0 = belum pernah disimpan)
    recipe: Recipe


class RenderBody(_Strict):
    version: int = Field(ge=1)          # versi resep yang dilihat pengguna saat menekan Render
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str = Field("", max_length=120)
