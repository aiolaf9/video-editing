"""P4: preview cepat (rencana, cache, remux, video panjang), sinkron A/V, dan pemisahan dari render final."""
import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from app import media, pipeline, resources, storage
from app.schemas import RenderRequest

FRAME = 1 / 30


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def sync_source(path: Path, size="1280x720", seconds=12, vcodec=("libx264", "-preset", "ultrafast"), bitrate=None,
                audio_delay_ms=200, start_offset=0.0):
    """Video hitam yang berkedip putih (3 frame) tiap 2 dtk + beep 1 kHz pada saat yang sama.
    Audio sengaja tertunda `audio_delay_ms` (kasus nyata) dan file bisa diberi start_time bukan nol."""
    venc = ["-c:v", *vcodec] + (["-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate] if bitrate else [])
    _ffmpeg("-f", "lavfi", "-i", f"color=c=black:s={size}:r=30:d={seconds},"
            "drawbox=c=white:t=fill:enable='lt(mod(t\\,2)\\,0.1)'",
            "-f", "lavfi", "-i", f"aevalsrc='0.8*sin(2*PI*1000*t)*lt(mod(t\\,2)\\,0.1)':s=48000:d={seconds},"
            f"adelay={audio_delay_ms}:all=1",
            *venc, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-t", str(seconds),
            *(["-output_ts_offset", str(start_offset)] if start_offset else []), str(path))
    return path


def flash_times(path):
    """Waktu (dtk sejak awal file) frame yang mulai berkedip putih."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-an", "-vf",
                          "scale=64:36,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-", "-f", "null", "-"],
                         capture_output=True, text=True).stdout
    times, prev = [], 0.0
    t = None
    for line in out.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            t = float(m.group(1))
        m = re.search(r"YAVG=([0-9.]+)", line)
        if m and t is not None:
            y = float(m.group(1))
            if y > 128 >= prev:
                times.append(t)
            prev = y
    # FFmpeg menormalkan timestamp keluaran ke 0 (start_time sumber sudah dikurangi), sama seperti saat
    # render memakai -ss dan saat browser memutar preview.
    return [round(x, 3) for x in times]


def beep_times(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    s = np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.float32))
    block = 40  # 5 ms
    env = s[: len(s) // block * block].reshape(-1, block).max(axis=1)
    on = env > 8000
    starts = np.flatnonzero(on[1:] & ~on[:-1]) + 1
    # file audio sudah relatif terhadap awal file (dekode dari 0); selisih start video vs audio ikut dihitung
    return [round(i * block / 8000, 3) for i in starts]


def _assert_same_timeline(src, prev):
    fs, fp = flash_times(src), flash_times(prev)
    bs, bp = beep_times(src), beep_times(prev)
    assert len(fs) >= 4 and len(fp) == len(fs) and len(bp) == len(bs) >= 4, (fs, fp, bs, bp)
    for a, b in zip(fs, fp):  # gambar: waktu yang sama di preview & sumber (render memakai waktu sumber)
        assert abs(a - b) <= FRAME + 0.002, (fs, fp)
    for a, b in zip(bs, bp):  # suara: waktu yang sama
        assert abs(a - b) <= 0.025, (bs, bp)
    gap_src = np.mean(np.array(bs[:4]) - np.array(fs[:4]))
    gap_prev = np.mean(np.array(bp[:4]) - np.array(fp[:4]))
    assert abs(gap_src - gap_prev) <= FRAME, (gap_src, gap_prev)  # jarak gambar-suara (200 ms) tetap


@pytest.fixture(autouse=True)
def _job_threads():
    resources.plan_job_threads(0, 0)
    yield
    resources.clear_job_threads()


# ------------------------------------------------------------------ rencana & cache

def test_plan_copy_for_browser_friendly_source(tmp_path):
    src = sync_source(tmp_path / "ringan.mp4", size="640x360", bitrate="800k")
    plan = media.preview_plan(src, 12)
    assert plan["mode"] == "copy" and plan["audio"] == "copy" and plan["height"] is None


def test_plan_transcode_for_heavy_source(tmp_path):
    src = sync_source(tmp_path / "berat.mp4", size="1920x1080", bitrate="12M")
    plan = media.preview_plan(src, 12)
    assert plan["mode"] == "transcode" and plan["height"] == 480 and plan["audio"] == "copy"
    assert not plan["fast_decode"]


def test_preview_is_cached_until_source_changes(tmp_path):
    src = sync_source(tmp_path / "s.mp4", size="1280x720", bitrate="6M")
    dst = tmp_path / "preview.mp4"
    first = media.make_preview(src, dst, 12)
    mtime = dst.stat().st_mtime_ns
    again = media.make_preview(src, dst, 12)
    assert not first["cached"] and again["cached"] and again["seconds"] == 0
    assert dst.stat().st_mtime_ns == mtime  # tidak di-transcode ulang
    os.utime(src, (time.time() + 5, time.time() + 5))  # sumber berubah -> preview dibuat ulang
    assert not media.make_preview(src, dst, 12)["cached"]


def test_hwaccel_failure_falls_back_to_cpu(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "PREVIEW_HWACCEL", "cuda")  # tidak ada GPU di sini: dekoder hardware gagal
    src = sync_source(tmp_path / "hw.mp4", size="1920x1080", bitrate="10M")  # wajib transcode
    out = media.make_preview(src, tmp_path / "p.mp4", 12)
    assert out["hwaccel"] == "cuda" and (tmp_path / "p.mp4").stat().st_size > 0


# ------------------------------------------------------------------ sinkron A/V & timeline

@pytest.mark.parametrize("case", ["transcode", "copy", "copy_start_offset", "long"])
def test_preview_keeps_av_sync_and_timeline(tmp_path, monkeypatch, case):
    if case == "transcode":
        src = sync_source(tmp_path / "s.mp4", size="1920x1080", bitrate="10M")
    elif case == "copy":
        src = sync_source(tmp_path / "s.mp4", size="640x360", bitrate="800k")
    elif case == "copy_start_offset":  # start_time bukan nol (umum pada video hasil potong/HP)
        src = sync_source(tmp_path / "s.mp4", size="640x360", bitrate="800k", start_offset=1.5)
    else:  # video panjang: 360p + dekode cepat (fps lebih rendah) tetap sinkron
        monkeypatch.setattr(media, "PREVIEW_LONG_MINUTES", 0.1)
        src = sync_source(tmp_path / "s.mp4", size="1920x1080", bitrate="10M",
                          vcodec=("libx264", "-preset", "ultrafast", "-bf", "3"))
    dst = tmp_path / "preview.mp4"
    out = media.make_preview(src, dst, 12)
    assert out["mode"] == ("copy" if case.startswith("copy") else "transcode")
    if case == "long":
        assert out["fast_decode"] and media.probe(dst)["height"] == 360
    _assert_same_timeline(src, dst)
    assert abs(media.probe(dst)["duration"] - media.probe(src)["duration"]) < 0.15


def test_long_video_preview(tmp_path, monkeypatch):
    """Video 3 menit dengan mode video panjang: lebih cepat dari resep normal, durasi & isi utuh."""
    src = tmp_path / "panjang.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=180,noise=alls=6:allf=t",
            "-f", "lavfi", "-i", "sine=duration=180", "-c:v", "libx264", "-preset", "ultrafast", "-bf", "3",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src))
    t = time.time()
    media.make_preview(src, tmp_path / "normal.mp4", 180)
    normal = time.time() - t
    monkeypatch.setattr(media, "PREVIEW_LONG_MINUTES", 2)
    t = time.time()
    out = media.make_preview(src, tmp_path / "long.mp4", 180)
    fast = time.time() - t
    assert out["fast_decode"] and media.probe(tmp_path / "long.mp4")["height"] == 360
    assert abs(media.probe(tmp_path / "long.mp4")["duration"] - 180) < 0.5
    assert fast < normal, (fast, normal)


# ------------------------------------------------------------------ preview vs render final

def test_render_uses_original_source_not_preview(tmp_path, monkeypatch):
    """Preview 480p, tetapi render final memakai video sumber (resolusi penuh) di timestamp yang sama."""
    pid = storage.create_project("render-sumber")["id"]
    folder = storage.project_dir(pid)
    src = sync_source(folder / "source.mp4", size="1920x1080", bitrate="10M")
    info = media.probe(src)
    storage.update_project(pid, source="source.mp4", status="siap", **info)
    media.make_preview(src, folder / "preview.mp4", info["duration"])
    assert media.probe(folder / "preview.mp4")["height"] == 480

    class J:
        def stage(self, *a):
            return lambda f: None

        def update(self, *a):
            pass

    req = RenderRequest(start=2, end=8, aspect="16:9", reframe="manual", animations=False,
                        subtitles={"enabled": False}, fade=False, normalize_audio=False)
    out = pipeline.render(J(), pid, req)
    rendered = folder / "renders" / out["file"]
    assert media.probe(rendered)["height"] == 1080
    # Kedipan pada 2, 4, 6 dtk sumber muncul di 0, 2, 4 dtk hasil render (klip mulai di 2 dtk).
    assert [round(x) for x in flash_times(rendered)][:3] == [0, 2, 4]
    storage.delete_project(pid)
