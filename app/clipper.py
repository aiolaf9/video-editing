"""Klipper otomatis: mencari kandidat potongan terbaik dari transkrip + energi audio.

Semua berjalan lokal tanpa AI berbayar. Skor menggabungkan:
- energi suara (momen ramai/emosional),
- kepadatan bicara (tidak banyak jeda kosong),
- kalimat pembuka yang "menggantung" (pertanyaan / kata pemancing),
- klip berakhir di ujung kalimat,
- kecocokan dengan durasi target.
"""
import re
from statistics import mean, pstdev
from typing import Optional

from .media import ENERGY_STEP

HOOK_WORDS = {
    # Indonesia
    "rahasia", "ternyata", "kenapa", "mengapa", "bagaimana", "gimana", "jangan", "penting",
    "pertama", "sebenarnya", "sebenernya", "faktanya", "masalahnya", "kesalahan", "salah",
    "bayangkan", "coba", "tahu", "tau", "gila", "parah", "wow", "serius", "intinya", "cara",
    "tips", "trik", "kunci", "terbesar", "terbaik", "terburuk", "pernah",
    # Inggris
    "secret", "why", "how", "never", "always", "mistake", "truth", "actually", "imagine",
    "biggest", "best", "worst", "stop", "listen", "crazy",
}
_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def _window_energy(energy: list[float], start: float, end: float) -> float:
    if not energy:
        return 0.0
    a = int(start / ENERGY_STEP)
    b = max(a + 1, int(end / ENERGY_STEP))
    chunk = energy[a:b]
    return mean(chunk) if chunk else 0.0


def _hook_score(text: str) -> float:
    words = [w.lower() for w in _WORD_RE.findall(text)][:12]
    score = 0.0
    if "?" in text:
        score += 0.6
    if any(w in HOOK_WORDS for w in words):
        score += 0.6
    return min(score, 1.0)


def _score_window(segs: list[dict], target: float, energy: list[float], e_mean: float, e_std: float,
                  next_start: Optional[float] = None) -> dict:
    start, end = segs[0]["start"], segs[-1]["end"]
    length = end - start
    text = " ".join(s["text"] for s in segs).strip()
    words = sum(len(s.get("words") or _WORD_RE.findall(s["text"])) for s in segs)
    gaps = sum(max(0.0, segs[i + 1]["start"] - segs[i]["end"]) for i in range(len(segs) - 1))

    z = (_window_energy(energy, start, end) - e_mean) / e_std if e_std > 0 else 0.0
    energy_s = max(0.0, min(1.0, 0.5 + z / 2))
    density_s = min((words / length) / 3.0, 1.0) if length > 0 else 0.0
    hook_s = _hook_score(segs[0]["text"])
    # Akhir kalimat: tanda baca, atau jeda bicara yang jelas setelahnya (transkrip tanpa tanda baca).
    pause_after = (next_start - end) if next_start is not None else 1.0
    ending_s = 1.0 if text.rstrip().endswith((".", "!", "?")) or pause_after >= 0.6 else 0.0
    fit_s = max(0.0, 1 - abs(length - target) / target)
    gap_pen = min(gaps / length, 0.5) if length > 0 else 0.0

    score = 0.30 * energy_s + 0.25 * density_s + 0.20 * hook_s + 0.10 * ending_s + 0.15 * fit_s - gap_pen

    reasons = []
    if energy_s >= 0.7:
        reasons.append("suara lebih bersemangat dari rata-rata")
    if density_s >= 0.75:
        reasons.append("padat bicara")
    if hook_s >= 0.6:
        reasons.append("pembuka memancing rasa penasaran")
    if ending_s:
        reasons.append("selesai di akhir kalimat")
    if gap_pen > 0.15:
        reasons.append("ada jeda panjang")

    return {
        "start": start,
        "end": end,
        "score": round(max(score, 0.0) * 100, 1),
        "text": text if len(text) <= 400 else text[:397] + "...",
        "reasons": reasons,
    }


def _pick_non_overlapping(cands: list[dict], top_n: int) -> list[dict]:
    chosen: list[dict] = []
    for cand in sorted(cands, key=lambda c: c["score"], reverse=True):
        if all(cand["end"] <= c["start"] or cand["start"] >= c["end"] for c in chosen):
            chosen.append(cand)
            if len(chosen) >= top_n:
                break
    return chosen


def _energy_only(energy: list[float], duration: float, target: float, top_n: int) -> list[dict]:
    """Fallback untuk video tanpa ucapan: pilih bagian paling ramai."""
    if duration <= 0:
        return []
    length = min(target, duration)
    e_mean = mean(energy) if energy else 0.0
    e_std = pstdev(energy) if len(energy) > 1 else 0.0
    cands, t = [], 0.0
    step = max(length / 2, 1.0)
    while t + length <= duration + 1e-6:
        z = (_window_energy(energy, t, t + length) - e_mean) / e_std if e_std > 0 else 0.0
        cands.append({
            "start": round(t, 2), "end": round(t + length, 2),
            "score": round(max(0.0, min(1.0, 0.5 + z / 2)) * 100, 1),
            "text": "", "reasons": ["dipilih dari energi audio"],
        })
        t += step
    return _pick_non_overlapping(cands, top_n)


def suggest(transcript: dict, energy: list[float], duration: float,
            target: float = 30, top_n: int = 8) -> list[dict]:
    target = max(5.0, float(target))
    segs = [s for s in (transcript or {}).get("segments", []) if s["end"] > s["start"]]
    if not segs:
        return _energy_only(energy, duration, target, top_n)

    min_len, max_len = target * 0.6, target * 1.5
    e_mean = mean(energy) if energy else 0.0
    e_std = pstdev(energy) if len(energy) > 1 else 0.0

    cands = []
    for i in range(len(segs)):
        for j in range(i, len(segs)):
            length = segs[j]["end"] - segs[i]["start"]
            if length > max_len:
                break
            if length >= min_len:
                nxt = segs[j + 1]["start"] if j + 1 < len(segs) else None
                cands.append(_score_window(segs[i:j + 1], target, energy, e_mean, e_std, nxt))

    if not cands:
        # Transkrip terlalu pendek untuk target: pakai seluruh ucapan sebagai satu klip.
        cands = [_score_window(segs, target, energy, e_mean, e_std)]

    picked = _pick_non_overlapping(cands, top_n)
    for c in picked:
        # Sedikit ruang napas di awal dan akhir agar potongan tidak terasa mendadak.
        c["start"] = round(max(0.0, c["start"] - 0.15), 2)
        c["end"] = round(min(duration or c["end"] + 0.3, c["end"] + 0.3), 2)
    return picked
