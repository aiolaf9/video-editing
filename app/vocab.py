"""Kosakata (istilah dakwah, Arab, nama lembaga & program) — M1.1.

Dua kegunaan, keduanya TANPA mengubah transkrip diam-diam:
1. `hotwords` saat transkripsi: membiaskan Whisper ke ejaan istilah ini hanya bila audionya mendukung.
2. Saran koreksi setelah transkripsi: ejaan varian yang terdaftar ("ejaan") atau kata mirip yang keyakinan Whisper-nya
   rendah ("mirip"). Setiap saran harus diterima pengguna (satu per satu atau semua untuk satu istilah); yang ditolak
   diingat per proyek (recipe.vocab_ignored).

Penyimpanan: tabel settings — "vocab:global" (seluruh server / aplikasi desktop) dan "vocab:unit:<id>" (per unit).
Kosakata bawaan (app/data/vocab_default.json) dapat dimatikan.
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from . import db
from .transcript_edit import norm_token

DEFAULT_FILE = Path(__file__).resolve().parent / "data" / "vocab_default.json"
MAX_ENTRIES = 1000
FUZZY_MIN_RATIO = 0.8          # kemiripan ejaan minimum untuk saran "mirip"
FUZZY_MAX_P = 0.7              # saran "mirip" hanya bila keyakinan Whisper pada kata itu rendah
FUZZY_MIN_LEN = 5              # istilah pendek (doa, duha) terlalu mirip kata umum ("dua"): hanya lewat varian
MAX_SUGGESTIONS = 500


class Entry(BaseModel):
    term: str = Field(min_length=1, max_length=60)
    variants: list[str] = Field(default_factory=list, max_length=20)
    kind: Literal["arab", "istilah", "lembaga", "program", "nama", "lain"] = "istilah"

    @field_validator("term")
    @classmethod
    def _term(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v or len(v.split()) > 5:
            raise ValueError("Istilah 1-5 kata.")
        return v

    @field_validator("variants")
    @classmethod
    def _variants(cls, v: list[str]) -> list[str]:
        out = []
        for x in v:
            x = " ".join(str(x).split())
            if x and len(x) <= 60 and len(x.split()) <= 5 and x not in out:
                out.append(x)
        return out


class VocabIn(BaseModel):
    entries: list[Entry] = Field(default_factory=list, max_length=MAX_ENTRIES)
    use_defaults: bool = True
    # Petunjuk ejaan untuk Whisper (hotwords). Bawaan MATI: evaluasi M1.1 (FLEURS id & sampel Windows) menunjukkan
    # hotwords dari daftar kosakata menaikkan WER dan dapat membuat Whisper melewatkan ucapan. Hanya istilah buatan
    # pengguna yang dipakai (tidak termasuk daftar bawaan); uji dengan sampel NH sebelum mengaktifkannya.
    whisper_hints: bool = False


def defaults() -> list[dict]:
    return json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))["entries"]


def _key(unit_id: Optional[str]) -> str:
    return f"vocab:unit:{unit_id}" if unit_id else "vocab:global"


def stored(unit_id: Optional[str]) -> dict:
    return db.get_setting(_key(unit_id), {"entries": [], "use_defaults": True, "whisper_hints": False})


def save(unit_id: Optional[str], body: VocabIn) -> dict:
    data = {"entries": [e.model_dump() for e in body.entries], "use_defaults": body.use_defaults,
            "whisper_hints": body.whisper_hints}
    db.set_setting(_key(unit_id), data)
    return data


def effective(unit_id: Optional[str]) -> list[dict]:
    """Bawaan (bila aktif) + global + unit. Istilah yang sama (tanpa beda huruf besar/kecil): yang lebih spesifik
    menang."""
    glob = stored(None)
    unit = stored(unit_id) if unit_id else {"entries": [], "use_defaults": True}
    use_defaults = glob.get("use_defaults", True) and unit.get("use_defaults", True)
    merged: dict[str, dict] = {}
    for e in (defaults() if use_defaults else []) + glob["entries"] + unit["entries"]:
        merged[e["term"].lower()] = {"variants": [], "kind": "istilah", **e}
    return list(merged.values())


def whisper_terms(unit_id: Optional[str]) -> list[str]:
    """Istilah untuk hotwords Whisper saat analisis: hanya bila diaktifkan (global atau unit), hanya istilah buatan
    pengguna. Bawaan: kosong (lihat VocabIn.whisper_hints)."""
    out: list[dict] = []
    for scope in ([None, unit_id] if unit_id else [None]):
        data = stored(scope)
        if data.get("whisper_hints"):
            out += data["entries"]
    return hotwords(out)


def hotwords(entries: list[dict]) -> list[str]:
    # Istilah khusus (lembaga/program/nama) lebih dulu: hotwords dipotong bila terlalu panjang.
    order = {"program": 0, "lembaga": 0, "nama": 0, "arab": 1, "istilah": 2, "lain": 3}
    return [e["term"] for e in sorted(entries, key=lambda e: order.get(e.get("kind"), 3))]


# ------------------------------------------------------------------ saran

_TRAIL = re.compile(r"[^\w']+$")


def _joined(tokens: list[str]) -> str:
    return "".join(norm_token(t) for t in tokens)


def suggest(segments: list[dict], entries: list[dict], ignored: Optional[set] = None) -> list[dict]:
    """Saran istilah untuk transkrip. Tidak mengubah apa pun. Satu saran = rentang kata [i, i+n) di segmen `seg`."""
    ignored = ignored or set()
    forms = []
    for e in entries:
        term = e["term"]
        tj = _joined(term.split())
        if len(tj) < 2:
            continue
        for v in [term, *e.get("variants", [])]:
            forms.append((len(v.split()), _joined(v.split()), term, tj, v != term))
    sizes = sorted({f[0] for f in forms} | {f[0] + 1 for f in forms if f[0] < 5} | {max(1, f[0] - 1) for f in forms})
    out: list[dict] = []
    for si, seg in enumerate(segments):
        words = seg.get("words") or []
        taken = [False] * len(words)
        found: list[dict] = []
        seen: dict[str, int] = {}
        for n in sorted(sizes, reverse=True):
            for i in range(0, len(words) - n + 1):
                if any(taken[i:i + n]):
                    continue
                win = words[i:i + n]
                text = " ".join(w["word"] for w in win)
                bare = _TRAIL.sub("", text)
                j = _joined([w["word"] for w in win])
                if not j:
                    continue
                exact = next((f for f in forms if f[1] == j), None)
                if exact and bare == exact[2]:
                    for k in range(i, i + n):        # sudah persis istilahnya: jangan disentuh saran lain
                        taken[k] = True
                    continue
                best = (1.0, exact[2], "ejaan") if exact else None
                if not best:
                    p_min = min(float(w.get("p", 1.0)) for w in win)
                    for size, _fj, term, tj, _var in forms:
                        if size != n or len(tj) < FUZZY_MIN_LEN or len(j) < FUZZY_MIN_LEN - 1 or p_min >= FUZZY_MAX_P:
                            continue
                        ratio = difflib.SequenceMatcher(a=j, b=tj).ratio()
                        if ratio >= FUZZY_MIN_RATIO and (not best or ratio > best[0]):
                            best = (ratio, term, "mirip")
                if not best:
                    continue
                _ratio, term, reason = best
                # ID berbasis isi (bukan posisi kata): tetap sama walau bagian lain segmen dikoreksi, sehingga saran
                # di layar & penolakan yang tersimpan tidak kedaluwarsa karena edit lain.
                key = f"{si}:{j}:{term}"
                seen[key] = seen.get(key, 0) + 1
                sid = f"{key}:{seen[key]}"
                if sid in ignored:
                    continue
                trail = _TRAIL.search(text)
                found.append({"id": sid, "seg": si, "i": i, "n": n, "found": text, "term": term,
                              "replacement": term + (trail.group(0) if trail else ""), "reason": reason,
                              "p": round(min(float(w.get("p", 1.0)) for w in win), 3), "start": win[0]["start"]})
                for k in range(i, i + n):
                    taken[k] = True
        out += sorted(found, key=lambda s: s["i"])
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out[:MAX_SUGGESTIONS]


def apply_to_text(segment: dict, accepted: list[dict]) -> str:
    """Teks segmen baru setelah saran diterima (dari kanan ke kiri agar indeks tetap sah)."""
    tokens = [w["word"] for w in segment.get("words") or []]
    for s in sorted(accepted, key=lambda s: s["i"], reverse=True):
        tokens[s["i"]:s["i"] + s["n"]] = s["replacement"].split()
    return " ".join(tokens)
