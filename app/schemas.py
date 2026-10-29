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


class RenderRequest(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str = Field("", max_length=120)
    aspect: Literal["9:16", "1:1", "4:5", "16:9", "original"] = "9:16"
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


class SuggestRequest(BaseModel):
    target: float = Field(30, ge=5, le=180)
    top_n: int = Field(8, ge=1, le=30)
