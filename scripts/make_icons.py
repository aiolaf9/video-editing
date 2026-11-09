"""Ikon PWA AI Video Studio (P2), digambar ulang secara deterministik dengan Pillow:

    python scripts/make_icons.py      # menulis web/icons/*.png

- icon-192.png, icon-512.png : ikon biasa (sudut membulat, transparan di luar)
- maskable-512.png           : ikon "maskable" Android (latar penuh, simbol di zona aman 80%)
- apple-touch-icon.png       : 180x180 untuk iPhone/iPad (tanpa transparansi)
- favicon-32.png             : favicon
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"
BG = (15, 17, 21)           # --bg (#0f1115)
ACCENT = (255, 59, 107)     # --accent
ACCENT2 = (255, 212, 59)    # --accent-2


def draw(size: int, safe: float, rounded: bool, transparent: bool) -> Image.Image:
    scale = 4                                   # gambar besar lalu perkecil: tepi halus
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0) if transparent else BG + (255,))
    d = ImageDraw.Draw(img)
    if transparent:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * (0.22 if rounded else 0)), fill=BG + (255,))
    # Simbol: bingkai klip vertikal 9:16 (kuning) dengan tombol putar (merah muda) di tengah.
    box = s * safe
    cx = cy = s / 2
    fw, fh = box * 0.5, box * 0.86
    d.rounded_rectangle([cx - fw / 2, cy - fh / 2, cx + fw / 2, cy + fh / 2], radius=int(box * 0.07),
                        outline=ACCENT2 + (255,), width=max(2, int(box * 0.055)))
    r = box * 0.17
    d.polygon([(cx - r * 0.75, cy - r), (cx - r * 0.75, cy + r), (cx + r * 1.05, cy)], fill=ACCENT + (255,))
    # Tiga garis "subtitle" di bawah tombol putar.
    for k, w in enumerate((0.34, 0.24)):
        y = cy + fh * 0.26 + k * box * 0.07
        d.rounded_rectangle([cx - box * w / 2, y, cx + box * w / 2, y + box * 0.035], radius=int(box * 0.02),
                            fill=(255, 255, 255, 235))
    out = img.resize((size, size), Image.LANCZOS)
    return out if transparent else out.convert("RGB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192, 0.9, True, True).save(OUT / "icon-192.png", optimize=True)
    draw(512, 0.9, True, True).save(OUT / "icon-512.png", optimize=True)
    draw(512, 0.72, False, False).save(OUT / "maskable-512.png", optimize=True)
    draw(180, 0.82, False, False).save(OUT / "apple-touch-icon.png", optimize=True)
    draw(32, 1.0, True, True).save(OUT / "favicon-32.png", optimize=True)


if __name__ == "__main__":
    main()
