"""Safe-zone dinamis untuk motion graphics (M2 penyempurnaan).

Untuk setiap bagian klip (tata letak dari app/framing.py) dihitung AREA TERPAKAI di bingkai output:
  - wajah pemateri (kepala & bahu), papan/materi        -> framing.occupied_rects (geometri crop yang sama)
  - pita subtitle (posisi per bagian: bawah / garis sambung Materi+Wajah / posisi bebas)
  - grafis lain yang tampil bersamaan (yang sudah ditempatkan lebih dulu)
Grafis lalu ditempatkan di posisi pertama yang bebas dari area itu selama SELURUH waktu tampilnya (termasuk bila
tata letak berganti di tengah). Bila tidak ada posisi bebas: diperkecil (80 %, 65 %). Bila tetap tidak ada:
  - grafis DEKORATIF (kinetic text, CTA, kata kunci) disembunyikan, dengan alasan;
  - grafis INFORMATIF (hook, lower-third, angka, kartu) tetap tampil di posisi yang paling sedikit menutupi
    (bobot: wajah > subtitle > papan), dengan alasan.
Subtitle (isi percakapan) tidak pernah disembunyikan oleh aturan ini.
Semua koordinat 0..1 terhadap bingkai output; waktu = timeline output.
"""
from __future__ import annotations

from typing import Optional

DECORATIVE = {"kinetic", "cta", "keyword", "chips"}
INFO = {"hook", "lower_third", "counter", "card", "checklist"}
WEIGHT = {"wajah": 3.0, "subtitle": 2.0, "papan": 1.0, "grafis": 2.0, "ui": 1.5}
TOUCH = 0.5          # penalti tambahan begitu menyentuh area (menutup sedikit wajah tetap buruk)
# Area tombol/teks aplikasi TikTok/Reels/Shorts di format vertikal (atas & bawah): hindari bila bisa.
UI_BANDS = ([0.0, 0.0, 1.0, 0.06], [0.0, 0.86, 1.0, 0.14])
# urutan penempatan: informasi dulu, dekorasi terakhir
PRIORITY = {"lower_third": 0, "counter": 1, "hook": 2, "card": 3, "checklist": 3, "cta": 4, "keyword": 5,
            "kinetic": 6, "chips": 6}
# posisi pilihan (pusat vertikal) per jenis, lalu pemindaian seluruh tinggi bingkai
PREFERRED = {"hook": [0.30, 0.18, 0.12, 0.42], "kinetic": [0.42, 0.30, 0.18], "counter": [0.32, 0.20, 0.45],
             "cta": [0.64, 0.12, 0.50], "lower_third": [0.648, 0.53, 0.40, 0.12], "keyword": [0.30, 0.18, 0.45],
             "card": [0.16, 0.30, 0.45]}
# M2.5 Motion Library: grafis ASS per `kind` (app/motion_kinds) ikut ditempatkan safe-zone
from . import motionlib as _ml  # noqa: E402
PREFERRED.update(_ml.PREFERRED_Y)
DECORATIVE |= set(_ml.DECORATIVE_KINDS)
INFO |= set(_ml.PREFERRED_Y) - set(_ml.DECORATIVE_KINDS)
PRIORITY.update({"progress": 1, "bars": 1, "chapter": 2, "title_cinematic": 2, "title_wipe": 3, "follow_bar": 4,
                 "title_slam": 5, "statcard": 1, "ring": 1, "donut": 1, "linechart": 1, "compare": 1, "timeline": 2,
                 "numlist": 2, "location": 0, "quote": 2, "end_card": 2, "title_typewriter": 3, "title_split": 3,
                 "title_boxed": 3, "title_outline_fill": 3, "title_marker": 3, "title_flip": 5, "title_glitch": 5,
                 "cta_button": 4})
SCAN = [round(0.04 + 0.01 * i, 2) for i in range(93)]       # 0,04 .. 0,96 (celah sempit antarpanel ikut dicoba)
SCALES = (1.0, 0.8, 0.65)
INFO_SCALES = SCALES + (0.5,)    # informasi boleh lebih kecil (nama ±35 px, angka ±92 px pada lebar 1080) daripada menutupi
NAMES = {"wajah": "wajah", "papan": "papan/materi", "subtitle": "subtitle", "grafis": "grafis lain",
         "ui": "area tombol aplikasi media sosial"}


def size_of(item: dict, ow: int, oh: int, scale: float = 1.0) -> tuple[float, float, float]:
    """(lebar, tinggi, x-kiri) ternormalisasi grafis motion pada skala tertentu (cocok dengan app/motion.build)."""
    unit = min(ow, oh)
    typ = item["type"]
    n = len((item.get("text") or "").split())
    if typ == "hook":
        return 1.0, 0.36 * unit * scale / oh, 0.0
    if typ == "kinetic":
        return 0.8, 0.20 * unit * 1.25 * scale / oh, 0.1
    if typ == "counter":
        h = 0.17 * unit * 1.1 + (0.20 * unit if item.get("label") else 0)
        return 0.7, h * scale / oh, 0.15
    if typ == "cta":
        w = min(0.92, (0.06 * unit * 0.62 * max(8, len(item.get("text") or "")) + 0.06 * unit) * scale / ow)
        return w, (0.06 * unit * 1.25 + 0.06 * unit) * scale / oh, (1 - w) / 2
    if typ == "lower_third":
        return 0.82 * scale, 0.17 * unit * scale / oh, 0.06
    if typ == "keyword":
        return 0.86, 0.15 * unit * max(1, min(3, n // 2 + 1)) * scale / oh, 0.07
    if typ == "card":
        return 0.84, (0.09 * unit + 0.072 * unit * max(1, min(3, n // 4 + 1))) * scale / oh, 0.08
    if typ in _ml.ASS_KINDS:
        return _ml.size_hint(typ, unit, oh, scale, item.get("params"))
    return 0.8, 0.1 * scale, 0.1


def _overlap(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return ix * iy


def subtitle_band(y_center: float, size: float, width: int, height: int) -> list[float]:
    """Pita subtitle (2 baris) di sekitar pusat vertikal y."""
    h = min(width, height) * 0.075 * max(0.4, size) * 2.6 / height
    return [0.04, max(0.0, y_center - h / 2), 0.92, h]


def timeline(frame: Optional[dict], start: float, speed: float, ow: int, oh: int, src: tuple[int, int],
             manual: tuple, distance: str, subs: Optional[dict]) -> list[dict]:
    """[{t0, t1, layout, rects:[{kind, rect}]}] di timeline output untuk setiap bagian tata letak."""
    from . import framing
    out = []
    segs = (frame or {}).get("segments") or [{"start": start, "end": start + 1e6, "layout": "center"}]
    for seg in segs:
        rects = framing.occupied_rects(seg, frame["data"], src[0], src[1], ow, oh, manual, distance) if frame else []
        if subs:
            y = subs["seam_y"] if seg["layout"] == "split" and subs.get("seam_y") else subs["y"]
            rects.append({"kind": "subtitle", "rect": subtitle_band(y, subs["size"], ow, oh)})
        out.append({"t0": (seg["start"] - start) / speed, "t1": (seg["end"] - start) / speed, "layout": seg["layout"],
                    "rects": rects})
    return out


def _search(it: dict, busy: list[dict], ow: int, oh: int):
    """(biaya, skala, y, rect, kena) terbaik; biaya 0 = bebas."""
    best = None
    for scale in (INFO_SCALES if it["type"] in INFO else SCALES):
        for y in (it.get("prefer") or []) + PREFERRED[it["type"]] + SCAN:
            w, h, x = size_of(it, ow, oh, scale)
            top = y - h / 2
            if top < 0.01 or top + h > 0.99:
                continue
            rect = [x, top, w, h]
            area = max(1e-6, w * h)
            hits: dict[str, float] = {}
            for b in busy:
                ov = _overlap(rect, b["rect"]) / area
                if ov > 0.01:
                    hits[b["kind"]] = max(hits.get(b["kind"], 0.0), ov)
            cost = sum(WEIGHT[k] * (v + TOUCH) for k, v in hits.items())
            cand = (cost, scale, y, rect, hits)
            if not hits:
                return cand
            if best is None or cost < best[0] - 1e-9:
                best = cand
    return best


def _busy(tl: list[dict], placed: list, s: float, e: float, vertical: bool) -> list[dict]:
    b = [r for seg in tl if seg["t0"] < e and seg["t1"] > s for r in seg["rects"]]
    b += [{"kind": "grafis", "rect": r} for ps, pe, r in placed if ps < e and pe > s]
    if vertical:
        b += [{"kind": "ui", "rect": r} for r in UI_BANDS]
    return b


def _describe(it: dict, found, layouts: list[str], default_y: float) -> dict:
    cost, scale, y, rect, hits = found
    what = ", ".join(NAMES[k] for k in sorted(hits, key=lambda k: -WEIGHT[k]))
    if not hits:
        moved = abs(y - default_y) > 0.005
        action = "diperkecil" if scale < 1 else ("dipindah" if moved else "tetap")
        reason = {"tetap": "posisi bawaan bebas",
                  "dipindah": f"dipindah agar tidak menutupi wajah/papan/subtitle ({'/'.join(layouts)})",
                  "diperkecil": f"diperkecil {int(scale * 100)} % agar muat di ruang kosong ({'/'.join(layouts)})"}[action]
    elif it["type"] in DECORATIVE:
        return {"action": "disembunyikan", "reason": f"grafis dekoratif; tidak ada ruang aman (akan menutupi {what})",
                "layouts": layouts}
    else:
        action = "tumpang tindih"
        reason = (f"informasi tetap ditampilkan; tidak ada ruang kosong, ditempatkan di posisi yang paling sedikit "
                  f"menutupi ({what}, skala {int(scale * 100)} %)")
    return {"y": round(y, 3), "scale": scale, "x": round(rect[0], 3), "action": action, "reason": reason,
            "layouts": layouts, "rect": [round(v, 3) for v in rect]}


def place(items: list[dict], tl: list[dict], ow: int, oh: int) -> dict[str, dict]:
    """Penempatan tiap grafis: {id: {y, scale, x, action, reason[, parts]}}. items berwaktu timeline output.
    Grafis yang melintasi pergantian tata letak dan tidak muat untuk keduanya dipecah per bagian: tiap bagian
    ditempatkan sendiri (berpindah tepat di titik potong tata letak, tanpa animasi masuk ulang)."""
    vertical = oh / ow >= 1.2
    placed: list[tuple[float, float, list[float]]] = []
    result: dict[str, dict] = {}
    for it in sorted(items, key=lambda i: (PRIORITY.get(i["type"], 9), i["start"])):
        if it["type"] not in PREFERRED:
            continue
        segs = [seg for seg in tl if seg["t0"] < it["end"] and seg["t1"] > it["start"]]
        layouts = sorted({seg["layout"] for seg in segs})
        default_y = (it.get("prefer") or PREFERRED[it["type"]])[0]
        whole = _search(it, _busy(tl, placed, it["start"], it["end"], vertical), ow, oh)
        if whole[4] and len(segs) > 1:
            parts = []
            for seg in segs:
                ps, pe = max(it["start"], seg["t0"]), min(it["end"], seg["t1"])
                if pe - ps < 0.2:
                    continue
                f = _search(it, _busy([seg], placed, ps, pe, vertical), ow, oh)
                d = _describe(it, f, [seg["layout"]], default_y)
                parts.append({"start": round(ps, 3), "end": round(pe, 3), **d})
            visible = [p for p in parts if p["action"] != "disembunyikan"]
            if visible:
                for p in visible:
                    placed.append((p["start"], p["end"], p["rect"]))
                worst = "tumpang tindih" if any(p["action"] == "tumpang tindih" for p in parts) else "dipindah"
                result[it["id"]] = {"action": f"{worst} per tata letak", "parts": parts, "layouts": layouts,
                                    "reason": "melintasi pergantian tata letak; posisi disesuaikan di tiap bagian: "
                                              + "; ".join(f"{p['layouts'][0]}: {p['reason']}" for p in parts)}
                continue
        d = _describe(it, whole, layouts, default_y)
        result[it["id"]] = d
        if d["action"] != "disembunyikan":
            placed.append((it["start"], it["end"], d["rect"]))
    return result
