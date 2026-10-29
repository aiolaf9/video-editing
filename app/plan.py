"""Rencana animasi ("edit plan"): daftar elemen grafis yang mengikuti isi omongan.

Jenis elemen:
- zoom      : punch-in singkat untuk penekanan
- keyword   : kata/frasa besar beranimasi di tengah layar
- card      : kartu kaca dengan label kecil + judul (mis. "Langkah 1 / Siapkan footage")
- chips     : deretan label kecil yang muncul bergantian
- checklist : daftar poin yang dicentang satu per satu

Tiga cara membuatnya:
1. heuristic()   : gratis, lokal, berbasis kata kunci & energi suara
2. build_prompt(): prompt ringkas untuk ditempel ke Claude.ai/Gemini/ChatGPT, JSON-nya ditempel balik (gratis)
3. lewat API AI pilihan (lihat ai.py): token sangat sedikit karena hanya teks yang dikirim
"""
import json
import re
from typing import Optional

from .clipper import HOOK_WORDS
from .media import ENERGY_STEP
from .storage import new_id

TYPES = ("zoom", "keyword", "card", "chips", "checklist")
_MIN_LEN = {"zoom": 0.8, "keyword": 0.9, "card": 1.5, "chips": 1.5, "checklist": 2.0}

STOPWORDS = {
    "yang", "dan", "di", "ke", "dari", "ini", "itu", "aku", "kamu", "kita", "kami", "dia", "mereka", "ada",
    "untuk", "dengan", "juga", "jadi", "kalau", "karena", "tapi", "atau", "bisa", "sudah", "akan", "lagi",
    "saja", "aja", "nya", "gitu", "kayak", "banget", "sama", "dalam", "pada", "oleh", "seperti", "tidak",
    "nggak", "gak", "enggak", "udah", "lebih", "harus", "the", "and", "that", "this", "with", "you", "your",
}
_ORDINALS = {"pertama": 1, "kedua": 2, "ketiga": 3, "keempat": 4, "kelima": 5,
             "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
_WORD_RE = re.compile(r"[\w'-]+", re.UNICODE)


def _clean(word: str) -> str:
    return word.strip(" .,!?;:\"'()[]").strip()


def _words_in(transcript: dict, start: float, end: float) -> list[dict]:
    words = []
    for seg in (transcript or {}).get("segments", []):
        if seg["end"] <= start or seg["start"] >= end:
            continue
        for w in seg.get("words") or []:
            if w["start"] >= start and w["end"] <= end:
                words.append(w)
    return words


def normalize(items: list[dict], start: float, end: float) -> list[dict]:
    """Validasi & rapikan elemen: tipe dikenal, waktu di dalam rentang, teks dipendekkan."""
    out = []
    for raw in items or []:
        typ = str(raw.get("type", "")).lower()
        if typ not in TYPES:
            continue
        try:
            s = float(raw.get("start", 0))
            e = float(raw.get("end", 0))
        except (TypeError, ValueError):
            continue
        s, e = max(start, s), min(end, e)
        if e - s < _MIN_LEN[typ] * 0.5:
            continue
        item = {
            "id": str(raw.get("id") or new_id()),
            "type": typ,
            "start": round(s, 2),
            "end": round(e, 2),
            "text": str(raw.get("text") or "").strip()[:80],
            "label": str(raw.get("label") or "").strip()[:40],
            "items": [str(x).strip()[:28] for x in (raw.get("items") or []) if str(x).strip()][:6],
        }
        if typ in ("keyword", "card") and not item["text"]:
            continue
        if typ in ("chips", "checklist") and not item["items"]:
            continue
        out.append(item)
    return sorted(out, key=lambda i: i["start"])


def merge(existing: list[dict], new_items: list[dict], start: float, end: float) -> list[dict]:
    """Ganti elemen di rentang [start, end) dengan yang baru; elemen di luar rentang dipertahankan."""
    kept = [i for i in existing or [] if i["end"] <= start or i["start"] >= end]
    return sorted(kept + new_items, key=lambda i: i["start"])


# ------------------------------------------------------------------ 1. heuristik lokal

def heuristic(transcript: dict, energy: list[float], start: float, end: float) -> list[dict]:
    words = _words_in(transcript, start, end)
    if not words:
        return []

    def loudness(w: dict) -> float:
        a, b = int(w["start"] / ENERGY_STEP), int(w["end"] / ENERGY_STEP) + 1
        chunk = energy[a:b] if energy else []
        return max(chunk) if chunk else -60.0

    items: list[dict] = []
    # Zoom pada kata penekanan: kata pemancing, akhir pertanyaan/seruan, atau suara paling keras.
    loud_cut = sorted((loudness(w) for w in words), reverse=True)[max(0, len(words) // 8 - 1)]
    last_zoom = -99.0
    for w in words:
        token = _clean(w["word"]).lower()
        emphatic = token in HOOK_WORDS or w["word"].endswith(("!", "?")) or loudness(w) >= loud_cut
        if emphatic and w["start"] - last_zoom >= 4.0:
            items.append({"type": "zoom", "start": w["start"] - 0.05, "end": w["start"] + 1.6})
            last_zoom = w["start"]

    # Kartu "Poin N" saat pembicara menghitung (pertama, kedua, ...).
    for i, w in enumerate(words):
        n = _ORDINALS.get(_clean(w["word"]).lower())
        if n:
            title = " ".join(_clean(x["word"]) for x in words[i + 1:i + 6]).strip()
            if title:
                items.append({"type": "card", "label": f"Poin {n}", "text": title[:1].upper() + title[1:],
                              "start": w["start"], "end": min(end, w["start"] + 3.5)})

    # Kata kunci besar: kata terpanjang yang bukan kata umum, maksimal satu tiap ~7 detik.
    last_kw = -99.0
    for w in words:
        token = _clean(w["word"])
        if (len(token) >= 7 or token.isdigit()) and token.lower() not in STOPWORDS \
                and w["start"] - last_kw >= 7.0:
            if any(it["type"] == "card" and it["start"] <= w["start"] <= it["end"] for it in items):
                continue
            items.append({"type": "keyword", "text": token.upper(), "start": w["start"], "end": w["start"] + 1.4})
            last_kw = w["start"]
    return normalize(items, start, end)


# ------------------------------------------------------------------ 2 & 3. lewat Claude

SYSTEM_PROMPT = """Kamu adalah editor video short-form (TikTok/Reels) profesional.
Tugasmu: dari transkrip bertimestamp, buat rencana animasi yang MEMPERJELAS apa yang sedang diucapkan.

Jenis elemen yang tersedia:
- "zoom": punch-in kamera singkat (0.8-2 detik) untuk menekankan kata penting. Isi text/label/items kosong.
- "keyword": 1-3 kata besar di layar saat kata itu diucapkan (1-2 detik). Isi "text".
- "card": kartu dengan "label" kecil (mis. "Langkah 1", "Fakta", "Tips") dan "text" judul singkat maks 6 kata (2-4 detik).
- "chips": 2-5 label pendek (maks 2 kata per label) di "items" saat pembicara menyebut beberapa hal (2-4 detik).
- "checklist": 2-5 poin pendek di "items" untuk langkah/daftar (3-6 detik).

Aturan:
- Waktu "start"/"end" dalam detik, sama dengan timestamp transkrip, dan harus di dalam rentang klip.
- Elemen muncul tepat saat hal itu diucapkan, bukan sebelumnya.
- Jangan menumpuk: maksimal satu elemen teks (keyword/card/chips/checklist) pada satu waktu. Zoom boleh bersamaan.
- Kira-kira satu elemen teks tiap 4-8 detik dan satu zoom tiap 3-6 detik. Lebih sedikit lebih baik daripada ramai.
- Pakai bahasa yang sama dengan transkrip. Teks singkat, padat, tanpa emoji.
"""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(TYPES)},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "text": {"type": "string"},
                    "label": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["type", "start", "end", "text", "label", "items"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _transcript_lines(transcript: dict, start: float, end: float) -> str:
    lines = []
    for seg in (transcript or {}).get("segments", []):
        if seg["end"] <= start or seg["start"] >= end:
            continue
        words = [w for w in seg.get("words") or [] if start <= w["start"] < end]
        if words:
            # Timestamp setiap ~4 kata: cukup presisi tanpa memboroskan token.
            parts = []
            for i, w in enumerate(words):
                parts.append(f"[{w['start']:.1f}] {w['word']}" if i % 4 == 0 else w["word"])
            lines.append(" ".join(parts))
        else:
            lines.append(f"[{seg['start']:.1f}] {seg['text']}")
    return "\n".join(lines)


def user_message(transcript: dict, start: float, end: float, guidebook: str = "") -> str:
    msg = f"Rentang klip: {start:.1f} sampai {end:.1f} detik.\n"
    if guidebook.strip():
        msg += f"\nGuidebook gaya dari pemilik channel (wajib diikuti):\n{guidebook.strip()}\n"
    msg += f"\nTranskrip:\n{_transcript_lines(transcript, start, end)}\n"
    return msg


JSON_INSTRUCTION = (
    '\nBalas HANYA dengan JSON, tanpa penjelasan, dengan format:\n'
    '{"items": [{"type": "card", "start": 0.0, "end": 0.0, "text": "", "label": "", "items": []}]}\n'
)


def build_prompt(transcript: dict, start: float, end: float, guidebook: str = "") -> str:
    """Prompt lengkap untuk disalin ke chatbot mana pun (Claude.ai, Gemini, ChatGPT) tanpa API."""
    return SYSTEM_PROMPT + "\n" + user_message(transcript, start, end, guidebook) + JSON_INSTRUCTION


def parse_reply(text: str) -> list[dict]:
    """Ambil daftar elemen dari balasan AI (boleh ada ```json ... ``` atau teks di sekitarnya)."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith(("{", "[")):
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("Tidak menemukan JSON pada teks yang ditempel.")
        text = text[a:b + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON tidak valid: {exc}") from exc
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError('JSON harus berisi daftar "items".')
    return items


def ask_claude(transcript: dict, start: float, end: float, guidebook: str,
               api_key: Optional[str], model: str) -> list[dict]:
    """Minta rencana animasi ke Claude API dengan structured output (JSON dijamin valid)."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message(transcript, start, end, guidebook)}],
        # Tugas ringan: effort rendah = lebih cepat & lebih hemat token.
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
        # Bila permintaan ditolak filter keamanan, API otomatis mengulang di model cadangan.
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude menolak permintaan ini. Coba klip lain atau mode Otomatis.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Jawaban Claude terpotong. Coba klip yang lebih pendek.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text).get("items", [])
