"""Analisis rekaman: memecah ucapan menjadi kalimat, menilai setiap kalimat, dan memotong adegan visual.

Hasil analisis adalah "katalog" bahan: daftar kalimat (utterance) dan adegan (shot) dengan ID unik.
Penyusun cerita (heuristik maupun AI) hanya boleh memilih ID dari katalog ini, sehingga setiap kata
di video hasil adalah ucapan asli narasumber (tidak ada kalimat karangan).
"""
import math
import re
from statistics import median
from typing import Optional

ROLES = ("hook", "problem", "solution", "impact", "closing")
ROLE_LABELS = {"hook": "Hook", "problem": "Masalah", "solution": "Proses/Solusi",
               "impact": "Dampak", "closing": "Penutup"}

_W = re.compile(r"[\w'-]+", re.UNICODE)

LEXICON = {
    "problem": {
        "masalah", "kendala", "sulit", "susah", "kesulitan", "kurang", "rusak", "banjir", "longsor", "kering",
        "kekeringan", "miskin", "mahal", "jauh", "terpaksa", "tidak", "belum", "dulu", "dulunya", "sebelumnya",
        "terancam", "hilang", "gagal", "korban", "warga", "mengeluh", "keluhan", "parah", "bahaya", "sakit",
        "krisis", "terisolasi", "putus", "macet", "minim", "terbatas", "problem", "problems", "difficult",
        "hard", "lack", "broken", "before", "never", "struggle", "cannot",
    },
    "solution": {
        "program", "bantuan", "membangun", "dibangun", "pembangunan", "memperbaiki", "diperbaiki", "pelatihan",
        "kami", "bersama", "gotong", "royong", "mulai", "memulai", "caranya", "cara", "proses", "kerja", "bekerja",
        "membuat", "dibuat", "menanam", "memasang", "dipasang", "relawan", "pemerintah", "dinas", "kolaborasi",
        "solusi", "inisiatif", "langkah", "upaya", "menyalurkan", "disalurkan", "mendampingi", "pendampingan",
        "teknologi", "alat", "fasilitas", "build", "built", "started", "together", "help", "training", "solution",
    },
    "impact": {
        "sekarang", "kini", "hasilnya", "hasil", "meningkat", "naik", "bertambah", "terbantu", "manfaat",
        "bermanfaat", "senang", "bahagia", "bangga", "lancar", "mudah", "lebih", "berhasil", "sukses", "ribu",
        "juta", "persen", "kali", "lipat", "panen", "penghasilan", "pendapatan", "anak-anak", "sejahtera",
        "berubah", "perubahan", "dampak", "alhamdulillah", "now", "result", "results", "better", "increase",
        "happy", "proud", "changed", "impact",
    },
    "closing": {
        "harapan", "berharap", "semoga", "mudah-mudahan", "terima", "kasih", "mari", "ayo", "yuk", "terus",
        "berlanjut", "masa", "depan", "generasi", "pesan", "ingin", "mohon", "dukung", "dukungan", "hope",
        "thank", "thanks", "future", "together", "keep",
    },
}

HOOK_WORDS = {
    "bayangkan", "tahukah", "siapa", "kenapa", "mengapa", "bagaimana", "apa", "pernah", "tidak", "pertama",
    "satu-satunya", "hanya", "selama", "bertahun-tahun", "puluhan", "ratusan", "ribuan", "jutaan", "rahasia",
    "ternyata", "justru", "nyawa", "mati", "hidup", "takut", "menangis", "air", "mata", "luar", "biasa", "nekat",
    "imagine", "why", "how", "never", "only", "first", "years",
}
EMOTION_WORDS = {
    "senang", "sedih", "bangga", "takut", "kaget", "terharu", "menangis", "bahagia", "syukur", "alhamdulillah",
    "berat", "perjuangan", "berjuang", "harapan", "mimpi", "cinta", "marah", "kecewa", "lega",
}
FILLERS = {"eh", "em", "emm", "ehm", "anu", "apa", "gitu", "ya", "yah", "hmm", "uh", "um", "kayak", "itu"}
WEAK_STARTS = {"dan", "terus", "lalu", "jadi", "karena", "tapi", "makanya", "nah", "trus", "kemudian", "and", "so"}
NUMBER_RE = re.compile(r"\d|ribu|juta|miliar|persen|puluh|ratus|belas", re.I)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _W.findall(text)]


# ---------------------------------------------------------------- kalimat

def _flat_words(transcript: dict) -> list[dict]:
    words = []
    for seg in (transcript or {}).get("segments", []):
        ws = seg.get("words") or []
        if not ws and seg.get("text"):  # tanpa timestamp per kata: bagi rata
            toks = seg["text"].split()
            step = (seg["end"] - seg["start"]) / max(len(toks), 1)
            ws = [{"start": seg["start"] + i * step, "end": seg["start"] + (i + 1) * step, "word": t}
                  for i, t in enumerate(toks)]
        words.extend(w for w in ws if w.get("word", "").strip())
    return words


def split_sentences(transcript: dict, max_len: float = 13.0, gap: float = 0.9) -> list[list[dict]]:
    """Kelompokkan kata menjadi kalimat: putus di . ! ? atau jeda panjang; kalimat panjang dipotong di koma."""
    sentences: list[list[dict]] = []
    cur: list[dict] = []
    for w in _flat_words(transcript):
        if cur and (w["start"] - cur[-1]["end"] > gap):
            sentences.append(cur)
            cur = []
        cur.append(w)
        dur = cur[-1]["end"] - cur[0]["start"]
        end_mark = w["word"].rstrip('"\')').endswith((".", "!", "?"))
        if end_mark or (dur > max_len and w["word"].endswith((",", ";", ":"))) or dur > max_len * 1.5:
            sentences.append(cur)
            cur = []
    if cur:
        sentences.append(cur)
    # gabungkan potongan yang terlalu pendek dengan kalimat sesudahnya bila jaraknya dekat
    merged: list[list[dict]] = []
    for s in sentences:
        if merged:
            prev = merged[-1]
            short = (prev[-1]["end"] - prev[0]["start"] < 1.0) or len(prev) < 3
            if short and s[0]["start"] - prev[-1]["end"] < 0.6 \
                    and (s[-1]["end"] - prev[0]["start"]) <= max_len * 1.5:
                merged[-1] = prev + s
                continue
        merged.append(s)
    return merged


def _text(words: list[dict]) -> str:
    return re.sub(r"\s+([,.!?;:])", r"\1", " ".join(w["word"].strip() for w in words)).strip()


def _mean_db(energy: list[float], start: float, end: float, step: float) -> Optional[float]:
    vals = energy[int(start / step): max(int(start / step) + 1, int(math.ceil(end / step)))]
    return sum(vals) / len(vals) if vals else None


def score_hook(text: str, duration: float, confidence: float, energy_rel: float) -> float:
    toks = _tokens(text)
    if not toks:
        return 0.0
    s = 0.0
    if "?" in text:
        s += 0.22
    if NUMBER_RE.search(text):
        s += 0.18
    s += min(0.3, 0.12 * len(set(toks) & HOOK_WORDS))
    s += min(0.2, 0.1 * len(set(toks) & EMOTION_WORDS))
    if toks[0] in WEAK_STARTS:  # kalimat yang bergantung pada konteks sebelumnya
        s -= 0.25
    s -= min(0.3, 0.08 * sum(1 for t in toks if t in FILLERS))
    # durasi ideal hook: 2.5 - 8 detik
    if duration < 1.8:
        s -= 0.35
    elif duration < 2.5:
        s -= 0.1
    elif duration > 10:
        s -= 0.25
    elif duration > 8:
        s -= 0.1
    if text.rstrip().endswith((".", "!", "?")):
        s += 0.08
    s += 0.15 * max(-1.0, min(1.0, energy_rel / 6.0))
    s += 0.25 * (confidence - 0.7)
    return round(max(0.0, min(1.0, 0.3 + s)), 3)


def role_scores(text: str, position: float) -> dict:
    """Kecocokan kalimat untuk tiap bagian cerita: kata kunci + posisi kronologis dalam liputan."""
    toks = set(_tokens(text))
    prior = {  # liputan lapangan biasanya kronologis: masalah di awal, dampak di akhir
        "problem": 0.25 * (1 - position),
        "solution": 0.25 * (1 - abs(position - 0.5) * 2),
        "impact": 0.25 * position,
        "closing": 0.2 * max(0.0, position - 0.6) / 0.4,
    }
    out = {}
    for role, words in LEXICON.items():
        hits = len(toks & words)
        out[role] = round(min(1.0, prior[role] + 0.22 * hits), 3)
    return out


def build_utterances(clip_id: str, transcript: dict, energy: list[float], clip_order: int, n_clips: int,
                     energy_step: float = 0.5) -> list[dict]:
    sentences = split_sentences(transcript)
    clip_med = median(energy) if energy else -30.0
    out = []
    for i, words in enumerate(sentences):
        start, end = words[0]["start"], words[-1]["end"]
        text = _text(words)
        conf = sum(w.get("p", 0.9) for w in words) / len(words)
        db = _mean_db(energy, start, end, energy_step)
        rel = (db - clip_med) if db is not None else 0.0
        # posisi kronologis gabungan: urutan klip + posisi di dalam klip
        pos = (clip_order + (i + 0.5) / max(1, len(sentences))) / max(1, n_clips)
        dur = end - start
        wps = len(words) / max(dur, 0.1)
        clarity = 1.0 - min(1.0, abs(wps - 2.6) / 3.0)  # tempo bicara wajar ~2-3 kata/detik
        out.append({
            "id": f"{clip_id}-u{i + 1}",
            "clip_id": clip_id,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(dur, 2),
            "text": text,
            "words": [{"start": w["start"], "end": w["end"], "word": w["word"]} for w in words],
            "confidence": round(conf, 3),
            "energy": round(rel, 2),
            "clarity": round(clarity, 3),
            "hook": score_hook(text, dur, conf, rel),
            "roles": role_scores(text, pos),
            "position": round(pos, 3),
        })
    return out


# ---------------------------------------------------------------- adegan visual

def build_shots(clip_id: str, duration: float, cuts: list[float], stats: list[dict],
                utterances: list[dict], window: float = 3.0, min_len: float = 1.2) -> list[dict]:
    """Potong klip menjadi adegan (berdasarkan pergantian kamera, lalu jendela ~3 detik) dan nilai kualitasnya."""
    bounds = [0.0] + [c for c in cuts if 0.3 < c < duration - 0.3] + [duration]
    pieces = []
    for a, b in zip(bounds, bounds[1:]):
        n = max(1, int(round((b - a) / window)))
        step = (b - a) / n
        pieces += [(a + k * step, a + (k + 1) * step) for k in range(n)]

    sharp_all = [s["sharp"] for s in stats] or [1.0]
    sharp_ref = max(1.0, sorted(sharp_all)[int(len(sharp_all) * 0.8)])
    shots = []
    for k, (a, b) in enumerate(pieces):
        if b - a < min_len:
            continue
        inside = [s for s in stats if a <= s["t"] < b] or stats[:1]
        if inside:
            sharp = sum(s["sharp"] for s in inside) / len(inside)
            bright = sum(s["bright"] for s in inside) / len(inside)
            motion = sum(s["motion"] for s in inside[1:]) / max(1, len(inside) - 1)
        else:
            sharp, bright, motion = sharp_ref, 120.0, 2.0
        sharp_n = min(1.0, math.log1p(sharp) / math.log1p(sharp_ref))
        bright_n = 1.0 - min(1.0, abs(bright - 125) / 110)
        # sedikit gerakan menarik; terlalu banyak = kamera goyang
        motion_n = 1.0 - min(1.0, abs(motion - 4.0) / 18.0)
        speech = sum(max(0.0, min(b, u["end"]) - max(a, u["start"])) for u in utterances) / (b - a)
        quality = 0.5 * sharp_n + 0.3 * bright_n + 0.2 * motion_n
        shots.append({
            "id": f"{clip_id}-v{len(shots) + 1}",
            "clip_id": clip_id,
            "start": round(a, 3),
            "end": round(b, 3),
            "duration": round(b - a, 2),
            "quality": round(quality, 3),
            "motion": round(motion, 2),
            "speech": round(min(1.0, speech), 2),
            "description": "",
            "tags": [],
            "talking_head": None,
        })
    return shots
