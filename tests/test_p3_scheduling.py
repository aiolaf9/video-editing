"""P3: pemisahan antrean, penjadwalan adil, batas resource, backpressure, dan monitoring."""
import threading
import time
import uuid

import pytest
from conftest import login_client
from sqlalchemy import insert, update

from app import config, db, jobcontrol, jobs, media, monitor, pipeline, resources, storage, transcribe, worker
from app.schemas import RenderRequest

SHORT = {"request": {"start": 0, "end": 20}}
LONG = {"request": {"start": 0, "end": 300}}


def _q():
    return f"uji-{uuid.uuid4().hex[:6]}"


def _project():
    return storage.create_project("uji-p3")["id"]


def _submit(queue, unit, payload=SHORT, task="render", created=None):
    job = jobs.submit(task, "render", _PID, payload, f"{unit}", queue=queue, unit_id=unit)
    if created is not None:
        with db.engine().begin() as c:
            c.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(created=created))
    return job["id"]


@pytest.fixture(autouse=True)
def _project_for_jobs():
    global _PID
    _PID = _project()


def _claim_order(queue, n, start=None):
    """Satu worker: ambil -> selesai -> ambil lagi. Mengembalikan label (unit) sesuai urutan."""
    order, t = [], start or time.time()
    for i in range(n):
        job = jobs.claim((queue,), "w-uji", now=t + i)
        if job is None:
            break
        order.append(job["label"])
        jobs.finish(job["id"], result={})
    return order


# ------------------------------------------------------------------ antrean

def test_queue_routing_and_legacy_names():
    assert jobs.queue_for("analyze") == "analisis" and jobs.queue_for("render") == "render"
    assert jobs.queue_for("download") == "io"
    assert jobs.expand_queues(["berat", "unduh"]) == ("render", "analisis", "io")  # render didahulukan
    assert jobs.expand_queues(["analisis", "berat"]) == ("analisis", "render")  # urutan = prioritas
    pid = _project()
    assert jobs.submit("render", "render", pid, SHORT, "x", queue="berat")["queue"] == "render"
    assert jobs.submit("analyze", "analisis", pid, {}, "x")["queue"] == "analisis"
    # Job dari versi sebelum P3 yang masih aktif dipindah; riwayat dibiarkan.
    with db.engine().begin() as c:
        for jid, task, queue, status in (("lama0analis", "analyze", "berat", "antre"),
                                         ("lama0unduh0", "download", "unduh", "antre"),
                                         ("lama0selesa", "render", "berat", "selesai")):
            c.execute(insert(db.jobs).values(id=jid, task=task, kind="x", queue=queue, project_id=pid, label="",
                                             payload="{}", status=status, progress=0, message="", attempts=0,
                                             created=time.time()))
    jobs.migrate_legacy_queues()
    assert jobs.get("lama0analis")["queue"] == "analisis" and jobs.get("lama0unduh0")["queue"] == "io"
    assert jobs.get("lama0selesa")["queue"] == "berat"
    for jid in ("lama0analis", "lama0unduh0"):
        jobs.request_cancel(jid)


def test_long_analysis_does_not_block_short_render(monkeypatch):
    """Worker analisis sibuk dengan video panjang; render pendek tetap langsung dikerjakan worker render."""
    qa, qr = _q(), _q()
    started, finished = {}, {}

    def fake_task(job):
        started[job.task] = time.time()
        end = time.time() + (6 if job.task == "analyze" else 0.2)
        while time.time() < end:
            jobcontrol.check()
            time.sleep(0.05)
        finished[job.task] = time.time()
        return {}

    monkeypatch.setattr(worker, "_run_task", fake_task)
    stop = threading.Event()
    for q in (qa, qr):
        threading.Thread(target=worker.loop, args=((q,), f"w-{q}", stop, 0.1), daemon=True).start()
    try:
        jobs.submit("analyze", "analisis", _PID, {}, "A", queue=qa, unit_id="unit-a")
        time.sleep(0.5)
        jobs.submit("render", "render", _PID, SHORT, "B", queue=qr, unit_id="unit-b")
        deadline = time.time() + 10
        while "render" not in finished and time.time() < deadline:
            time.sleep(0.05)
        assert "render" in finished and "analyze" not in finished  # render selesai saat analisis masih jalan
        assert started["render"] - started["analyze"] < 2
    finally:
        stop.set()


# ------------------------------------------------------------------ penjadwalan

def test_round_robin_between_workspaces():
    q = _q()
    for _ in range(5):
        _submit(q, "A")
    _submit(q, "B")
    _submit(q, "C")
    assert _claim_order(q, 7) == ["A", "B", "C", "A", "A", "A", "A"]


def test_fewest_running_unit_first():
    q = _q()
    for _ in range(4):
        _submit(q, "A")
    _submit(q, "B")
    first = jobs.claim((q,), "w1")
    second = jobs.claim((q,), "w2")  # A sudah punya 1 job berjalan, B belum
    assert (first["label"], second["label"]) == ("A", "B")


def test_short_render_before_long_render():
    q = _q()
    _submit(q, "A", LONG)
    _submit(q, "A", SHORT)
    job = jobs.claim((q,), "w")
    assert jobs.get(job["id"])["priority"] == 10


def test_aging_prevents_starvation():
    q = _q()
    old_long = _submit(q, "A", LONG, created=time.time() - 30 * 60)  # 20 - 30 menit = -10
    _submit(q, "A", SHORT)                                           # 10
    assert jobs.claim((q,), "w")["id"] == old_long


def test_running_limit_per_workspace(monkeypatch):
    monkeypatch.setattr(jobs, "MAX_RUNNING_PER_UNIT", 2)
    q = _q()
    for _ in range(4):
        _submit(q, "A")
    assert jobs.claim((q,), "w1") and jobs.claim((q,), "w2")
    assert jobs.claim((q,), "w3") is None   # unit A sudah 2 berjalan; job lain menunggu
    _submit(q, "B")
    assert jobs.claim((q,), "w3")["label"] == "B"  # unit lain tetap dilayani


def test_global_concurrency_limit(monkeypatch):
    q = _q()
    monkeypatch.setattr(jobs, "MAX_CONCURRENT", {q: 1})
    _submit(q, "A")
    _submit(q, "B")
    first = jobs.claim((q,), "w1")
    assert jobs.claim((q,), "w2") is None
    jobs.finish(first["id"], result={})
    assert jobs.claim((q,), "w2")["label"] == "B"


def test_concurrent_claims_respect_limits(monkeypatch):
    """12 worker mengambil bersamaan (SQLite & PostgreSQL): tidak ada job ganda, batas tidak terlewati."""
    q = _q()
    monkeypatch.setattr(jobs, "MAX_RUNNING_PER_UNIT", 2)
    monkeypatch.setattr(jobs, "MAX_CONCURRENT", {q: 5})
    for unit in ("A", "B", "C"):
        for _ in range(10):
            _submit(q, unit)
    got, barrier = [], threading.Barrier(12)

    def grab(i):
        barrier.wait()
        job = jobs.claim((q,), f"w{i}")
        if job:
            got.append(job)

    threads = [threading.Thread(target=grab, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    ids = [j["id"] for j in got]
    assert len(ids) == len(set(ids)) == 5
    per_unit = {u: sum(j["label"] == u for j in got) for u in ("A", "B", "C")}
    assert max(per_unit.values()) <= 2 and min(per_unit.values()) >= 1


def test_cancellation_under_load(monkeypatch):
    q = _q()
    ran = []

    def slow(job):
        ran.append(job.id)
        while True:
            jobcontrol.check()
            time.sleep(0.05)

    monkeypatch.setattr(worker, "_run_task", slow)
    ids = [_submit(q, unit) for unit in ("A", "B", "C") for _ in range(4)]
    stop = threading.Event()
    threading.Thread(target=worker.loop, args=((q,), "w-batal", stop, 0.1), daemon=True).start()
    try:
        deadline = time.time() + 10
        while not ran and time.time() < deadline:
            time.sleep(0.05)
        t0 = time.time()
        running = [j for j in ids if jobs.get(j)["status"] == "jalan"]
        for jid in [j for j in ids if j not in running] + running:  # yang menunggu dulu, lalu yang berjalan
            jobs.request_cancel(jid)
        while time.time() - t0 < 15 and any(jobs.get(j)["status"] not in jobs.DONE for j in ids):
            time.sleep(0.1)
        assert all(jobs.get(j)["status"] == "batal" for j in ids)
        assert time.time() - t0 < 8
        time.sleep(0.5)
        assert len(ran) == 1  # job yang dibatalkan saat antre tidak pernah dijalankan
    finally:
        stop.set()


# ------------------------------------------------------------------ resource

def test_low_memory_defers_jobs(monkeypatch):
    q = _q()
    monkeypatch.setattr(resources, "memory", lambda: {"total": 4 * 2**30, "available": 300 * 2**20,
                                                      "used": 3.7 * 2**30})
    monkeypatch.setattr(worker, "_run_task", lambda job: {})
    jid = _submit(q, "A")
    stop = threading.Event()
    name = f"w-ram-{q}"
    threading.Thread(target=worker.loop, args=((q,), name, stop, 0.1), daemon=True).start()
    try:
        time.sleep(2.5)
        assert jobs.get(jid)["status"] == "antre"  # menunggu, bukan dijalankan lalu dibunuh OOM
        w = {r["name"]: r for r in monitor.list_workers()}[name]
        assert w["state"] == "menunggu" and "RAM" in (w["reason"] or "")
        monkeypatch.setattr(resources, "memory", lambda: {"total": 16 * 2**30, "available": 12 * 2**30,
                                                          "used": 4 * 2**30})
        deadline = time.time() + 10
        while jobs.get(jid)["status"] != "selesai" and time.time() < deadline:
            time.sleep(0.1)
        assert jobs.get(jid)["status"] == "selesai"
    finally:
        stop.set()


def test_cpu_saturation_defers_then_proceeds(monkeypatch):
    monkeypatch.setattr(resources, "CPU_MAX_LOAD_PER_CORE", 1.5)  # opsional (bawaan mati)
    monkeypatch.setattr(resources, "load_per_core", lambda: 4.0)
    monkeypatch.setattr(resources, "memory", lambda: None)
    allowed, reason = worker.admitted(("render", "io"), None)
    assert allowed == ("io",) and "CPU" in reason  # unduhan tidak memakai CPU berat: tetap jalan
    allowed, _ = worker.admitted(("render",), time.time() - config.CPU_WAIT_MAX - 1)
    assert allowed == ("render",)  # tidak menunggu selamanya
    monkeypatch.setattr(resources, "CPU_MAX_LOAD_PER_CORE", 0)
    assert worker.admitted(("render",), None)[0] == ("render",)  # bawaan: tidak ditunda karena load


def test_thread_budget(monkeypatch):
    monkeypatch.setattr(resources, "WORKER_THREADS", 0)
    monkeypatch.setattr(resources, "HOST_HEAVY_WORKERS", 2)
    monkeypatch.setattr(resources, "effective_cpus", lambda: 8)
    assert resources.cpu_threads() == 4                 # tanpa job: jatah adil 8/2
    assert resources.plan_job_threads(0, 0) == 8        # host kosong: pakai semua core (work-conserving)
    assert resources.cpu_threads() == 8
    assert resources.plan_job_threads(8, 1) == 4        # 1 job lain memakai semua core: bagi dua
    assert resources.plan_job_threads(12, 2) == 2       # 2 job lain: sepertiga
    assert resources.plan_job_threads(3, 1) == 5        # sebagian core kosong: pakai yang kosong
    assert resources.plan_job_threads(8) == 4           # jumlah job tidak diketahui: jatah 8/HOST_HEAVY_WORKERS
    resources.clear_job_threads()
    monkeypatch.setattr(resources, "WORKER_THREADS", 3)
    assert resources.cpu_threads() == 3
    args = media.limit_threads(["-i", "in.mp4", "-c:v", "libx264", "out.mp4"])
    assert args[:2] == ["-filter_threads", "3"] and args[-3:] == ["-threads", "3", "out.mp4"]
    explicit = ["-i", "in.mp4", "-threads", "1", "out.mp4"]
    assert media.limit_threads(explicit) == explicit


def test_worker_priority_per_queue():
    """nice per thread: render didahulukan, analisis paling rendah (thread uji ini saja yang berubah)."""
    import os

    box = {}

    def run():
        box["n"] = resources.lower_priority(("analisis",))
        box["actual"] = os.getpriority(os.PRIO_PROCESS, threading.get_native_id())

    t = threading.Thread(target=run)
    t.start()
    t.join()
    assert box["n"] == box["actual"] >= 15
    assert os.getpriority(os.PRIO_PROCESS, threading.get_native_id()) < 15  # thread lain tidak terpengaruh


def test_ffmpeg_really_runs_with_thread_limit(monkeypatch):
    seen = []
    real = jobcontrol.popen

    def spy(cmd, **kw):
        seen.append(cmd)
        return real(cmd, **kw)

    monkeypatch.setattr(jobcontrol, "popen", spy)
    monkeypatch.setattr(resources, "WORKER_THREADS", 2)
    media.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=1", "-f", "null", "-"], 1)
    cmd = seen[0]
    assert cmd[cmd.index("-threads") + 1] == "2" and cmd[cmd.index("-filter_threads") + 1] == "2"


def test_gpu_never_assumed(monkeypatch):
    monkeypatch.setattr(transcribe, "WHISPER_DEVICE", "auto")
    monkeypatch.setattr(resources, "gpus", lambda max_age=30: [])
    assert transcribe.pick_device() == "cpu"
    monkeypatch.setattr(resources, "gpus", lambda max_age=30: [
        {"index": 0, "name": "T4", "mem_total_mb": 15360, "mem_used_mb": 14800, "util_pct": 90}])
    assert transcribe.pick_device() == "cpu"  # VRAM hampir penuh
    monkeypatch.setattr(resources, "gpus", lambda max_age=30: [
        {"index": 0, "name": "T4", "mem_total_mb": 15360, "mem_used_mb": 1000, "util_pct": 5}])
    assert transcribe.pick_device() == "cuda"


def test_nvenc_request_without_gpu_uses_cpu(monkeypatch):
    monkeypatch.setattr(resources, "gpus", lambda max_age=30: [])
    used = []

    def stop_here(*args, **kw):
        used.append(args[2].encoder)
        raise RuntimeError("cukup")

    monkeypatch.setattr(pipeline, "build_command", stop_here)
    pid = _project()
    (storage.project_dir(pid) / "source.mp4").write_bytes(b"x")
    storage.update_project(pid, source="source.mp4", status="siap", duration=10, width=1280, height=720)

    class J:
        def stage(self, *a):
            return lambda f: None

        def update(self, *a):
            pass

    req = RenderRequest(start=0, end=5, encoder="nvidia", reframe="manual", animations=False,
                        subtitles={"enabled": False})
    with pytest.raises(RuntimeError):
        pipeline.render(J(), pid, req)
    assert used == ["cpu"]


# ------------------------------------------------------------------ backpressure API

@pytest.fixture
def ready(client, sample_video, fake_transcript, monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("p3.mp4", fh, "video/mp4")}).json()
    deadline = time.time() + 90
    while client.get(f"/api/jobs/{res['job']['id']}").json()["status"] not in ("selesai", "gagal"):
        assert time.time() < deadline
        time.sleep(0.2)
    return res["project"]["id"]


def test_backpressure_per_unit_and_server(client, ready, monkeypatch):
    monkeypatch.setattr(jobs, "claim", lambda *a, **k: None)  # worker bawaan berhenti mengambil: antrean menumpuk
    body = {"start": 0, "end": 4, "subtitles": {"enabled": False}, "animations": False, "reframe": "manual"}
    monkeypatch.setattr(jobs, "MAX_QUEUED_PER_UNIT", 2)
    codes = [client.post(f"/api/projects/{ready}/render", json=body).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = client.post(f"/api/projects/{ready}/render", json=body)
    assert r.json()["queue_full"] and "penuh" in r.json()["detail"]
    monkeypatch.setattr(jobs, "MAX_QUEUED_PER_UNIT", 0)
    monkeypatch.setattr(jobs, "MAX_QUEUE_DEPTH", 2)
    r = client.post(f"/api/projects/{ready}/render", json=body)
    assert r.status_code == 503 and r.headers["retry-after"] == "60"
    for j in jobs.for_project(ready):
        if j["status"] == "antre":
            jobs.request_cancel(j["id"])


# ------------------------------------------------------------------ monitoring

def test_metrics_endpoint(client, ready, monkeypatch):
    snap = client.get("/api/admin/metrics").json()
    assert {"web", "disk", "workers", "queues", "units"} <= set(snap)
    assert set(snap["queues"]) == {"analisis", "render", "io"}
    ana = snap["queues"]["analisis"]
    assert ana["done"] >= 1 and ana["duration"]["analyze"]["p95"] is not None and ana["wait_p95"] is not None
    names = {w["name"]: w for w in snap["workers"]}
    assert any(n.startswith("lokal-render") and w["alive"] for n, w in names.items())
    assert snap["web"]["cpus"] >= 1 and snap["web"]["mem_total"] > 0

    stamp = uuid.uuid4().hex[:8]
    unit = client.post("/api/admin/units", json={"name": f"M {stamp}"}).json()
    client.post("/api/admin/users", json={"email": f"m{stamp}@org.id", "password": "password-123",
                                          "role": "unit_admin", "unit_id": unit["id"]})
    assert login_client(f"m{stamp}@org.id", "password-123").get("/api/admin/metrics").status_code == 403

    from app import main
    assert client.get("/metrics").status_code == 404  # mati bila METRICS_TOKEN kosong
    monkeypatch.setattr(main, "METRICS_TOKEN", "rahasia-metrik")
    assert client.get("/metrics").status_code == 401
    text = client.get("/metrics", headers={"Authorization": "Bearer rahasia-metrik"}).text
    assert 've_queue_jobs{queue="render",state="queued"}' in text and "ve_worker_up" in text


def test_dead_worker_shown_as_down():
    name = f"w-mati-{uuid.uuid4().hex[:6]}"
    monitor.register(name, ("render",))
    with db.engine().begin() as c:
        c.execute(update(db.workers).where(db.workers.c.name == name).values(heartbeat=time.time() - 600))
    w = {r["name"]: r for r in monitor.list_workers()}[name]
    assert not w["alive"] and w["heartbeat_age"] >= 600
