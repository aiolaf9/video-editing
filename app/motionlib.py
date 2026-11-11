"""Motion Library Pro (M2.5): perpustakaan preset motion BERBASIS DATA (app/data/motion_presets.json).

Preset = data (kategori, kind, nilai bawaan, kontrol yang boleh diatur, bobot visual). Engine = satu renderer per
`kind`, dipakai ulang oleh banyak preset, sehingga menambah preset tidak mengubah engine:

  caption           -> app/subtitles (animasi libass per kata)                 [subtitle utama / per rentang]
  title_* / chapter / follow_bar / progress / bars -> app/motion.build (ASS)   [item fx di resep]
  camera            -> ekspresi zoom/geser/goyang di app/render (FFmpeg)       [item fx]
  transition        -> kilat/whip di titik waktu (FFmpeg eq/avgblur + kamera)  [item fx]

Item fx di resep: PlanItem(type="fx", preset=<id>, params={...}, start, end). Semua bisa diedit, dihapus, di-undo
(riwayat editor), tersimpan di resep proyek, dan dirender dengan jalur yang sama untuk pratinjau dan MP4 final.

Intensitas visual klip: ringan / seimbang (bawaan) / dinamis -> pengali amplitudo + batas kepadatan efek
(guard): tidak ada dua efek kamera bersamaan, satu judul besar pada satu waktu, jarak minimum antartransisi, dan
batas total "bobot visual" per jendela 6 detik. Efek yang melanggar dilewati dengan alasan (tidak diam-diam).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

DATA = Path(__file__).resolve().parent / "data" / "motion_presets.json"
LEVELS = {"ringan": 0.6, "seimbang": 1.0, "dinamis": 1.45}
LEVEL_RULES = {   # bobot maksimum per jendela 6 dtk, jarak minimum antartransisi (dtk)
    "ringan": {"budget": 3, "tr_gap": 6.0},
    "seimbang": {"budget": 5, "tr_gap": 4.0},
    "dinamis": {"budget": 8, "tr_gap": 2.5},
}
ASS_KINDS = ("title_cinematic", "title_slam", "title_wipe", "chapter", "follow_bar", "progress", "bars",
             # tahap 2
             "title_typewriter", "title_split", "title_boxed", "title_glitch", "title_outline_fill", "title_marker",
             "title_flip", "ring", "donut", "location", "timeline", "compare", "numlist", "linechart", "statcard",
             "quote", "cta_button", "end_card", "tr_light_leak", "tr_swipe", "tr_iris", "tr_blinds")
FULLFRAME_KINDS = ("tr_light_leak", "tr_swipe", "tr_iris", "tr_blinds")    # transisi layar penuh: tanpa safe-zone
EASE_ASS = {"out": 0.5, "in": 2.0, "inout": 1.0, "linear": 1.0}      # \t accel libass
ENGINE_VERSION = "m25-2"     # naikkan bila tampilan renderer berubah (cache galeri dibuat ulang)


@lru_cache(maxsize=1)
def _load() -> dict:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    seen = set()
    for p in data["presets"]:
        assert p["id"] not in seen, f"preset ganda: {p['id']}"
        assert p["category"] in data["categories"], p["id"]
        seen.add(p["id"])
    return data


def presets() -> list[dict]:
    return _load()["presets"]


def categories() -> dict:
    return _load()["categories"]


def get(pid: str) -> Optional[dict]:
    return next((p for p in presets() if p["id"] == pid), None)


def public() -> dict:
    cats = categories()
    return {"categories": [{"id": k, "label": v, "count": sum(p["category"] == k for p in presets())}
                           for k, v in cats.items()],
            "presets": [{k: p[k] for k in ("id", "category", "label", "desc", "kind", "weight", "defaults", "controls")}
                        for p in presets()],
            "levels": list(LEVELS)}


def resolve(item: dict, level: str = "seimbang") -> Optional[dict]:
    """Item fx -> spesifikasi render: nilai bawaan preset + params item + skala intensitas."""
    p = get(item.get("preset") or "")
    if not p:
        return None
    given = (clean_item(item) or {}).get("params", {})     # hanya kunci dikenal & nilai aman (bukan tag ASS)
    params = {**p["defaults"], **given}
    for key in ("text", "label"):            # teks juga boleh diisi lewat kolom biasa PlanItem
        if item.get(key) and key not in given:
            params[key] = item[key]
    if item.get("items") and "items" not in given:
        params["items"] = item["items"]
    params["k"] = LEVELS.get(level, 1.0) * float(params.get("intensity", 1.0) or 1.0)
    return {"id": item.get("id"), "preset": p["id"], "kind": p["kind"], "category": p["category"],
            "weight": p.get("weight", 1), "start": item["start"], "end": item["end"], "params": params}


# ------------------------------------------------------------------------------------------- guard kombinasi

def guard(specs: list[dict], level: str = "seimbang") -> tuple[list[dict], dict]:
    """Saring efek agar tidak berlebihan. -> (yang dipakai, {id: alasan dilewati})."""
    rules = LEVEL_RULES.get(level, LEVEL_RULES["seimbang"])
    kept: list[dict] = []
    skipped: dict = {}
    order = {"captions": -1, "graphics": 0, "story": 1, "titles": 2, "camera": 3, "transitions": 4}
    for s in sorted(specs, key=lambda x: (order.get(x["category"], 5), x["start"])):
        reason = None
        same_time = [k for k in kept if k["start"] < s["end"] and k["end"] > s["start"]]
        if s["category"] == "captions":
            # caption = gaya subtitle (selalu ada); tidak dihitung ke bobot, hanya satu preset per rentang
            if any(k["category"] == "captions" for k in same_time):
                skipped[s["id"]] = "sudah ada preset caption lain pada rentang yang sama"
            else:
                kept.append(s)
            continue
        if s["category"] == "camera" and any(k["category"] == "camera" for k in same_time):
            reason = "sudah ada efek kamera pada waktu yang sama"
        elif s["category"] in ("titles",) and any(k["category"] == "titles" for k in same_time):
            reason = "sudah ada judul besar pada waktu yang sama"
        elif s["category"] == "transitions":
            mid = (s["start"] + s["end"]) / 2
            near = [k for k in kept if k["category"] == "transitions" and abs((k["start"] + k["end"]) / 2 - mid) < rules["tr_gap"]]
            if near:
                reason = f"terlalu dekat dengan transisi lain (< {rules['tr_gap']:g} dtk untuk tingkat {level})"
        if not reason:
            win0, win1 = s["start"] - 3, s["end"] + 3
            load = sum(k["weight"] for k in kept if k["category"] != "captions" and k["start"] < win1
                       and k["end"] > win0) + s["weight"]
            if load > rules["budget"]:
                reason = f"terlalu ramai di sekitar {s['start']:.1f} dtk (tingkat {level}: bobot maks {rules['budget']})"
        if reason:
            skipped[s["id"]] = reason
        else:
            kept.append(s)
    return sorted(kept, key=lambda x: x["start"]), skipped


# ------------------------------------------------------------------------------------------- kamera & transisi

def _ease(u: str, easing: str) -> str:
    if easing == "in":
        return f"pow({u},2)"
    if easing == "out":
        return f"(1-pow(1-{u},2))"
    if easing == "linear":
        return u
    return f"(3*pow({u},2)-2*pow({u},3))"


def camera_terms(specs: list[dict], speed: float = 1.0) -> dict:
    """Ekspresi FFmpeg (waktu = detik klip masukan): tambahan zoom, geser x/y (-1..1 dari ruang tepi), kecerahan,
    dan jendela blur. Dipakai app/render.build_command."""
    z, dx, dy, bright, blur, rot, rgb, pix = [], [], [], [], [], [], [], []
    for s in specs:
        p, k = s["params"], s["params"]["k"]
        t0, t1 = s["start"] * speed, s["end"] * speed
        d = max(0.1, t1 - t0)
        if s["kind"] == "camera":
            move = p.get("move")
            amt = float(p.get("amount", 0.1)) * k
            if move == "kenburns":
                u = f"clip((t-{t0:.3f})/{d:.3f},0,1)"
                e = _ease(u, p.get("easing", "inout"))
                win = f"between(t,{t0:.3f},{t1:.3f})"
                z.append(f"{amt:.4f}*{e}*{win}")
                sign = {"kiri": -1, "kanan": 1, "atas": 0, "bawah": 0}.get(p.get("direction", "kanan"), 1)
                if sign:
                    dx.append(f"{0.8 * sign:.2f}*{e}*{win}")
                else:
                    dy.append(f"{(-0.6 if p.get('direction') == 'atas' else 0.6):.2f}*{e}*{win}")
            elif move == "punch":
                r = min(0.25, d / 3)
                u = f"clip(min((t-{t0:.3f})/{r:.3f},({t1:.3f}-t)/{r:.3f}),0,1)"
                z.append(f"{amt:.4f}*{_ease(u, p.get('easing', 'out'))}")
            elif move == "shake":
                f = 1.3 * float(p.get("speed", 1.0))
                ramp = f"clip(min((t-{t0:.3f})/0.3,({t1:.3f}-t)/0.3),0,1)"
                z.append(f"{amt * 5:.4f}*{ramp}")
                a = 0.55
                dx.append(f"{a:.2f}*{ramp}*(sin(6.2832*{f:.2f}*t)+0.5*sin(6.2832*{f * 1.73:.2f}*t+1.3))/1.5")
                dy.append(f"{a:.2f}*{ramp}*(cos(6.2832*{f * 0.87:.2f}*t)+0.5*sin(6.2832*{f * 2.1:.2f}*t+0.4))/1.5")
            elif move == "zoomout":                   # mulai dekat lalu mundur memperlihatkan seluruh adegan
                u = f"clip((t-{t0:.3f})/{d:.3f},0,1)"
                z.append(f"{amt:.4f}*(1-{_ease(u, p.get('easing', 'out'))})*between(t,{t0:.3f},{t1:.3f})")
            elif move in ("pan", "tilt"):              # geser tanpa zoom berarti: zoom kecil tetap + geser penuh
                u = f"clip((t-{t0:.3f})/{d:.3f},0,1)"
                e = _ease(u, p.get("easing", "inout"))
                ramp = f"clip(min((t-{t0:.3f})/0.4,({t1:.3f}-t)/0.4),0,1)"
                z.append(f"{amt:.4f}*{ramp}")
                rev = p.get("direction") in ("kiri", "atas")
                term = f"{ramp}*({'1-2*' if not rev else '-1+2*'}{e})"
                (dy if move == "tilt" else dx).append(f"{0.9:.2f}*{term}")
            elif move == "breathe":                    # zoom keluar-masuk pelan (bernapas)
                f = 0.35 * float(p.get("speed", 1.0))
                ramp = f"clip(min((t-{t0:.3f})/0.5,({t1:.3f}-t)/0.5),0,1)"
                z.append(f"{amt:.4f}*{ramp}*(0.5-0.5*cos(6.2832*{f:.2f}*(t-{t0:.3f})))")
            elif move == "rotate":                     # miring pelan (derajat) + zoom kompensasi agar tanpa sudut hitam
                deg = float(p.get("angle", 3.0)) * k
                sign = -1 if p.get("direction") == "kiri" else 1
                u = f"clip((t-{t0:.3f})/{d:.3f},0,1)"
                ramp = f"clip(min((t-{t0:.3f})/0.5,({t1:.3f}-t)/0.5),0,1)"
                a = f"{sign * deg * 0.017453:.5f}*{ramp}*{_ease(u, p.get('easing', 'inout'))}"
                rot.append(a)
                z.append(f"{abs(deg) * 0.017453 * 1.9:.5f}*{ramp}")
            elif move == "focus":                      # fokus menajam: blur dua arah mengecil bertahap
                n = 4
                for i in range(n):
                    blur.append((t0 + d * 0.5 * i / n, t0 + d * 0.5 * (i + 1) / n, int(min(40, 18 * k) * (n - i) / n), "xy"))
            elif move == "steps":                      # zoom bertingkat: 3 hentakan kecil
                n = 3
                for i in range(n):
                    a0 = t0 + d * i / n
                    z.append(f"{amt / n:.4f}*clip((t-{a0:.3f})/0.08,0,1)*between(t,{t0:.3f},{t1:.3f})")
        elif s["kind"] == "transition":
            mid, half = (t0 + t1) / 2, max(0.05, d / 2)
            b = f"max(0,1-abs(t-{mid:.3f})/{half:.3f})"
            if p.get("effect") == "zoomblur":
                z.append(f"{0.35 * min(1.4, k):.3f}*{b}")
                blur.append((mid - half * 0.6, mid + half * 0.6, int(min(50, 22 * k)), "xy"))
            elif p.get("effect") == "spin":
                deg = 14 * min(1.4, k)
                rot.append(f"{deg * 0.017453:.5f}*{b}")
                z.append(f"{deg * 0.017453 * 1.9 + 0.1:.4f}*{b}")
            elif p.get("effect") == "glitch":
                amt = int(min(70, 34 * k))           # M2.5.1: 18 px nyaris tak terlihat di 1080 px
                for i, (r_, b_) in enumerate(((amt, -amt), (-amt // 2, amt), (amt // 3, -amt // 3))):
                    rgb.append((t0 + d * i / 3, t0 + d * (i + 1) / 3, r_, b_))
            elif p.get("effect") == "pixelate":
                big = int(min(110, 54 * k))          # M2.5.1: blok lebih besar agar efek piksel jelas
                pix.append((t0, t0 + d * 0.3, max(4, big // 3)))
                pix.append((t0 + d * 0.3, t0 + d * 0.7, big))
                pix.append((t0 + d * 0.7, t1, max(4, big // 3)))
            if p.get("effect") == "flash":
                dark = str(p.get("color", "#FFFFFF")).upper() in ("#000000", "BLACK")
                bright.append(f"{(-0.9 if dark else 0.85) * min(1.3, k):.3f}*{b}")
            elif p.get("effect") == "whip":
                sign = -1 if p.get("direction") == "kiri" else 1
                z.append(f"{0.18 * min(1.4, k):.3f}*{b}")
                dx.append(f"{0.98 * sign:.2f}*{b}")
                blur.append((mid - half * 0.7, mid + half * 0.7, int(min(60, 26 * k))))
    return {"z": z, "dx": dx, "dy": dy, "bright": bright, "blur": blur, "rot": rot, "rgb": rgb, "pix": pix}


# ------------------------------------------------------------------------------------------- penempatan

def size_hint(kind: str, unit: int, oh: int, scale: float = 1.0, params: Optional[dict] = None) -> tuple[float, float, float]:
    """(lebar, tinggi, x) ternormalisasi untuk safe-zone (cocok dengan renderer app/motion)."""
    h = {"title_cinematic": 0.24, "title_slam": 0.42, "title_wipe": 0.16, "chapter": 0.30, "follow_bar": 0.15,
         "progress": 0.20, "bars": 0.50, "title_typewriter": 0.10, "title_split": 0.24, "title_boxed": 0.16,
         "title_glitch": 0.12, "title_outline_fill": 0.14, "title_marker": 0.12, "title_flip": 0.16, "ring": 0.40,
         "donut": 0.40, "location": 0.16, "timeline": 0.30, "compare": 0.36, "numlist": 0.32, "linechart": 0.46,
         "statcard": 0.32, "quote": 0.34, "cta_button": 0.16, "end_card": 0.30}.get(kind, 0.2)
    w = {"follow_bar": 0.88, "progress": 0.84, "bars": 0.80, "ring": 0.5, "location": 0.8, "statcard": 0.66,
         "cta_button": 0.7}.get(kind, 0.9)
    return w, h * unit * scale / oh, (1 - w) / 2


PREFERRED_Y = {"title_cinematic": [0.30, 0.20], "title_slam": [0.30, 0.25], "title_wipe": [0.25, 0.15, 0.40],
               "chapter": [0.28, 0.18], "follow_bar": [0.72, 0.12, 0.62], "progress": [0.25, 0.15, 0.42],
               "bars": [0.30, 0.25, 0.40], "title_typewriter": [0.28, 0.18, 0.40], "title_split": [0.28, 0.20, 0.40],
               "title_boxed": [0.25, 0.15, 0.40], "title_glitch": [0.28, 0.18, 0.40],
               "title_outline_fill": [0.28, 0.18, 0.40], "title_marker": [0.28, 0.18, 0.40],
               "title_flip": [0.28, 0.18, 0.40], "ring": [0.30, 0.25, 0.42], "donut": [0.30, 0.25, 0.42],
               "location": [0.22, 0.14, 0.62], "timeline": [0.30, 0.22, 0.42], "compare": [0.30, 0.25, 0.42],
               "numlist": [0.30, 0.25, 0.42], "linechart": [0.30, 0.26, 0.42], "statcard": [0.30, 0.22, 0.42],
               "quote": [0.30, 0.25, 0.42], "cta_button": [0.70, 0.62, 0.15], "end_card": [0.45, 0.35]}
DECORATIVE_KINDS = {"title_slam", "title_wipe", "follow_bar", "title_glitch", "title_flip", "cta_button"}


# ------------------------------------------------------------------------------------------- validasi item resep

FONTS = ("Anton", "Poppins", "Bebas Neue", "Arial")
DIRECTIONS = ("kiri", "kanan", "atas", "bawah")
_HEX = __import__("re").compile(r"^#[0-9A-Fa-f]{6}$")
_NUM = {"intensity": (0.3, 2.0), "speed": (0.4, 2.5), "value": (0, 100), "pos_y": (0.05, 0.95),
        "amount": (0.0, 0.4), "size": (0.4, 2.5), "words_per_line": (1, 8)}


def clean_item(raw: dict) -> Optional[dict]:
    """Validasi item fx dari resep/klien: preset harus dikenal; params hanya kunci yang dikenal & dalam batas.
    -> {"preset", "params", "text", "label", "items"} atau None."""
    p = get(str(raw.get("preset") or ""))
    if not p:
        return None
    src = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    out: dict = {}
    for key, val in src.items():
        if key in _NUM:
            lo, hi = _NUM[key]
            try:
                out[key] = round(min(hi, max(lo, float(val))), 3)
            except (TypeError, ValueError):
                continue
        elif key in ("accent", "highlight", "color") and isinstance(val, str) and _HEX.match(val):
            out[key] = val.upper()
        elif key == "font" and val in FONTS:
            out[key] = val
        elif key == "easing" and val in EASE_ASS:
            out[key] = val
        elif key == "direction" and val in DIRECTIONS:
            out[key] = val
        elif key == "uppercase" and isinstance(val, bool):
            out[key] = val
        elif key in ("text", "label") and isinstance(val, str):
            out[key] = val.strip()[:80 if key == "text" else 40]
        elif key == "items" and isinstance(val, list):
            out[key] = [str(x).strip()[:28] for x in val if str(x).strip()][:6]
    return {"preset": p["id"], "params": out}


def caption_extra(spec: dict) -> dict:
    """Spesifikasi caption -> tampilan bagian subtitle (app/subtitles opts.sections[i][3])."""
    p = spec["params"]
    out = {k: p[k] for k in ("highlight", "font", "size", "uppercase", "words_per_line", "pos_y", "speed", "box") if k in p}
    out["k"] = p.get("k", 1.0)
    return out
