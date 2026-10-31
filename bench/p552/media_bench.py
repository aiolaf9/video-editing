"""P5.5.2-B: benchmark preview & render memakai fungsi aplikasi asli (media.make_preview, pipeline.render).

Per aset (lihat assets.py) dan per operasi, N run di subprocess terpisah (waktu dinding, CPU & puncak RSS FFmpeg
terisolasi). Dari keluaran dideteksi ulang: flash (video) & beep (audio) -> offset/drift A/V; munculnya subtitle
di pita bawah -> selisih terhadap waktu kata yang DISUNTIKKAN ke transkrip (uji timing subtitle/render, BUKAN uji
Whisper); SSIM/PSNR render "original" terhadap segmen sumber yang sama.

    VIDEO_APP_DATA=<dir> python bench/p552/media_bench.py <folder-aset> <keluaran.jsonl> [--runs 3] [--only a,b]
"""
import argparse
import json
import os
import re
import resource
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

CLIP = (10.0, 40.0)
LONG_CLIP = (1700.0, 1730.0)
WORD_T = 0.8          # kata ke-k pada 2k + 0.8 dtk (di antara flash), durasi 0.5 dtk


class FakeJob:
    user_id = None

    def update(self, *_a, **_k):
        pass

    def stage(self, *_a, **_k):
        return lambda *_x, **_y: None


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,width,height,avg_frame_rate,channels,sample_rate:"
                          "format=duration", "-of", "json", str(path)], capture_output=True, text=True, check=True)
    d = json.loads(out.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), {})
    num, den = (v.get("avg_frame_rate") or "0/1").split("/")
    return {"duration": round(float(d["format"]["duration"]), 3), "vcodec": v.get("codec_name"),
            "size": [v.get("width"), v.get("height")], "fps": round(float(num) / float(den or 1), 3),
            "acodec": a.get("codec_name"), "channels": a.get("channels"), "rate": int(a.get("sample_rate") or 0)}


def _series(path, vf, key="YAVG"):
    """[(waktu, nilai)] per frame dari filter video `vf` + signalstats (YAVG = rata-rata, YMAX = maksimum luma)."""
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-i", str(path), "-an", "-vf",
           f"{vf},signalstats,metadata=print:key=lavfi.signalstats.{key}:file=-", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    times = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", out)]
    vals = [float(x) for x in re.findall(rf"{key}=([0-9.]+)", out)]
    return list(zip(times, vals))


def _onsets(series, threshold, min_gap=0.5):
    on, last, prev = [], -1e9, False
    for t, v in series:
        hi = v > threshold
        if hi and not prev and t - last > min_gap:
            on.append(round(t, 4))
            last = t
        prev = hi
    return on


def flashes(path):
    return _onsets(_series(path, "crop=iw:ih*0.12:0:0"), 128)


def beeps(path):
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-i", str(path), "-vn", "-af",
           "aresample=48000,asetnsamples=96,astats=metadata=1:reset=1,"
           "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    times = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", out)]
    lv = [float(x) if x not in ("-inf", "inf") else -200.0 for x in re.findall(r"RMS_level=(\S+)", out)]
    return _onsets(list(zip(times, lv)), -30)


def av_offsets(path):
    """Offset audio - video (ms, positif = audio terlambat) untuk tiap flash yang punya beep terdekat (< 0,5 dtk)."""
    f, b = flashes(path), beeps(path)
    pairs = []
    for t in f:
        near = min(b, key=lambda x: abs(x - t), default=None)
        if near is not None and abs(near - t) < 0.5:
            pairs.append((t, (near - t) * 1000))
    if not pairs:
        return {"flash": len(f), "beep": len(b), "pairs": 0}
    offs = [o for _, o in pairs]
    n = len(pairs)
    mt, mo = sum(t for t, _ in pairs) / n, sum(offs) / n
    var = sum((t - mt) ** 2 for t, _ in pairs)
    slope = sum((t - mt) * (o - mo) for t, o in pairs) / var if var else 0.0
    return {"flash": len(f), "beep": len(b), "pairs": n, "offset_ms_median": round(statistics.median(offs), 1),
            "offset_ms_max_abs": round(max(abs(o) for o in offs), 1),
            "drift_ms_per_min": round(slope * 60, 2), "first_flash": f[0] if f else None}


def subtitle_errors(path, words_rel):
    """Selisih (ms) munculnya subtitle di pita bawah vs waktu kata yang disuntikkan (relatif awal klip)."""
    band = _series(path, "crop=iw:ih*0.15:0:ih*0.72", "YMAX")    # teks kecil: luma maksimum, bukan rata-rata
    top = dict(_series(path, "crop=iw:ih*0.12:0:0"))
    clean = [(t, v) for t, v in band if top.get(t, 0) < 128]          # buang frame flash (seluruh layar putih)
    on = _onsets(clean, 100, min_gap=0.8)
    errs = []
    for w in words_rel:
        near = min(on, key=lambda x: abs(x - w), default=None)
        if near is not None and abs(near - w) < 0.6:
            errs.append(round((near - w) * 1000, 1))
    return {"words": len(words_rel), "detected": len(errs), "err_ms": errs,
            "err_ms_max_abs": max((abs(e) for e in errs), default=None)}


def quality(render, src, start, dur):
    cmd = ["ffmpeg", "-hide_banner", "-v", "info", "-i", str(render), "-ss", str(start), "-t", str(dur),
           "-i", str(src), "-lavfi", "[0:v]setpts=PTS-STARTPTS[a];[1:v]setpts=PTS-STARTPTS[b];[a][b]scale2ref"
           "[a2][b2];[a2][b2]ssim;[0:v]setpts=PTS-STARTPTS[c];[1:v]setpts=PTS-STARTPTS[d];[c][d]scale2ref[c2][d2];"
           "[c2][d2]psnr", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True).stderr
    ssim = re.search(r"SSIM .*All:([0-9.]+)", err)
    psnr = re.search(r"PSNR .*average:([0-9.inf]+)", err)
    return {"ssim": float(ssim.group(1)) if ssim else None, "psnr": psnr.group(1) if psnr else None}


# ---------------------------------------------------------------- satu run (dijalankan di subprocess)

def one(asset_path: Path, op: str, out_json: Path):
    from app import pipeline, storage
    from app.media import make_preview
    from app.schemas import RenderRequest

    info = probe(asset_path)
    pid = storage.create_project(f"bench-{asset_path.stem}-{op}")["id"]
    folder = storage.project_dir(pid)
    shutil.copy(asset_path, folder / f"source{asset_path.suffix}")
    from app import media
    meta = media.probe(folder / f"source{asset_path.suffix}")
    storage.update_project(pid, source=f"source{asset_path.suffix}", **meta)
    long_ = info["duration"] > 600
    start, end = LONG_CLIP if long_ else CLIP
    res = {"asset": asset_path.stem, "op": op, "source": info}
    t0 = time.perf_counter()
    if op == "preview":
        r = make_preview(folder / f"source{asset_path.suffix}", folder / "preview.mp4", meta["duration"],
                         meta["has_audio"])
        res["wall_s"] = round(time.perf_counter() - t0, 3)
        res["output"] = probe(folder / "preview.mp4")
        res["plan"] = {k: r.get(k) for k in ("mode", "cached", "reason") if k in r}
        res["rtf"] = round(res["wall_s"] / info["duration"], 4)
        out = folder / "preview.mp4"
    else:
        # Transkrip DISUNTIKKAN (waktu kata diketahui) untuk menguji timing subtitle, bukan keluaran Whisper.
        words = [{"start": round(k * 2 + WORD_T, 3), "end": round(k * 2 + WORD_T + 0.5, 3), "word": f"KATA{k}"}
                 for k in range(int(start // 2), int(end // 2))]
        storage.write_data(pid, "transcript", {"language": "id", "segments": [
            {"start": w["start"], "end": w["end"], "text": w["word"], "words": [w]} for w in words]})
        storage.write_data(pid, "faces", [])
        if op == "render_9x16_subs":
            req = RenderRequest(start=start, end=end, aspect="9:16", reframe="auto", fade=False,
                                normalize_audio=False, animations=False,
                                subtitles={"enabled": True, "words_per_line": 1, "uppercase": True})
        else:  # render_original: kualitas encode, tanpa efek
            req = RenderRequest(start=start, end=end, aspect="original", reframe="manual", fade=False,
                                normalize_audio=False, animations=False, subtitles={"enabled": False})
        item = pipeline.render(FakeJob(), pid, req)
        res["wall_s"] = round(time.perf_counter() - t0, 3)
        out = folder / "renders" / item["file"]
        res["output"] = probe(out)
        res["rtf"] = round(res["wall_s"] / (end - start), 4)
        res["clip"] = [start, end]
        if op == "render_9x16_subs":
            res["subtitles"] = subtitle_errors(out, [w["start"] - start for w in words])
        else:
            res["quality"] = quality(out, folder / f"source{asset_path.suffix}", start, end - start)
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    res["ffmpeg_cpu_s"] = round(ru.ru_utime + ru.ru_stime, 2)
    res["ffmpeg_peak_rss_mb"] = round(ru.ru_maxrss / 1024, 1)
    res["sync"] = av_offsets(out)
    out_json.write_text(json.dumps(res))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("assets")
    ap.add_argument("out")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--ops", default="preview,render_9x16_subs,render_original")
    ap.add_argument("--one", nargs=3, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.one:
        one(Path(a.one[0]), a.one[1], Path(a.one[2]))
        return
    manifest = json.loads((Path(a.assets) / "manifest.json").read_text())
    out = Path(a.out)
    for name, m in manifest.items():
        if a.only and name not in a.only.split(","):
            continue
        for op in a.ops.split(","):
            for run in range(1, a.runs + 1):
                tmp = out.with_suffix(".one.json")
                proc = subprocess.run([sys.executable, __file__, a.assets, a.out, "--one",
                                       str(Path(a.assets) / m["file"]), op, str(tmp)], capture_output=True, text=True,
                                      env={**os.environ, "PYTHONWARNINGS": "ignore"})
                if proc.returncode:
                    row = {"asset": name, "op": op, "run": run, "error": proc.stderr.strip()[-600:]}
                else:
                    row = {**json.loads(tmp.read_text()), "run": run}
                with out.open("a") as f:
                    f.write(json.dumps(row) + "\n")
                print(json.dumps({k: row.get(k) for k in ("asset", "op", "run", "wall_s", "rtf", "error")}),
                      flush=True)


if __name__ == "__main__":
    main()
