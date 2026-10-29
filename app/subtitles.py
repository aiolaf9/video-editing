"""Pembuat subtitle ASS bergaya kekinian (karaoke, pop, satu kata) untuk dibakar oleh FFmpeg/libass."""
from dataclasses import dataclass

STYLES = ("karaoke", "pop", "word", "simple")


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
    return out


def build_ass(transcript: dict, clip_start: float, clip_end: float, width: int, height: int,
              opts: SubtitleOptions, speed: float = 1.0) -> str:
    style = opts.style if opts.style in STYLES else "karaoke"
    words = collect_words(transcript, clip_start, clip_end, speed)
    per_line = 1 if style == "word" else max(1, min(int(opts.words_per_line), 8))

    base = min(width, height) * 0.075 * max(0.4, min(float(opts.size), 2.5))
    if style == "word":
        base *= 1.4
    size = int(round(base))
    outline = max(2, round(size * 0.08))
    align, margin_v = {
        "top": (8, int(height * 0.12)),
        "middle": (5, 0),
    }.get(opts.position, (2, int(height * 0.18)))
    margin_h = int(width * 0.06)

    primary = ass_color(opts.color)
    hl = ass_color(opts.highlight)
    font = opts.font.replace(",", " ") or "Arial"

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
        f"Style: Default,{font},{size},{primary},&H000000FF,{ass_color(opts.outline_color)},&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},{max(1, outline // 2)},{align},{margin_h},{margin_h},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def event(start: float, end: float, text: str) -> None:
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}")

    chunks = chunk_words(words, per_line)
    for ci, chunk in enumerate(chunks):
        tokens = [_clean(w["word"], opts.uppercase) for w in chunk]
        chunk_start = chunk[0]["start"]
        next_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        # Tahan teks sedikit setelah kata terakhir, tapi jangan menabrak baris berikutnya.
        chunk_end = chunk[-1]["end"] + 0.25
        if next_start is not None:
            chunk_end = min(chunk_end, next_start)
        chunk_end = max(chunk_end, chunk[-1]["end"])

        if style == "simple":
            event(chunk_start, chunk_end, " ".join(tokens))
            continue

        if style == "word":
            pop = r"{\fscx70\fscy70\t(0,90,\fscx100\fscy100)}"
            event(chunk_start, chunk_end, pop + tokens[0])
            continue

        for k, w in enumerate(chunk):
            start = chunk_start if k == 0 else w["start"]
            end = chunk[k + 1]["start"] if k + 1 < len(chunk) else chunk_end
            if end <= start:
                continue
            parts = []
            for i, tok in enumerate(tokens):
                if i == k:
                    if style == "pop":
                        parts.append(rf"{{\c{hl}\fscx118\fscy118}}{tok}{{\c{primary}\fscx100\fscy100}}")
                    else:
                        parts.append(rf"{{\c{hl}}}{tok}{{\c{primary}}}")
                else:
                    parts.append(tok)
            event(start, end, " ".join(parts))

    return "\n".join(lines) + "\n"
