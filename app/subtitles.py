"""Pembuat subtitle ASS bergaya kekinian (karaoke, pop, satu kata) untuk dibakar oleh FFmpeg/libass."""
import logging
import re
import os
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("video-editing.subtitles")
MIN_WORD = 0.12          # durasi tampil minimum kata yang digeser (detik)
# Log per kata yang digeser (memuat teks transkrip): hanya untuk uji acceptance, bawaan mati.
TRACE = os.environ.get("SUBTITLE_TRACE") == "1"
LEGACY_STYLES = ("karaoke", "pop", "word", "simple")
RENDER_VERSION = "m2-1"     # naikkan bila tampilan build_ass berubah: klip galeri yang tersimpan dibuat ulang
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
    # M2.5 Motion Library
    "wordbounce": {"label": "Kata Memantul", "desc": "Satu kata besar meluncur dari bawah, memantul, miring bergantian",
                   "anim": "wordbounce", "font": "Anton", "uppercase": True, "words_per_line": 1,
                   "highlight": "#FFFFFF", "size": 1.3},
    "letterpop": {"label": "Huruf Meletup", "desc": "Huruf muncul satu per satu mengikuti ucapan, tiap huruf meletup",
                  "anim": "letterpop", "font": "Poppins", "uppercase": True, "words_per_line": 3,
                  "highlight": "#FFD43B", "size": 1.0},
    "typewriter": {"label": "Isi Warna", "desc": "Warna mengisi kata dari kiri ke kanan mengikuti ucapan",
                   "anim": "typewriter", "font": "Anton", "uppercase": True, "words_per_line": 3,
                   "highlight": "#FFE600", "color": "#FFFFFF"},
}
STYLES = tuple(PRESETS)
ONE_WORD = ("word", "wordbounce")
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
    suppress: tuple = ()        # teks besar (hook/kinetic) yang ekuivalen dengan ucapan: lihat _duplicates_big_text
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
    per_line = 1 if style in ONE_WORD else max(1, min(int(opts.words_per_line), 8))

    base = min(width, height) * 0.075 * max(0.4, min(float(opts.size), 2.5))
    if style in ONE_WORD:
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
    hl = base_hl = ass_color(opts.highlight)
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
        # M2.5: bagian ber-preset caption "kotak" (mis. Kotak Liputan) memakai gaya ini (BorderStyle 3 per gaya)
        f"Style: Boxed,{font},{size},{primary},&H000000FF,{ass_color('#000000', _alpha(0.65))},"
        f"{ass_color('#000000', _alpha(0.65))},-1,0,0,0,100,100,0,0,3,{max(outline, int(size * 0.22))},0,{align},"
        f"{margin_h},{margin_h},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    cur_style = ["Default"]

    def event(start: float, end: float, text: str, layer: int = 0) -> None:
        lines.append(f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{cur_style[0]},,0,0,0,,{text}")

    terms = {t.lower() for term in opts.emphasis_terms for t in str(term).split()} if opts.emphasis else set()
    legacy = style in LEGACY_STYLES and opts.pos_y is None
    # Posisi tetap (\an5\pos) untuk preset M2 & posisi bebas: animasi gerak (slide) butuh koordinat absolut.
    cy = int(height * (opts.pos_y if opts.pos_y is not None else
                       {"top": 0.16, "middle": 0.5}.get(opts.position, 0.76)))
    cx = width // 2
    pos = rf"\an5\pos({cx},{cy})"

    # M2.4: gaya animasi per bagian (opts.sections). Kata dikelompokkan per gaya lalu dipotong jadi baris.
    base_style, base_per_line = style, per_line

    # M2.5: bagian boleh membawa tampilan preset caption Motion Library (z[3]: highlight, font, size, huruf besar,
    # kata per baris, intensitas k) -> rentang waktu berbeda bisa memakai preset caption berbeda.
    def section_at(t: float):
        return next((z for z in opts.sections if z[0] <= t < z[1] and z[2] in STYLES), None)
    chunks, chunk_styles, chunk_extra = [], [], []
    run: list[dict] = []
    run_key = run_sec = None
    for w in words + [None]:
        sec = section_at(w["start"]) if w else None
        key = (sec[2] if sec else base_style, id(sec) if sec and len(sec) > 3 else 0) if w else None
        if run and (w is None or key != run_key):
            run_style = run_key[0]
            extra = run_sec[3] if run_sec and len(run_sec) > 3 else {}
            if run_style in ONE_WORD:
                pl = 1
            elif extra.get("words_per_line"):
                pl = max(1, min(int(extra["words_per_line"]), 8))
            else:
                pl = base_per_line if base_style not in ONE_WORD else max(1, min(int(opts.words_per_line), 8))
            for c in chunk_words(run, pl):
                chunks.append(c)
                chunk_styles.append(run_style)
                chunk_extra.append(extra)
            run = []
        if w:
            run.append(w)
            run_key, run_sec = key, sec
    default_pos, default_cy = pos, cy
    skipped = 0
    for ci, chunk in enumerate(chunks):
        if _duplicates_big_text(chunk, opts.suppress):
            skipped += 1
            continue
        style = chunk_styles[ci]
        extra = chunk_extra[ci]
        cur_style[0] = "Boxed" if extra.get("box") else "Default"
        zone = next((z for z in opts.zones if z[0] <= chunk[0]["start"] < z[1]), None)
        if zone:
            cy = int(height * zone[2])
            pos, legacy = rf"\an5\pos({cx},{cy})", False
        else:
            pos, cy = default_pos, default_cy
            legacy = style in LEGACY_STYLES and opts.pos_y is None
        hl = ass_color(extra["highlight"]) if extra.get("highlight") else base_hl
        if extra:
            legacy = False
            if not zone and extra.get("pos_y") is not None:
                cy = int(height * float(extra["pos_y"]))
            tags = rf"\an5\pos({cx},{cy})"
            if extra.get("font"):
                tags += rf"\fn{str(extra['font']).replace(',', ' ')}"
            if extra.get("size"):
                tags += rf"\fs{int(round(size * float(extra['size']) / max(0.4, float(opts.size))))}"
            pos = tags
        upper = extra.get("uppercase", opts.uppercase) if extra else opts.uppercase
        tokens = [_clean(w["word"], upper) for w in chunk]
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

        if style == "wordbounce":
            # M2.5: kata tunggal meluncur dari bawah, melonjak (overshoot), kembali, miring bergantian
            k = float(extra.get("k", 1.0)) if extra else 1.0
            sp = max(0.4, float(extra.get("speed", 1.0))) if extra else 1.0
            rot = (1 if ci % 2 else -1) * min(8.0, 3.0 * k)
            dy = int(height * 0.045 * min(1.6, k))
            m1, m2, m3 = int(120 / sp), int(200 / sp), int(260 / sp)
            col = rf"\c{hl}" if extra.get("highlight") or opts.highlight != "#FFFFFF" else ""
            fx = (rf"\an5\move({cx},{cy + dy},{cx},{cy},0,{m1})\fscx80\fscy120\frz{rot:.1f}"
                  rf"\t(0,{m1},\fscx112\fscy92)\t({m1},{m2},\fscx96\fscy104)\t({m2},{m3},\fscx100\fscy100){col}")
            extra_tags = pos.split(")", 1)[1] if ")" in pos else ""
            event(chunk_start, chunk_end, "{" + fx + extra_tags + "}" + base_tok[0])
            continue

        if style == "letterpop":
            # M2.5: tiap huruf muncul (alpha) + meletup (skala) tepat saat katanya diucapkan, merata sepanjang kata
            parts = []
            for kk, w in enumerate(chunk):
                w0 = int(round((w["start"] - chunk_start) * 1000))
                wd = max(80, int(round((w["end"] - w["start"]) * 1000)))
                tok = tokens[kk]
                n = max(1, len(tok))
                col = rf"\c{emph}" if kk in imp else ""
                chars = []
                for j, ch in enumerate(tok):
                    t = w0 + int(wd * 0.7 * j / n)
                    chars.append(rf"{{\alpha&HFF&\fscx40\fscy40{col}\t({t},{t + 1},\alpha&H00&)"
                                 rf"\t({t},{t + 90},\fscx125\fscy125)\t({t + 90},{t + 160},\fscx100\fscy100)}}{ch}")
                parts.append("".join(chars))
            event(chunk_start, chunk_end, "{" + pos + "}" + " ".join(parts))
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

    if skipped:
        log.info("Subtitle: %d baris tidak ditampilkan karena isinya sudah tampil sebagai teks pembuka/kinetic.", skipped)
    return "\n".join(lines) + "\n"


def norm_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", (text or "").lower()) if t]


def _contiguous_in(toks: list[str], seq: tuple) -> bool:
    n = len(toks)
    return any(list(seq[i:i + n]) == toks for i in range(len(seq) - n + 1))


def spoken_span(big_tokens: tuple, words: list[dict], t0: float, t1: float) -> Optional[tuple[float, float]]:
    """Rentang waktu (timeline output) saat teks besar itu BENAR-BENAR diucapkan: deret kata transkrip terpanjang
    di sekitar waktu tampilnya yang sama persis & berurutan dengan kata-kata teks besar. None bila tidak ada deret
    yang cukup panjang (min. 3 kata, atau seluruh teks bila lebih pendek) -> tidak ada subtitle yang disembunyikan."""
    if not big_tokens:
        return None
    near = [w for w in words if w["end"] > t0 - 1.0 and w["start"] < t1 + 1.0]
    toks = [(norm_tokens(w["word"]) or [""])[0] for w in near]
    best = (0, None)
    for i in range(len(near)):
        for j in range(len(big_tokens)):
            k = 0
            while i + k < len(near) and j + k < len(big_tokens) and toks[i + k] == big_tokens[j + k]:
                k += 1
            if k > best[0]:
                best = (k, (near[i]["start"], near[i + k - 1]["end"]))
    return best[1] if best[0] >= min(3, len(big_tokens)) else None


def _duplicates_big_text(chunk: list[dict], windows: tuple) -> bool:
    """Baris subtitle disembunyikan HANYA bila ekuivalen dengan teks besar (hook/kinetic) yang sedang tampil:
    semua katanya sama persis & berurutan dengan teks besar, DAN kata-kata itu diucapkan di dalam rentang waktu
    kalimat teks besar tersebut (spoken_span), DAN baris itu muncul saat teks besar masih tampil.
    windows = ((span_awal, span_akhir, token_teks_besar, tampil_awal, tampil_akhir), ...)."""
    if not windows:
        return False
    s, e = chunk[0]["start"], chunk[-1]["end"]
    toks = norm_tokens(" ".join(w["word"] for w in chunk))
    if not toks:
        return False
    for a, b, seq, t0, t1 in windows:
        if s >= a - 0.05 and e <= b + 0.05 and s < t1 and e > t0 and _contiguous_in(toks, seq):
            return True
    return False


def public_presets() -> list[dict]:
    """Daftar preset untuk galeri editor."""
    keys = ("font", "uppercase", "words_per_line", "highlight", "color", "size", "outline", "shadow", "box",
            "box_opacity", "pos_y")
    return [{"id": k, "label": v["label"], "desc": v["desc"], "defaults": {x: v[x] for x in keys if x in v}}
            for k, v in PRESETS.items()]
