"""Koreksi transkrip oleh pengguna (sumber subtitle saat pratinjau & render).

M1.1: timing kata DIJAGA saat teks dikoreksi. Teks baru diselaraskan dengan kata lama (difflib, tanpa beda huruf
besar/kecil & tanda baca): kata yang sama tetap memakai waktu & probabilitas aslinya, kata yang diganti mengisi
rentang waktu kata yang digantikannya, kata sisipan mengisi celah di antara tetangganya. Sebelumnya (M1) seluruh kata
segmen dibagi rata begitu jumlah kata berubah, sehingga sorotan karaoke bergeser dari ucapan.

Hasil edit per segmen disimpan di resep (`seg_edits`, ringkas: [kata, mulai, akhir, p]); transkrip mentah dari
Whisper tidak pernah diubah. Kata yang diketik pengguna diberi p = 1 (sudah dikonfirmasi).
"""
from __future__ import annotations

import copy
import difflib
import re
import unicodedata
from typing import Optional

MAX_TEXT = 2000
MAX_WORDS = 400
MIN_WORD = 0.05            # durasi minimum kata hasil pembagian (detik)
UNSURE_P = 0.5             # kata dengan probabilitas Whisper di bawah ini ditandai "perlu ditinjau"


class EditError(ValueError):
    pass


def norm_token(token: str) -> str:
    t = unicodedata.normalize("NFKC", token).lower()
    return re.sub(r"[^\w]", "", t)


def _seg_span(seg: dict, old: list[dict]) -> tuple[float, float]:
    """Rentang segmen. Kata baru tidak pernah ditaruh di luar rentang ini (tidak menabrak segmen tetangga); rentang
    kata Whisper hanya dipakai bila rentang segmen tidak sah."""
    start, end = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
    if end <= start and old:
        start, end = float(old[0]["start"]), float(old[-1]["end"])
    if end <= start:
        end = start + MIN_WORD
    return start, end


def _clamp(a: float, b: float, lo: float, hi: float) -> tuple[float, float]:
    a, b = max(lo, min(a, hi)), max(lo, min(b, hi))
    return a, max(b, a)


def _spread(tokens: list[str], start: float, end: float) -> list[dict]:
    if not tokens:
        return []
    end = max(end, start + MIN_WORD * len(tokens))
    step = (end - start) / len(tokens)
    return [{"start": round(start + k * step, 3), "end": round(start + (k + 1) * step, 3), "word": t, "p": 1.0}
            for k, t in enumerate(tokens)]


def realign(seg: dict, text: str) -> list[dict]:
    """Kata baru untuk `seg` setelah teksnya diganti `text`, dengan waktu kata lama dipertahankan sebisanya."""
    old = [w for w in (seg.get("words") or []) if str(w.get("word", "")).strip()]
    tokens = text.split()
    if len(tokens) > MAX_WORDS:
        raise EditError("Segmen terlalu banyak kata.")
    seg_start, seg_end = _seg_span(seg, old)
    if not old:
        return _spread(tokens, seg_start, seg_end)
    sm = difflib.SequenceMatcher(a=[norm_token(w["word"]) for w in old], b=[norm_token(t) for t in tokens],
                                 autojunk=False)
    out: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        new = tokens[j1:j2]
        if tag == "equal":
            for w, t in zip(old[i1:i2], new):
                # Ejaan/tanda baca yang diketik pengguna dipakai; waktu & keyakinan dari Whisper tetap.
                out.append({"start": w["start"], "end": w["end"], "word": t,
                            "p": w.get("p", 1.0) if norm_token(w["word"]) == norm_token(t) and w["word"] == t
                            else 1.0})
        elif tag == "replace":
            out += _spread(new, *_clamp(float(old[i1]["start"]), float(old[i2 - 1]["end"]), seg_start, seg_end))
        elif tag == "insert":
            left = float(old[i1 - 1]["end"]) if i1 > 0 else seg_start
            right = float(old[i1]["start"]) if i1 < len(old) else seg_end
            left, right = _clamp(left, right, seg_start, seg_end)
            if right - left < MIN_WORD * len(new):     # tidak ada jeda: bagi rentang kata tetangga
                if i1 > 0 and out:
                    prev = out.pop()
                    out += _spread([prev["word"], *new], float(prev["start"]), max(right, float(prev["end"])))
                    continue
                right = left + MIN_WORD * len(new)
            out += _spread(new, left, right)
        # "delete": kata lama dibuang
    return out


def retime(seg: dict, start: float, end: float) -> dict:
    """Geser/regangkan segmen ke [start, end]; posisi relatif kata dipertahankan."""
    if end - start < MIN_WORD:
        raise EditError("Akhir subtitle harus setelah awal.")
    words = seg.get("words") or []
    old_start, old_end = _seg_span(seg, words)
    scale = (end - start) / (old_end - old_start)
    seg["words"] = [{**w, "start": round(start + (float(w["start"]) - old_start) * scale, 3),
                     "end": round(start + (float(w["end"]) - old_start) * scale, 3)} for w in words]
    seg["start"], seg["end"] = round(start, 3), round(end, 3)
    return seg


def set_word_times(seg: dict, times: list[dict]) -> dict:
    """Waktu per kata dari editor ([{i, start, end}]). Dijaga berurutan & di dalam batas kata tetangga."""
    words = seg.get("words") or []
    for t in times:
        i = int(t["i"])
        if not 0 <= i < len(words):
            raise EditError(f"Kata {i} tidak ada.")
        lo = float(words[i - 1]["end"]) if i > 0 else 0.0
        hi = float(words[i + 1]["start"]) if i + 1 < len(words) else float("inf")
        s, e = max(lo, float(t["start"])), min(hi, float(t["end"]))
        if e - s < 0.02:
            raise EditError("Waktu kata bertabrakan dengan kata sebelah.")
        words[i] = {**words[i], "start": round(s, 3), "end": round(e, 3)}
    if words:
        seg["start"], seg["end"] = min(float(seg["start"]), words[0]["start"]), max(float(seg["end"]), words[-1]["end"])
    return seg


def apply_edits(transcript: dict, edits: list[dict]) -> dict:
    """Koreksi teks per segmen (format lama `text_fixes`, juga validasi masukan PUT /transcript)."""
    segs = transcript.get("segments") or []
    for e in edits:
        i = e["index"]
        if not 0 <= i < len(segs):
            raise EditError(f"Segmen {i} tidak ada.")
        seg = segs[i]
        if e.get("text") is not None:
            text = " ".join(str(e["text"]).split())
            if len(text) > MAX_TEXT:
                raise EditError("Teks segmen terlalu panjang.")
            seg["words"] = realign(seg, text)
            seg["text"] = text
        if e.get("start") is not None or e.get("end") is not None:
            retime(seg, float(e["start"] if e.get("start") is not None else seg["start"]),
                   float(e["end"] if e.get("end") is not None else seg["end"]))
        if e.get("word_times"):
            set_word_times(seg, e["word_times"])
        seg["edited"] = True
    return transcript


# ------------------------------------------------------------------ penyimpanan ringkas di resep

def pack(seg: dict) -> dict:
    return {"text": seg.get("text", ""), "start": seg["start"], "end": seg["end"],
            "words": [[w["word"], w["start"], w["end"], w.get("p", 1.0)] for w in seg.get("words") or []]}


def unpack(base: dict, packed: dict) -> dict:
    out = dict(base)
    out.update(text=packed["text"], start=packed["start"], end=packed["end"], edited=True,
               words=[{"word": w[0], "start": w[1], "end": w[2], "p": w[3]} for w in packed["words"]])
    return out


def apply_seg_edits(transcript: Optional[dict], seg_edits: dict, reviewed: Optional[list] = None) -> Optional[dict]:
    if not transcript or (not seg_edits and not reviewed):
        return transcript
    out = copy.deepcopy(transcript)
    segs = out.get("segments") or []
    for key, packed in (seg_edits or {}).items():
        i = int(key)
        if i < len(segs):
            segs[i] = unpack(segs[i], packed)
    for i in reviewed or []:
        if i < len(segs):
            segs[i]["reviewed"] = True
    return out


# ------------------------------------------------------------------ cari & ganti, kata tidak yakin

def find_replace(segments: list[dict], find: str, replace: str, case_sensitive: bool = False,
                 whole_word: bool = True) -> list[tuple[int, str, str]]:
    """(index, teks lama, teks baru) untuk segmen yang berubah. Tidak mengubah apa pun."""
    find = " ".join(find.split())
    if not find:
        raise EditError("Teks yang dicari kosong.")
    flags = 0 if case_sensitive else re.IGNORECASE
    body = re.escape(find).replace(r"\ ", r"\s+")
    pattern = re.compile(rf"(?<!\w){body}(?!\w)" if whole_word else body, flags)
    replacement = " ".join(replace.split())
    out = []
    for i, seg in enumerate(segments):
        text = seg.get("text") or " ".join(w["word"] for w in seg.get("words") or [])
        new = " ".join(pattern.sub(lambda _m: replacement, text).split())
        if new != text:
            out.append((i, text, new))
    return out


def unsure_words(segments: list[dict], start: Optional[float] = None, end: Optional[float] = None,
                 threshold: float = UNSURE_P) -> list[dict]:
    """Kata yang perlu ditinjau (p < threshold) di rentang [start, end], kecuali segmen yang sudah ditinjau."""
    out = []
    for i, seg in enumerate(segments):
        if seg.get("reviewed"):
            continue
        for k, w in enumerate(seg.get("words") or []):
            if (start is not None and w["end"] <= start) or (end is not None and w["start"] >= end):
                continue
            if float(w.get("p", 1.0)) < threshold:
                out.append({"seg": i, "i": k, "word": w["word"], "start": w["start"], "p": w.get("p")})
    return out
