"""M2.5.1 Saran Area Dilindungi (EKSPERIMENTAL, opsional): deteksi objek lokal YOLOX-nano (Megvii, Apache-2.0,
3,7 MB ONNX, kelas COCO) lewat onnxruntime CPU, tanpa GPU, internet, atau API berbayar.

Hasilnya hanya SARAN untuk editor: deteksi digabung per jendela 2 dtk menjadi kotak area dilindungi yang bisa
diperiksa, diubah, atau dihapus. Batas yang diketahui (uji video peternakan nyata): ±74 % sampel berisi hewan
terdeteksi; subjek kabur/terpotong/sangat dekat sering terlewat; label kelas tidak andal (kambing tidak ada di COCO,
terbaca anjing/beruang/burung). Penandaan manual tetap cara utama.
"""
from __future__ import annotations

import hashlib
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .config import FFMPEG

MODEL = Path(__file__).resolve().parent.parent / "models" / "yolox_nano.onnx"
MODEL_SHA256 = "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"  # pragma: allowlist secret
SIZE = 416
FPS = 2.0
WINDOW = 2.0
COCO = ("person bicycle car motorcycle airplane bus train truck boat traffic_light fire_hydrant stop_sign "
        "parking_meter bench bird cat dog horse sheep cow elephant bear zebra giraffe backpack umbrella handbag tie "
        "suitcase frisbee skis snowboard sports_ball kite baseball_bat baseball_glove skateboard surfboard "
        "tennis_racket bottle wine_glass cup fork knife spoon bowl banana apple sandwich orange broccoli carrot "
        "hot_dog pizza donut cake chair couch potted_plant bed dining_table toilet tv laptop mouse remote keyboard "
        "cell_phone microwave oven toaster sink refrigerator book clock vase scissors teddy_bear hair_drier "
        "toothbrush").split()
GROUPS = {
    "hewan": {"bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"},
    "orang": {"person"},
    "kendaraan": {"bicycle", "car", "motorcycle", "bus", "truck", "boat"},
    "produk": {"bottle", "cup", "bowl", "banana", "apple", "orange", "broccoli", "carrot", "cake", "book",
               "cell_phone", "laptop", "backpack", "handbag", "suitcase", "vase"},
}
LABELS = {"hewan": "Saran: hewan", "orang": "Saran: orang", "kendaraan": "Saran: kendaraan", "produk": "Saran: produk"}


def available() -> bool:
    return MODEL.is_file()


@lru_cache(maxsize=1)
def _session():
    import onnxruntime as ort
    data = MODEL.read_bytes()
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise RuntimeError("Model deteksi tidak cocok (sha256). Pasang ulang aplikasi.")
    so = ort.SessionOptions()
    so.intra_op_num_threads = 2
    so.inter_op_num_threads = 1
    return ort.InferenceSession(data, so, providers=["CPUExecutionProvider"])


@lru_cache(maxsize=1)
def _grid():
    import numpy as np
    grids, strides = [], []
    for st in (8, 16, 32):
        n = SIZE // st
        yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((n * n, 1), st))
    return np.concatenate(grids), np.concatenate(strides)


def detect_frame(frame, min_score: float = 0.35) -> list[dict]:
    """Satu frame BGR -> [{cls, score, box (0..1 x,y,w,h)}]."""
    import cv2
    import numpy as np
    h0, w0 = frame.shape[:2]
    r = min(SIZE / w0, SIZE / h0)
    img = np.full((SIZE, SIZE, 3), 114, np.uint8)
    img[: int(h0 * r), : int(w0 * r)] = cv2.resize(frame, (int(w0 * r), int(h0 * r)))
    out = _session().run(None, {"images": img.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
    grids, strides = _grid()
    xy = (out[:, :2] + grids) * strides
    wh = np.exp(np.clip(out[:, 2:4], -10, 10)) * strides
    scores = out[:, 4:5] * out[:, 5:]
    cls, sc = scores.argmax(1), scores.max(1)
    keep = sc > min_score
    if not keep.any():
        return []
    boxes = np.concatenate([xy - wh / 2, wh], 1)[keep] / r
    idx = np.array(cv2.dnn.NMSBoxes(boxes.tolist(), sc[keep].tolist(), min_score, 0.45)).flatten()
    out_list = []
    for i in idx:
        x, y, w, h = (boxes[i] / [w0, h0, w0, h0]).tolist()
        x0, y0 = max(0.0, x), max(0.0, y)
        x1, y1 = min(1.0, x + w), min(1.0, y + h)
        if x1 - x0 > 0.01 and y1 - y0 > 0.01:
            out_list.append({"cls": COCO[int(cls[keep][i])], "score": round(float(sc[keep][i]), 2),
                             "box": [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]})
    return out_list


def _frames(video: Path, start: float, end: float, width: int, height: int):
    import numpy as np
    w = 640
    h = max(2, int(w * height / max(1, width)) // 2 * 2)
    cmd = [FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(video), "-an",
           "-vf", f"fps={FPS},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = w * h * 3
    k = 0
    try:
        while True:
            buf = proc.stdout.read(n)
            if len(buf) < n:
                break
            yield start + k / FPS, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            k += 1
    finally:
        proc.kill()
        proc.wait()


def suggest(video: Path, start: float, end: float, width: int, height: int,
            groups: tuple = ("hewan", "orang", "kendaraan", "produk"), pad: float = 0.04) -> dict:
    """Saran area per jendela 2 dtk untuk OBJEK UTAMA (terbesar x paling yakin per sampel) bila terlihat di ≥ 50 %
    sampel jendela; kotak = gabungan posisinya selama jendela (mengikuti gerak). Jendela berurutan dengan kotak
    hampir sama (IoU > 0,75) digabung tanpa memperbesar kotak. -> {areas: [...], samples, seen, method}."""
    wanted = set().union(*(GROUPS[g] for g in groups if g in GROUPS)) if groups else set()
    samples = []
    for t, frame in _frames(video, start, end, width, height):
        dets = [d for d in detect_frame(frame) if d["cls"] in wanted]
        samples.append((t, dets))
    areas: list[dict] = []
    t = start
    while t < end - 0.2:
        win = [d for s, d in samples if t <= s < t + WINDOW]
        hits = [d for d in win if d]
        if win and len(hits) >= max(1, len(win) // 2):
            # objek UTAMA per sampel (terbesar x paling yakin), digabung dalam jendela = gerak subjek selama 2 dtk
            mains = [max(dets, key=lambda d: d["box"][2] * d["box"][3] * d["score"]) for dets in hits]
            boxes = [m["box"] for m in mains]
            x0 = max(0.0, min(b[0] for b in boxes) - pad)
            y0 = max(0.0, min(b[1] for b in boxes) - pad)
            x1 = min(1.0, max(b[0] + b[2] for b in boxes) + pad)
            y1 = min(1.0, max(b[1] + b[3] for b in boxes) + pad)
            grp = _group_of([m["cls"] for m in mains])
            box = [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]
            prev = areas[-1] if areas else None
            if prev and prev["end"] >= t - 1e-6 and prev["label"] == LABELS[grp] and _iou(prev["box"], box) > 0.75:
                prev["end"] = round(min(end, t + WINDOW), 2)          # subjek praktis diam: perpanjang, kotak tetap
            else:
                areas.append({"start": round(t, 2), "end": round(min(end, t + WINDOW), 2), "box": box,
                              "label": LABELS[grp], "points": []})
        t += WINDOW
    seen = sum(1 for _, d in samples if d)
    return {"areas": areas, "samples": len(samples), "seen": seen,
            "method": "YOLOX-nano lokal (eksperimental): periksa & sesuaikan setiap saran"}


def _group_of(classes: list[str]) -> str:
    for g in ("hewan", "orang", "kendaraan", "produk"):
        if any(c in GROUPS[g] for c in classes):
            return g
    return "produk"


def _iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / max(1e-9, a[2] * a[3] + b[2] * b[3] - inter)


def _union(a: list[float], b: list[float]) -> list[float]:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def suggest_project(pid: str, start: float, end: float, groups: Optional[list[str]] = None) -> dict:
    from . import storage
    from .pipeline import source_path
    meta = storage.load_project(pid)
    end = min(end, meta.get("duration") or end, start + 180)
    return suggest(source_path(pid), start, end, meta["width"], meta["height"], tuple(groups or GROUPS))
