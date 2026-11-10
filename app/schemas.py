from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class SubtitleSettings(BaseModel):
    enabled: bool = True
    style: Literal["karaoke", "pop", "word", "simple"] = "karaoke"
    font: str = Field("Arial", max_length=60)
    size: float = Field(1.0, ge=0.4, le=2.5)
    color: str = Field("#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    highlight: str = Field("#FFE600", pattern=r"^#[0-9A-Fa-f]{6}$")
    position: Literal["bottom", "middle", "top"] = "bottom"
    uppercase: bool = True
    words_per_line: int = Field(3, ge=1, le=8)


class MusicSettings(BaseModel):
    file: Optional[str] = None      # nama file di folder music proyek
    volume: float = Field(0.25, ge=0.0, le=1.5)
    ducking: bool = True            # musik mengecil otomatis saat ada suara


class PlanItem(BaseModel):
    id: Optional[str] = Field(None, max_length=40)
    type: Literal["zoom", "keyword", "card", "chips", "checklist"]
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field("", max_length=200)
    label: str = Field("", max_length=80)
    items: list[str] = Field(default_factory=list, max_length=10)


MAX_OVERLAYS = 300
MAX_TEXT_FIXES = 500
MAX_SEG_EDITS = 5000


class RenderRequest(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str = Field("", max_length=120)
    aspect: Literal["9:16", "1:1", "4:5", "16:9", "original"] = "9:16"
    reframe: Literal["manual", "auto"] = "auto"  # auto = bingkai mengikuti wajah
    animations: bool = True                       # pakai rencana animasi di rentang klip
    crop_x: float = Field(0.5, ge=0, le=1)   # posisi horizontal bingkai (0 = kiri, 1 = kanan)
    crop_y: float = Field(0.5, ge=0, le=1)
    color: Literal["none", "vivid", "cinematic", "warm", "cool", "bw", "vintage"] = "none"
    brightness: float = Field(0.0, ge=-0.3, le=0.3)
    contrast: float = Field(1.0, ge=0.5, le=1.8)
    saturation: float = Field(1.0, ge=0.0, le=2.0)
    speed: float = Field(1.0, ge=0.5, le=2.0)
    zoom: Literal["none", "slow"] = "none"
    fade: bool = True
    normalize_audio: bool = True
    encoder: Literal["cpu", "nvidia", "intel", "amd", "mac"] = "cpu"
    subtitles: SubtitleSettings = SubtitleSettings()
    music: MusicSettings = MusicSettings()
    # Snapshot resep (P1, render dari resep): None = pakai isi resep proyek saat render dijalankan.
    overlays: Optional[list[PlanItem]] = Field(None, max_length=MAX_OVERLAYS)
    text_fixes: Optional[dict[str, str]] = Field(None, max_length=MAX_TEXT_FIXES)
    seg_edits: Optional[dict[str, dict]] = Field(None, max_length=MAX_SEG_EDITS)   # M1.1: koreksi + timing

    @model_validator(mode="after")
    def _check_range(self):
        if self.end - self.start < 1:
            raise ValueError("Durasi klip minimal 1 detik.")
        if self.end - self.start > 600:
            raise ValueError("Durasi klip maksimal 10 menit.")
        return self


class UrlImport(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    language: Optional[str] = None
    quality: Optional[str] = None


class WordTime(BaseModel):
    i: int = Field(ge=0, le=400)
    start: float = Field(ge=0, le=86400)
    end: float = Field(ge=0, le=86400)


class SegmentEdit(BaseModel):
    """Satu koreksi segmen: teks (timing kata dijaga), rentang segmen, dan/atau waktu per kata (M1.1)."""
    index: int = Field(ge=0)
    text: Optional[str] = Field(None, max_length=2000)
    start: Optional[float] = Field(None, ge=0, le=86400)
    end: Optional[float] = Field(None, ge=0, le=86400)
    word_times: Optional[list[WordTime]] = Field(None, max_length=400)


class TranscriptEdit(BaseModel):
    segments: list[SegmentEdit] = Field(default_factory=list, max_length=500)
    reviewed: list[int] = Field(default_factory=list, max_length=5000)        # tandai sudah ditinjau
    unreviewed: list[int] = Field(default_factory=list, max_length=5000)      # batalkan tanda tinjau


class ReplaceRequest(BaseModel):
    find: str = Field(min_length=1, max_length=200)
    replace: str = Field("", max_length=200)
    case_sensitive: bool = False
    whole_word: bool = True
    dry_run: bool = True


class SuggestRequest(BaseModel):
    target: float = Field(30, ge=5, le=180)
    top_n: int = Field(8, ge=1, le=30)


class RangeRequest(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    guidebook: str = Field("", max_length=4000)


class PlanImport(RangeRequest):
    text: str = Field(min_length=2, max_length=100_000)


class Settings(BaseModel):
    """Pengaturan AI untuk penyedia yang dipilih. api_key kosong = kunci lama dipertahankan."""
    provider: str = Field("none", max_length=30)   # bawaan: tanpa AI eksternal
    guidebook: str = Field("", max_length=4000)
    api_key: Optional[str] = Field(None, max_length=400)
    clear_key: bool = False
    model: Optional[str] = Field(None, max_length=120)
    base_url: Optional[str] = Field(None, max_length=300, pattern=r"^(https?://.*)?$")


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class PasswordChange(BaseModel):
    old_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class UnitIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    daily_render_quota: int = Field(0, ge=0, le=100_000)
    daily_ai_quota: int = Field(0, ge=0, le=100_000)
    guidebook: str = Field("", max_length=4000)
    # Kuota media sementara (MB). Tidak dikirim = tidak diubah; null = bawaan server; 0 = tanpa batas.
    storage_quota_mb: Optional[int] = Field(None, ge=0, le=100_000_000)


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    name: str = Field("", max_length=120)
    password: str = Field(min_length=1, max_length=200)
    role: Literal["admin", "unit_admin", "editor"] = "editor"
    unit_id: Optional[str] = None


class UserUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=120)
    role: Optional[Literal["admin", "unit_admin", "editor"]] = None
    unit_id: Optional[str] = None
    active: Optional[bool] = None
    password: Optional[str] = Field(None, max_length=200)


class GuidebookIn(BaseModel):
    guidebook: str = Field("", max_length=4000)


class EvalFolder(BaseModel):
    """Menu "Uji akurasi" desktop (M1.1): folder berisi video NH + transkrip rujukan .txt."""
    folder: str = Field(..., min_length=1, max_length=1000)


class EvalStart(EvalFolder):
    runs: int = Field(2, ge=1, le=3)
    qualities: list[str] = Field(default_factory=list, max_length=3)
