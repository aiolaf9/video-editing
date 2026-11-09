"""Preset warna: satu tabel untuk render FFmpeg dan pratinjau browser (P1).

`ffmpeg` = filter yang dibakar saat render. `css` = padanan CSS `filter` untuk pratinjau langsung di browser
(murah: dikerjakan GPU perangkat, tanpa membuat ulang MP4). Penyesuaian kecerahan/kontras/saturasi memakai rumus
yang sama di kedua sisi (lihat css_adjust di web/app.js dan eq di app/render.py). Padanan CSS untuk preset berbasis
curves/colorbalance adalah perkiraan yang disetel dengan pengukuran (nilai CSS terbaik dari kisi kandidat terhadap output
FFmpeg); selisihnya diuji di tests/test_p1_browser_editor.py::test_color_preset_parity_css_vs_ffmpeg.
"""

LOOKS = {
    "none": {"label": "Asli", "ffmpeg": [], "css": ""},
    "vivid": {"label": "Cerah", "ffmpeg": ["eq=saturation=1.35:contrast=1.08"],
              "css": "contrast(1.08) saturate(1.35)"},
    "cinematic": {"label": "Sinematik",
                  "ffmpeg": ["curves=preset=medium_contrast",
                             "colorbalance=rs=-0.05:bs=0.08:rh=0.06:bh=-0.04",
                             "eq=saturation=0.9"],
                  "css": "contrast(1.08) saturate(0.95)"},
    "warm": {"label": "Hangat", "ffmpeg": ["colorbalance=rs=0.08:gs=0.02:bs=-0.08:rm=0.05:bm=-0.05"],
             "css": "sepia(0.1) saturate(1.1)"},
    "cool": {"label": "Sejuk", "ffmpeg": ["colorbalance=rs=-0.06:bs=0.08:bm=0.05"],
             "css": "saturate(0.95)"},
    "bw": {"label": "Hitam putih", "ffmpeg": ["hue=s=0", "eq=contrast=1.15"],
           "css": "grayscale(1) contrast(1.1)"},
    "vintage": {"label": "Vintage", "ffmpeg": ["curves=preset=vintage", "eq=saturation=0.85"],
                "css": "sepia(0.35) contrast(0.9) brightness(0.95)"},
}


def public() -> dict:
    return {k: {"label": v["label"], "css": v["css"]} for k, v in LOOKS.items()}
