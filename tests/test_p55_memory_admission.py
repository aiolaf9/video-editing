"""P5.5.1: admission RAM di container (cgroup v1/v2, page cache, cadangan host vs ruang aman container, job paralel)."""
import importlib
import threading
import time

import pytest

from app import config, resources

MB = 2**20


def _cg(tmp_path, version, limit, used, inactive=0):
    if version == 2:
        (tmp_path / "memory.max").write_text(limit if isinstance(limit, str) else f"{limit}\n")
        (tmp_path / "memory.current").write_text(f"{used}\n")
        (tmp_path / "memory.stat").write_text(f"anon 1\nfile 2\ninactive_file {inactive}\nactive_file 3\n")
    else:
        (tmp_path / "memory").mkdir()
        (tmp_path / "memory/memory.limit_in_bytes").write_text(f"{limit}\n")
        (tmp_path / "memory/memory.usage_in_bytes").write_text(f"{used}\n")
        (tmp_path / "memory/memory.stat").write_text(f"cache 5\ntotal_inactive_file {inactive}\n")
    return tmp_path


@pytest.mark.parametrize("version", [1, 2])
def test_cgroup_limit_and_usage_detected(tmp_path, version):
    root = _cg(tmp_path, version, 512 * MB, 300 * MB, inactive=200 * MB)
    # Page cache tidak aktif (berkas unduhan yang baru ditulis) tidak dihitung sebagai RAM terpakai.
    assert resources._cgroup_memory(root) == (512 * MB, 100 * MB)


@pytest.mark.parametrize("version,limit", [(2, "max\n"), (1, 2**63 - 4096)])
def test_unlimited_cgroup_means_host_rules(tmp_path, version, limit):
    assert resources._cgroup_memory(_cg(tmp_path, version, limit, 100 * MB)) is None


def test_no_cgroup_files_means_host_rules(tmp_path):
    assert resources._cgroup_memory(tmp_path) is None


def test_inactive_larger_than_usage_never_negative(tmp_path):
    assert resources._cgroup_memory(_cg(tmp_path, 2, 512 * MB, 100 * MB, inactive=900 * MB)) == (512 * MB, 0)


def test_page_cache_from_large_download_does_not_starve_worker(tmp_path, monkeypatch):
    """worker-io (512 MB) setelah menulis berkas besar: memory.current ±500 MB, sebagian besar page cache."""
    monkeypatch.setattr(resources, "CGROUP_ROOT", _cg(tmp_path, 2, 512 * MB, 500 * MB, inactive=400 * MB))
    monkeypatch.setattr(resources, "_meminfo", lambda: {"MemTotal": 16 * 2**30, "MemAvailable": 10 * 2**30})
    assert resources.admit("io") == (True, "")
    # Pemakaian NYATA (anonim) yang tinggi tetap menahan job: tidak memulai job yang berisiko langsung OOM.
    (tmp_path / "busy").mkdir()
    monkeypatch.setattr(resources, "CGROUP_ROOT", _cg(tmp_path / "busy", 2, 512 * MB, 480 * MB, inactive=10 * MB))
    ok, why = resources.admit("io")
    assert not ok and "container" in why


def test_host_without_cgroup_keeps_host_reserve(monkeypatch):
    monkeypatch.setattr(resources, "_cgroup_memory", lambda root=None: None)
    monkeypatch.setattr(resources, "_meminfo", lambda: {"MemTotal": 8 * 2**30, "MemAvailable": 700 * MB})
    ok, why = resources.admit("io")                               # 300 + 512 (cadangan host) > 700
    assert not ok and "RAM tersedia" in why and "container" not in why
    monkeypatch.setattr(resources, "_meminfo", lambda: {"MemTotal": 8 * 2**30, "MemAvailable": 900 * MB})
    assert resources.admit("io")[0]


def test_reserve_is_not_double_counted(monkeypatch):
    """Cadangan host diterapkan pada RAM host, ruang aman pada container — tidak dijumlahkan berdua."""
    monkeypatch.setattr(resources, "memory", lambda: {"total": 512 * MB, "available": 370 * MB, "used": 142 * MB,
                                                     "host_available": 10 * 2**30, "container_available": 370 * MB})
    assert resources.admit("io")[0]                               # 300 + 64 <= 370, padahal 300 + 512 + 64 > 370


def test_headroom_is_configurable_with_minimum(monkeypatch):
    for value, expected in (("200", 200), ("1", 32), ("0", 32)):
        monkeypatch.setenv("CONTAINER_MEM_HEADROOM_MB", value)
        assert importlib.reload(config).CONTAINER_MEM_HEADROOM_MB == expected
    monkeypatch.delenv("CONTAINER_MEM_HEADROOM_MB")
    importlib.reload(config)


def test_concurrent_admission_in_one_process_cannot_overcommit(monkeypatch):
    """Dua thread di proses yang sama (mode tanam): hanya satu job io yang muat di 512 MB container."""
    monkeypatch.setattr(resources, "memory", lambda: {"total": 512 * MB, "available": 412 * MB, "used": 100 * MB,
                                                     "host_available": 10 * 2**30, "container_available": 412 * MB})
    monkeypatch.setattr(resources, "_inflight", {})
    barrier, results = threading.Barrier(2), []

    def claim(job_id):
        barrier.wait()
        with resources.ADMISSION_LOCK:
            ok, _ = resources.admit_memory("io")
            if ok:
                resources.reserve(job_id, "io")
        results.append(ok)

    threads = [threading.Thread(target=claim, args=(f"j{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False, True]
    # Setelah masa ramp-up, pemakaian aktual (terukur) yang dihitung: cadangan tidak dihitung dua kali selamanya.
    job, (size, _) = next(iter(resources._inflight.items()))
    resources._inflight[job] = (size, time.time() - config.RAM_RAMP_SECONDS - 1)
    assert resources.reserved_bytes() == 0
    resources.release(job)
    assert resources._inflight == {}


def test_claimed_job_is_requeued_when_memory_no_longer_fits(client, monkeypatch):
    """Job yang diklaim tetapi tidak muat lagi dikembalikan ke antrean dengan pesan jelas, tidak dijalankan."""
    from app import jobs, storage, worker

    pid = storage.create_project("admission")["id"]
    # Antrean khusus tes: worker bawaan (mode tanam) tidak mengambilnya, jadi tidak ada balapan klaim.
    jid = jobs.submit("download", "uji-admission", pid, {"url": "https://example.com/v.mp4"}, "Unduh",
                      queue="uji-admission")["id"]
    ran = []
    monkeypatch.setattr(worker, "_run_task", lambda job: ran.append(job.id))
    monkeypatch.setattr(resources, "admit_memory", lambda queue: (False, "RAM container tersedia 100 MB < 364 MB"))
    assert worker.run_one(("uji-admission",), "uji-admission-worker") is False
    job = jobs.get(jid)
    assert ran == [] and job["status"] == "antre" and "RAM" in job["message"]


# ------------------------------------------------------------------ job tidak boleh menggantung diam-diam (P5.5.1 C)

def _mem(monkeypatch, host_total, limit=None):
    out = {"total": limit or host_total, "available": limit or host_total, "used": 0,
           "host_total": host_total, "host_available": host_total}
    if limit:
        out.update(container_limit=limit, container_available=limit)
    monkeypatch.setattr(resources, "memory", lambda: out)


def test_capacity_shortfall_detects_jobs_that_can_never_fit(monkeypatch):
    _mem(monkeypatch, 16 * 2**30, limit=256 * MB)                 # io butuh 300 + 64 > 256
    assert "batas RAM container 256 MB" in resources.capacity_shortfall("io")
    _mem(monkeypatch, 700 * MB)                                     # tanpa cgroup: 300 + 512 > 700
    assert "RAM host 700 MB" in resources.capacity_shortfall("io")
    _mem(monkeypatch, 16 * 2**30, limit=512 * MB)                   # konfigurasi compose: muat
    assert resources.capacity_shortfall("io") == ""


def test_worker_never_claims_queue_it_can_never_fit(monkeypatch):
    from app import worker

    monkeypatch.setattr(resources, "admit", lambda q: (True, ""))
    allowed, why = worker.admitted(("analisis", "render"), None, {"analisis": "batas RAM container 3072 MB < ..."})
    assert allowed == ("render",) and "tidak akan pernah muat" in why


def _worker(name, queues, incapable=None, state="idle", blocked_since=None):
    from app import monitor

    monitor.register(name, tuple(queues.split(",")))
    monitor.report(name, state, reason="uji" if state == "menunggu" else None, system=True,
                   extra={"incapable": incapable or {}, "blocked_since": blocked_since})


def _drop_workers(*names):
    from sqlalchemy import delete

    from app import db
    with db.engine().begin() as c:
        c.execute(delete(db.workers).where(db.workers.c.name.in_(names)))


def test_unservable_only_when_no_alive_worker_can_serve(client):
    from app import monitor

    q = "uji-cap"
    try:
        _worker("uji-cap-kecil", q, {q: "batas RAM container 256 MB < 364 MB"})
        assert monitor.unservable_queues() == {q: "uji-cap-kecil: batas RAM container 256 MB < 364 MB"}
        _worker("uji-cap-besar", q)                                  # worker lain mampu: jangan gagalkan
        assert q not in monitor.unservable_queues()
    finally:
        _drop_workers("uji-cap-kecil", "uji-cap-besar")
    assert q not in monitor.unservable_queues()                      # tanpa worker hidup: mungkin sedang start


def test_unservable_jobs_fail_with_clear_error_after_grace(client, monkeypatch):
    import time as _t

    from sqlalchemy import update

    from app import db, jobs, storage, worker

    q = "uji-cap2"
    pid = storage.create_project("tidak-muat")["id"]
    old = jobs.submit("download", "uji", pid, {"url": "https://example.com/a.mp4"}, "Unduh", queue=q)["id"]
    new = jobs.submit("download", "uji", pid, {"url": "https://example.com/b.mp4"}, "Unduh", queue=q)["id"]
    with db.engine().begin() as c:
        c.execute(update(db.jobs).where(db.jobs.c.id == old).values(created=_t.time() - 3600))
    try:
        _worker("uji-cap2-kecil", q, {q: "batas RAM container 256 MB < 364 MB"})
        assert worker.fail_unservable() == 1
        failed = jobs.get(old)
        assert failed["status"] == "gagal" and "Tidak ada worker dengan RAM cukup" in failed["error"]
        assert "256 MB" in failed["error"]
        assert jobs.get(new)["status"] == "antre"                    # masih dalam masa tenggang
    finally:
        _drop_workers("uji-cap2-kecil")
        jobs.request_cancel(new)


def test_blocked_worker_is_visible_and_unhealthy_after_threshold(client, capsys):
    import time as _t

    from app import manage, monitor

    host = "uji-blok-host"
    name = f"{host}-1-io"
    try:
        _worker(name, "io", state="menunggu", blocked_since=_t.time() - 120)
        w = next(w for w in monitor.list_workers() if w["name"] == name)
        assert w["blocked"] and 115 <= w["blocked_seconds"] <= 130
        text = monitor.prometheus({**monitor.snapshot(), "workers": [w]})
        assert f've_worker_blocked{{worker="{name}",queues="io"}} 1' in text
        assert "ve_worker_blocked_seconds{" in text
        assert manage.worker_health(60, host=host, max_blocked=600) == 0   # tertahan sebentar: masih sehat
        assert manage.worker_health(60, host=host, max_blocked=60) == 1    # tertahan terlalu lama: tidak sehat
        assert "tertahan" in capsys.readouterr().out
        _worker(name, "io")                                           # kembali siap
        w = next(w for w in monitor.list_workers() if w["name"] == name)
        assert not w["blocked"] and w["blocked_seconds"] is None
        assert manage.worker_health(60, host=host, max_blocked=60) == 0
    finally:
        _drop_workers(name)


def test_loop_reports_blocked_since_and_reason(client, monkeypatch):
    """Loop worker sungguhan: RAM kurang -> status 'menunggu' + alasan + blocked_since di monitoring."""
    import threading
    import time as _t

    from app import monitor, worker

    monkeypatch.setattr(resources, "admit", lambda q: (False, "RAM container tersedia 100 MB < kebutuhan 364 MB"))
    monkeypatch.setattr(resources, "capacity_shortfall", lambda q: "")
    stop = threading.Event()
    name = "uji-loop-blok"
    t = threading.Thread(target=worker.loop, args=(("uji-loop",), name, stop, 0.1), daemon=True)
    t.start()
    try:
        deadline = _t.time() + 10
        w = None
        while _t.time() < deadline:
            w = next((w for w in monitor.list_workers() if w["name"] == name), None)
            if w and w["blocked"]:
                break
            _t.sleep(0.1)
        assert w and w["blocked"] and "364 MB" in w["reason"] and w["system"]["blocked_since"]
    finally:
        stop.set()
        t.join(10)
        _drop_workers(name)
