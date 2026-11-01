"""P5.5.2 W5 lapisan 1: admission RAM yang memperhitungkan durasi audio (pipeline lama tidak lagi hanya ukuran model).

Antrean khusus tes ("uji-w5-*") dipakai agar worker bawaan (mode tanam) tidak ikut mengklaim job.
"""
import threading
import time

import pytest

from app import config, jobs, resources, storage, worker

MB = 2**20


def _container(monkeypatch, limit_mb, state):
    """Container ber-batas `limit_mb` dengan pemakaian state["used"] (byte); RSS pohon proses tetap."""
    def mem():
        return {"total": limit_mb * MB, "available": limit_mb * MB - state["used"], "used": state["used"],
                "host_total": 16 * 2**30, "host_available": 10 * 2**30,
                "container_limit": limit_mb * MB, "container_available": limit_mb * MB - state["used"]}
    monkeypatch.setattr(resources, "memory", mem)
    monkeypatch.setattr(resources, "process_tree_usage", lambda root=None: {"rss": 80 * MB, "cpu_seconds": 0,
                                                                              "processes": 1})
    monkeypatch.setattr(resources, "_inflight", {})
    monkeypatch.setattr(resources, "_own_base", None)
    monkeypatch.setattr(resources, "_model_loaded", lambda: False)
    monkeypatch.setattr(worker, "_holds", {})


def _analyze_job(name, duration, queue):
    pid = storage.create_project(name)["id"]
    if duration is not None:
        storage.update_project(pid, duration=duration)
    return jobs.submit("analyze", "analisis", pid, {"language": None}, "Analisis", queue=queue)["id"], pid


# ------------------------------------------------------------------ rumus

def test_need_grows_with_duration_and_chunked_is_bounded():
    legacy = [config.analysis_need_mb(m * 60, "small", chunked=False) for m in (1, 10, 30, 60)]
    assert legacy == sorted(legacy) and legacy[-1] - legacy[0] > 2500       # ±50 MiB per menit audio
    # Pipeline lama: 60 menit small tidak muat di worker-analisis 3 GiB (terukur 3566 MiB RSS di CI #25).
    assert legacy[-1] + config.CONTAINER_MEM_HEADROOM_MB > 3072
    chunked = [config.analysis_need_mb(m * 60, "small", chunked=True) for m in (10, 60, 360)]
    assert chunked[1] == chunked[2] < legacy[-1]                           # mode potongan: tidak tumbuh lagi
    assert chunked[2] + config.CONTAINER_MEM_HEADROOM_MB < 3072
    assert config.analysis_need_mb(None, "small", chunked=False) == config.analysis_need_mb(0, "small", chunked=False)


def test_need_covers_measured_peaks_with_margin():
    """Estimasi tidak boleh di bawah RSS puncak terukur (CI #25, container 12 GiB) untuk model yang diukur."""
    measured = {"tiny": {10: 832, 30: 1784, 60: 3333}, "base": {10: 902, 30: 1809, 60: 3358},
                "small": {10: 1521, 30: 2079, 60: 3566}}
    for model, points in measured.items():
        for minutes, peak in points.items():
            assert config.analysis_need_mb(minutes * 60, model, chunked=False) >= peak, (model, minutes)


def test_job_need_uses_project_duration_and_loaded_model(client, monkeypatch):
    monkeypatch.setattr(resources, "_model_loaded", lambda: False)
    jid, pid = _analyze_job("durasi", 1800, "uji-w5-need")
    data = jobs.get(jid)
    assert resources.job_need_mb(data) == config.analysis_need_mb(1800)
    monkeypatch.setattr(resources, "_model_loaded", lambda: True)
    assert resources.job_need_mb(data) == config.analysis_need_mb(1800) - config.whisper_resident_mb()
    jid2, _ = _analyze_job("tanpa-durasi", None, "uji-w5-need")
    assert resources.job_need_mb(jobs.get(jid2)) == resources.required_mb("uji-w5-need")
    for j in (jid, jid2):
        jobs.request_cancel(j)


# ------------------------------------------------------------------ worker: permanen vs sementara

def test_too_long_video_fails_clearly_instead_of_oom(client, monkeypatch):
    monkeypatch.setattr(config, "WHISPER_CHUNKED", False)
    _container(monkeypatch, 3072, {"used": 100 * MB})
    ran = []
    monkeypatch.setattr(worker, "_run_task", lambda job: ran.append(job.id))
    jid, pid = _analyze_job("60-menit", 3600, "uji-w5-big")
    assert worker.run_one(("uji-w5-big",), "uji-w5-big-worker") is True
    job = jobs.get(jid)
    assert ran == [] and job["status"] == "gagal" and job["error"] == worker.FAIL_TOO_LARGE
    assert "MB" not in job["error"]                                   # tanpa detail teknis untuk pengguna
    assert storage.load_project(pid)["status"] == "gagal"
    assert resources._inflight == {} and worker._holds == {}


def test_temporary_shortage_requeues_and_holds_queue_against_starvation(client, monkeypatch):
    state = {"used": 2000 * MB}
    _container(monkeypatch, 3072, state)
    monkeypatch.setattr(config, "WHISPER_CHUNKED", False)
    ran = []
    monkeypatch.setattr(worker, "_run_task", lambda job: ran.append(job.id) or {})
    big, _ = _analyze_job("30-menit", 1800, "uji-w5-hold")          # butuh ±2915 MiB, muat di 3 GiB bila kosong
    assert worker.run_one(("uji-w5-hold",), "w") is False
    assert jobs.get(big)["status"] == "antre" and jobs.get(big)["message"] == worker.WAIT_TEMPORARY
    assert worker._holds["uji-w5-hold"][0] == big
    small = jobs.submit("render", "render", storage.create_project("kecil")["id"], {"request": {}}, "Render",
                        queue="uji-w5-hold", priority=0)["id"]       # prioritas lebih tinggi dari job besar
    assert worker.run_one(("uji-w5-hold",), "w") is False            # tetap hanya mencoba job besar
    assert ran == [] and jobs.get(small)["status"] == "antre"
    state["used"] = 100 * MB                                         # job lain selesai: RAM lega
    assert worker.run_one(("uji-w5-hold",), "w") is True
    assert ran == [big] and worker._holds == {} and resources._inflight == {}
    assert worker.run_one(("uji-w5-hold",), "w") is True             # penahanan selesai: job kecil jalan
    assert ran == [big, small]


def test_hold_expires_so_queue_is_not_blocked_forever(client, monkeypatch):
    _container(monkeypatch, 3072, {"used": 2000 * MB})
    monkeypatch.setattr(config, "WHISPER_CHUNKED", False)
    ran = []
    monkeypatch.setattr(worker, "_run_task", lambda job: ran.append(job.id) or {})
    big, _ = _analyze_job("30-menit-b", 1800, "uji-w5-exp")
    assert worker.run_one(("uji-w5-exp",), "w") is False
    worker._holds["uji-w5-exp"] = (big, time.time() - 1)             # masa penahanan habis
    small = jobs.submit("render", "render", storage.create_project("kecil-b")["id"], {"request": {}}, "Render",
                        queue="uji-w5-exp", priority=0)["id"]
    assert worker.run_one(("uji-w5-exp",), "w") is True
    assert ran == [small]
    jobs.request_cancel(big)


def test_hold_cleared_when_held_job_cancelled(client, monkeypatch):
    _container(monkeypatch, 3072, {"used": 2000 * MB})
    monkeypatch.setattr(config, "WHISPER_CHUNKED", False)
    monkeypatch.setattr(worker, "_run_task", lambda job: {})
    big, _ = _analyze_job("30-menit-c", 1800, "uji-w5-cancel")
    assert worker.run_one(("uji-w5-cancel",), "w") is False
    jobs.request_cancel(big)
    assert worker.run_one(("uji-w5-cancel",), "w") is False and worker._holds == {}


# ------------------------------------------------------------------ penyesuaian setelah probe

def test_adjust_reservation_after_probe(monkeypatch):
    state = {"used": 100 * MB}
    _container(monkeypatch, 3072, state)
    monkeypatch.setattr(config, "WHISPER_CHUNKED", False)
    resources.reserve("J", "analisis", 1265)
    resources.ensure_job_memory("J", 1800)                          # 30 menit: naik ke kebutuhan sebenarnya
    assert resources.reserved_for("J") == config.analysis_need_mb(1800)
    resources.ensure_job_memory("J", 60)                            # tidak pernah turun
    assert resources.reserved_for("J") == config.analysis_need_mb(1800)
    with pytest.raises(resources.CapacityExceeded):
        resources.ensure_job_memory("J", 3600)                      # 60 menit pipeline lama: > 3 GiB
    resources.release("J")
    resources.reserve("K", "analisis", 1265)
    state["used"] = 2500 * MB
    with pytest.raises(resources.CapacityWait):
        resources.ensure_job_memory("K", 1800)
    assert resources.reserved_for("K") == 1265                      # gagal = reservasi tidak berubah
    resources.release("K")
    assert resources._inflight == {}


def test_job_outside_worker_only_checks_permanent_limit(monkeypatch):
    _container(monkeypatch, 3072, {"used": 2900 * MB})
    monkeypatch.setattr(config, "WHISPER_CHUNKED", False)
    resources.ensure_job_memory("tanpa-reservasi", 600)             # tidak ada reservasi yang bocor
    assert resources._inflight == {}
    with pytest.raises(resources.CapacityExceeded):
        resources.ensure_job_memory("tanpa-reservasi", 3600)


@pytest.mark.parametrize("exc,status", [(resources.CapacityWait("RAM penuh"), "antre"),
                                        (resources.CapacityExceeded("batas 3072 MB"), "gagal")])
def test_worker_handles_capacity_errors_from_pipeline(client, monkeypatch, exc, status):
    _container(monkeypatch, 3072, {"used": 100 * MB})

    def task(job):
        raise exc
    monkeypatch.setattr(worker, "_run_task", task)
    jid, _ = _analyze_job(f"probe-{status}", None, f"uji-w5-pipe-{status}")
    worker.run_one((f"uji-w5-pipe-{status}",), "w")
    job = jobs.get(jid)
    assert job["status"] == status and resources._inflight == {}
    if status == "antre":
        assert job["message"] == worker.WAIT_TEMPORARY and worker._holds
        jobs.request_cancel(jid)
    else:
        assert job["error"] == worker.FAIL_TOO_LARGE


def test_safe_analyze_capacity_wait_restores_project_status(client, monkeypatch):
    from app import pipeline

    pid = storage.create_project("status")["id"]
    storage.update_project(pid, status="siap")

    def analyze(job, pid, language=None):
        storage.update_project(pid, status="dianalisis")
        raise resources.CapacityWait("RAM penuh")
    monkeypatch.setattr(pipeline, "analyze", analyze)
    with pytest.raises(resources.CapacityWait):
        pipeline.safe_analyze(None, pid)
    assert storage.load_project(pid)["status"] == "siap"


# ------------------------------------------------------------------ concurrency (mode tanam, banyak thread)

def test_concurrent_workers_admit_only_what_fits(client, monkeypatch):
    """Dua thread worker mengklaim dua job 30 menit bersamaan di container 3 GiB: hanya satu yang boleh jalan."""
    _container(monkeypatch, 3072, {"used": 100 * MB})
    monkeypatch.setattr(config, "WHISPER_CHUNKED", False)
    running, release = [], threading.Event()

    def task(job):
        running.append(job.id)
        release.wait(5)
        return {}
    monkeypatch.setattr(worker, "_run_task", task)
    a, _ = _analyze_job("par-a", 1800, "uji-w5-par")
    b, _ = _analyze_job("par-b", 1800, "uji-w5-par")
    results, start = [], threading.Barrier(2)

    def go(name):
        start.wait()
        results.append(worker.run_one(("uji-w5-par",), name))
    threads = [threading.Thread(target=go, args=(n,)) for n in ("w1", "w2")]
    for t in threads:
        t.start()
    deadline = time.time() + 5
    while len(running) + sum(r is False for r in results) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert len(running) == 1                                    # yang kedua dikembalikan ke antrean
    release.set()
    for t in threads:
        t.join(5)
    statuses = sorted(jobs.get(j)["status"] for j in (a, b))
    assert statuses == ["antre", "selesai"] and resources._inflight == {}
    for j in (a, b):
        jobs.request_cancel(j)
