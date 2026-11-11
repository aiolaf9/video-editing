"""Renderer ASS per `kind` Motion Library (M2.5). Satu renderer dipakai banyak preset; preset hanya data.

Setiap renderer menerima spesifikasi dari app/motionlib.resolve (params + intensitas k) yang sudah berwaktu
timeline output dan (opsional) `place` dari safe-zone, lalu menulis event ASS lewat `ev`. Semua animasi
memakai tag libass (\\move, \\t dengan akselerasi, \\clip, \\fsp, \\blur, gambar vektor \\p) sehingga render
CPU biasa (tanpa GPU) dan pratinjau = MP4 final.
"""
from __future__ import annotations

from .motionlib import EASE_ASS
from .subtitles import ass_color


def _t(text) -> str:
    return str(text or "").replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _rect(w: float, h: float) -> str:
    w, h = int(w), int(h)
    return f"m 0 0 l {w} 0 {w} {h} 0 {h}"


def _circle(r: float) -> str:
    r = int(r)
    c = int(r * 0.5523)
    return (f"m 0 {-r} b {c} {-r} {r} {-c} {r} 0 b {r} {c} {c} {r} 0 {r} b {-c} {r} {-r} {c} {-r} 0 "
            f"b {-r} {-c} {-c} {-r} 0 {-r}")


def render(it: dict, ev, width: int, height: int) -> None:
    p = it["params"]
    kind = it["kind"]
    fn = globals().get(f"_k_{kind}")
    if fn:
        fn(it, p, ev, width, height)


def _common(it, p, width, height, default_y):
    unit = min(width, height)
    pl = it.get("place") or {}
    sc = float(pl.get("scale") or 1.0)
    y = int(height * (pl["y"] if pl.get("y") is not None else default_y))
    sp = max(0.4, float(p.get("speed", 1.0) or 1.0))
    a = EASE_ASS.get(p.get("easing", "out"), 1.0)
    k = float(p.get("k", 1.0))
    acc = ass_color(p.get("accent") or p.get("color") or "#FFD43B")
    font = str(p.get("font") or "Poppins").replace(",", " ")
    return unit, sc, y, sp, a, k, acc, font


def _ms(x: float, sp: float) -> int:
    return int(x * 1000 / sp)


def _k_title_cinematic(it, p, ev, W, H):
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.30)
    s, e = it["start"], it["end"]
    bh = int(H * 0.085 * min(1.2, k))
    ev(s, e, "Shape", rf"{{\move(0,{-bh},0,0,0,{_ms(0.45, sp)})\c&H000000&\fad(0,250)\p1}}{_rect(W, bh)}{{\p0}}", 1)
    ev(s, e, "Shape", rf"{{\move(0,{H},0,{H - bh},0,{_ms(0.45, sp)})\c&H000000&\fad(0,250)\p1}}{_rect(W, bh)}{{\p0}}", 1)
    fs = int(unit * 0.085 * sc)
    sp0, sp1 = int(unit * 0.05 * k), max(1, int(unit * 0.006))
    ev(s, e, "Fx", rf"{{\an5\pos({W // 2},{y})\fn{font}\fs{fs}\b1\fsp{sp0}\bord0\shad2\alpha&HFF&"
                   rf"\t(0,{_ms(0.5, sp)},\alpha&H00&)\t(0,{_ms(1.1, sp)},{a},\fsp{sp1})\fad(0,300)}}{_t(p.get('text')).upper()}", 3)
    if p.get("label"):
        ev(s + 0.35 / sp, e, "Fx", rf"{{\an5\pos({W // 2},{y + int(unit * 0.075 * sc)})\fn{font}\fs{int(unit * 0.042 * sc)}"
                                   rf"\c{acc}\fsp{int(unit * 0.012)}\bord0\shad1\fad({_ms(0.35, sp)},300)}}{_t(p.get('label')).upper()}", 3)


def _k_title_slam(it, p, ev, W, H):
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.30)
    s, e = it["start"], it["end"]
    words = _t(p.get("text")).upper().split()
    lines, cur = [], []
    for w in words:
        cur.append(w)
        if len(" ".join(cur)) >= 9 or len(cur) == 2:
            lines.append(" ".join(cur))
            cur = []
    if cur:
        lines.append(" ".join(cur))
    lines = lines[:4]
    fs = int(unit * 0.12 * sc)
    lh = int(fs * 1.02)
    top = y - lh * (len(lines) - 1) // 2
    dt = 0.28 / sp
    rot = max(1.0, 4 * k)
    for i, ln in enumerate(lines):
        col = rf"\c{acc}" if i == len(lines) - 1 else ""
        ev(s + i * dt, e, "Fx", rf"{{\an5\pos({W // 2},{top + i * lh})\fn{font}\fs{fs}{col}\bord{max(3, unit // 150)}\shad0"
                                rf"\fscx250\fscy250\blur8\alpha&HFF&\t(0,{_ms(0.13, sp)},{a},\fscx100\fscy100\blur0\alpha&H00&)"
                                rf"\t({_ms(0.13, sp)},{_ms(0.17, sp)},\frz{rot:.1f})\t({_ms(0.17, sp)},{_ms(0.21, sp)},\frz{-rot * 0.6:.1f})"
                                rf"\t({_ms(0.21, sp)},{_ms(0.25, sp)},\frz0)\fad(0,250)}}{ln}", 3)


def _k_title_wipe(it, p, ev, W, H):
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.25)
    s, e = it["start"], it["end"]
    text = _t(p.get("text"))
    fs = int(unit * 0.085 * sc)
    tw = min(int(W * 0.92), int(len(text) * fs * 0.55))
    x0, x1 = W // 2 - tw // 2 - 10, W // 2 + tw // 2 + 10
    th = int(fs * 1.25)
    dur = _ms(0.7, sp)
    ev(s, e, "Fx", rf"{{\an5\pos({W // 2},{y})\fn{font}\fs{fs}\b1\bord0\shad2\clip({x0},0,{x0},{H})"
                   rf"\t(0,{dur},{a},\clip({x0},0,{x1},{H}))\fad(0,250)}}{text}", 3)
    bw = int(unit * 0.035)
    ev(s, min(e, s + 1.1 / sp), "Shape", rf"{{\move({x0 - bw},{y - th // 2},{x1},{y - th // 2},0,{dur})\c{acc}"
                                         rf"\t({dur},{dur + 250},\alpha&HFF&)\p1}}{_rect(bw, th)}{{\p0}}", 4)
    ev(s + 0.5 / sp, e, "Shape", rf"{{\pos({x0},{y + th // 2 + 6})\c{acc}\fscx0\t(0,{_ms(0.5, sp)},{a},\fscx100)\fad(0,250)\p1}}"
                                 rf"{_rect(x1 - x0, max(4, unit // 120))}{{\p0}}", 3)


def _k_chapter(it, p, ev, W, H):
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.28)
    s, e = it["start"], it["end"]
    x = int(W * 0.08)
    num = _t(p.get("label") or "01")
    nfs = int(unit * 0.22 * sc)
    ev(s, e, "Fx", rf"{{\an4\move({x - int(W * 0.12)},{y},{x},{y},0,{_ms(0.45, sp)})\fnAnton\fs{nfs}\1a&HFF&\bord{max(3, unit // 200)}"
                   rf"\3c{acc}\shad0\fad(150,250)}}{num}", 3)
    tx = x + int(nfs * 0.62 * max(1, len(num)))
    ev(s + 0.2 / sp, e, "Fx", rf"{{\an4\move({tx + int(W * 0.1)},{y - int(unit * 0.02)},{tx},{y - int(unit * 0.02)},0,{_ms(0.45, sp)})"
                              rf"\fn{font}\fs{int(unit * 0.07 * sc)}\b1\bord0\shad2\fad({_ms(0.3, sp)},250)}}{_t(p.get('text'))}", 3)
    ev(s + 0.4 / sp, e, "Shape", rf"{{\pos({tx},{y + int(unit * 0.035 * sc)})\c{acc}\fscx0\t(0,{_ms(0.6, sp)},{a},\fscx100)\fad(0,250)\p1}}"
                                 rf"{_rect(W - tx - x, max(4, unit // 110))}{{\p0}}", 3)


def _k_follow_bar(it, p, ev, W, H):
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.72)
    s, e = it["start"], it["end"]
    bw, bh = int(W * 0.88), int(unit * 0.13 * sc)
    x0, top = (W - bw) // 2, y - bh // 2
    ev(s, e, "Shape", rf"{{\move({x0},{H},{x0},{top},0,{_ms(0.4, sp)})\c&H18141A&\alpha&H18&\fad(0,250)\p1}}{_rect(bw, bh)}{{\p0}}", 1)
    cx, cy, r = x0 + bh // 2 + 8, y, int(bh * 0.30)
    ev(s + 0.25 / sp, e, "Shape", rf"{{\pos({cx},{cy})\c{acc}\fscx0\fscy0\t(0,{_ms(0.25, sp)},\fscx100\fscy100)\fad(0,250)\p1}}{_circle(r)}{{\p0}}", 3)
    for i in range(int(max(1, (e - s - 0.6) / 1.0))):
        t0 = s + 0.6 + i * 1.0
        ev(t0, min(e, t0 + 0.8), "Shape", rf"{{\pos({cx},{cy})\c{acc}\alpha&H40&\t(0,700,\fscx{int(190 * min(1.3, k))}\fscy{int(190 * min(1.3, k))}"
                                          rf"\alpha&HFF&)\p1}}{_circle(r)}{{\p0}}", 2)
    tx = cx + r + int(unit * 0.035)
    ev(s + 0.3 / sp, e, "Fx", rf"{{\an4\pos({tx},{cy - int(bh * 0.14)})\fn{font}\fs{int(unit * 0.05 * sc)}\b1\bord0\shad0"
                              rf"\alpha&HFF&\t(0,{_ms(0.3, sp)},\alpha&H00&)\fad(0,250)}}{_t(p.get('text'))}", 3)
    if p.get("label"):
        ev(s + 0.45 / sp, e, "Fx", rf"{{\an4\pos({tx},{cy + int(bh * 0.22)})\fn{font}\fs{int(unit * 0.042 * sc)}\c{acc}\bord0\shad0"
                                   rf"\fad({_ms(0.3, sp)},250)}}{_t(p.get('label'))}", 3)


def _k_progress(it, p, ev, W, H):
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.25)
    s, e = it["start"], it["end"]
    val = max(0, min(100, float(p.get("value", 70))))
    tw, th = int(W * 0.80 * min(1.0, sc + 0.1)), max(10, int(unit * 0.038 * sc))
    x0, top = (W - tw) // 2, y
    ev(s, e, "Fx", rf"{{\an1\pos({x0},{top - int(unit * 0.03)})\fn{font}\fs{int(unit * 0.058 * sc)}\b1\bord0\shad2\fad(250,250)}}"
                   rf"{_t(p.get('text'))}", 3)
    ev(s, e, "Shape", rf"{{\pos({x0},{top})\c&HFFFFFF&\alpha&HB0&\fad(250,250)\p1}}{_rect(tw, th)}{{\p0}}", 2)
    grow0, grow1 = _ms(0.3, sp), _ms(0.3 + 1.6, sp)
    ev(s, e, "Shape", rf"{{\pos({x0},{top})\c{acc}\fscx0\t({grow0},{grow1},{a},\fscx{val:.0f})\fad(0,250)\p1}}{_rect(tw, th)}{{\p0}}", 3)
    steps = 24
    for i in range(steps + 1):
        t0 = s + (grow0 + (grow1 - grow0) * i / steps) / 1000
        t1 = s + (grow0 + (grow1 - grow0) * (i + 1) / steps) / 1000 if i < steps else e
        frac = 1 - (1 - i / steps) ** 2 if a < 1 else i / steps
        ev(t0, t1, "Fx", rf"{{\an3\pos({x0 + tw},{top - int(unit * 0.02)})\fnAnton\fs{int(unit * 0.09 * sc)}\c{acc}\bord{max(2, unit // 300)}"
                         rf"\shad0{chr(92) + 'fad(0,250)' if i == steps else ''}}}{val * frac:.0f}%", 3)


def _k_bars(it, p, ev, W, H):
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.30)
    s, e = it["start"], it["end"]
    data = []
    for raw in (p.get("items") or [])[:4]:
        lab, _, num = str(raw).partition(":")
        try:
            data.append((_t(lab), float(num.replace(",", "."))))
        except ValueError:
            continue
    if not data:
        return
    vmax = max(v for _, v in data) or 1.0
    n = len(data)
    area_w, area_h = int(W * 0.76), int(unit * 0.30 * sc)
    bw = int(area_w / n * 0.62)
    gap = (area_w - bw * n) // max(1, n - 1) if n > 1 else 0
    x0 = (W - area_w) // 2
    base = y + area_h // 2
    ev(s, e, "Fx", rf"{{\an5\pos({W // 2},{base - area_h - int(unit * 0.06)})\fn{font}\fs{int(unit * 0.06 * sc)}\b1\bord0\shad2"
                   rf"\fad(250,250)}}{_t(p.get('text'))}", 3)
    for i, (lab, v) in enumerate(data):
        h = max(6, int(area_h * v / vmax))
        bx = x0 + i * (bw + gap)
        d0 = _ms(0.2 + i * 0.25, sp)
        ev(s, e, "Shape", rf"{{\an1\pos({bx},{base})\c{acc}\fscy0\t({d0},{d0 + _ms(0.7, sp)},{a},\fscy100)\fad(0,250)\p1}}"
                          rf"{_rect(bw, h)}{{\p0}}", 3)
        num = f"{v:g}".replace(".", ",")
        ev(s + (d0 + _ms(0.6, sp)) / 1000, e, "Fx", rf"{{\an2\pos({bx + bw // 2},{base - h - 6})\fnAnton\fs{int(unit * 0.07 * sc)}"
                                                     rf"\bord{max(2, unit // 300)}\shad0\fad(200,250)}}{num}", 3)
        ev(s, e, "Fx", rf"{{\an8\pos({bx + bw // 2},{base + 6})\fn{font}\fs{int(unit * 0.05 * sc)}\bord0\shad2\fad(250,250)}}{lab}", 3)


# =============================================================================================== M2.5 tahap 2: judul

def _k_title_typewriter(it, p, ev, W, H):
    """Huruf muncul satu per satu (alpha per karakter berjeda) + kursor berkedip."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.28)
    s, e = it["start"], it["end"]
    text = _t(p.get("text"))
    fs = int(unit * 0.075 * sc)
    step = max(18, int(55 / sp))
    body = "".join(rf"{{\alpha&HFF&\t({i * step},{i * step + 1},\alpha&H00&)}}{ch}" for i, ch in enumerate(text))
    ev(s, e, "Fx", rf"{{\an5\pos({W // 2},{y})\fn{font}\fs{fs}\bord{max(2, unit // 260)}\shad1\fad(0,250)}}" + body, 3)
    end_ms = len(text) * step
    tw = int(len(text) * fs * 0.52)
    cx = W // 2 + tw // 2 + int(fs * 0.15)
    for i in range(int(max(1, (e - s) * 1000 - end_ms) // 500)):
        t0 = s + (end_ms + i * 500) / 1000
        if t0 + 0.25 > e:
            break
        ev(t0, t0 + 0.25, "Shape", rf"{{\an5\pos({cx},{y})\c{acc}\p1}}{_rect(max(4, fs // 9), fs)}{{\p0}}", 4)


def _k_title_split(it, p, ev, W, H):
    """Dua baris meluncur dari sisi berlawanan dan bertemu di tengah; garis aksen di antaranya."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.28)
    s, e = it["start"], it["end"]
    words = _t(p.get("text")).upper().split()
    half = max(1, (len(words) + 1) // 2)
    l1, l2 = " ".join(words[:half]), " ".join(words[half:]) or _t(p.get("label")).upper()
    fs = int(unit * 0.095 * sc)
    d = _ms(0.55, sp)
    off = int(W * 0.7 * min(1.3, k))
    ev(s, e, "Fx", rf"{{\an5\move({W // 2 - off},{y - fs // 2 - 6},{W // 2},{y - fs // 2 - 6},0,{d})\fn{font}\fs{fs}\b1"
                   rf"\bord0\shad2\fad(0,250)}}{l1}", 3)
    ev(s + 0.08 / sp, e, "Fx", rf"{{\an5\move({W // 2 + off},{y + fs // 2 + 6},{W // 2},{y + fs // 2 + 6},0,{d})\fn{font}"
                               rf"\fs{fs}\b1\c{acc}\bord0\shad2\fad(0,250)}}{l2}", 3)
    ev(s + 0.3 / sp, e, "Shape", rf"{{\an5\pos({W // 2},{y})\c{acc}\fscx0\t(0,{_ms(0.4, sp)},{a},\fscx100)\fad(0,250)\p1}}"
                                 rf"{_rect(W * 0.6, max(4, unit // 150))}{{\p0}}", 2)


def _k_title_boxed(it, p, ev, W, H):
    """Kotak warna melebar dari tengah (skala x), lalu teks gelap muncul di dalamnya."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.25)
    s, e = it["start"], it["end"]
    text = _t(p.get("text")).upper()
    fs = int(unit * 0.07 * sc)
    bw, bh = min(int(W * 0.92), int(len(text) * fs * 0.6 + fs)), int(fs * 1.6)
    ev(s, e, "Shape", rf"{{\an5\pos({W // 2},{y})\c{acc}\fscx0\t(0,{_ms(0.35, sp)},{a},\fscx100)\fad(0,250)\p1}}"
                      rf"{_rect(bw, bh)}{{\p0}}", 2)
    ev(s + 0.25 / sp, e, "Fx", rf"{{\an5\pos({W // 2},{y})\fn{font}\fs{fs}\b1\c&H141414&\bord0\shad0\alpha&HFF&"
                               rf"\t(0,{_ms(0.25, sp)},\alpha&H00&)\fad(0,250)}}{text}", 3)
    if p.get("label"):
        ev(s + 0.45 / sp, e, "Fx", rf"{{\an8\pos({W // 2},{y + bh // 2 + 8})\fn{font}\fs{int(fs * 0.55)}\bord0\shad2"
                                   rf"\fad({_ms(0.3, sp)},250)}}{_t(p.get('label'))}", 3)


def _k_title_glitch(it, p, ev, W, H):
    """Salinan merah & biru bergeser acak beberapa kali lalu menyatu (efek glitch RGB)."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.28)
    s, e = it["start"], it["end"]
    text = _t(p.get("text")).upper()
    fs = int(unit * 0.09 * sc)
    base = rf"\an5\fn{font}\fs{fs}\b1\bord0\shad0"
    jit = [(9, -4), (-12, 3), (6, 5), (-5, -6), (3, 2), (0, 0)]
    amp = min(1.6, k)
    dt = 0.07 / sp
    for col, sign in (("&H3030FF&", 1), ("&HFFC030&", -1)):        # merah, biru (BGR)
        for i, (dx, dy) in enumerate(jit):
            t0 = s + i * dt
            t1 = e if i == len(jit) - 1 else t0 + dt
            ev(t0, t1, "Fx", rf"{{{base}\pos({W // 2 + int(sign * dx * amp * unit / 400)},{y + int(sign * dy * amp * unit / 400)})"
                             rf"\c{col}\alpha&H50&{chr(92) + 'fad(0,250)' if t1 == e else ''}}}{text}", 2)
    ev(s + 2 * dt, e, "Fx", rf"{{{base}\pos({W // 2},{y})\fad(0,250)}}{text}", 3)


def _k_title_outline_fill(it, p, ev, W, H):
    """Teks bergaris luar (isi transparan), lalu warna mengisi dari bawah ke atas (clip vertikal)."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.28)
    s, e = it["start"], it["end"]
    text = _t(p.get("text")).upper()
    fs = int(unit * 0.11 * sc)
    bord = max(3, unit // 220)
    ev(s, e, "Fx", rf"{{\an5\pos({W // 2},{y})\fnAnton\fs{fs}\1a&HFF&\bord{bord}\3c&HFFFFFF&\shad0\fad(200,250)}}{text}", 2)
    top, bot = y - fs // 2 - 6, y + fs // 2 + 6
    ev(s + 0.3 / sp, e, "Fx", rf"{{\an5\pos({W // 2},{y})\fnAnton\fs{fs}\c{acc}\bord{bord}\3c&HFFFFFF&\shad0"
                              rf"\clip(0,{bot},{W},{bot})\t(0,{_ms(0.8, sp)},{a},\clip(0,{top},{W},{bot}))\fad(0,250)}}{text}", 3)


def _k_title_marker(it, p, ev, W, H):
    """Teks muncul, lalu stabilo (persegi miring semi-transparan) tergores di belakang kata terakhir."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.28)
    s, e = it["start"], it["end"]
    words = _t(p.get("text")).split()
    if not words:
        return
    fs = int(unit * 0.08 * sc)
    head, last = " ".join(words[:-1]), words[-1]
    full_w = len(" ".join(words)) * fs * 0.52
    x_last = W // 2 - full_w / 2 + (len(head) + 1) * fs * 0.52 if head else W // 2 - full_w / 2
    mw, mh = int(len(last) * fs * 0.55 + fs * 0.3), int(fs * 0.9)
    ev(s + 0.35 / sp, e, "Shape", rf"{{\an4\pos({int(x_last - fs * 0.15)},{y + int(fs * 0.08)})\c{acc}\alpha&H40&\frz2"
                                  rf"\fscx0\t(0,{_ms(0.4, sp)},{a},\fscx100)\fad(0,250)\p1}}{_rect(mw, mh)}{{\p0}}", 2)
    ev(s, e, "Fx", rf"{{\an5\pos({W // 2},{y})\fn{font}\fs{fs}\b1\bord0\shad2\alpha&HFF&\t(0,{_ms(0.3, sp)},\alpha&H00&)"
                   rf"\fad(0,250)}}{' '.join(words)}", 3)


def _k_title_flip(it, p, ev, W, H):
    """Teks berputar masuk pada sumbu X (efek 3D flip libass \\frx), sedikit memantul."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.28)
    s, e = it["start"], it["end"]
    text = _t(p.get("text")).upper()
    fs = int(unit * 0.09 * sc)
    over = -12 * min(1.5, k)
    ev(s, e, "Fx", rf"{{\an5\pos({W // 2},{y})\org({W // 2},{y})\fn{font}\fs{fs}\b1\bord{max(2, unit // 300)}\shad2"
                   rf"\frx90\t(0,{_ms(0.35, sp)},{a},\frx{over:.0f})\t({_ms(0.35, sp)},{_ms(0.5, sp)},\frx0)\fad(0,250)}}{text}", 3)
    if p.get("label"):
        ev(s + 0.4 / sp, e, "Fx", rf"{{\an5\pos({W // 2},{y + int(fs * 0.95)})\fn{font}\fs{int(fs * 0.45)}\c{acc}"
                                  rf"\fsp{int(unit * 0.008)}\bord0\shad1\fad({_ms(0.3, sp)},250)}}{_t(p.get('label')).upper()}", 3)


# =============================================================================================== tahap 2: grafis

import math  # noqa: E402


def _poly(points) -> str:
    pts = [(int(x), int(y)) for x, y in points]
    return f"m {pts[0][0]} {pts[0][1]} l " + " ".join(f"{x} {y}" for x, y in pts[1:])


def _arc_segment(cx, cy, r0, r1, a0, a1, steps=4) -> str:
    """Segmen cincin (sudut derajat, 0 = atas, searah jarum jam) sebagai poligon."""
    def pt(r, ang):
        t = math.radians(ang - 90)
        return cx + r * math.cos(t), cy + r * math.sin(t)
    outer = [pt(r1, a0 + (a1 - a0) * i / steps) for i in range(steps + 1)]
    inner = [pt(r0, a1 - (a1 - a0) * i / steps) for i in range(steps + 1)]
    return _poly(outer + inner)


def _items(p, n=4):
    out = []
    for raw in (p.get("items") or [])[:n]:
        lab, _, num = str(raw).partition(":")
        try:
            out.append((_t(lab), float(num.replace(",", "."))))
        except ValueError:
            out.append((_t(raw), 0.0))
    return out


def _k_ring(it, p, ev, W, H):
    """Cincin progres: 48 segmen menyala berurutan sampai nilai, angka persen di tengah ikut naik."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.30)
    s, e = it["start"], it["end"]
    val = max(0, min(100, float(p.get("value", 70))))
    r1 = int(unit * 0.16 * sc)
    r0 = int(r1 * 0.78)
    cx = W // 2
    n = 48
    ev(s, e, "Shape", rf"{{\an7\pos(0,0)\c&HFFFFFF&\alpha&HC0&\fad(200,250)\p1}}{_arc_segment(cx, y, r0, r1, 0, 359.9, 48)}{{\p0}}", 1)
    lit = int(round(n * val / 100))
    grow = 1.4 / sp
    for i in range(lit):
        t0 = s + 0.2 / sp + grow * i / max(1, lit)
        ev(t0, e, "Shape", rf"{{\an7\pos(0,0)\c{acc}\fad(60,250)\p1}}{_arc_segment(cx, y, r0, r1, i * 360 / n, (i + 1) * 360 / n - 1)}{{\p0}}", 2)
    steps = 20
    for i in range(steps + 1):
        t0 = s + 0.2 / sp + grow * i / steps
        t1 = s + 0.2 / sp + grow * (i + 1) / steps if i < steps else e
        ev(t0, t1, "Fx", rf"{{\an5\pos({cx},{y})\fnAnton\fs{int(r0 * 0.75)}\bord0\shad1}}{val * i / steps:.0f}%", 3)
    if p.get("text"):
        ev(s, e, "Fx", rf"{{\an8\pos({cx},{y + r1 + 10})\fn{font}\fs{int(unit * 0.05 * sc)}\b1\bord0\shad2\fad(250,250)}}"
                       rf"{_t(p.get('text'))}", 3)


def _k_donut(it, p, ev, W, H):
    """Diagram donat 2-4 bagian; tiap bagian tumbuh searah jarum jam bergantian, legenda di bawah."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.30)
    s, e = it["start"], it["end"]
    data = [(lab, v) for lab, v in _items(p) if v > 0]
    if not data:
        return
    tot = sum(v for _, v in data)
    cols = [acc, "&H3BD4FF&", "&H6B3BFF&", "&HE6E15C&"]
    r1 = int(unit * 0.15 * sc)
    r0 = int(r1 * 0.55)
    cx = W // 2 - int(unit * 0.12 * sc)
    ang = 0.0
    for i, (lab, v) in enumerate(data):
        span = 360 * v / tot
        n = max(2, int(span / 8))
        for j in range(n):
            t0 = s + (0.2 + i * 0.45 + 0.4 * j / n) / sp
            a0 = ang + span * j / n
            ev(t0, e, "Shape", rf"{{\an7\pos(0,0)\c{cols[i % 4]}\fad(40,250)\p1}}{_arc_segment(cx, y, r0, r1, a0, a0 + span / n + 0.4, 2)}{{\p0}}", 2)
        lx, ly = cx + r1 + int(unit * 0.06), y - r1 + i * int(unit * 0.07 * sc)
        t0 = s + (0.2 + i * 0.45) / sp
        ev(t0, e, "Shape", rf"{{\an7\pos({lx},{ly})\c{cols[i % 4]}\fad(150,250)\p1}}{_rect(unit * 0.035, unit * 0.035)}{{\p0}}", 2)
        ev(t0, e, "Fx", rf"{{\an7\pos({lx + int(unit * 0.05)},{ly - 4})\fn{font}\fs{int(unit * 0.042 * sc)}\bord0\shad2"
                        rf"\fad(150,250)}}{lab} {100 * v / tot:.0f}%", 3)
        ang += span
    if p.get("text"):
        ev(s, e, "Fx", rf"{{\an2\pos({W // 2},{y - r1 - 12})\fn{font}\fs{int(unit * 0.055 * sc)}\b1\bord0\shad2\fad(250,250)}}"
                       rf"{_t(p.get('text'))}", 3)


def _pin(r: float) -> str:
    """Ikon penanda lokasi (tetes terbalik) dengan titik pusat pada ujung bawah."""
    r = int(r)
    c = int(r * 0.5523)
    return (f"m 0 0 b {-r // 3} {-r} {-r} {-r - c} {-r} {-2 * r} b {-r} {-2 * r - c} {-c} {-3 * r} 0 {-3 * r} "
            f"b {c} {-3 * r} {r} {-2 * r - c} {r} {-2 * r} b {r} {-r - c} {r // 3} {-r} 0 0")


def _k_location(it, p, ev, W, H):
    """Pin lokasi jatuh & memantul, gelombang lingkaran di dasar, label tempat + keterangan meluncur."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.30)
    s, e = it["start"], it["end"]
    r = unit * 0.035 * sc
    px = int(W * 0.18)
    d = _ms(0.35, sp)
    ev(s, e, "Shape", rf"{{\an7\move({px},{y - int(unit * 0.3)},{px},{y},0,{d})\c{acc}\bord0\shad2"
                      rf"\t({d},{d + 90},\fscy85\fscx110)\t({d + 90},{d + 180},\fscy100\fscx100)\fad(0,250)\p1}}{_pin(r)}{{\p0}}", 3)
    ev(s + 0.05 / sp + d / 1000, e, "Shape", rf"{{\an7\pos({px},{y - int(2 * r)})\c&HFFFFFF&\p1}}{_circle(r * 0.4)}{{\p0}}", 4)
    for i in range(3):
        t0 = s + d / 1000 + i * 0.5
        if t0 + 0.6 > e:
            break
        ev(t0, t0 + 0.8, "Shape", rf"{{\an7\pos({px},{y})\c{acc}\alpha&H60&\fscy35\t(0,800,\fscx260\fscy90\alpha&HFF&)\p1}}"
                                  rf"{_circle(r)}{{\p0}}", 1)
    tx = px + int(r * 2)
    ev(s + 0.3 / sp, e, "Fx", rf"{{\an1\move({tx + 60},{y - int(r * 1.2)},{tx},{y - int(r * 1.2)},0,{_ms(0.35, sp)})\fn{font}"
                              rf"\fs{int(unit * 0.065 * sc)}\b1\bord0\shad2\fad({_ms(0.3, sp)},250)}}{_t(p.get('text'))}", 3)
    if p.get("label"):
        ev(s + 0.45 / sp, e, "Fx", rf"{{\an7\pos({tx},{y - int(r * 1.0)})\fn{font}\fs{int(unit * 0.042 * sc)}\c{acc}\bord0"
                                   rf"\shad1\fad({_ms(0.3, sp)},250)}}{_t(p.get('label'))}", 3)


def _k_timeline(it, p, ev, W, H):
    """Garis waktu: garis tumbuh kiri ke kanan, titik tiap tahap muncul saat garis lewat, label di atas/bawah."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.30)
    s, e = it["start"], it["end"]
    steps = [str(x) for x in (p.get("items") or [])][:5] or ["Tahap 1", "Tahap 2", "Tahap 3"]
    x0, x1 = int(W * 0.1), int(W * 0.9)
    lw = x1 - x0
    dur = 1.6 / sp
    ev(s, e, "Shape", rf"{{\an4\pos({x0},{y})\c{acc}\fscx0\t(0,{int(dur * 1000)},{a},\fscx100)\fad(0,250)\p1}}"
                      rf"{_rect(lw, max(5, unit // 120))}{{\p0}}", 2)
    n = len(steps)
    for i, lab in enumerate(steps):
        x = x0 + lw * (i + 0.5) / n
        t0 = s + dur * (i + 0.5) / n
        ev(t0, e, "Shape", rf"{{\an7\pos({int(x)},{y})\c&HFFFFFF&\3c{acc}\bord{max(3, unit // 200)}\fscx0\fscy0"
                           rf"\t(0,180,\fscx120\fscy120)\t(180,260,\fscx100\fscy100)\fad(0,250)\p1}}{_circle(unit * 0.022)}{{\p0}}", 3)
        up = i % 2 == 0
        ly = y - int(unit * 0.05) if up else y + int(unit * 0.05)
        ev(t0 + 0.1, e, "Fx", rf"{{\an{'2' if up else '8'}\pos({int(x)},{ly})\fn{font}\fs{int(unit * 0.04 * sc)}\b1\bord0\shad2"
                              rf"\fad(200,250)}}{_t(lab)}", 3)
    if p.get("text"):
        ev(s, e, "Fx", rf"{{\an2\pos({W // 2},{y - int(unit * 0.14)})\fn{font}\fs{int(unit * 0.055 * sc)}\b1\bord0\shad2"
                       rf"\fad(250,250)}}{_t(p.get('text'))}", 3)


def _k_compare(it, p, ev, W, H):
    """Perbandingan dua kolom (sebelum / sesudah): panel meluncur dari kiri & kanan, nilai membesar, tanda panah."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.30)
    s, e = it["start"], it["end"]
    data = _items(p, 2)
    while len(data) < 2:
        data.append(("", 0.0))
    pw, ph = int(W * 0.38), int(unit * 0.26 * sc)
    for i, (lab, v) in enumerate(data):
        cx = int(W * (0.27 if i == 0 else 0.73))
        sx = cx + (-1 if i == 0 else 1) * int(W * 0.5)
        col = "&H404040&" if i == 0 else acc
        t0 = s + i * 0.35 / sp
        ev(t0, e, "Shape", rf"{{\an5\move({sx},{y},{cx},{y},0,{_ms(0.4, sp)})\c{col}\alpha&H20&\fad(0,250)\p1}}"
                           rf"{_rect(pw, ph)}{{\p0}}", 1)
        num = f"{v:g}".replace(".", ",")
        ev(t0 + 0.35 / sp, e, "Fx", rf"{{\an5\pos({cx},{y - int(ph * 0.08)})\fnAnton\fs{int(ph * 0.42)}\bord0\shad1"
                                    rf"\fscx40\fscy40\t(0,200,\fscx100\fscy100)\fad(0,250)}}{num}", 3)
        ev(t0 + 0.35 / sp, e, "Fx", rf"{{\an5\pos({cx},{y + int(ph * 0.3)})\fn{font}\fs{int(unit * 0.04 * sc)}\bord0\shad0"
                                    rf"\fad(200,250)}}{lab}", 3)
    ev(s + 0.9 / sp, e, "Fx", rf"{{\an5\pos({W // 2},{y})\fnAnton\fs{int(unit * 0.09)}\c{acc}\bord{max(2, unit // 300)}"
                              rf"\fscx0\t(0,200,\fscx100)\fad(0,250)}}→", 4)
    if p.get("text"):
        ev(s, e, "Fx", rf"{{\an2\pos({W // 2},{y - ph // 2 - 12})\fn{font}\fs{int(unit * 0.055 * sc)}\b1\bord0\shad2"
                       rf"\fad(250,250)}}{_t(p.get('text'))}", 3)


def _k_numlist(it, p, ev, W, H):
    """Daftar bernomor: lingkaran angka memantul masuk lalu teks meluncur, berurutan."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.30)
    s, e = it["start"], it["end"]
    rows = [_t(x) for x in (p.get("items") or [])][:5] or ["Poin pertama", "Poin kedua", "Poin ketiga"]
    lh = int(unit * 0.095 * sc)
    top = y - lh * (len(rows) - 1) // 2
    x = int(W * 0.12)
    r = int(lh * 0.36)
    for i, row in enumerate(rows):
        t0 = s + (0.15 + i * 0.4) / sp
        yy = top + i * lh
        ev(t0, e, "Shape", rf"{{\an7\pos({x + r},{yy})\c{acc}\fscx0\fscy0\t(0,160,\fscx115\fscy115)\t(160,240,\fscx100\fscy100)"
                           rf"\fad(0,250)\p1}}{_circle(r)}{{\p0}}", 2)
        ev(t0, e, "Fx", rf"{{\an5\pos({x + r},{yy})\fnAnton\fs{int(r * 1.2)}\c&H141414&\bord0\shad0\alpha&HFF&"
                        rf"\t(120,200,\alpha&H00&)\fad(0,250)}}{i + 1}", 3)
        ev(t0 + 0.12 / sp, e, "Fx", rf"{{\an4\move({x + 2 * r + 60},{yy},{x + 2 * r + 20},{yy},0,{_ms(0.3, sp)})\fn{font}"
                                    rf"\fs{int(lh * 0.42)}\b1\bord0\shad2\fad({_ms(0.25, sp)},250)}}{row}", 3)


def _k_linechart(it, p, ev, W, H):
    """Grafik garis tergambar kiri ke kanan (clip bergerak), titik data muncul saat garis lewat."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.30)
    s, e = it["start"], it["end"]
    data = _items(p, 6)
    if len(data) < 2:
        return
    vmax = max(v for _, v in data) or 1.0
    vmin = min(0.0, min(v for _, v in data))
    x0, x1 = int(W * 0.12), int(W * 0.88)
    ah = int(unit * 0.26 * sc)
    base = y + ah // 2
    pts = [(x0 + (x1 - x0) * i / (len(data) - 1), base - ah * (v - vmin) / (vmax - vmin or 1)) for i, (_, v) in enumerate(data)]
    th = max(3, unit // 150)
    poly = pts + [(x, yy + th) for x, yy in reversed(pts)]
    dur = _ms(1.4, sp)
    ev(s, e, "Shape", rf"{{\an7\pos(0,0)\c&HFFFFFF&\alpha&HC8&\fad(200,250)\p1}}{_rect(1, 1)}{{\p0}}", 0)
    ev(s, e, "Shape", rf"{{\an7\pos(0,{base})\c&HFFFFFF&\alpha&HA0&\fad(200,250)\p1}}m {x0} 0 l {x1} 0 {x1} 2 {x0} 2{{\p0}}", 1)
    ev(s, e, "Shape", rf"{{\an7\pos(0,0)\c{acc}\bord0\clip({x0 - 5},0,{x0 - 5},{H})\t(0,{dur},{a},\clip({x0 - 5},0,{x1 + 10},{H}))"
                      rf"\fad(0,250)\p1}}{_poly(poly)}{{\p0}}", 2)
    for i, ((lab, v), (px, py)) in enumerate(zip(data, pts)):
        t0 = s + (dur * i / (len(data) - 1)) / 1000
        ev(t0, e, "Shape", rf"{{\an7\pos({int(px)},{int(py) + th // 2})\c&HFFFFFF&\3c{acc}\bord{th}\fad(120,250)\p1}}"
                           rf"{_circle(unit * 0.012)}{{\p0}}", 3)
        ev(t0, e, "Fx", rf"{{\an2\pos({int(px)},{int(py) - 10})\fnAnton\fs{int(unit * 0.045 * sc)}\bord{max(2, unit // 300)}"
                        rf"\shad0\fad(150,250)}}{v:g}", 3)
        ev(s, e, "Fx", rf"{{\an8\pos({int(px)},{base + 8})\fn{font}\fs{int(unit * 0.042 * sc)}\bord0\shad2\fad(250,250)}}{lab}", 3)
    if p.get("text"):
        ev(s, e, "Fx", rf"{{\an2\pos({W // 2},{base - ah - int(unit * 0.06)})\fn{font}\fs{int(unit * 0.055 * sc)}\b1\bord0"
                       rf"\shad2\fad(250,250)}}{_t(p.get('text'))}", 3)


def _k_statcard(it, p, ev, W, H):
    """Kartu statistik: kartu kaca terbuka dari bawah (skala y), angka besar berhitung naik, label, garis aksen."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.30)
    s, e = it["start"], it["end"]
    raw = str(p.get("value_text") or p.get("label") or "")
    try:
        target = float(p.get("value", 0))
    except (TypeError, ValueError):
        target = 0.0
    cw, ch = int(W * 0.62), int(unit * 0.3 * sc)
    ev(s, e, "Shape", rf"{{\an5\pos({W // 2},{y})\c&H1E1A16&\alpha&H30&\fscy0\t(0,{_ms(0.3, sp)},{a},\fscy100)\fad(0,250)\p1}}"
                      rf"{_rect(cw, ch)}{{\p0}}", 1)
    ev(s + 0.2 / sp, e, "Shape", rf"{{\an5\pos({W // 2},{y - ch // 2 + 6})\c{acc}\fscx0\t(0,{_ms(0.4, sp)},\fscx100)\fad(0,250)\p1}}"
                                 rf"{_rect(cw, max(6, unit // 110))}{{\p0}}", 2)
    steps = 24
    grow = 1.2 / sp
    for i in range(steps + 1):
        t0 = s + 0.3 / sp + grow * i / steps
        t1 = s + 0.3 / sp + grow * (i + 1) / steps if i < steps else e
        val = target * (1 - (1 - i / steps) ** 3)
        txt = f"{val:,.0f}".replace(",", ".")
        ev(t0, t1, "Fx", rf"{{\an5\pos({W // 2},{y - int(ch * 0.06)})\fnAnton\fs{int(ch * 0.42)}\c{acc}\bord0\shad1}}{txt}{_t(raw)}", 3)
    if p.get("text"):
        ev(s + 0.35 / sp, e, "Fx", rf"{{\an5\pos({W // 2},{y + int(ch * 0.3)})\fn{font}\fs{int(unit * 0.045 * sc)}\bord0\shad0"
                                   rf"\fad(200,250)}}{_t(p.get('text'))}", 3)


# =============================================================================================== tahap 2: cerita & CTA

def _k_quote(it, p, ev, W, H):
    """Kutipan: tanda kutip besar memudar & membesar, kalimat tampil kata demi kata, nama sumber meluncur."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.28)
    s, e = it["start"], it["end"]
    words = _t(p.get("text")).split()
    fs = int(unit * 0.062 * sc)
    ev(s, e, "Fx", rf"{{\an5\pos({int(W * 0.14)},{y - int(fs * 1.6)})\fnAnton\fs{int(fs * 3.2)}\c{acc}\bord0\shad0"
                   rf"\fscx60\fscy60\alpha&HFF&\t(0,{_ms(0.35, sp)},\fscx100\fscy100\alpha&H00&)\fad(0,250)}}“", 2)
    lines, cur = [], []
    for w in words:
        cur.append(w)
        if len(" ".join(cur)) > 22:
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    lines = lines[:4]
    step = max(60, int(160 / sp))
    idx = 0
    for li, ln in enumerate(lines):
        body = ""
        for w in ln:
            body += rf"{{\alpha&HFF&\t({250 + idx * step},{250 + idx * step + 120},\alpha&H00&)}}{w} "
            idx += 1
        ev(s, e, "Fx", rf"{{\an4\pos({int(W * 0.12)},{y + (li - (len(lines) - 1) / 2) * fs * 1.25:.0f})\fn{font}\fs{fs}\i1"
                       rf"\bord0\shad2\fad(0,250)}}{body.strip()}", 3)
    if p.get("label"):
        ty = y + int(len(lines) * fs * 0.7) + int(fs * 0.8)
        ev(s + (250 + idx * step) / 1000, e, "Fx", rf"{{\an4\move({int(W * 0.12) + 60},{ty},{int(W * 0.12)},{ty},0,300)\fn{font}"
                                                    rf"\fs{int(fs * 0.7)}\c{acc}\b1\bord0\shad1\fad(250,250)}}— {_t(p.get('label'))}", 3)


def _k_cta_button(it, p, ev, W, H):
    """Tombol ajakan membesar masuk, kursor (panah) bergerak & 'mengklik' (tombol mengecil-membesar), ikon lonceng."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.70)
    s, e = it["start"], it["end"]
    text = _t(p.get("text")).upper() or "IKUTI"
    fs = int(unit * 0.08 * sc)
    bw, bh = int(len(text) * fs * 0.62 + fs * 2.2), int(fs * 1.9)
    click = _ms(0.9, sp)
    ev(s, e, "Shape", rf"{{\an5\pos({W // 2},{y})\c{acc}\fscx0\fscy0\t(0,{_ms(0.25, sp)},\fscx105\fscy105)"
                      rf"\t({_ms(0.25, sp)},{_ms(0.35, sp)},\fscx100\fscy100)\t({click},{click + 90},\fscx92\fscy92)"
                      rf"\t({click + 90},{click + 200},\fscx100\fscy100)\fad(0,250)\p1}}{_rect(bw, bh)}{{\p0}}", 2)
    ev(s + 0.2 / sp, e, "Fx", rf"{{\an5\pos({W // 2 + int(fs * 0.5)},{y})\fn{font}\fs{fs}\b1\bord0\shad0\t({click - 200},{click - 110},"
                              rf"\fscx92\fscy92)\t({click - 110},{click},\fscx100\fscy100)\fad(150,250)}}{text}", 3)
    bx = W // 2 - bw // 2 + int(fs * 0.9)
    ev(s + 0.2 / sp, e, "Shape", rf"{{\an5\pos({bx},{y})\c&HFFFFFF&\fad(150,250)\t({click + 200},{click + 260},\frz-18)"
                                 rf"\t({click + 260},{click + 320},\frz14)\t({click + 320},{click + 380},\frz0)\p1}}"
                                 rf"{_poly([(0, -fs * 0.45), (fs * 0.32, fs * 0.2), (-fs * 0.32, fs * 0.2)])}{{\p0}}", 4)
    arrow = _poly([(0, 0), (0, fs * 1.1), (fs * 0.3, fs * 0.82), (fs * 0.55, fs * 1.3), (fs * 0.72, fs * 1.22),
                   (fs * 0.48, fs * 0.74), (fs * 0.85, fs * 0.72)])
    ev(s + 0.35 / sp, e, "Shape", rf"{{\an7\move({W // 2 + bw},{y + bh * 2},{W // 2 + bw // 4},{y + bh // 6},0,{click - _ms(0.35, sp)})"
                                  rf"\c&HFFFFFF&\3c&H000000&\bord2\shad1\fad(150,250)\p1}}{arrow}{{\p0}}", 5)


def _k_end_card(it, p, ev, W, H):
    """Kartu penutup: bingkai garis tergambar mengelilingi layar, judul terima kasih & dua baris ajakan."""
    unit, sc, y, sp, a, k, acc, font = _common(it, p, W, H, 0.45)
    s, e = it["start"], it["end"]
    m = int(unit * 0.05)
    th = max(5, unit // 110)
    d = _ms(0.35, sp)
    ev(s, e, "Shape", rf"{{\an7\pos({m},{m})\c{acc}\fscx0\t(0,{d},\fscx100)\fad(0,250)\p1}}{_rect(W - 2 * m, th)}{{\p0}}", 2)
    ev(s + d / 1000, e, "Shape", rf"{{\an9\pos({W - m},{m})\c{acc}\fscy0\t(0,{d},\fscy100)\fad(0,250)\p1}}{_rect(th, H - 2 * m)}{{\p0}}", 2)
    ev(s + 2 * d / 1000, e, "Shape", rf"{{\an3\pos({W - m},{H - m})\c{acc}\fscx0\t(0,{d},\fscx100)\fad(0,250)\p1}}{_rect(W - 2 * m, th)}{{\p0}}", 2)
    ev(s + 3 * d / 1000, e, "Shape", rf"{{\an1\pos({m},{H - m})\c{acc}\fscy0\t(0,{d},\fscy100)\fad(0,250)\p1}}{_rect(th, H - 2 * m)}{{\p0}}", 2)
    fs = int(unit * 0.1 * sc)
    ev(s + 0.2 / sp, e, "Fx", rf"{{\an5\pos({W // 2},{y})\fn{font}\fs{fs}\b1\bord0\shad2\fscx70\fscy70\alpha&HFF&"
                              rf"\t(0,{_ms(0.35, sp)},{a},\fscx100\fscy100\alpha&H00&)\fad(0,250)}}{_t(p.get('text'))}", 3)
    for i, line in enumerate([x for x in [p.get("label"), *(p.get("items") or [])] if x][:2]):
        ev(s + (0.5 + i * 0.25) / sp, e, "Fx", rf"{{\an5\pos({W // 2},{y + int(fs * (1.1 + 0.7 * i))})\fn{font}\fs{int(fs * 0.45)}"
                                               rf"\c{acc if i == 0 else '&HFFFFFF&'}\bord0\shad1\fad(250,250)}}{_t(line)}", 3)


# =============================================================================================== tahap 2: transisi (ASS)

def _k_tr_light_leak(it, p, ev, W, H):
    """Kebocoran cahaya: dua bulatan hangat sangat kabur menyapu diagonal dengan alpha naik-turun."""
    s, e = it["start"], it["end"]
    k = float(p.get("k", 1.0))
    d = int((e - s) * 1000)
    r = int(max(W, H) * 0.45)
    peak = max(0, min(255, int(0x50 - 0x30 * min(1.4, k))))
    for i, col in enumerate((ass_color(p.get("accent") or "#FF9A3D"), "&H6E5CFF&")):
        x0, y0 = (-r, int(H * (0.2 + 0.4 * i)))
        x1, y1 = (W + r, int(H * (0.5 - 0.2 * i)))
        ev(s, e, "Shape", rf"{{\an5\move({x0},{y0},{x1},{y1},0,{d})\c{col}\blur{int(r * 0.35)}\alpha&HFF&"
                          rf"\t(0,{d // 2},\alpha&H{peak:02X}&)\t({d // 2},{d},\alpha&HFF&)\p1}}{_circle(r)}{{\p0}}", 6)


def _k_tr_swipe(it, p, ev, W, H):
    """Sapuan: bidang warna menutup layar dari kiri lalu membuka ke kanan."""
    s, e = it["start"], it["end"]
    d = int((e - s) * 1000)
    col = ass_color(p.get("accent") or "#FFD43B")
    sign = -1 if p.get("direction") == "kiri" else 1
    ev(s, e, "Shape", rf"{{\an7\move({-W * sign},0,{W * sign},0,0,{d})\c{col}\p1}}{_rect(W, H)}{{\p0}}", 6)


def _k_tr_iris(it, p, ev, W, H):
    """Iris: lubang lingkaran (isi transparan, garis tepi hitam sangat tebal) mengecil ke titik tengah lalu membuka."""
    s, e = it["start"], it["end"]
    d = int((e - s) * 1000)
    R = int((W ** 2 + H ** 2) ** 0.5 / 2) + 10
    ev(s, e, "Shape", rf"{{\an7\pos({W // 2},{H // 2})\1a&HFF&\3c&H000000&\bord{R + 40}\shad0\fscx100\fscy100"
                      rf"\t(0,{d // 2},\fscx1\fscy1)\t({d // 2},{d},\fscx100\fscy100)\p1}}{_circle(R)}{{\p0}}", 6)


def _k_tr_blinds(it, p, ev, W, H):
    """Tirai: enam bilah horizontal menutup bergantian (skala y) lalu membuka."""
    s, e = it["start"], it["end"]
    d = int((e - s) * 1000)
    col = ass_color(p.get("accent") or "#141414")
    n = 6
    bh = H / n
    for i in range(n):
        lag = int(d * 0.06 * i)
        half = max(60, d // 2 - lag)
        ev(s, e, "Shape", rf"{{\an4\pos(0,{int(bh * i + bh / 2)})\c{col}\fscy0\t({lag},{lag + half},\fscy105)"
                          rf"\t({d - half},{d},\fscy0)\p1}}m 0 {-int(bh / 2)} l {W} {-int(bh / 2)} {W} {int(bh / 2)} 0 {int(bh / 2)}{{\p0}}", 6)
