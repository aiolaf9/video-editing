"""Penyusun cerita: Hook -> Masalah -> Proses/Solusi -> Dampak -> Penutup.

Rencana cerita (StoryPlan) hanya berisi ID kalimat dan ID adegan dari katalog analisis.
Tiga sumber rencana:
1. heuristic_plan()  : lokal & gratis, memakai skor dari analyze.py
2. ai.plan_story()   : Claude membaca seluruh transkrip lalu memilih ID (lihat ai.py)
3. pengguna          : rencana bisa diedit di UI (urutan, ganti hook, hapus/tambah kalimat)
Semua sumber melewati validate_plan(): ID yang tidak ada dibuang dan durasi disesuaikan ke target.
"""
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .analyze import ROLES, _tokens

PAD_IN = 0.12        # detik sebelum kata pertama
PAD_OUT = 0.28       # detik sesudah kata terakhir
TRANSITION = 0.30    # durasi crossfade antar potongan
VISUAL_MAX = 3.0     # adegan tanpa ucapan (suara alam) paling lama 3 detik
BROLL_MIN_SPEECH = 4.5

# Pembagian waktu ideal (proporsi dari target durasi)
BUDGET = {"hook": 0.12, "problem": 0.24, "solution": 0.30, "impact": 0.22, "closing": 0.12}


class PlanItem(BaseModel):
    kind: Literal["speech", "visual"] = "speech"
    ref: str                                   # ID kalimat (speech) atau ID adegan (visual)
    broll: list[str] = Field(default_factory=list)   # ID adegan pendukung yang menutupi gambar saat bicara


class PlanSection(BaseModel):
    role: Literal["hook", "problem", "solution", "impact", "closing"]
    items: list[PlanItem] = Field(default_factory=list)


class StoryPlan(BaseModel):
    title: str = ""
    hook_reason: str = ""
    source: str = "heuristic"                  # heuristic | claude | manual
    target: float = 60.0
    sections: list[PlanSection] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- utilitas katalog

def index(catalog: dict) -> tuple[dict, dict]:
    utt = {u["id"]: u for u in catalog.get("utterances", [])}
    shots = {s["id"]: s for s in catalog.get("shots", [])}
    return utt, shots


def item_duration(item: PlanItem, utt: dict, shots: dict) -> float:
    if item.kind == "speech":
        u = utt[item.ref]
        return u["end"] - u["start"] + PAD_IN + PAD_OUT
    s = shots[item.ref]
    return min(VISUAL_MAX, s["end"] - s["start"])


def plan_duration(plan: StoryPlan, catalog: dict) -> float:
    utt, shots = index(catalog)
    items = [it for sec in plan.sections for it in sec.items]
    total = sum(item_duration(it, utt, shots) for it in items)
    return max(0.0, total - TRANSITION * max(0, len(items) - 1))


# ---------------------------------------------------------------- penilaian

def _value(u: dict, role: str) -> float:
    """Nilai sebuah kalimat untuk bagian tertentu."""
    base = 0.45 * u["roles"].get(role, 0) + 0.2 * u["clarity"] + 0.2 * u["confidence"] \
        + 0.15 * max(0.0, min(1.0, 0.5 + u["energy"] / 12))
    if role == "closing" and u["text"].rstrip().endswith((".", "!")):
        base += 0.05
    if u["duration"] < 1.5:
        base -= 0.25
    if u["duration"] > 14:
        base -= 0.15
    first = (_tokens(u["text"]) or [""])[0]
    if first in {"dan", "terus", "lalu", "nah", "trus"}:
        base -= 0.08
    return base


def pick_hook(utterances: list[dict]) -> Optional[dict]:
    cands = [u for u in utterances if 1.8 <= u["duration"] <= 10]
    pool = cands or utterances
    return max(pool, key=lambda u: u["hook"], default=None)


def hook_candidates(catalog: dict, n: int = 5) -> list[dict]:
    us = [u for u in catalog.get("utterances", []) if 1.8 <= u["duration"] <= 10]
    return [{"id": u["id"], "text": u["text"], "score": u["hook"], "duration": u["duration"]}
            for u in sorted(us, key=lambda u: -u["hook"])[:n]]


def _overlap(text: str, shot: dict) -> float:
    words = set(_tokens(shot.get("description", ""))) | {t.lower() for t in shot.get("tags", [])}
    if not words:
        return 0.0
    return len(set(_tokens(text)) & words) / max(1, min(len(words), 6))


def choose_broll(u: dict, shots: list[dict], used: set, count: int = 1) -> list[str]:
    """Pilih adegan pendukung untuk menutup gambar saat narasumber bicara."""
    def score(s: dict) -> float:
        sc = s["quality"]
        sc += 0.35 * _overlap(u["text"], s)
        if s.get("talking_head") is True:
            sc -= 0.6
        elif s.get("talking_head") is None:
            sc -= 0.4 * s["speech"]   # tanpa deskripsi AI: bagian tanpa ucapan biasanya B-roll
        if s["clip_id"] == u["clip_id"] and s["start"] < u["end"] and s["end"] > u["start"]:
            sc -= 1.0                 # gambar yang sama dengan pembicara: bukan cutaway
        if s.get("usable") is False:
            sc -= 1.0
        return sc
    pool = sorted((s for s in shots if s["id"] not in used and s["duration"] >= 1.5), key=score, reverse=True)
    picks = [s["id"] for s in pool[:count] if score(s) > 0.15]
    used.update(picks)
    return picks


def auto_broll(plan: StoryPlan, catalog: dict) -> StoryPlan:
    """Lengkapi B-roll untuk kalimat panjang yang belum punya. Hook dibiarkan menampilkan narasumber."""
    utt, _shots = index(catalog)
    shots = catalog.get("shots", [])
    used = {b for sec in plan.sections for it in sec.items for b in it.broll}
    used |= {it.ref for sec in plan.sections for it in sec.items if it.kind == "visual"}
    for sec in plan.sections:
        if sec.role == "hook":
            continue
        for it in sec.items:
            if it.kind != "speech" or it.broll:
                continue
            u = utt[it.ref]
            if u["duration"] >= BROLL_MIN_SPEECH:
                it.broll = choose_broll(u, shots, used, count=2 if u["duration"] > 9 else 1)
    return plan


# ---------------------------------------------------------------- heuristik

def heuristic_plan(catalog: dict, target: float = 60.0, hook_id: Optional[str] = None) -> StoryPlan:
    utterances = [u for u in catalog.get("utterances", []) if u["duration"] >= 1.0]
    if not utterances:
        return StoryPlan(target=target, warnings=["Tidak ada ucapan yang terdeteksi di rekaman."])
    utt, _ = index(catalog)
    hook = utt.get(hook_id) if hook_id else None
    hook = hook or pick_hook(utterances)
    used = {hook["id"]}

    # Tetapkan setiap kalimat ke bagian yang paling cocok.
    assigned: dict[str, list[dict]] = {r: [] for r in ROLES if r != "hook"}
    for u in utterances:
        if u["id"] in used:
            continue
        role = max(assigned, key=lambda r: _value(u, r))
        assigned[role].append(u)

    sections = [PlanSection(role="hook", items=[PlanItem(ref=hook["id"])])]
    spent = hook["duration"] + PAD_IN + PAD_OUT
    for role in ("problem", "solution", "impact", "closing"):
        budget = target * BUDGET[role]
        pool = sorted(assigned[role], key=lambda u: -_value(u, role))
        if not pool:  # bagian kosong: pinjam kalimat terbaik yang tersisa
            pool = sorted((u for u in utterances if u["id"] not in used), key=lambda u: -_value(u, role))[:1]
        chosen, used_t = [], 0.0
        for u in pool:
            d = u["duration"] + PAD_IN + PAD_OUT - TRANSITION
            if u["id"] in used:
                continue
            if chosen and used_t + d > budget * 1.25:
                continue
            if spent + d > target * 1.08:
                continue
            chosen.append(u)
            used.add(u["id"])
            used_t += d
            spent += d
            if used_t >= budget * 0.85:
                break
        chosen.sort(key=lambda u: (u["position"], u["start"]))  # urut kronologis di dalam bagian
        if chosen:
            sections.append(PlanSection(role=role, items=[PlanItem(ref=u["id"]) for u in chosen]))

    words = hook["text"].split()
    plan = StoryPlan(
        title=" ".join(words[:8]).rstrip(",.;:") + ("…" if len(words) > 8 else ""),
        hook_reason=f"Skor hook tertinggi ({hook['hook']:.2f}) di antara kalimat berdurasi 2-10 detik.",
        source="heuristic", target=target, sections=sections,
    )
    return auto_broll(plan, catalog)


# ---------------------------------------------------------------- validasi

def validate_plan(raw: dict | StoryPlan, catalog: dict, target: float = 60.0,
                  fill_broll: bool = True) -> StoryPlan:
    """Pastikan rencana hanya memakai bahan asli, tidak ada duplikasi, dan durasinya mendekati target."""
    plan = raw if isinstance(raw, StoryPlan) else StoryPlan.model_validate(raw)
    plan.target = target
    utt, shots = index(catalog)
    warnings: list[str] = []
    seen_u, seen_s = set(), set()
    by_role: dict[str, PlanSection] = {}
    for sec in plan.sections:
        dst = by_role.setdefault(sec.role, PlanSection(role=sec.role))
        for it in sec.items:
            if it.kind == "speech":
                if it.ref not in utt:
                    warnings.append(f"Kalimat {it.ref} tidak ada di rekaman, dibuang.")
                    continue
                if it.ref in seen_u:
                    continue
                seen_u.add(it.ref)
            else:
                if it.ref not in shots or it.ref in seen_s:
                    continue
                seen_s.add(it.ref)
            broll = []
            for b in it.broll:
                if b in shots and b not in seen_s:
                    seen_s.add(b)
                    broll.append(b)
                elif b not in shots:
                    warnings.append(f"Adegan {b} tidak ada, dibuang.")
            dst.items.append(PlanItem(kind=it.kind, ref=it.ref, broll=broll if it.kind == "speech" else []))
    plan.sections = [by_role[r] for r in ROLES if r in by_role and by_role[r].items]

    if not plan.sections or plan.sections[0].role != "hook":
        warnings.append("Rencana tidak memiliki hook; memakai kalimat hook terbaik.")
        hook = pick_hook([u for u in catalog.get("utterances", []) if u["id"] not in seen_u])
        if hook:
            plan.sections.insert(0, PlanSection(role="hook", items=[PlanItem(ref=hook["id"])]))

    _fit(plan, catalog, target, warnings)
    if fill_broll:
        auto_broll(plan, catalog)
    total = plan_duration(plan, catalog)
    if total < target * 0.6:
        warnings.append(f"Durasi cerita hanya {total:.0f} detik: bahan ucapan yang layak terbatas.")
    plan.warnings = warnings
    return plan


def _fit(plan: StoryPlan, catalog: dict, target: float, warnings: list[str]) -> None:
    """Pangkas kalimat bernilai rendah sampai durasi <= target + 10%."""
    utt, shots = index(catalog)
    limit = target * 1.10
    while plan_duration(plan, catalog) > limit:
        best = None
        for sec in plan.sections:
            if sec.role == "hook" or len(sec.items) <= 1:
                continue
            for it in sec.items:
                val = _value(utt[it.ref], sec.role) if it.kind == "speech" else -1.0
                if best is None or val < best[0]:
                    best = (val, sec, it)
        if best is None:
            # setiap bagian tinggal satu: buang bagian dengan prioritas terendah
            for role in ("closing", "problem", "solution", "impact"):
                sec = next((s for s in plan.sections if s.role == role), None)
                if sec and len(plan.sections) > 2:
                    plan.sections.remove(sec)
                    warnings.append(f"Bagian {role} dibuang agar durasi tidak melebihi target.")
                    break
            else:
                break
            continue
        _val, sec, it = best
        sec.items.remove(it)


def plan_summary(plan: StoryPlan, catalog: dict) -> dict:
    """Rencana yang diperkaya teks & durasi untuk ditampilkan di UI."""
    utt, shots = index(catalog)
    out = plan.model_dump()
    for sec in out["sections"]:
        for it in sec["items"]:
            if it["kind"] == "speech":
                u = utt[it["ref"]]
                it.update(text=u["text"], clip_id=u["clip_id"], start=u["start"], end=u["end"],
                          duration=round(u["duration"], 2))
            else:
                s = shots[it["ref"]]
                it.update(text=s.get("description") or "(adegan tanpa ucapan)", clip_id=s["clip_id"],
                          start=s["start"], end=s["end"], duration=round(min(VISUAL_MAX, s["duration"]), 2))
    out["duration"] = round(plan_duration(plan, catalog), 1)
    return out


def catalog_text(catalog: dict, max_shots: int = 120) -> str:
    """Katalog ringkas untuk dibaca model AI: hanya teks & angka, tanpa video."""
    lines = ["# KLIP"]
    for c in catalog.get("clips", []):
        lines.append(f"{c['id']}: \"{c['name']}\" durasi {c['duration']:.1f}s")
    lines.append("\n# KALIMAT (id | klip | durasi | skor_hook | teks)")
    for u in catalog.get("utterances", []):
        lines.append(f"{u['id']} | {u['clip_id']} | {u['duration']:.1f}s | {u['hook']:.2f} | {u['text']}")
    shots = sorted(catalog.get("shots", []), key=lambda s: -s["quality"])[:max_shots]
    shots.sort(key=lambda s: (s["clip_id"], s["start"]))
    lines.append("\n# ADEGAN VISUAL (id | klip | durasi | kualitas | ada_ucapan | deskripsi)")
    for s in shots:
        desc = s.get("description") or "-"
        lines.append(f"{s['id']} | {s['clip_id']} | {s['duration']:.1f}s | {s['quality']:.2f} | "
                     f"{s['speech']:.1f} | {desc}")
    return "\n".join(lines)


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def parse_plan_text(text: str) -> dict:
    """Ambil objek JSON dari jawaban teks (untuk mode salin-tempel)."""
    import json

    m = _JSON_BLOCK.search(text or "")
    if not m:
        raise ValueError("Tidak ditemukan JSON di jawaban.")
    return json.loads(m.group(0))
