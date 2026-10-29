"""Menggambar elemen animasi (kartu, kata kunci, chips, checklist) sebagai PNG transparan.

Setiap PNG lalu ditumpuk FFmpeg di atas video dengan animasi fade + geser (lihat render.py).
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import FONTS_DIR

ANIM_IN = 0.25     # durasi animasi masuk (detik)
STAGGER = 0.18     # jeda antar chip/poin checklist


@dataclass
class Layer:
    """Satu PNG yang ditumpuk: posisi (x, y) di kanvas output dan waktunya di timeline output."""
    path: Path
    x: int
    y: int
    start: float
    end: float
    slide: int = 30   # jarak geser saat masuk (px, dari bawah)


@lru_cache(maxsize=32)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / name
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def _rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha


def _text_size(draw: ImageDraw.ImageDraw, text: str, fnt) -> tuple[int, int, int]:
    l, t, r, b = draw.textbbox((0, 0), text, font=fnt)
    return r - l, b - t, t


def _wrap(draw, text: str, fnt, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and _text_size(draw, trial, fnt)[0] > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines[:3]


def _shadowed(img: Image.Image, radius: int, alpha: int = 110) -> Image.Image:
    """Tambahkan bayangan lembut di belakang gambar (butuh padding transparan di sekeliling)."""
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    mask = img.split()[3].point(lambda a: min(alpha, a))
    shadow.putalpha(mask)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius))
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.alpha_composite(shadow, (0, radius // 2))
    out.alpha_composite(img)
    return out


def _pill(text: str, fnt, pad_x: int, pad_y: int, fill, color, icon: str = "") -> Image.Image:
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw, th, top = _text_size(probe, text, fnt)
    icon_w = int(th * 1.3) if icon else 0
    w, h = tw + pad_x * 2 + icon_w, th + pad_y * 2
    margin = pad_y
    img = Image.new("RGBA", (w + margin * 2, h + margin * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((margin, margin, margin + w, margin + h), radius=h // 2, fill=fill)
    x = margin + pad_x
    if icon == "check":
        r = th // 2
        cx, cy = x + r, margin + h // 2
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_rgba("#3DDC97"))
        d.line([(cx - r * 0.45, cy), (cx - r * 0.1, cy + r * 0.4), (cx + r * 0.5, cy - r * 0.35)],
               fill=(15, 15, 15, 255), width=max(2, r // 3), joint="curve")
        x += icon_w
    d.text((x, margin + pad_y - top), text, font=fnt, fill=color)
    return _shadowed(img, margin // 2 + 2)


def render_item(item: dict, out_dir: Path, canvas: tuple[int, int], t0: float, t1: float,
                accent: str = "#FF3B6B") -> list[Layer]:
    """Buat PNG untuk satu elemen. t0/t1 sudah dalam timeline output."""
    W, H = canvas
    unit = min(W, H)
    typ = item["type"]
    layers: list[Layer] = []
    base = out_dir / f"ov_{item['id']}"

    if typ == "keyword":
        fnt = font("Anton-Regular.ttf", int(unit * 0.13))
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        lines = _wrap(probe, item["text"].upper(), fnt, int(W * 0.86))
        sizes = [_text_size(probe, ln, fnt) for ln in lines]
        line_h = int(unit * 0.15)
        pad = int(unit * 0.04)
        w = max(s[0] for s in sizes) + pad * 2
        h = line_h * len(lines) + pad * 2
        # Gradasi warna aksen -> kuning, dipotong oleh bentuk huruf.
        mask = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(mask)
        for i, (ln, (tw, _, top)) in enumerate(zip(lines, sizes)):
            md.text(((w - tw) // 2, pad + i * line_h - top), ln, font=fnt, fill=255)
        grad = Image.new("RGBA", (w, h))
        a, b = _rgba(accent), _rgba("#FFD43B")
        gd = ImageDraw.Draw(grad)
        for x in range(w):
            k = x / max(1, w - 1)
            gd.line([(x, 0), (x, h)], fill=tuple(int(a[c] + (b[c] - a[c]) * k) for c in range(4)))
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        stroke = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(stroke)
        for i, (ln, (tw, _, top)) in enumerate(zip(lines, sizes)):
            sd.text(((w - tw) // 2, pad + i * line_h - top), ln, font=fnt, fill=(0, 0, 0, 255),
                    stroke_width=max(3, unit // 180), stroke_fill=(0, 0, 0, 255))
        img.alpha_composite(stroke)
        img.paste(grad, (0, 0), mask)
        img = _shadowed(img, pad // 2)
        path = base.with_suffix(".png")
        img.save(path)
        layers.append(Layer(path, (W - w) // 2, int(H * 0.30) - h // 2, t0, t1, slide=40))

    elif typ == "card":
        pad = int(unit * 0.045)
        label_f = font("Poppins-SemiBold.ttf", int(unit * 0.034))
        title_f = font("Poppins-Bold.ttf", int(unit * 0.058))
        max_w = int(W * 0.84)
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        title_lines = _wrap(probe, item["text"], title_f, max_w - pad * 2)
        lw, lh, ltop = _text_size(probe, item.get("label") or " ", label_f)
        tsizes = [_text_size(probe, ln, title_f) for ln in title_lines]
        title_lh = int(unit * 0.072)
        inner_w = max([lw] + [s[0] for s in tsizes])
        w = min(max_w, inner_w + pad * 2)
        h = pad * 2 + (lh + int(unit * 0.018) if item.get("label") else 0) + title_lh * len(title_lines)
        m = pad
        img = Image.new("RGBA", (w + m * 2, h + m * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((m, m, m + w, m + h), radius=int(unit * 0.035), fill=(22, 24, 30, 215),
                            outline=(255, 255, 255, 60), width=2)
        y = m + pad
        if item.get("label"):
            d.rounded_rectangle((m + pad, y + lh // 2 - 3, m + pad + int(unit * 0.012), y + lh // 2 + 3 + 6),
                                radius=4, fill=_rgba(accent))
            d.text((m + pad + int(unit * 0.025), y - ltop), item["label"], font=label_f, fill=_rgba(accent))
            y += lh + int(unit * 0.018)
        for ln, (_, _, top) in zip(title_lines, tsizes):
            d.text((m + pad, y - top), ln, font=title_f, fill=(255, 255, 255, 255))
            y += title_lh
        img = _shadowed(img, m // 2)
        path = base.with_suffix(".png")
        img.save(path)
        layers.append(Layer(path, (W - img.width) // 2, int(H * 0.10), t0, t1))

    elif typ in ("chips", "checklist"):
        check = typ == "checklist"
        fnt = font("Poppins-SemiBold.ttf", int(unit * (0.040 if check else 0.042)))
        pad_x, pad_y = int(unit * 0.035), int(unit * 0.018)
        pills = []
        for i, text in enumerate(item["items"]):
            img = _pill(text, fnt, pad_x, pad_y,
                        fill=(22, 24, 30, 220) if check else (255, 255, 255, 235),
                        color=(255, 255, 255, 255) if check else (20, 20, 24, 255),
                        icon="check" if check else "")
            p = out_dir / f"ov_{item['id']}_{i}.png"
            img.save(p)
            pills.append((p, img.width, img.height))
        gap = int(unit * 0.012)
        if check:
            # Satu poin per baris, rata kiri, mulai dari atas.
            x0 = int(W * 0.08)
            y = int(H * 0.12)
            for i, (p, w, h) in enumerate(pills):
                layers.append(Layer(p, x0, y, t0 + i * STAGGER * 2, t1, slide=20))
                y += h - pad_y + gap
        else:
            # Chips dibungkus ke beberapa baris, rata tengah.
            rows, cur, cur_w = [], [], 0
            max_w = int(W * 0.9)
            for pill in pills:
                if cur and cur_w + pill[1] > max_w:
                    rows.append(cur)
                    cur, cur_w = [], 0
                cur.append(pill)
                cur_w += pill[1]
            if cur:
                rows.append(cur)
            y = int(H * 0.14)
            k = 0
            for row in rows:
                x = (W - sum(p[1] for p in row)) // 2
                for p, w, h in row:
                    layers.append(Layer(p, x, y, t0 + k * STAGGER, t1, slide=20))
                    x += w
                    k += 1
                y += row[0][2] - pad_y
    return layers
