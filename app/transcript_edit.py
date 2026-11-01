"""Koreksi teks transkrip oleh pengguna (dipakai juga sebagai sumber subtitle saat render).

Waktu tidak diubah per segmen: bila jumlah kata sama, tiap kata baru memakai waktu kata lama di posisinya; bila
berbeda, kata dibagi rata di dalam rentang [start, end] segmen (tidak pernah keluar). Teks kosong = tanpa subtitle.
"""
from __future__ import annotations

MAX_TEXT = 2000


class EditError(ValueError):
    pass


def apply_edits(transcript: dict, edits: list[dict]) -> dict:
    segs = transcript.get("segments") or []
    for e in edits:
        i, text = e["index"], " ".join(str(e["text"]).split())
        if not 0 <= i < len(segs):
            raise EditError(f"Segmen {i} tidak ada.")
        if len(text) > MAX_TEXT:
            raise EditError("Teks segmen terlalu panjang.")
        seg = segs[i]
        old = [w for w in (seg.get("words") or []) if str(w.get("word", "")).strip()]
        tokens = text.split()
        start, end = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
        if end <= start and old:   # segmen tanpa rentang yang sah: pakai rentang kata lama
            start, end = float(old[0]["start"]), float(old[-1]["end"])
        if len(tokens) == len(old):
            words = [{**w, "word": t} for w, t in zip(old, tokens)]
        else:
            step = (end - start) / max(1, len(tokens))
            words = [{"start": round(start + k * step, 3), "end": round(start + (k + 1) * step, 3), "word": t}
                     for k, t in enumerate(tokens)]
        seg["text"], seg["words"], seg["edited"] = text, words, True
    return transcript
