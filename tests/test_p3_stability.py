"""P3 final: stabilitas produksi (starvation, fallback GPU, biaya monitoring, kebocoran resource)."""
import os
import stat
import threading
import time
import uuid

import pytest
from sqlalchemy import event, insert, update

from app import db, jobs, monitor, resources, storage, transcribe, worker

SHORT = {"request": {"start": 0, "end": 20}}
LONG = {"request": {"start": 0, "end": 300}}


def _q():
    return f"uji-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def pid():
    return storage.create_project("uji-stabil")["id"]


def _submit(pid, queue, unit, payload, created):
    job = jobs.submit("render", "render", pid, payload, unit, queue=queue, unit_id=unit)
    with db.engine().begin() as c:
        c.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(created=created))
    return job["id"]


# ------------------------------------------------------------------ starvation

def test_small_unit_never_starves_under_flood(pid):
    """Unit A terus mengirim render; satu job unit B tetap diambil pada giliran berikutnya."""
    q, t = _q(), time.time()
    for _ in range(3):
        _submit(pid, q, "A", SHORT, t)
    b = None
    for step in range(10):
        now = t + step * 30
        for _ in range(2):  # banjir terus-menerus
            _submit(pid, q, "A", SHORT, now)
        if step == 2:
            b = _submit(pid, q, "B", LONG, now)  # job panjang (prioritas rendah) dari unit kecil
        job = jobs.claim((q,), "w", now=now + 1)
        jobs.finish(job["id"], result={})
        if job["id"] == b:
            assert step == 2  # langsung giliran berikutnya, walau A punya 20+ job menunggu
            return
    pytest.fail("job unit B tidak pernah diambil")


def test_long_job_within_unit_eventually_runs(pid):
    """Di dalam satu unit, render panjang tidak kalah selamanya dari render pendek yang terus datang:
    aging menaikkan prioritasnya (20 vs 10 -> menang setelah ±10 x AGING_SECONDS)."""
    q, t = _q(), time.time()
    long_id = _submit(pid, q, "A", LONG, t)
    limit = int(11 * jobs.AGING_SECONDS / 30) + 2
    for step in range(limit):
        now = t + step * 30
        _submit(pid, q, "A", SHORT, now)  # job pendek baru setiap 30 dtk
        job = jobs.claim((q,), "w", now=now + 1)
        jobs.finish(job["id"], result={})
        if job["id"] == long_id:
            assert (now - t) <= 11 * jobs.AGING_SECONDS
            return
    pytest.fail("render panjang kelaparan")


# ------------------------------------------------------------------ GPU fallback

@pytest.mark.parametrize("script", [
    "#!/bin/sh\nexit 9\n",                                   # driver rusak
    "#!/bin/sh\necho 'NVIDIA-SMI has failed because it couldn't communicate with the driver'\n",
    "#!/bin/sh\nsleep 30\n",                                 # macet: dihentikan oleh timeout
])
def test_broken_nvidia_smi_means_cpu(tmp_path, monkeypatch, script):
    fake = tmp_path / "nvidia-smi"
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(resources, "_gpu_cache", (0.0, None))
    started = time.time()
    assert resources.gpus() == []
    assert time.time() - started < 8
    monkeypatch.setattr(transcribe, "WHISPER_DEVICE", "auto")
    assert transcribe.pick_device() == "cpu"


def test_cuda_load_failure_falls_back_to_cpu(monkeypatch):
    loaded = []

    def fake_load(device):
        if device == "cuda":
            raise RuntimeError("CUDA driver version is insufficient")
        loaded.append(device)
        return object()

    monkeypatch.setattr(transcribe, "_load", fake_load)
    monkeypatch.setattr(transcribe, "_model", None)
    monkeypatch.setattr(transcribe, "pick_device", lambda: "cuda")
    assert transcribe._get_model() is not None and loaded == ["cpu"]


# ------------------------------------------------------------------ biaya monitoring

def _count_queries(fn):
    """Query yang dijalankan fn sendiri (worker bawaan di thread lain ikut memakai engine yang sama)."""
    n, me = [0], threading.get_ident()

    def before(*_a, **_k):
        if threading.get_ident() == me:
            n[0] += 1

    event.listen(db.engine(), "before_cursor_execute", before)
    try:
        fn()
    finally:
        event.remove(db.engine(), "before_cursor_execute", before)
    return n[0]


def test_monitoring_query_count_is_bounded(pid):
    """Jumlah query snapshot monitoring tetap, tidak bertambah dengan banyaknya job/unit."""
    first = _count_queries(monitor.snapshot)
    now = time.time()
    with db.engine().begin() as c:
        c.execute(insert(db.jobs), [dict(id=uuid.uuid4().hex[:12], task="render", kind="x", queue="render",
                                          project_id=pid, unit_id=f"u{i % 40}", label="", payload="{}",
                                          status="selesai", progress=100, message="", attempts=1,
                                          created=now - 100, started=now - 90, finished=now - 30)
                                     for i in range(2000)])
    second = _count_queries(monitor.snapshot)
    assert second == first and second <= 20


def test_reading_metrics_never_writes(pid):
    """Membaca monitoring tidak menulis ke database (dulu: DELETE worker lama di setiap baca)."""
    writes, me = [0], threading.get_ident()

    def before(_conn, _cursor, statement, *_a):
        if threading.get_ident() == me and statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes[0] += 1

    event.listen(db.engine(), "before_cursor_execute", before)
    try:
        monitor.snapshot()
        monitor.list_workers()
    finally:
        event.remove(db.engine(), "before_cursor_execute", before)
    assert writes[0] == 0


def test_stale_worker_rows_pruned_on_register():
    old = f"w-lama-{uuid.uuid4().hex[:6]}"
    monitor.register(old, ("render",))
    with db.engine().begin() as c:
        c.execute(update(db.workers).where(db.workers.c.name == old).values(heartbeat=time.time() - 3 * 86400))
    monitor.register(f"w-baru-{uuid.uuid4().hex[:6]}", ("render",))
    assert old not in {w["name"] for w in monitor.list_workers()}


# ------------------------------------------------------------------ kebocoran resource

def test_worker_stop_leaves_no_running_job_or_thread_budget(pid, monkeypatch):
    q = _q()
    name = f"w-bersih-{q}"
    monkeypatch.setattr(worker, "_run_task", lambda job: (time.sleep(0.3), {})[1])
    ids = [_submit(pid, q, "A", SHORT, time.time()) for _ in range(3)]
    stop = threading.Event()
    t = threading.Thread(target=worker.loop, args=((q,), name, stop, 0.1), daemon=True)
    t.start()
    deadline = time.time() + 15
    while any(jobs.get(j)["status"] != "selesai" for j in ids) and time.time() < deadline:
        time.sleep(0.1)
    stop.set()
    t.join(10)
    assert all(jobs.get(j)["status"] == "selesai" and jobs.get(j)["attempts"] == 1 for j in ids)
    w = {r["name"]: r for r in monitor.list_workers()}[name]
    assert w["state"] == "berhenti" and w["job_id"] is None and w["jobs_ok"] == 3
    assert resources.cpu_threads() == resources.fair_share()  # anggaran thread job tidak tertinggal
