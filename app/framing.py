"""Smart Framing (M2.1): deteksi wajah pemateri + area materi, lalu rencana bingkai per bagian klip.

JUJUR TENTANG KEMAMPUAN: "area materi" dideteksi dengan HEURISTIK gambar, bukan pemahaman isi:
  - papan/layar  = area terang, warna netral, berbentuk persegi, dan berisi garis/tulisan (kepadatan tepi Canny);
  - area detail  = kumpulan sel gambar dengan tepi terpadat di luar wajah (cadangan bila tidak ada papan terang);
  - aktivitas    = perubahan piksel di dalam area materi antar sampel (menulis, menunjuk, ganti slide).
Aplikasi tidak membaca tulisan dan tidak tahu apakah isi papan penting.

Analisis (sekali per proyek, dari preview 480p, 2 sampel/detik, 320 px): framing.json
  {"v": 1, "fps": 2, "samples": [{"f": [x, y, w, h] | None, "nf": jumlah wajah,
                                  "b": [x, y, w, h] | None, "bk": "papan" | "detail", "ink": kepadatan tepi,
                                  "act": % piksel area materi yang berubah}, ...]}
  koordinat ternormalisasi 0..1 terhadap frame sumber.

Rencana (per klip): daftar bagian {start, end, layout, reason, ...} dengan layout:
  face     = bingkai mengikuti wajah (x & y), jarak otomatis/dekat/sedang/lebar
  split    = atas: area materi, bawah: wajah (format vertikal)
  material = area materi utuh di atas latar blur (tidak ada wajah)
  center   = bingkai tetap (manual, atau tidak ada wajah/materi)
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from statistics import median
from typing import Callable, Optional

import numpy as np

from . import jobcontrol, media
from .config import FFMPEG
from .faces import MODEL_PATH, SAMPLE_FPS

log = logging.getLogger("video-editing.framing")

MODES = ("auto", "face", "material", "manual")
ANALYSIS_WIDTH = 640       # lebar frame analisis (dari preview 480p)
MIN_SECTION = 3.0          # bagian otomatis minimal 3 dtk (tidak berganti-ganti tata letak)
BOARD_MIN_AREA = 0.04      # papan minimal 4 % frame
ACT_THRESHOLD = 0.8        # % piksel area materi yang berubah antar sampel (0,5 dtk): ada yang menulis/menunjuk
MAX_AUTO_ZOOM = 1.6        # bingkai dekat otomatis paling besar 1,6x (lebih dari itu gambar terlalu pecah)
SPLIT_MAX_ZOOM = 2.0       # panel wajah (bawah) pada tata letak Materi + Wajah
# Batas pembesaran piksel SUMBER ke output. Zoom/crop tidak boleh membuat 1 piksel sumber menjadi lebih dari
# MAX_UPSCALE piksel output (otomatis) atau MAX_UPSCALE_MANUAL (zoom yang diatur pengguna). Contoh sumber 1080p ->
# 9:16 1080x1920: crop penuh sudah 1,78x, jadi zoom otomatis tinggal maks 1,125x. Sumber 4K boleh zoom lebih jauh.
MAX_UPSCALE = 2.0
MAX_UPSCALE_MANUAL = 2.5


def zoom_limit(src_w: int, src_h: int, ow: int, oh: int, manual: bool = False) -> float:
    """Zoom terbesar (>= 1) agar jendela crop rasio ow:oh tidak diperbesar lebih dari batas."""
    cw, ch = crop_window(src_w, src_h, ow / oh, 1.0)
    base = oh / ch                                     # pembesaran crop penuh (tanpa zoom)
    return max(1.0, (MAX_UPSCALE_MANUAL if manual else MAX_UPSCALE) / base)


def upscale_of(crop_h: int, out_h: int) -> float:
    return round(out_h / max(1, crop_h), 2)


# ---------------------------------------------------------------------------------------------------- analisis

def _faces(detector, frame) -> tuple[Optional[list[float]], int]:
    h, w = frame.shape[:2]
    _, faces = detector.detect(frame)
    if faces is None or len(faces) == 0:
        return None, 0
    fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])[:4]
    return [round(float(fx) / w, 4), round(float(fy) / h, 4), round(float(fw) / w, 4), round(float(fh) / h, 4)], len(faces)


def _overlap(a: list[float], b: list[float]) -> float:
    """Bagian kotak a yang tertutup kotak b."""
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return ix * iy / max(1e-6, a[2] * a[3])


def _body_box(face: Optional[list[float]]) -> Optional[list[float]]:
    """Perkiraan badan pemateri (kemeja putih bukan papan): 3x lebar wajah, dari wajah sampai bawah frame."""
    if not face:
        return None
    x, y, w, h = face
    return [x - w, y, w * 3, 1.0 - y]


def detect_board(frame, face: Optional[list[float]]) -> tuple[Optional[list[float]], str, float]:
    """(kotak ternormalisasi, jenis, kepadatan tinta) area materi pada satu frame BGR kecil."""
    import cv2
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    body = _body_box(face)
    best, best_score = None, 0.0
    # Papan tulis/layar: jauh lebih terang daripada sekitarnya dan hampir tanpa warna. Ambang kecerahan relatif
    # terhadap frame (ruangan gelap/terang) tetapi minimal 170; dinding krem/kuning (saturasi > 40) tidak lolos.
    v = hsv[..., 2]
    bright = max(170, int(np.percentile(v, 75)) + 10)
    mask = ((v > min(bright, 235)) & (hsv[..., 1] < 40)).astype(np.uint8) * 255
    k = max(3, w // 45)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        rx, ry, rw, rh = cv2.boundingRect(c)
        area = rw * rh / (w * h)
        if area < BOARD_MIN_AREA or area > 0.85 or not 0.7 <= rw / max(1, rh) <= 3.2:
            continue
        fill = cv2.contourArea(c) / max(1, rw * rh)
        if fill < 0.6:
            continue
        box = [rx / w, ry / h, rw / w, rh / h]
        if body and _overlap(box, body) > 0.5:
            continue                                  # kemeja/baju putih pemateri
        inner = edges[ry + 2: ry + rh - 2, rx + 2: rx + rw - 2]
        ink = float(inner.mean() / 255) if inner.size else 0.0
        if ink < 0.008:
            continue                                  # dinding putih polos: tidak ada tulisan
        score = area * fill * min(ink / 0.03, 1.0)
        if score > best_score:
            best, best_score = (box, "papan", ink), score
    if best:
        return best
    # Cadangan: area dengan tepi terpadat (layar gelap, poster, benda yang diperagakan) di luar wajah/badan.
    gh, gw = 9, 16
    cells = edges[: h // gh * gh, : w // gw * gw].reshape(gh, h // gh, gw, w // gw).mean(axis=(1, 3)) / 255
    if body:
        bx0, bx1 = int(body[0] * gw), int(np.ceil((body[0] + body[2]) * gw))
        cells[int(max(0, body[1] - face[3]) * gh):, max(0, bx0):max(0, bx1)] = 0
    dense = cells > max(0.12, float(np.percentile(cells, 85)))
    if dense.sum() < 6:
        return None, "", 0.0
    ys, xs = np.nonzero(dense)
    box = [xs.min() / gw, ys.min() / gh, (xs.max() + 1 - xs.min()) / gw, (ys.max() + 1 - ys.min()) / gh]
    if box[2] * box[3] < 0.06 or box[2] * box[3] > 0.7:
        return None, "", 0.0
    return [round(float(v), 4) for v in box], "detail", round(float(cells[dense].mean()), 4)


def analyze(video: Path, width: int, height: int, duration: float,
            progress: Optional[Callable[[float], None]] = None) -> dict:
    """Satu kali baca preview: wajah + area materi + aktivitas per sampel."""
    import cv2

    from . import resources
    threads = resources.cpu_threads()
    cv2.setNumThreads(threads)
    # 640 px (bukan 320 seperti M1): wajah pemateri yang jauh (±5 % tinggi frame) masih ±18 px dan terdeteksi YuNet.
    scale = min(1.0, ANALYSIS_WIDTH / max(width, height))
    w, h = max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)
    detector = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (w, h), 0.6, 0.3, 20)
    detector.setInputSize((w, h))
    cmd = [FFMPEG, "-v", "error", "-threads", str(threads), *media.INPUT_PROTOCOLS, "-i", str(video), "-an",
           "-vf", f"fps={SAMPLE_FPS},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = jobcontrol.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    samples: list[dict] = []
    total = max(1, int(duration * SAMPLE_FPS))
    prev_gray = None
    try:
        while True:
            jobcontrol.check()
            buf = proc.stdout.read(w * h * 3)
            if len(buf) < w * h * 3:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            face, nf = _faces(detector, frame)
            board, kind, ink = detect_board(frame, face)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            act = 0.0
            if board is not None and prev_gray is not None:
                x0, y0 = int(board[0] * w), int(board[1] * h)
                x1, y1 = int((board[0] + board[2]) * w), int((board[1] + board[3]) * h)
                diff = cv2.absdiff(gray[y0:y1, x0:x1], prev_gray[y0:y1, x0:x1])
                act = round(float((diff > 25).mean() * 100), 2) if diff.size else 0.0   # % piksel area berubah
            prev_gray = gray
            samples.append({"f": face, "nf": nf, "b": board, "bk": kind, "ink": round(ink, 4), "act": act})
            if progress and len(samples) % 20 == 0:
                progress(min(1.0, len(samples) / total))
    finally:
        if proc.poll() is None:
            jobcontrol.terminate(proc)
        proc.wait()
        jobcontrol.release(proc)
    jobcontrol.check()
    return {"v": 1, "fps": SAMPLE_FPS, "samples": samples}


def faces_compat(data: dict) -> list[Optional[dict]]:
    """Format lama faces.json (x, y pusat; s tinggi) untuk kode M1 yang masih memakainya."""
    out = []
    for s in data.get("samples", []):
        f = s.get("f")
        out.append({"x": round(f[0] + f[2] / 2, 4), "y": round(f[1] + f[3] / 2, 4), "s": f[3]} if f else None)
    return out


def from_faces(track: list[Optional[dict]]) -> dict:
    """Proyek lama (hanya faces.json): wajah saja, tanpa area materi."""
    samples = []
    for t in track or []:
        if t:
            fh = t.get("s", 0.2)
            fw = fh * 0.56
            samples.append({"f": [t["x"] - fw / 2, t["y"] - fh / 2, fw, fh], "nf": 1, "b": None, "bk": "",
                            "ink": 0, "act": 0})
        else:
            samples.append({"f": None, "nf": 0, "b": None, "bk": "", "ink": 0, "act": 0})
    return {"v": 1, "fps": SAMPLE_FPS, "samples": samples}


# ---------------------------------------------------------------------------------------------------- rencana

def _window(data: dict, start: float, end: float) -> list[dict]:
    fps = data.get("fps", SAMPLE_FPS)
    a, b = max(0, int(start * fps)), int(end * fps) + 1
    return data.get("samples", [])[a:b]


def _median_box(boxes: list[list[float]]) -> Optional[list[float]]:
    if not boxes:
        return None
    return [round(median(b[i] for b in boxes), 4) for i in range(4)]


def summarize(data: dict, start: float, end: float) -> dict:
    """Ringkasan rentang: wajah ada berapa %, area materi (median), aktivitas — dipakai UI & rencana."""
    win = _window(data, start, end)
    n = max(1, len(win))
    faces = [s["f"] for s in win if s.get("f")]
    boards = [s["b"] for s in win if s.get("b")]
    kinds = [s.get("bk") for s in win if s.get("b")]
    return {"samples": len(win), "face_share": round(len(faces) / n, 3), "board_share": round(len(boards) / n, 3),
            "face": _median_box(faces), "board": _median_box(boards),
            "board_kind": max(set(kinds), key=kinds.count) if kinds else None,
            "multi_face_share": round(sum(s.get("nf", 0) > 1 for s in win) / n, 3)}


def _label(s: dict, board: Optional[list[float]]) -> tuple[str, str]:
    """Pilihan per sampel untuk mode Otomatis + alasannya (dapat dijelaskan ke pengguna)."""
    face = s.get("f")
    if board and s.get("b"):
        if not face:
            return "material", "wajah tidak terlihat (mungkin menghadap papan)"
        cx = face[0] + face[2] / 2
        near = board[0] - 0.08 <= cx <= board[0] + board[2] + 0.08
        if near:
            return "split", "pemateri berada di dekat area materi"
        if s.get("act", 0) >= ACT_THRESHOLD:
            return "split", "ada perubahan di area materi (menulis/menunjuk)"
        return "face", "area materi tidak berubah, fokus ke pemateri"
    if face:
        return "face", "wajah terdeteksi, tidak ada area materi"
    return "center", "tidak ada wajah maupun area materi"


def _merge(labels: list[tuple[str, str]], fps: float, start: float, end: float) -> list[dict]:
    """Label per sampel -> bagian minimal MIN_SECTION detik (bagian pendek digabung ke tetangganya)."""
    if not labels:
        return [{"start": start, "end": end, "layout": "center", "reason": "tidak ada data analisis"}]
    runs: list[list] = []
    for i, (lay, why) in enumerate(labels):
        t = start + i / fps
        if runs and runs[-1][0] == lay:
            runs[-1][2] = t + 1 / fps
            runs[-1][3][why] = runs[-1][3].get(why, 0) + 1
        else:
            runs.append([lay, t, t + 1 / fps, {why: 1}])
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for i, r in enumerate(runs):
            if r[2] - r[1] < MIN_SECTION:
                j = i - 1 if i > 0 and (i == len(runs) - 1 or runs[i - 1][2] - runs[i - 1][1] >= runs[i + 1][2] - runs[i + 1][1]) else i + 1
                lo, hi = sorted((i, j))
                keep = runs[j]
                keep[1], keep[2] = runs[lo][1], runs[hi][2]
                runs[lo:hi + 1] = [keep]
                changed = True
                break
    out = []
    for lay, s, e, whys in runs:
        out.append({"start": round(max(start, s), 3), "end": round(min(end, e), 3), "layout": lay,
                    "reason": max(whys, key=whys.get)})
    out[0]["start"], out[-1]["end"] = start, end
    for a, b in zip(out, out[1:]):
        b["start"] = a["end"]
    return out


def plan(data: Optional[dict], start: float, end: float, mode: str = "auto",
         sections: Optional[list[dict]] = None) -> list[dict]:
    """Rencana bingkai klip [start, end] (detik sumber). `sections` = pilihan manual pengguna per bagian
    {start, end, layout} yang menimpa hasil otomatis di rentangnya."""
    data = data or {"samples": []}
    fps = data.get("fps", SAMPLE_FPS)
    summ = summarize(data, start, end)
    board = summ["board"] if summ["board_share"] >= 0.3 else None
    win = _window(data, start, end)
    if mode == "manual":
        segs = [{"start": start, "end": end, "layout": "center", "reason": "bingkai manual"}]
    elif mode == "face":
        segs = [{"start": start, "end": end, "layout": "face" if summ["face"] else "center",
                 "reason": "Fokus Wajah" if summ["face"] else "Fokus Wajah: wajah tidak terdeteksi, bingkai tengah"}]
    elif mode == "material":
        if not board:
            lay = "face" if summ["face"] else "center"
            segs = [{"start": start, "end": end, "layout": lay, "reason": "Fokus Materi: area materi tidak terdeteksi"}]
        else:
            labels = [("split" if s.get("f") else "material", "Fokus Materi") for s in win]
            segs = _merge(labels, fps, start, end)
    else:
        segs = _merge([_label(s, board) for s in win], fps, start, end) if win else \
            [{"start": start, "end": end, "layout": "center", "reason": "tidak ada data analisis"}]
    for sec in sections or []:
        segs = _override(segs, sec, start, end)
    for seg in segs:
        sm = summarize(data, seg["start"], seg["end"])
        seg["face"] = sm["face"]
        seg["board"] = sm["board"] or board
        wanted = seg["layout"]
        if seg["layout"] in ("split", "material") and not seg["board"]:
            seg["layout"] = "face" if seg["face"] else "center"
        if seg["layout"] == "split" and not seg["face"]:
            seg["layout"] = "material"
        if seg["layout"] == "face" and not seg["face"]:
            seg["layout"] = "center"
        if seg.get("manual") and seg["layout"] != wanted:
            why = "area materi tidak terdeteksi" if wanted in ("split", "material") and not seg["board"] else \
                "wajah tidak terdeteksi"
            seg["reason"] = f"dipilih pengguna: {LAYOUT_NAMES[wanted]} tidak tersedia ({why})"
    return [s for s in segs if s["end"] - s["start"] > 0.05]


def _override(segs: list[dict], sec: dict, start: float, end: float) -> list[dict]:
    s, e = max(start, sec["start"]), min(end, sec["end"])
    if e - s < 0.3:
        return segs
    out = []
    for seg in segs:
        if seg["end"] <= s or seg["start"] >= e:
            out.append(seg)
            continue
        if seg["start"] < s:
            out.append({**seg, "end": s})
        if seg["end"] > e:
            out.append({**seg, "start": e})
    out.append({"start": s, "end": e, "layout": sec["layout"], "reason": "dipilih pengguna", "manual": True})
    return sorted(out, key=lambda x: x["start"])


# ---------------------------------------------------------------------------------------------------- filter

def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def face_keys(data: dict, start: float, end: float, dead_zone: float = 0.06, move: float = 0.7) -> list[tuple]:
    """Titik kunci kamera (t relatif bagian, cx, cy) ternormalisasi: diam, lalu bergeser halus bila wajah pindah."""
    fps = data.get("fps", SAMPLE_FPS)
    win = _window(data, start, end)
    first = max(0, int(start * fps))
    pts = []
    last = None
    for i, s in enumerate(win):
        f = s.get("f")
        if f:
            last = (f[0] + f[2] / 2, f[1] + f[3] / 2)
        if last:
            pts.append((max(0.0, (first + i) / fps - start), *last))
    if not pts:
        return []
    k = 2
    sm = [(t, median(p[1] for p in pts[max(0, i - k): i + k + 1]), median(p[2] for p in pts[max(0, i - k): i + k + 1]))
          for i, (t, _, _) in enumerate(pts)]
    keys = [(0.0, sm[0][1], sm[0][2])]
    cx, cy = sm[0][1], sm[0][2]
    for t, x, y in sm[1:]:
        if (abs(x - cx) > dead_zone or abs(y - cy) > dead_zone * 1.5) and t >= keys[-1][0] + move:
            keys.append((t, cx, cy))
            keys.append((t + move, x, y))
            cx, cy = x, y
    return keys


def _expr(keys: list[tuple[float, int]]) -> str:
    """Ekspresi FFmpeg nilai(t) bertahap dengan perpindahan smoothstep (kamera tidak menyentak)."""
    expr = str(keys[-1][1])
    for (t0, v0), (t1, v1) in reversed(list(zip(keys, keys[1:]))):
        if v0 == v1:
            seg = str(v0)
        else:
            u = f"clip((t-{t0:.3f})/{max(t1 - t0, 1e-3):.3f},0,1)"
            seg = f"{v0}+({v1 - v0})*(3*pow({u},2)-2*pow({u},3))"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return expr


def _face_zoom(face: Optional[list[float]], distance: str, zoom: float) -> float:
    if zoom and zoom > 1.0:
        return min(2.0, zoom)
    target = {"close": 0.30, "medium": 0.20, "wide": 0.0}.get(distance, 0.20)
    if not face or target <= 0:
        return 1.0
    # wajah kecil (pemateri jauh): dekatkan sampai tinggi wajah ±target dari tinggi bingkai
    return round(max(1.0, min(MAX_AUTO_ZOOM, target / max(face[3], 1e-3))), 3)


def crop_window(src_w: int, src_h: int, aspect: float, zoom: float = 1.0) -> tuple[int, int]:
    """(lebar, tinggi) jendela crop terbesar dengan rasio `aspect` (w/h), diperkecil `zoom`."""
    if src_w / src_h > aspect:
        cw, ch = src_h * aspect, src_h
    else:
        cw, ch = src_w, src_w / aspect
    return _even(min(src_w, cw / zoom)), _even(min(src_h, ch / zoom))


def _region_window(box: list[float], src_w: int, src_h: int, aspect: float, pad: float = 0.06) -> tuple[int, int, int, int]:
    """Jendela (w, h, x, y) rasio `aspect` yang memuat kotak materi + sedikit ruang, di dalam frame."""
    bw, bh = box[2] * src_w * (1 + 2 * pad), box[3] * src_h * (1 + 2 * pad)
    cxp, cyp = (box[0] + box[2] / 2) * src_w, (box[1] + box[3] / 2) * src_h
    if bw / bh > aspect:
        bh = bw / aspect
    else:
        bw = bh * aspect
    scale = min(1.0, src_w / bw, src_h / bh)
    w, h = _even(bw * scale), _even(bh * scale)
    x = int(min(max(cxp - w / 2, 0), src_w - w))
    y = int(min(max(cyp - h / 2, 0), src_h - h))
    return w, h, x, y


def segment_filter(seg: dict, data: dict, inp: str, out: str, src_w: int, src_h: int, ow: int, oh: int,
                   manual: tuple[float, float, float], distance: str = "auto") -> str:
    """Filter FFmpeg untuk satu bagian: masukan `inp` (mulai t=0 di awal bagian) -> `out` berukuran ow x oh."""
    mx, my, mzoom = manual
    lay = seg["layout"]
    vertical = oh / ow >= 1.2
    if lay == "split" and not vertical:
        lay = "material" if not seg.get("face") else "split_h"
    if lay == "face":
        z = min(_face_zoom(seg.get("face"), distance, mzoom),
                zoom_limit(src_w, src_h, ow, oh, manual=bool(mzoom and mzoom > 1.0)))
        cw, ch = crop_window(src_w, src_h, ow / oh, z)
        keys = face_keys(data, seg["start"], seg["end"]) or [(0.0, 0.5, 0.5)]

        def left(cx):
            return int(min(max(cx * src_w - cw / 2, 0), src_w - cw))

        def top(cy):
            return int(min(max(cy * src_h - ch * 0.38, 0), src_h - ch))     # wajah di sepertiga atas
        xs = _expr([(t, left(x)) for t, x, _ in keys])
        ys = _expr([(t, top(y)) for t, _, y in keys])
        return f"{inp}crop={cw}:{ch}:x='{xs}':y='{ys}',scale={ow}:{oh}:flags=lanczos,setsar=1{out}"
    if lay == "split":
        top_h = _even(oh * 0.56)
        bot_h = oh - top_h
        bw, bh, bx, by = _region_window(seg["board"], src_w, src_h, ow / top_h)
        face = seg["face"]
        # Panel bawah lebih kecil: wajah dibuat ±30 % tinggi panel, perbesaran maksimum 2x dari tinggi sumber.
        fz = max(1.0, min(SPLIT_MAX_ZOOM, 0.30 / max(face[3], 1e-3), zoom_limit(src_w, src_h, ow, bot_h)))
        fw, fh = crop_window(src_w, src_h, ow / bot_h, fz)
        fx = int(min(max((face[0] + face[2] / 2) * src_w - fw / 2, 0), src_w - fw))
        fy = int(min(max((face[1] + face[3] / 2) * src_h - fh * 0.42, 0), src_h - fh))
        keys = face_keys(data, seg["start"], seg["end"])
        if keys:
            xs = _expr([(t, int(min(max(x * src_w - fw / 2, 0), src_w - fw))) for t, x, _ in keys])
        else:
            xs = str(fx)
        tag = out.strip("[]")
        return (f"{inp}split=2[{tag}a][{tag}b];[{tag}a]crop={bw}:{bh}:{bx}:{by},scale={ow}:{top_h}:flags=lanczos,"
                f"setsar=1[{tag}t];[{tag}b]crop={fw}:{fh}:x='{xs}':y={fy},scale={ow}:{bot_h}:flags=lanczos,setsar=1,"
                f"drawbox=x=0:y=0:w=iw:h=6:color=white@0.85:t=fill[{tag}u];[{tag}t][{tag}u]vstack{out}")
    if lay == "split_h":       # format melebar: materi kiri, wajah kanan
        left_w = _even(ow * 0.62)
        right_w = ow - left_w
        bw, bh, bx, by = _region_window(seg["board"], src_w, src_h, left_w / oh)
        fw, fh = crop_window(src_w, src_h, right_w / oh, 1.0)
        face = seg["face"]
        fx = int(min(max((face[0] + face[2] / 2) * src_w - fw / 2, 0), src_w - fw))
        tag = out.strip("[]")
        return (f"{inp}split=2[{tag}a][{tag}b];[{tag}a]crop={bw}:{bh}:{bx}:{by},scale={left_w}:{oh},setsar=1[{tag}t];"
                f"[{tag}b]crop={fw}:{fh}:{fx}:0,scale={right_w}:{oh},setsar=1[{tag}u];[{tag}t][{tag}u]hstack{out}")
    if lay == "material":
        if vertical:
            fg_w = ow
            board_aspect = (seg["board"][2] * src_w) / max(1.0, seg["board"][3] * src_h)
            fg_h = _even(min(oh * 0.7, fg_w / board_aspect))
            bw, bh, bx, by = _region_window(seg["board"], src_w, src_h, fg_w / fg_h, pad=0.04)
            tag = out.strip("[]")
            # Latar blur dibuat di 1/4 resolusi lalu diperbesar: tampilan sama (buram), ±2x lebih cepat di CPU.
            bw4, bh4 = _even(ow / 4), _even(oh / 4)
            return (f"{inp}split=2[{tag}a][{tag}b];[{tag}a]scale={bw4}:{bh4}:force_original_aspect_ratio=increase,"
                    f"crop={bw4}:{bh4},boxblur=6:2,eq=brightness=-0.10:saturation=0.8,scale={ow}:{oh},setsar=1[{tag}bg];"
                    f"[{tag}b]crop={bw}:{bh}:{bx}:{by},scale={fg_w}:{fg_h}:flags=lanczos,setsar=1[{tag}fg];"
                    f"[{tag}bg][{tag}fg]overlay=x=(W-w)/2:y=(H-h)*0.40{out}")
        bw, bh, bx, by = _region_window(seg["board"], src_w, src_h, ow / oh)
        return f"{inp}crop={bw}:{bh}:{bx}:{by},scale={ow}:{oh}:flags=lanczos,setsar=1{out}"
    # center / manual
    cw, ch = crop_window(src_w, src_h, ow / oh, min(max(1.0, mzoom or 1.0), zoom_limit(src_w, src_h, ow, oh, True)))
    x, y = int((src_w - cw) * mx), int((src_h - ch) * my)
    return f"{inp}crop={cw}:{ch}:{x}:{y},scale={ow}:{oh}:flags=lanczos,setsar=1{out}"


def _clamp_rect(r: list[float]) -> list[float]:
    x0, y0 = max(0.0, r[0]), max(0.0, r[1])
    x1, y1 = min(1.0, r[0] + r[2]), min(1.0, r[1] + r[3])
    return [round(x0, 4), round(y0, 4), round(max(0.0, x1 - x0), 4), round(max(0.0, y1 - y0), 4)]


def _head(r: list[float]) -> list[float]:
    """Kotak wajah -> area kepala & bahu yang dilindungi (rambut di atas, dagu & leher di bawah)."""
    x, y, w, h = r
    return _clamp_rect([x - 0.35 * w, y - 0.6 * h, w * 1.7, h * 2.2])


def occupied_rects(seg: dict, data: dict, src_w: int, src_h: int, ow: int, oh: int,
                   manual: tuple[float, float, float], distance: str = "auto") -> list[dict]:
    """Area penting di OUTPUT (0..1) untuk satu bagian: wajah pemateri & papan/materi. Geometri sama dengan
    segment_filter (crop, zoom, panel) sehingga safe-zone grafis cocok dengan gambar yang benar-benar dirender.
    Posisi wajah = median bagian (kamera mengikuti wajah, jadi wajah relatif diam di bingkai)."""
    mx, my, mzoom = manual
    lay = seg["layout"]
    face, board = seg.get("face"), seg.get("board")
    vertical = oh / ow >= 1.2
    out: list[dict] = []

    def face_in(win_x, win_y, win_w, win_h, oy=0.0, sh=1.0):
        if not face:
            return
        r = [(face[0] * src_w - win_x) / win_w, oy + (face[1] * src_h - win_y) / win_h * sh,
             face[2] * src_w / win_w, face[3] * src_h / win_h * sh]
        if r[0] + r[2] > 0 and r[0] < 1 and r[2] > 0:
            out.append({"kind": "wajah", "rect": _head(r)})

    if lay == "split" and not vertical:
        lay = "material" if not face else "split_h"
    if lay == "face":
        z = min(_face_zoom(face, distance, mzoom), zoom_limit(src_w, src_h, ow, oh, manual=bool(mzoom and mzoom > 1.0)))
        cw, ch = crop_window(src_w, src_h, ow / oh, z)
        if face:
            cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
            x = min(max(cx * src_w - cw / 2, 0), src_w - cw)
            y = min(max(cy * src_h - ch * 0.38, 0), src_h - ch)
            face_in(x, y, cw, ch)
    elif lay == "split":
        top_h = _even(oh * 0.56)
        bot_h = oh - top_h
        bw, bh, bx, by = _region_window(board, src_w, src_h, ow / top_h)
        sy = top_h / oh
        out.append({"kind": "papan", "rect": _clamp_rect([(board[0] * src_w - bx) / bw, (board[1] * src_h - by) / bh * sy,
                                                          board[2] * src_w / bw, board[3] * src_h / bh * sy])})
        fz = max(1.0, min(SPLIT_MAX_ZOOM, 0.30 / max(face[3], 1e-3), zoom_limit(src_w, src_h, ow, bot_h)))
        fw, fh = crop_window(src_w, src_h, ow / bot_h, fz)
        fx = min(max((face[0] + face[2] / 2) * src_w - fw / 2, 0), src_w - fw)
        fy = min(max((face[1] + face[3] / 2) * src_h - fh * 0.42, 0), src_h - fh)
        face_in(fx, fy, fw, fh, oy=sy, sh=bot_h / oh)
    elif lay == "split_h":
        out.append({"kind": "papan", "rect": [0.0, 0.0, 0.62, 1.0]})
        out.append({"kind": "wajah", "rect": [0.62, 0.0, 0.38, 1.0]})
    elif lay == "material":
        if vertical:
            board_aspect = (board[2] * src_w) / max(1.0, board[3] * src_h)
            fg_h = _even(min(oh * 0.7, ow / board_aspect))
            out.append({"kind": "papan", "rect": _clamp_rect([0.0, (oh - fg_h) * 0.40 / oh, 1.0, fg_h / oh])})
        else:
            out.append({"kind": "papan", "rect": [0.0, 0.0, 1.0, 1.0]})
    else:
        cw, ch = crop_window(src_w, src_h, ow / oh, min(max(1.0, mzoom or 1.0), zoom_limit(src_w, src_h, ow, oh, True)))
        face_in((src_w - cw) * mx, (src_h - ch) * my, cw, ch)
    return out


LAYOUT_NAMES = {"face": "Fokus Wajah", "split": "Materi + Wajah", "material": "Fokus Materi", "center": "Bingkai tetap"}


def public_plan(segs: list[dict]) -> list[dict]:
    """Bentuk untuk UI: tanpa koordinat mentah berlebih."""
    names = LAYOUT_NAMES
    return [{"start": s["start"], "end": s["end"], "layout": s["layout"], "label": names.get(s["layout"], s["layout"]),
             "reason": s.get("reason", ""), "manual": bool(s.get("manual")), "board": s.get("board"),
             "face": s.get("face")} for s in segs]
