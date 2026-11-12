"""Subtitle ASS untuk video vertikal: teks persis dari transkrip ucapan, kata aktif disorot."""
from dataclasses import dataclass

STYLES = ("highlight", "simple")


@dataclass
class SubtitleOptions:
    style: str = "highlight"
    font: str = "Poppins ExtraBold"
    size: float = 1.0
    color: str = "#FFFFFF"
    highlight: str = "#FFD400"
    uppercase: bool = False
    max_words: int = 4
    max_chars: int = 26
    position: float = 0.72   # posisi vertikal (0 = atas, 1 = bawah)


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str, upper: bool) -> str:
    text = text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ").strip()
    return text.upper() if upper else text


def chunk_words(words: list[dict], max_words: int = 4, max_chars: int = 26, max_gap: float = 0.5) -> list[list[dict]]:
    """Baris pendek yang mudah dibaca di layar HP; putus di tanda baca, jeda, atau batas segmen."""
    chunks, cur = [], []
    for w in words:
        if cur:
            text_len = sum(len(x["word"]) + 1 for x in cur) + len(w["word"])
            if (len(cur) >= max_words or text_len > max_chars or w["start"] - cur[-1]["end"] > max_gap
                    or w.get("seg") != cur[-1].get("seg")):
                chunks.append(cur)
                cur = []
        cur.append(w)
        if w["word"].endswith((".", "!", "?", ",", ";", ":")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[dict], width: int, height: int, opts: SubtitleOptions,
              title: str = "", title_until: float = 0.0) -> str:
    size = int(round(width * 0.068 * max(0.5, min(opts.size, 2.0))))
    outline = max(3, round(size * 0.09))
    margin_h = int(width * 0.08)
    y = int(height * opts.position)
    primary, hl = ass_color(opts.color), ass_color(opts.highlight)
    font = opts.font.replace(",", " ")
    tsize = int(size * 0.9)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,{font},{size},{primary},&H000000FF,&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},{max(1, outline // 2)},5,{margin_h},{margin_h},0,1",
        f"Style: Title,{font},{tsize},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,"
        f"-1,0,0,0,100,100,0,0,3,{int(tsize * 0.35)},0,8,{margin_h},{margin_h},{int(height * 0.09)},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def event(start, end, text, style="Sub", layer=0):
        lines.append(f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}")

    if title and title_until > 0:
        event(0.0, title_until, r"{\fad(200,250)}" + _clean(title, False), style="Title", layer=1)

    pos = rf"{{\an5\pos({width // 2},{y})}}"
    chunks = chunk_words(words, opts.max_words, opts.max_chars)
    for ci, chunk in enumerate(chunks):
        toks = [_clean(w["word"], opts.uppercase) for w in chunk]
        c_start = chunk[0]["start"]
        nxt = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        c_end = chunk[-1]["end"] + 0.2
        if nxt is not None:
            c_end = min(c_end, nxt)
        c_end = max(c_end, chunk[-1]["end"])
        if opts.style == "simple":
            event(c_start, c_end, pos + " ".join(toks))
            continue
        for k, w in enumerate(chunk):
            s = c_start if k == 0 else w["start"]
            e = chunk[k + 1]["start"] if k + 1 < len(chunk) else c_end
            if e <= s:
                continue
            parts = [rf"{{\c{hl}}}{t}{{\c{primary}}}" if i == k else t for i, t in enumerate(toks)]
            event(s, e, pos + " ".join(parts))
    return "\n".join(lines) + "\n"
