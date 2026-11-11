"""Motion graphics (M2.3) sebagai animasi libass (ASS): hook pembuka, kinetic typography, lower-third narasumber,
animasi angka, dan CTA. Dibakar FFmpeg dengan filter `ass` yang sama seperti subtitle, sehingga pratinjau cepat dan
render final identik. Gambar/logo memakai PNG overlay (app/overlays.py).

Semua waktu di sini sudah dalam timeline OUTPUT (detik sejak awal klip, setelah kecepatan).
"""
from __future__ import annotations

import re
from typing import Optional

from .subtitles import ass_color, ass_time

TYPES = ("hook", "kinetic", "lower_third", "counter", "cta")
ACCENT = "#FFD43B"
_NUM = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+(?:,\d+)?)")


def _raw(text: str) -> str:
    return (text or "").replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _txt(text: str) -> str:
    return (text or "").replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def parse_number(text: str) -> Optional[tuple[str, float, int, str]]:
    """'Rp 2,5 juta' -> ('Rp ', 2.5, 1, ' juta'). Format Indonesia: koma = desimal, titik = ribuan."""
    m = _NUM.search(text or "")
    if not m:
        return None
    raw = m.group(1)
    decimals = len(raw.split(",")[1]) if "," in raw else 0
    value = float(raw.replace(".", "").replace(",", "."))
    return text[: m.start()], value, decimals, text[m.end():]


def fmt_number(v: float, decimals: int, thousands: bool) -> str:
    s = f"{v:,.{decimals}f}"            # 1,234.5
    s = s.replace(",", "\x00").replace(".", ",").replace("\x00", "." if thousands else "")
    return s


def build(items: list[dict], width: int, height: int, accent: str = ACCENT) -> str:
    """Satu berkas ASS untuk semua elemen motion bertipe teks. `items` berwaktu timeline output."""
    unit = min(width, height)
    acc = ass_color(accent)
    white, black = "&H00FFFFFF", "&H00000000"
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Hook,Anton,{int(unit * 0.105)},{white},&H000000FF,{black},&H90000000,0,0,0,0,100,100,0,0,1,"
        f"{max(3, unit // 160)},{max(2, unit // 260)},5,{int(width * 0.07)},{int(width * 0.07)},0,1",
        f"Style: Kinetic,Anton,{int(unit * 0.20)},{white},&H000000FF,{black},&H80000000,0,0,0,0,100,100,0,0,1,"
        f"{max(3, unit // 140)},0,5,20,20,0,1",
        f"Style: LowerName,Poppins,{int(unit * 0.064)},{white},&H000000FF,{black},&H00000000,-1,0,0,0,100,100,0,0,1,0,0,"
        f"4,0,0,0,1",
        f"Style: LowerRole,Poppins,{int(unit * 0.044)},{acc},&H000000FF,{black},&H00000000,0,0,0,0,100,100,0,0,1,0,0,"
        f"4,0,0,0,1",
        f"Style: Counter,Anton,{int(unit * 0.17)},{acc},&H000000FF,{black},&H80000000,0,0,0,0,100,100,0,0,1,"
        f"{max(3, unit // 120)},{max(2, unit // 200)},5,0,0,0,1",
        f"Style: Caption,Poppins,{int(unit * 0.045)},{white},&H000000FF,{black},&H80000000,-1,0,0,0,100,100,0,0,1,"
        f"{max(2, unit // 300)},1,5,0,0,0,1",
        f"Style: Cta,Poppins,{int(unit * 0.06)},{black},&H000000FF,{acc},&H60000000,-1,0,0,0,100,100,0,0,3,"
        f"{int(unit * 0.03)},{max(2, unit // 250)},5,0,0,0,1",
        f"Style: Shape,Arial,20,{white},&H000000FF,{black},&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def ev(s: float, e: float, style: str, text: str, layer: int = 2) -> None:
        if e - s > 0.02:
            lines.append(f"Dialogue: {layer},{ass_time(s)},{ass_time(e)},{style},,0,0,0,,{text}")

    cx = width // 2
    for it in sorted(items, key=lambda i: i["start"]):
        s, e, typ = it["start"], it["end"], it["type"]
        text = _txt(it.get("text", ""))
        # Penempatan dari safe-zone (app/safezone.py): pusat vertikal & skala; tanpa itu = posisi bawaan.
        pl = it.get("place") or {}
        sc = float(pl.get("scale") or 1.0)
        intro = it.get("intro", True)          # False: lanjutan setelah pindah posisi (tanpa animasi masuk ulang)
        fin = r"\fad(0,220)" if e >= it.get("span", (s, e))[1] - 0.01 else ""

        def py(default: float) -> float:
            return float(pl["y"]) if pl.get("y") is not None else default
        if typ == "hook" and text:
            # Pita gelap + teks besar yang "dibanting" masuk, kata terakhir berwarna aksen, keluar memudar.
            words = text.upper().split()
            body = " ".join(words[:-1]) + (" " if len(words) > 1 else "") + rf"{{\c{acc}}}{words[-1]}"
            y = int(height * py(0.30))
            band_h = int(unit * 0.36 * sc)
            band_in = r"\alpha&HFF&\t(0,160,\alpha&H50&)" if intro else r"\alpha&H50&"
            ev(s, e, "Shape", rf"{{\pos(0,{y - band_h // 2})\c&H000000&{band_in}{fin}\p1}}"
               rf"m 0 0 l {width} 0 {width} {band_h} 0 {band_h}{{\p0}}", layer=1)
            slam = (r"\fscx40\fscy40\alpha&HFF&\t(0,170,\fscx110\fscy110\alpha&H00&)\t(170,260,\fscx100\fscy100)"
                    if intro else "")
            ev(s, e, "Hook", rf"{{\pos({cx},{y})\fs{int(unit * 0.105 * sc)}{slam}{fin}}}{body}")
        elif typ == "kinetic" and text:
            # Kata demi kata, bergantian miring kiri/kanan, masuk dengan zoom.
            words = text.upper().split()[:12]
            s0, e0 = it.get("span", (s, e))        # jadwal kata dari durasi asli (tetap sama bila dipecah)
            step = (e0 - s0) / max(1, len(words))
            for k, w in enumerate(words):
                ws, we = max(s, s0 + k * step), min(e, s0 + (k + 1) * step)
                if we - ws < 0.02:
                    continue
                rot = (-6, 5, -3, 6)[k % 4]
                col = acc if len(w) >= 6 or any(c.isdigit() for c in w) else white
                ev(ws, we, "Kinetic", rf"{{\pos({cx},{int(height * py(0.42))})\fs{int(unit * 0.20 * sc)}\frz{rot}\c{col}\fscx160\fscy160\blur6"
                   rf"\t(0,140,\fscx100\fscy100\blur0)}}{w}")
        elif typ == "lower_third" and text:
            # Bilah aksen meluncur dari kiri, nama & jabatan menyusul; posisi di atas area subtitle.
            bar_w, bar_h = int(unit * 0.02 * sc), int(unit * 0.17 * sc)
            x0, y0 = int(width * 0.06), int(height * py(0.648) - bar_h / 2)
            card_w = int(width * 0.82 * sc)
            # lanjutan (setelah pindah posisi): langsung di tempat, tanpa meluncur masuk lagi
            fade = fin.replace("220", "200")
            if intro:
                card_pos, bar_pos = rf"\move({-card_w},{y0},{x0},{y0},0,260)", rf"\move({-bar_w},{y0},{x0},{y0},0,200)"
            else:
                card_pos = bar_pos = rf"\pos({x0},{y0})"
            ev(s, e, "Shape", rf"{{{card_pos}\c&H141418&\alpha&H30&{fade}\p1}}"
               rf"m 0 0 l {card_w} 0 {card_w} {bar_h} 0 {bar_h}{{\p0}}", layer=1)
            ev(s, e, "Shape", rf"{{{bar_pos}\c{acc}{fade}\p1}}"
               rf"m 0 0 l {bar_w} 0 {bar_w} {bar_h} 0 {bar_h}{{\p0}}", layer=3)
            tx = x0 + bar_w * 2

            def line(txt, style, frac, fs, d0, d1):
                yy = y0 + int(bar_h * frac)
                if intro:
                    return rf"{{\move({tx - 60},{yy},{tx},{yy},{d0},{d1})\fs{fs}\alpha&HFF&\t({d0},{d1},\alpha&H00&){fade}}}{txt}"
                return rf"{{\pos({tx},{yy})\fs{fs}{fade}}}{txt}"
            ev(s, e, "LowerName", line(text, "LowerName", 0.12, int(unit * 0.064 * sc), 120, 380), layer=3)
            role = _txt(it.get("label", ""))
            if role:
                ev(s, e, "LowerRole", line(role, "LowerRole", 0.58, int(unit * 0.044 * sc), 220, 480), layer=3)
        elif typ == "counter" and text:
            num = parse_number(text)
            cap = _txt(it.get("label", ""))
            # place.y = pusat kelompok angka (+ keterangan bila ada)
            y = int(height * py(0.32) - (unit * 0.10 * sc if cap and pl.get("y") is not None else 0))
            fs = rf"\fs{int(unit * 0.17 * sc)}"
            if not num or not intro:          # lanjutan setelah pindah posisi: angka akhir, tanpa hitung ulang
                ev(s, e, "Counter", rf"{{\pos({cx},{y}){fs}{fin}}}{text if not num else _raw(num[0]) + fmt_number(num[1], num[2], '.' in text and num[2] == 0) + _raw(num[3])}")
            else:
                pre, value, dec, post = num
                thousands = "." in text and dec == 0
                dur = min(1.2, (e - s) * 0.5)
                steps = max(2, int(dur * 20))
                for k in range(steps + 1):
                    a = s + dur * k / steps
                    b = s + dur * (k + 1) / steps if k < steps else e
                    frac = 1 - (1 - k / steps) ** 3           # easing keluar: cepat lalu melambat
                    shown = fmt_number(value * frac, dec, thousands)
                    pop = r"\t(0,160,\fscx118\fscy118)\t(160,300,\fscx100\fscy100)" if k == steps else ""
                    ev(a, b, "Counter", rf"{{\pos({cx},{y}){fs}{pop}\fad({120 if k == 0 else 0},{200 if k == steps else 0})}}"
                       rf"{_raw(pre)}{shown}{_raw(post)}")
            if cap:
                ev(s + (0.15 if intro else 0), e, "Caption", rf"{{\pos({cx},{y + int(unit * 0.14 * sc)})\fs{int(unit * 0.045 * sc)}"
                   rf"\fad(200,200)}}{cap}")
        elif typ == "cta" and text:
            # Tombol aksen yang muncul membal lalu berdenyut pelan.
            y = int(height * py(0.64))
            pulses = "".join(rf"\t({t},{t + 300},\fscx106\fscy106)\t({t + 300},{t + 600},\fscx100\fscy100)"
                             for t in range(700, int((e - s) * 1000) - 300, 1200))
            pop = r"\fscx30\fscy30\t(0,180,\fscx112\fscy112)\t(180,280,\fscx100\fscy100)" if intro else ""
            ev(s, e, "Cta", rf"{{\pos({cx},{y})\fs{int(unit * 0.06 * sc)}{pop}{pulses}{fin}}}{text}")
    return "\n".join(lines) + "\n"


def hook_suggestion(words: list[dict], start: float, end: float, max_words: int = 9) -> Optional[dict]:
    """Kalimat pembuka yang paling memancing dari klip (pertanyaan > angka > kata pemancing > kalimat pertama).
    Aturan sederhana; teksnya selalu bisa diedit pengguna."""
    from .clipper import HOOK_WORDS
    sents, cur = [], []
    for w in words:
        if w["start"] < start or w["end"] > end:
            continue
        cur.append(w)
        if w["word"].endswith((".", "!", "?")) or len(cur) >= 18:
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    if not sents:
        return None
    limit = start + (end - start) * 0.7

    def score(sent):
        txt = " ".join(x["word"] for x in sent)
        low = txt.lower()
        sc = 3 * txt.endswith("?") + 2 * any(c.isdigit() for c in txt)
        sc += 2 * any(h in low.split() for h in HOOK_WORDS) + (3 <= len(sent) <= 12)
        return sc - (sent[0]["start"] > limit) * 5
    best = max(sents, key=score)
    text = " ".join(x["word"] for x in best[:max_words]).strip(" ,")
    if len(best) > max_words:
        text = text.rstrip(".,!?") + "…"
    return {"type": "hook", "text": text, "start": round(start, 2), "end": round(min(end, start + 2.8), 2),
            "label": "", "items": []}
