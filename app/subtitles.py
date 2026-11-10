"""Pembuat subtitle ASS bergaya kekinian (karaoke, pop, satu kata) untuk dibakar oleh FFmpeg/libass."""
import logging
import os
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("video-editing.subtitles")
MIN_WORD = 0.12          # durasi tampil minimum kata yang digeser (detik)
# Log per kata yang digeser (memuat teks transkrip): hanya untuk uji acceptance, bawaan mati.
TRACE = os.environ.get("SUBTITLE_TRACE") == "1"
LEGACY_STYLES = ("karaoke", "pop", "word", "simple")
# M2.2: preset animasi. Tiap preset memakai teknik libass yang berbeda (lihat _events_m2), bukan sekadar warna.
PRESETS = {
    "karaoke": {"label": "Karaoke", "desc": "Kata yang diucapkan berganti warna", "anim": "karaoke",
                "font": "Anton", "uppercase": True, "words_per_line": 3, "highlight": "#FFE600"},
    "pop": {"label": "Pop", "desc": "Kata aktif membesar & berwarna", "anim": "pop",
            "font": "Poppins", "uppercase": True, "words_per_line": 3, "highlight": "#3DDC97"},
    "word": {"label": "Satu Kata", "desc": "Satu kata besar, muncul dengan hentakan", "anim": "word",
             "font": "Anton", "uppercase": True, "words_per_line": 1, "highlight": "#FFE600", "size": 1.2},
    "simple": {"label": "Sederhana", "desc": "Teks tenang tanpa animasi", "anim": "simple",
               "font": "Poppins", "uppercase": False, "words_per_line": 5, "highlight": "#FFFFFF", "size": 0.8},
    "bounce": {"label": "Bounce", "desc": "Baris memantul masuk, kata aktif berwarna", "anim": "bounce",
               "font": "Poppins", "uppercase": True, "words_per_line": 3, "highlight": "#FF8A3D"},
    "slide": {"label": "Slide", "desc": "Baris meluncur naik sambil muncul", "anim": "slide",
              "font": "Bebas Neue", "uppercase": True, "words_per_line": 3, "highlight": "#5CE1E6", "size": 1.15},
    "zoom": {"label": "Zoom", "desc": "Baris membesar dari kabur menjadi tajam", "anim": "zoom",
             "font": "Anton", "uppercase": True, "words_per_line": 2, "highlight": "#FFD43B", "size": 1.1},
    "fade": {"label": "Fade", "desc": "Kata muncul perlahan satu per satu", "anim": "fade",
             "font": "Poppins", "uppercase": False, "words_per_line": 4, "highlight": "#FFFFFF", "size": 0.9},
    "cinematic": {"label": "Cinematic", "desc": "Huruf renggang menyempit, fade lembut, tanpa sorotan",
                  "anim": "cinematic", "font": "Poppins", "uppercase": True, "words_per_line": 5,
                  "highlight": "#E8C872", "size": 0.62, "outline": 0.0, "shadow": 1.5, "pos_y": 0.80},
    "documentary": {"label": "Dokumenter", "desc": "Kalimat utuh di kotak gelap, seperti liputan",
                    "anim": "documentary", "font": "Poppins", "uppercase": False, "words_per_line": 7,
                    "highlight": "#FFFFFF", "size": 0.68, "box": True, "box_opacity": 0.65, "outline": 0.6,
                    "pos_y": 0.83},
    "storytelling": {"label": "Bercerita", "desc": "Kalimat tampil redup, kata yang diucapkan menyala",
                     "anim": "storytelling", "font": "Poppins", "uppercase": False, "words_per_line": 6,
                     "highlight": "#FFFFFF", "color": "#FFFFFF", "size": 0.78, "pos_y": 0.62},
    "highlight": {"label": "Sorot Kotak", "desc": "Kata aktif diberi kotak warna di belakangnya",
                  "anim": "highlight", "font": "Poppins", "uppercase": True, "words_per_line": 3,
                  "highlight": "#7C3AED", "size": 1.0},
    "neon": {"label": "Neon", "desc": "Huruf berpendar, kata aktif paling terang", "anim": "neon",
             "font": "Bebas Neue", "uppercase": True, "words_per_line": 3, "highlight": "#39FF14",
             "color": "#E6FFF0", "size": 1.15, "outline": 0.5},
    "typewriter": {"label": "Isi Warna", "desc": "Warna mengisi kata dari kiri ke kanan mengikuti ucapan",
                   "anim": "typewriter", "font": "Anton", "uppercase": True, "words_per_line": 3,
                   "highlight": "#FFE600", "color": "#FFFFFF"},
}
STYLES = tuple(PRESETS)
# Kata umum yang tidak pernah disorot sebagai "kata penting".
STOPWORDS = set("""yang dan di ke dari untuk dengan itu ini ada pada adalah juga akan tidak sudah saya kita kami
kalian mereka dia ia atau karena jadi kalau bahwa dalam bisa lebih sangat seperti agar supaya oleh para sebuah satu
the and of to in is it that ya nah lalu kemudian maka namun tetapi tapi sehingga ketika saat apa siapa bagaimana""".split())


@dataclass
class SubtitleOptions:
    style: str = "karaoke"
    font: str = "Arial"
    size: float = 1.0           # pengali ukuran dasar
    color: str = "#FFFFFF"
    highlight: str = "#FFE600"
    outline_color: str = "#000000"
    position: str = "bottom"    # bottom | middle | top
    uppercase: bool = True
    words_per_line: int = 3
    # M2.2 (bawaan = tampilan M1.1, sehingga proyek lama tidak berubah)
    outline: float = 1.0        # pengali tebal outline (0 = tanpa)
    shadow: float = 1.0         # pengali bayangan (0 = tanpa)
    shadow_color: str = "#000000"
    box: bool = False           # latar kotak di belakang teks
    box_color: str = "#000000"
    box_opacity: float = 0.6
    pos_y: Optional[float] = None       # posisi vertikal bebas 0 (atas) .. 1 (bawah); None = `position`
    emphasis: bool = False      # sorot kata penting (istilah kosakata, angka, kata kunci panjang)
    emphasis_color: str = "#FFD43B"
    emphasis_terms: tuple = ()  # istilah kosakata (dari app/vocab) yang dianggap penting
    sections: tuple = ()        # ((mulai, akhir, gaya), ...) di timeline output: gaya animasi per bagian (M2.4)
    zones: tuple = ()           # ((mulai, akhir, y 0..1), ...) di timeline output: posisi khusus per bagian
    #                             (mis. garis sambung papan/wajah pada tata letak Materi + Wajah)


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (format warna ASS)."""
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str, upper: bool) -> str:
    text = text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ").strip()
    return text.upper() if upper else text


def chunk_words(words: list[dict], max_words: int, max_gap: float = 0.6) -> list[list[dict]]:
    """Kelompokkan kata menjadi baris pendek; putus di tanda baca atau jeda panjang."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > max_gap):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w["word"].endswith((".", "!", "?", ",")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def collect_words(transcript: dict, clip_start: float, clip_end: float, speed: float = 1.0) -> list[dict]:
    """Ambil kata di rentang klip dan ubah waktunya ke timeline video hasil."""
    out = []
    for seg in (transcript or {}).get("segments", []):
        if seg["end"] <= clip_start or seg["start"] >= clip_end:
            continue
        words = seg.get("words") or []
        if not words:  # tanpa timestamp per kata: bagi rata durasi segmen
            tokens = seg["text"].split()
            step = (seg["end"] - seg["start"]) / max(len(tokens), 1)
            words = [{"start": seg["start"] + i * step, "end": seg["start"] + (i + 1) * step, "word": t}
                     for i, t in enumerate(tokens)]
        for w in words:
            if w["end"] <= clip_start or w["start"] >= clip_end:
                continue
            s = (max(w["start"], clip_start) - clip_start) / speed
            e = (min(w["end"], clip_end) - clip_start) / speed
            if e > s:
                out.append({"start": s, "end": e, "word": w["word"]})
    # Segmen bersebelahan bisa tumpang tindih (mis. batas potongan transkripsi): urutkan menurut waktu dan geser awal
    # kata agar tidak mendahului akhir kata sebelumnya, supaya baris subtitle tidak bertumpuk. Tidak ada kata yang
    # dibuang: kata yang seluruhnya tertutup kata sebelumnya ditampilkan tepat sesudahnya (minimal MIN_WORD detik);
    # jumlah kata yang digeser dicatat di log.
    out.sort(key=lambda w: (w["start"], w["end"]))
    length = (clip_end - clip_start) / speed
    clean, last_end, moved = [], 0.0, []
    for w in out:
        s, e = max(w["start"], last_end), w["end"]
        if e - s < MIN_WORD:
            e = min(max(e, s + MIN_WORD), length)
            s = min(s, max(0.0, e - MIN_WORD))
        if (s, e) != (w["start"], w["end"]):
            moved.append((w["word"], w["start"], w["end"], s, e))
        clean.append({**w, "start": s, "end": e})
        last_end = max(last_end, e)
    log.info("Subtitle: %d kata di rentang klip sebelum pengurutan, %d sesudah, %d digeser (tidak ada kata yang "
             "dibuang).", len(out), len(clean), len(moved))
    if TRACE:   # rinci per kata hanya untuk uji acceptance (SUBTITLE_TRACE=1): berisi teks transkrip
        for word, s0, e0, s1, e1 in moved[:50]:
            log.info("Subtitle: geser %r %.3f-%.3f -> %.3f-%.3f", word, s0, e0, s1, e1)
    return clean


def important_words(chunk: list[dict], terms: set[str]) -> set[int]:
    """Indeks kata penting dalam satu baris (maks 1): istilah kosakata > angka > kata kunci panjang.
    Aturan sederhana yang bisa dijelaskan; bukan pemahaman makna."""
    best, rank = None, 0
    for i, w in enumerate(chunk):
        raw = w["word"].strip(".,!?;:\"'()").lower()
        if not raw or raw in STOPWORDS:
            continue
        r = 3 if raw in terms else 2 if any(c.isdigit() for c in raw) else 1 if len(raw) >= 7 else 0
        if r > rank or (r == rank and r and best is not None and len(raw) > len(chunk[best]["word"])):
            best, rank = i, r
    return {best} if best is not None and rank else set()


def _alpha(opacity: float) -> int:
    return max(0, min(255, int(round((1 - opacity) * 255))))


def build_ass(transcript: dict, clip_start: float, clip_end: float, width: int, height: int,
              opts: SubtitleOptions, speed: float = 1.0) -> str:
    style = opts.style if opts.style in STYLES else "karaoke"
    words = collect_words(transcript, clip_start, clip_end, speed)
    per_line = 1 if style == "word" else max(1, min(int(opts.words_per_line), 8))

    base = min(width, height) * 0.075 * max(0.4, min(float(opts.size), 2.5))
    if style == "word":
        base *= 1.4
    size = int(round(base))
    outline = max(2, round(size * 0.08)) if opts.outline > 0 else 0
    outline = int(round(outline * opts.outline)) if opts.outline not in (0, 1) else outline
    shadow = int(round(max(1, outline // 2) * opts.shadow)) if opts.shadow > 0 else 0
    align, margin_v = {
        "top": (8, int(height * 0.12)),
        "middle": (5, 0),
    }.get(opts.position, (2, int(height * 0.18)))
    margin_h = int(width * 0.06)

    primary = ass_color(opts.color)
    hl = ass_color(opts.highlight)
    emph = ass_color(opts.emphasis_color)
    font = opts.font.replace(",", " ") or "Arial"
    border_style, out_col, back_col = 1, ass_color(opts.outline_color), ass_color(opts.shadow_color, 0x80)
    if opts.box and style != "highlight":
        # BorderStyle 3: kotak buram berwarna OutlineColour, tebal outline = jarak tepi kotak ke teks.
        border_style, out_col = 3, ass_color(opts.box_color, _alpha(opts.box_opacity))
        outline = max(outline, int(size * 0.22))
        back_col = ass_color(opts.box_color, _alpha(opts.box_opacity))
    spacing = 0

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{size},{primary},&H000000FF,{out_col},{back_col},"
        f"-1,0,0,0,100,100,{spacing},0,{border_style},{outline},{shadow},{align},{margin_h},{margin_h},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def event(start: float, end: float, text: str, layer: int = 0) -> None:
        lines.append(f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}")

    terms = {t.lower() for term in opts.emphasis_terms for t in str(term).split()} if opts.emphasis else set()
    legacy = style in LEGACY_STYLES and opts.pos_y is None
    # Posisi tetap (\an5\pos) untuk preset M2 & posisi bebas: animasi gerak (slide) butuh koordinat absolut.
    cy = int(height * (opts.pos_y if opts.pos_y is not None else
                       {"top": 0.16, "middle": 0.5}.get(opts.position, 0.76)))
    cx = width // 2
    pos = rf"\an5\pos({cx},{cy})"

    # M2.4: gaya animasi per bagian (opts.sections). Kata dikelompokkan per gaya lalu dipotong jadi baris.
    base_style, base_per_line = style, per_line

    def style_at(t: float) -> str:
        return next((z[2] for z in opts.sections if z[0] <= t < z[1] and z[2] in STYLES), base_style)
    chunks, chunk_styles = [], []
    run: list[dict] = []
    run_style = None
    for w in words + [None]:
        st = style_at(w["start"]) if w else None
        if run and (w is None or st != run_style):
            pl = 1 if run_style == "word" else (base_per_line if base_style != "word" else max(1, min(int(opts.words_per_line), 8)))
            for c in chunk_words(run, pl):
                chunks.append(c)
                chunk_styles.append(run_style)
            run = []
        if w:
            run.append(w)
            run_style = st
    default_pos, default_cy = pos, cy
    for ci, chunk in enumerate(chunks):
        style = chunk_styles[ci]
        zone = next((z for z in opts.zones if z[0] <= chunk[0]["start"] < z[1]), None)
        if zone:
            cy = int(height * zone[2])
            pos, legacy = rf"\an5\pos({cx},{cy})", False
        else:
            pos, cy = default_pos, default_cy
            legacy = style in LEGACY_STYLES and opts.pos_y is None
        tokens = [_clean(w["word"], opts.uppercase) for w in chunk]
        imp = important_words(chunk, terms) if opts.emphasis else set()
        base_tok = [rf"{{\c{emph}}}{t}{{\c{primary}}}" if i in imp else t for i, t in enumerate(tokens)]
        chunk_start = chunk[0]["start"]
        next_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        # Tahan teks sedikit setelah kata terakhir, tapi jangan menabrak baris berikutnya.
        chunk_end = chunk[-1]["end"] + 0.25
        if next_start is not None:
            chunk_end = min(chunk_end, next_start)
        chunk_end = max(chunk_end, chunk[-1]["end"])
        lead = "" if legacy else "{" + pos + "}"

        if style in ("simple", "documentary", "cinematic"):
            if style == "documentary":
                lead = "{" + pos + r"\fad(160,120)}"
            elif style == "cinematic":
                lead = "{" + pos + rf"\fsp{max(6, size // 4)}\fad(260,220)\t(0,520,\fsp{max(1, size // 18)})}}"
            event(chunk_start, chunk_end, lead + " ".join(base_tok))
            continue

        if style == "word":
            pop = r"{\fscx70\fscy70\t(0,90,\fscx100\fscy100)}"
            if not legacy:
                pop = "{" + pos + r"\fscx70\fscy70\t(0,90,\fscx100\fscy100)}"
            event(chunk_start, chunk_end, pop + base_tok[0])
            continue

        if style == "typewriter":
            # \kf: warna Secondary (redup) -> Primary disapu kiri ke kanan selama durasi kata.
            parts = []
            for k, w in enumerate(chunk):
                nxt = chunk[k + 1]["start"] if k + 1 < len(chunk) else w["end"]
                dur = max(1, int(round((nxt - (chunk_start if k == 0 else w["start"])) * 100)))
                col = emph if k in imp else hl
                parts.append(rf"{{\kf{dur}\1c{col}}}{tokens[k]}")
            sec = ass_color(opts.color, 0x30)
            event(chunk_start, chunk_end, "{" + pos + rf"\2c{sec}}}" + " ".join(parts))
            continue

        for k, w in enumerate(chunk):
            start = chunk_start if k == 0 else w["start"]
            end = chunk[k + 1]["start"] if k + 1 < len(chunk) else chunk_end
            if end <= start:
                continue
            parts = []
            for i, tok in enumerate(tokens):
                plain = base_tok[i]
                if style == "fade":
                    if i < k:
                        parts.append(plain)
                    elif i == k:
                        parts.append(rf"{{\alpha&HFF&\t(0,180,\alpha&H00&)}}{plain}{{\alpha&H00&}}")
                    else:
                        parts.append(rf"{{\alpha&HFF&}}{plain}{{\alpha&H00&}}")
                    continue
                if style == "storytelling":
                    if i < k:
                        parts.append(plain)
                    elif i == k:
                        parts.append(rf"{{\fscx106\fscy106}}{plain}{{\fscx100\fscy100}}")
                    else:
                        parts.append(rf"{{\alpha&H99&}}{tok}{{\alpha&H00&}}")
                    continue
                if i != k:
                    parts.append(plain)
                elif style == "pop":
                    parts.append(rf"{{\c{hl}\fscx118\fscy118}}{tok}{{\c{primary}\fscx100\fscy100}}")
                elif style == "highlight":
                    pad = max(4, int(size * 0.26))
                    parts.append(rf"{{\c&H00FFFFFF&\3c{hl}\bord{pad}\shad0}}{tok}"
                                 rf"{{\c{primary}\3c{ass_color(opts.outline_color)}\bord{outline}\shad{shadow}}}")
                elif style == "neon":
                    parts.append(rf"{{\c&H00FFFFFF&\3c{hl}}}{tok}{{\c{primary}\3c{hl}}}")
                else:   # karaoke, bounce, slide, zoom
                    parts.append(rf"{{\c{hl}}}{tok}{{\c{primary}}}")
            text = " ".join(parts)
            if legacy:
                event(start, end, text)
                continue
            fx = pos
            if k == 0 and style == "bounce":
                fx += r"\fscx70\fscy70\t(0,110,\fscx114\fscy114)\t(110,210,\fscx95\fscy95)\t(210,280,\fscx100\fscy100)"
            elif k == 0 and style == "slide":
                fx = rf"\an5\move({cx},{cy + int(height * 0.05)},{cx},{cy},0,200)\fad(180,0)"
            elif k == 0 and style == "zoom":
                fx += r"\fscx155\fscy155\blur8\alpha&H60&\t(0,220,\fscx100\fscy100\blur0\alpha&H00&)"
            elif style == "neon":
                glow = rf"{{{pos}\bord{max(3, size // 7)}\blur{max(4, size // 9)}\3c{hl}\1a&HFF&\shad0}}"
                event(start, end, glow + " ".join(tokens), layer=0)
                fx += rf"\3c{hl}\bord{max(1, size // 30)}\shad0"
                event(start, end, "{" + fx + "}" + text, layer=1)
                continue
            elif style == "fade" and k == 0:
                fx += r"\fad(120,0)"
            event(start, end, "{" + fx + "}" + text)

    return "\n".join(lines) + "\n"


def public_presets() -> list[dict]:
    """Daftar preset untuk galeri editor."""
    keys = ("font", "uppercase", "words_per_line", "highlight", "color", "size", "outline", "shadow", "box",
            "box_opacity", "pos_y")
    return [{"id": k, "label": v["label"], "desc": v["desc"], "defaults": {x: v[x] for x in keys if x in v}}
            for k, v in PRESETS.items()]
