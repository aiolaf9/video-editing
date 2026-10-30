"""P1: batas waktu, pembatalan, dan penghentian subprocess (tingkat unit, tanpa worker)."""
import os
import subprocess
import threading
import time
import uuid
from types import SimpleNamespace

import numpy as np
import pytest

from app import jobcontrol, media, transcribe
from app.jobcontrol import JobCancelled, JobControl, SubprocessStalled


def _alive(marker: str) -> bool:
    out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.strip()
    return bool(out)


def _endless_ffmpeg_args(marker: str) -> list[str]:
    # Sumber lavfi tanpa batas durasi: FFmpeg berjalan terus sampai dihentikan.
    return ["-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10", "-metadata", f"comment={marker}", "-f", "null", "-"]


def _run_in_thread(fn):
    box = {}

    def target():
        try:
            box["result"] = fn()
        except BaseException as exc:  # termasuk JobCancelled (BaseException)
            box["error"] = exc

    t = threading.Thread(target=target)
    t.start()
    return t, box


def test_cancel_kills_running_ffmpeg():
    marker = f"vetest-{uuid.uuid4().hex[:8]}"
    control = JobControl("uji", timeout=600)

    def work():
        with jobcontrol.activate(control):
            media.run_ffmpeg(_endless_ffmpeg_args(marker), duration=1000)

    t, box = _run_in_thread(work)
    deadline = time.time() + 10
    while not _alive(marker) and time.time() < deadline:
        time.sleep(0.1)
    assert _alive(marker), "FFmpeg tidak sempat berjalan"
    started = time.time()
    control.cancel("dibatalkan")
    t.join(15)
    assert not t.is_alive()
    assert isinstance(box.get("error"), JobCancelled) and box["error"].reason == "dibatalkan"
    assert time.time() - started < 8
    assert not _alive(marker), "Proses FFmpeg masih hidup setelah dibatalkan"


def test_deadline_stops_ffmpeg_via_progress_check():
    marker = f"vetest-{uuid.uuid4().hex[:8]}"
    control = JobControl("uji", timeout=1.5)

    def work():
        with jobcontrol.activate(control):
            # Callback progress = titik pemeriksaan (sama seperti Job.update di pipeline).
            media.run_ffmpeg(_endless_ffmpeg_args(marker), duration=1000, progress=lambda _f: jobcontrol.check())

    t, box = _run_in_thread(work)
    t.join(20)
    assert isinstance(box.get("error"), JobCancelled) and box["error"].reason == "timeout"
    assert "batas waktu" in box["error"].message
    assert not _alive(marker)


@pytest.mark.skipif(os.name != "posix", reason="butuh FIFO (POSIX)")
def test_stalled_ffmpeg_is_killed(tmp_path):
    fifo = tmp_path / f"macet-{uuid.uuid4().hex[:6]}.fifo"
    os.mkfifo(fifo)
    marker = fifo.name
    started = time.time()
    with pytest.raises(SubprocessStalled):
        # Membuka FIFO tanpa penulis = FFmpeg tertahan tanpa output sama sekali.
        media.run_ffmpeg(["-i", str(fifo), "-f", "null", "-"], stall_timeout=2)
    assert time.time() - started < 15
    assert not _alive(marker)


def test_run_capture_cancellable():
    control = JobControl("uji", timeout=600)

    def work():
        with jobcontrol.activate(control):
            jobcontrol.run_capture(["sleep", "30"])

    t, box = _run_in_thread(work)
    time.sleep(0.5)
    control.cancel("dibatalkan")
    t.join(10)
    assert isinstance(box.get("error"), JobCancelled)


def test_run_capture_timeout():
    with pytest.raises(SubprocessStalled):
        jobcontrol.run_capture(["sleep", "30"], timeout=0.5)


def test_process_started_after_cancel_is_killed_immediately():
    control = JobControl("uji", timeout=600)
    control.cancel("dibatalkan")
    with jobcontrol.activate(control):
        proc = jobcontrol.popen(["sleep", "30"])
        assert proc.wait(10) is not None
        jobcontrol.release(proc)


def test_without_active_job_check_is_noop():
    jobcontrol.check()  # tidak ada job aktif: tidak boleh error
    assert jobcontrol.run_capture(["true"]).returncode == 0


def test_extend_deadline():
    control = JobControl("uji", timeout=0.1)
    control.extend_deadline(600)
    time.sleep(0.2)
    control.check()  # tidak timeout karena batas waktu diperpanjang
    control.extend_deadline(0)
    with pytest.raises(JobCancelled):
        control.check()


def test_whisper_loop_stops_on_cancel(sample_video, monkeypatch):
    """Whisper tidak bisa di-kill dari luar: pembatalan dicek di antara segmen."""
    produced = []

    class SlowModel:
        def transcribe(self, audio, **kwargs):
            def segments():
                for i in range(1000):
                    time.sleep(0.05)
                    produced.append(i)
                    yield SimpleNamespace(start=i, end=i + 1, text=" kata", words=[])
            return segments(), SimpleNamespace(language="id")

    monkeypatch.setattr(transcribe, "_get_model", lambda force_cpu=False: SlowModel())
    monkeypatch.setattr(media, "decode_audio", lambda path: np.zeros(16000, dtype=np.float32))
    control = JobControl("uji", timeout=600)

    def work():
        with jobcontrol.activate(control):
            transcribe.transcribe(sample_video, 1000, "id")

    t, box = _run_in_thread(work)
    time.sleep(0.5)
    control.cancel("dibatalkan")
    t.join(10)
    assert isinstance(box.get("error"), JobCancelled)
    assert len(produced) < 100  # berhenti cepat, tidak menyelesaikan 1000 segmen


def test_cancel_all_on_shutdown():
    marker = f"vetest-{uuid.uuid4().hex[:8]}"
    control = JobControl("uji", timeout=600)

    def work():
        with jobcontrol.activate(control):
            media.run_ffmpeg(_endless_ffmpeg_args(marker), duration=1000)

    t, box = _run_in_thread(work)
    deadline = time.time() + 10
    while not _alive(marker) and time.time() < deadline:
        time.sleep(0.1)
    assert jobcontrol.cancel_all() >= 1
    t.join(15)
    assert isinstance(box.get("error"), JobCancelled)
    assert not _alive(marker)
