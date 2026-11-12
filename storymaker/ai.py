"""Claude sebagai editor berita: mendeskripsikan adegan (vision) dan menyusun cerita dari transkrip.

Prinsip kejujuran fakta:
- Model TIDAK menulis kalimat baru untuk diucapkan/ditampilkan. Ia hanya memilih ID kalimat asli.
- Subtitle selalu berasal dari transkrip ucapan narasumber.
- Rencana divalidasi ulang di story.validate_plan(): ID yang tidak ada dibuang.
"""
import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional

from . import story
from .config import CLAUDE_MODEL

log = logging.getLogger("storymaker.ai")


class AIError(RuntimeError):
    pass


def available(api_key: Optional[str] = None) -> bool:
    return bool(api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _client(api_key: Optional[str]):
    import anthropic

    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def _call(api_key: Optional[str], system: str, content, schema: dict, effort: str = "high",
          max_tokens: int = 16000) -> dict:
    import anthropic

    client = _client(api_key)
    try:
        resp = client.beta.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",   # bila ditolak filter keamanan, server mencoba model cadangan
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError as exc:
        raise AIError("API key Claude ditolak. Periksa ANTHROPIC_API_KEY.") from exc
    except anthropic.RateLimitError as exc:
        raise AIError("Batas pemakaian Claude tercapai. Coba lagi beberapa saat lagi.") from exc
    except anthropic.APIStatusError as exc:
        raise AIError(f"Claude API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AIError("Tidak bisa terhubung ke Claude API.") from exc
    if resp.stop_reason == "refusal":
        raise AIError("Claude menolak permintaan ini.")
    if resp.stop_reason == "max_tokens":
        raise AIError("Jawaban Claude terpotong (max_tokens).")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise AIError(f"Jawaban Claude bukan JSON valid: {text[:200]}") from exc


# ---------------------------------------------------------------- deskripsi adegan

SHOT_SCHEMA = {
    "type": "object",
    "properties": {
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "talking_head": {"type": "boolean"},
                    "usable": {"type": "boolean"},
                },
                "required": ["id", "description", "tags", "talking_head", "usable"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["shots"],
    "additionalProperties": False,
}

SHOT_SYSTEM = (
    "Anda asisten editor video berita lapangan. Untuk setiap gambar (satu frame dari satu adegan), "
    "tulis deskripsi visual singkat dan faktual dalam Bahasa Indonesia (maks 15 kata): apa yang terlihat, "
    "bukan tafsiran. Beri 3-6 tag kata benda/kegiatan (huruf kecil). talking_head=true bila gambar "
    "didominasi orang yang berbicara ke kamera/wawancara. usable=false bila gambar gelap, kabur, "
    "tertutup jari, atau tidak jelas."
)


def describe_shots(shots: list[dict], thumbs: dict[str, Path], api_key: Optional[str] = None,
                   batch: int = 24) -> dict[str, dict]:
    """Kirim thumbnail adegan (bukan video) ke Claude. Kembalikan {shot_id: {description, tags, ...}}."""
    out: dict[str, dict] = {}
    todo = [s for s in shots if s["id"] in thumbs and Path(thumbs[s["id"]]).exists()]
    for i in range(0, len(todo), batch):
        part = todo[i: i + batch]
        content: list[dict] = []
        for s in part:
            data = base64.standard_b64encode(Path(thumbs[s["id"]]).read_bytes()).decode()
            content.append({"type": "text", "text": f"Adegan {s['id']}:"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        content.append({"type": "text", "text": "Deskripsikan setiap adegan di atas sesuai ID-nya."})
        result = _call(api_key, SHOT_SYSTEM, content, SHOT_SCHEMA, effort="low", max_tokens=8000)
        for row in result.get("shots", []):
            if row.get("id") in thumbs:
                out[row["id"]] = row
    return out


# ---------------------------------------------------------------- penyusunan cerita

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hook_reason": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["hook", "problem", "solution", "impact", "closing"]},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["speech", "visual"]},
                                "ref": {"type": "string"},
                                "broll": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["kind", "ref", "broll"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["role", "items"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "hook_reason", "sections"],
    "additionalProperties": False,
}

PLAN_SYSTEM = """Anda editor senior video berita/dokumenter pendek untuk media sosial (format vertikal).
Anda menerima katalog bahan dari beberapa video liputan lapangan: daftar KALIMAT (ucapan asli narasumber,
hasil transkripsi) dan daftar ADEGAN VISUAL. Susun satu cerita utuh berdurasi sekitar {target} detik.

STRUKTUR WAJIB, berurutan:
1. hook     : tepat 1 kalimat paling memancing rasa ingin tahu di 3 detik pertama, TETAP sesuai fakta.
              Utamakan kalimat utuh yang bisa dipahami tanpa konteks (bukan diawali "dan", "terus", "jadi").
2. problem  : masalah/kondisi awal yang dihadapi.
3. solution : proses atau solusi yang dilakukan.
4. impact   : dampak/hasil nyata (angka, perubahan, kesaksian).
5. closing  : penutup yang kuat (harapan, pesan, ajakan) - 1 kalimat.

ATURAN FAKTA (tidak boleh dilanggar):
- Anda HANYA boleh memakai ID yang ada di katalog. Jangan mengarang ID, kalimat, angka, atau kutipan.
- Jangan menyusun kalimat sehingga maknanya berubah dari maksud narasumber (mis. memotong penyangkalan,
  menggabungkan dua orang seolah satu pernyataan yang bertentangan dengan konteks).
- Hook harus kutipan asli yang benar; jangan pilih kalimat yang menyesatkan bila dilepas dari konteksnya.
- Lewati kalimat yang terpotong, tidak jelas, atau berisi banyak kata pengisi.

ATURAN EDITORIAL:
- Total durasi kalimat terpilih + adegan visual mendekati {target} detik (toleransi -10%..+10%).
  Setiap potongan menambah ~0.4 detik jeda; transisi mengurangi ~0.3 detik.
- Ambil bahan dari berbagai klip, bukan satu klip saja, bila tersedia.
- "speech": ref = ID kalimat. broll = 0-2 ID adegan yang menutupi gambar saat kalimat itu diucapkan
  (suara tetap dari narasumber). Pilih adegan yang isinya mendukung kalimat; jangan pakai adegan wawancara
  orang lain sebagai B-roll; kosongkan broll untuk hook agar wajah narasumber terlihat.
- "visual": ref = ID adegan tanpa ucapan (suara alam), maksimal 1-2 di seluruh cerita, mis. sebagai
  pembuka suasana sesudah hook. broll harus kosong.
- Satu ID hanya dipakai sekali.
- title: judul singkat (maks 8 kata) yang faktual berdasarkan isi kalimat; hook_reason: 1 kalimat alasan.
{guidance}"""


def build_plan_prompt(catalog: dict, target: float, guidance: str = "") -> tuple[str, str]:
    g = f"\nCATATAN DARI REDAKSI:\n{guidance.strip()}\n" if guidance.strip() else ""
    system = PLAN_SYSTEM.format(target=int(target), guidance=g)
    user = story.catalog_text(catalog) + "\n\nSusun rencana cerita sekarang."
    return system, user


def plan_story(catalog: dict, target: float = 60.0, api_key: Optional[str] = None,
               guidance: str = "") -> dict:
    system, user = build_plan_prompt(catalog, target, guidance)
    raw = _call(api_key, system, user, PLAN_SCHEMA, effort="high")
    raw["source"] = "claude"
    return raw


def manual_prompt(catalog: dict, target: float = 60.0, guidance: str = "") -> str:
    """Prompt untuk disalin ke Claude.ai (tanpa API). Jawaban JSON-nya ditempel kembali ke aplikasi."""
    system, user = build_plan_prompt(catalog, target, guidance)
    return (system + "\n\nJawab HANYA dengan satu objek JSON dengan format:\n"
            '{"title": "...", "hook_reason": "...", "sections": [{"role": "hook", "items": '
            '[{"kind": "speech", "ref": "ID", "broll": []}]}, ...]}\n\n' + user)
