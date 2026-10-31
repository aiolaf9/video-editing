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
    resources.release(next(iter(resources._inflight)))
    assert resources._inflight == {} and resources.reserved_bytes() == 0


# ------------------------------------------------------------------ reservasi sepanjang hidup job (P5.5.1 D)

def _fake_container(monkeypatch, limit_mb, state):
    def mem():
        return {"total": limit_mb * MB, "available": limit_mb * MB - state["used"], "used": state["used"],
                "host_total": 16 * 2**30, "host_available": 10 * 2**30,
                "container_limit": limit_mb * MB, "container_available": limit_mb * MB - state["used"]}
    monkeypatch.setattr(resources, "memory", mem)
    monkeypatch.setattr(resources, "process_tree_usage",
                        lambda root=None: {"rss": state["rss"], "cpu_seconds": 0, "processes": 1})
    monkeypatch.setattr(resources, "_inflight", {})
    monkeypatch.setattr(resources, "_own_base", None)


def test_reservation_survives_until_peak_of_long_job(monkeypatch):
    """Dulu: setelah 60 dtk reservasi hilang dan job kedua lolos walau job pertama belum mencapai puncak (tes ini gagal
    pada implementasi jendela 60 dtk). Kini sisa kebutuhan job A tetap dicadangkan sampai job A selesai."""
    state = {"used": 100 * MB, "rss": 80 * MB}
    _fake_container(monkeypatch, 1500, state)
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    assert resources.admit_memory("render")[0]
    resources.reserve("A", "render")
    clock[0] += 3600                                    # job panjang: satu jam kemudian
    state["used"] += 100 * MB
    state["rss"] += 100 * MB                            # job A baru memakai 100 dari 700 MB
    assert resources.reserved_bytes() == 600 * MB
    ok, why = resources.admit_memory("render")          # 200 + 600 (sisa A) + 700 + 64 > 1500
    assert not ok and "container" in why
    resources.release("A")
    assert resources.admit_memory("render")[0]          # setelah A selesai, kapasitas kembali


def test_actual_usage_and_reservation_are_not_double_counted(monkeypatch):
    state = {"used": 100 * MB, "rss": 80 * MB}
    _fake_container(monkeypatch, 1600, state)
    resources.reserve("A", "render")
    state["used"] += 700 * MB
    state["rss"] += 700 * MB                            # job A memakai seluruh perkiraannya
    assert resources.reserved_bytes() == 0              # tidak dihitung lagi di atas pemakaian aktual
    ok, why = resources.admit_memory("render")          # 800 terpakai + 700 + 64 = 1564 <= 1600
    assert ok, why
    state["used"] += 300 * MB
    state["rss"] += 300 * MB                            # job A melebihi perkiraan: pemakaian aktual yang menentukan
    assert not resources.admit_memory("render")[0]
    resources.release("A")


@pytest.mark.parametrize("ending", ["selesai", "gagal", "batal", "shutdown"])
def test_reservation_released_however_job_ends(client, monkeypatch, ending):
    from app import jobcontrol, jobs, storage, worker

    monkeypatch.setattr(resources, "_inflight", {})
    monkeypatch.setattr(resources, "admit_memory", lambda queue: (True, ""))
    seen = []

    def task(job):
        seen.append(dict(resources._inflight))
        if ending == "gagal":
            raise RuntimeError("uji gagal")
        if ending in ("batal", "shutdown"):
            raise jobcontrol.JobCancelled("dibatalkan" if ending == "batal" else "shutdown")
        return {}

    monkeypatch.setattr(worker, "_run_task", task)
    pid = storage.create_project(f"reservasi-{ending}")["id"]
    jid = jobs.submit("download", "uji", pid, {"url": "https://example.com/v.mp4"}, "Unduh", queue="uji-res")["id"]
    assert worker.run_one(("uji-res",), "uji-res-worker")
    assert list(seen[0]) == [jid]                       # dicadangkan selama job berjalan
    assert resources._inflight == {} and resources._own_base is None
    if ending == "shutdown":
        jobs.request_cancel(jid)                        # job dikembalikan ke antrean: bersihkan


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
    assert ran == [] and job["status"] == "antre" and job["message"] == worker.WAIT_TEMPORARY


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


def _worker(name, queues, incapable=None, state="idle", blocked_since=None, offline=False, reported=True):
    from sqlalchemy import update

    from app import db, monitor

    monitor.register(name, tuple(queues.split(",")))
    if reported:
        monitor.report(name, state, reason="uji" if state == "menunggu" else None, system=True,
                       extra={"incapable": incapable or {}, "blocked_since": blocked_since})
    if offline:
        with db.engine().begin() as c:
            c.execute(update(db.workers).where(db.workers.c.name == name).values(heartbeat=time.time() - 3600))


def _drop_workers(*names):
    from sqlalchemy import delete

    from app import db
    with db.engine().begin() as c:
        c.execute(delete(db.workers).where(db.workers.c.name.in_(names)))


SMALL = {"uji-cap": "batas RAM container 256 MB < kebutuhan 300 MB + ruang aman 64 MB"}


@pytest.mark.parametrize("case,others,expected", [
    ("1. melebihi kapasitas SEMUA worker yang dikenal", [], "konfigurasi"),
    ("2. worker besar offline", [dict(offline=True)], "mampu_offline"),
    ("2b. worker besar berhenti rapi", [dict(state="berhenti", offline=True)], "mampu_offline"),
    ("3. worker besar sedang start (belum melapor)", [dict(reported=False)], "siap"),
    ("4. worker besar sibuk", [dict(state="sibuk")], "siap"),
    ("5. worker besar ada, RAM sementara kurang", [dict(state="menunggu", blocked_since=time.time() - 30)],
     "sementara"),
])
def test_queue_capacity_cases(client, case, others, expected):
    from app import monitor

    names = ["uji-cap-kecil"] + [f"uji-cap-besar-{i}" for i in range(len(others))]
    try:
        _worker(names[0], "uji-cap", SMALL)
        for name, kw in zip(names[1:], others):
            _worker(name, "uji-cap", **kw)
        cap = monitor.queue_capacity()["uji-cap"]
        assert cap["state"] == expected, case
        assert (monitor.unservable_queues().get("uji-cap") is not None) == (expected == "konfigurasi")
    finally:
        _drop_workers(*names)


def test_no_alive_worker_at_all_is_not_a_configuration_failure(client):
    from app import monitor

    try:
        _worker("uji-cap-mati", "uji-cap", SMALL, offline=True)     # hanya worker kecil, dan sedang offline
        assert monitor.queue_capacity()["uji-cap"]["state"] == "tanpa_worker"
        assert "uji-cap" not in monitor.unservable_queues()
    finally:
        _drop_workers("uji-cap-mati")


def _queued_job(queue, age):
    from sqlalchemy import update

    from app import db, jobs, storage

    pid = storage.create_project(f"tidak-muat-{queue}")["id"]
    jid = jobs.submit("download", "uji", pid, {"url": "https://example.com/a.mp4"}, "Unduh", queue=queue)["id"]
    with db.engine().begin() as c:
        c.execute(update(db.jobs).where(db.jobs.c.id == jid).values(created=time.time() - age))
    return jid


def test_unservable_waits_by_default_with_clear_user_message(client, monkeypatch):
    from app import jobs, worker

    monkeypatch.setattr(worker, "UNSERVABLE_FAIL_AFTER_SECONDS", 0)
    monkeypatch.setattr(worker, "_unservable_since", {})
    jid = _queued_job("uji-cap", age=86400)
    try:
        _worker("uji-cap-kecil", "uji-cap", SMALL)
        assert worker.handle_unservable(time.time()) == 0
        assert worker.handle_unservable(time.time() + 10 * 86400) == 0  # sampai kapan pun: tidak digagalkan
        assert worker._unservable_since                                  # tetapi memang terdeteksi & diperingatkan
        job = jobs.get(jid)
        assert job["status"] == "antre" and job["message"] == worker.WAIT_CONFIG
        assert "MB" not in job["message"] and "uji-cap-kecil" not in job["message"]  # tanpa detail server
    finally:
        _drop_workers("uji-cap-kecil")
        jobs.request_cancel(jid)


def test_optional_permanent_failure_needs_continuous_observation(client, monkeypatch):
    from app import jobs, worker

    monkeypatch.setattr(worker, "UNSERVABLE_FAIL_AFTER_SECONDS", 300)
    monkeypatch.setattr(worker, "_unservable_since", {})
    jid = _queued_job("uji-cap", age=3600)
    t0 = time.time()
    try:
        _worker("uji-cap-kecil", "uji-cap", SMALL)
        assert worker.handle_unservable(t0) == 0                     # baru teramati: belum
        # 6. konfigurasi berubah saat job menunggu: worker besar muncul -> hitungan diulang
        _worker("uji-cap-besar", "uji-cap")
        assert worker.handle_unservable(t0 + 200) == 0 and worker._unservable_since == {}
        _drop_workers("uji-cap-besar")
        assert worker.handle_unservable(t0 + 400) == 0                # teramati lagi sejak t0+400, bukan t0
        assert jobs.get(jid)["status"] == "antre"
        # 2. worker besar hanya offline: tetap menunggu walau sudah lewat batas
        _worker("uji-cap-besar", "uji-cap", offline=True)
        assert worker.handle_unservable(t0 + 2000) == 0 and jobs.get(jid)["status"] == "antre"
        assert worker._unservable_since == {}                         # bukan "konfigurasi", tidak dihitung
        _drop_workers("uji-cap-besar")
        assert worker.handle_unservable(t0 + 2100) == 0
        assert worker.handle_unservable(t0 + 2100 + 301) == 1         # 1. pasti tidak muat, teramati >= 300 dtk
        job = jobs.get(jid)
        assert job["status"] == "gagal" and job["error"] == worker.FAIL_CONFIG
    finally:
        _drop_workers("uji-cap-kecil", "uji-cap-besar")


def test_temporary_requeue_message_has_no_server_details(client, monkeypatch):
    from app import jobs, storage, worker

    pid = storage.create_project("admission-pesan")["id"]
    jid = jobs.submit("download", "uji", pid, {"url": "https://example.com/v.mp4"}, "Unduh", queue="uji-pesan")["id"]
    monkeypatch.setattr(resources, "admit_memory", lambda q: (False, "RAM container tersedia 100 MB < 364 MB"))
    assert worker.run_one(("uji-pesan",), "uji-pesan-worker") is False
    job = jobs.get(jid)
    assert job["status"] == "antre" and job["message"] == worker.WAIT_TEMPORARY
    jobs.request_cancel(jid)


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


# ------------------------------------------------------------------ verifikasi lanjutan (P5.5.1 final)

def test_rss_drop_or_page_cache_change_never_makes_admission_optimistic(monkeypatch):
    state = {"used": 100 * MB, "rss": 80 * MB}
    _fake_container(monkeypatch, 1500, state)
    resources.reserve("A", "render")                         # perkiraan 700
    state["rss"] -= 30 * MB                                  # RSS turun di bawah titik awal (GC, unmap)
    assert resources.reserved_bytes() == 700 * MB            # tidak pernah lebih kecil dari perkiraan penuh
    state["rss"] += 30 * MB + 500 * MB                       # naik 500 ...
    state["used"] += 500 * MB
    state["rss"] -= 400 * MB                                 # ... lalu turun 400 (antar-fase job)
    state["used"] -= 400 * MB
    assert resources.reserved_bytes() == 600 * MB            # sisa mengikuti pemakaian saat ini: 700 - 100
    # Page cache: hanya inactive_file yang dikeluarkan; cache aktif tetap dihitung terpakai (konservatif).
    resources.release("A")


def test_page_cache_active_counts_as_used_inactive_does_not(tmp_path):
    root = _cg(tmp_path, 2, 512 * MB, 400 * MB, inactive=0)
    assert resources._cgroup_memory(root) == (512 * MB, 400 * MB)       # cache aktif: tetap terpakai
    root2 = tmp_path / "b"
    root2.mkdir()
    assert resources._cgroup_memory(_cg(root2, 2, 512 * MB, 400 * MB, inactive=300 * MB)) == (512 * MB, 100 * MB)


def test_child_process_memory_counts_towards_reservation(monkeypatch):
    """Subprocess (seperti FFmpeg) termasuk pohon proses: RAM yang sudah dipakainya mengurangi sisa cadangan."""
    import subprocess
    import sys

    monkeypatch.setattr(resources, "_inflight", {})
    monkeypatch.setattr(resources, "_own_base", None)
    resources.reserve("A", "io")                                            # 300 MB
    child = subprocess.Popen([sys.executable, "-c", "import sys,time; b=bytearray(200*2**20); "
                              "b[::4096]=b'x'*len(b[::4096]); print('ok', flush=True); time.sleep(30)"],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ok"
        tree = resources.process_tree_usage()
        assert tree["processes"] >= 2
        remaining = resources.reserved_bytes()
        assert 60 * MB <= remaining <= 130 * MB, remaining / MB             # 300 - ±200 (child) +- noise
    finally:
        child.kill()
        child.wait()
        resources.release("A")


def test_real_ffmpeg_child_is_in_process_tree():
    import subprocess

    before = resources.process_tree_usage()
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=10",
                             "-f", "null", "-"], stdin=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        during = resources.process_tree_usage()
        assert during["processes"] == before["processes"] + 1 and during["rss"] > before["rss"] + 5 * MB
    finally:
        proc.kill()
        proc.wait()


def test_reservation_does_not_survive_crash():
    """Reservasi hanya di memori proses: proses yang mati (crash/SIGKILL) tidak meninggalkan sisa."""
    import subprocess
    import sys

    code = ("import os; from app import resources; resources.reserve('A', 'render'); "
            "assert resources._inflight; os._exit(9)")
    assert subprocess.run([sys.executable, "-c", code], cwd=str(__import__("pathlib").Path(__file__).parents[1]),
                          timeout=60).returncode == 9
    check = "from app import resources; print(len(resources._inflight), resources.reserved_bytes())"
    out = subprocess.run([sys.executable, "-c", check], cwd=str(__import__("pathlib").Path(__file__).parents[1]),
                         capture_output=True, text=True, timeout=60).stdout.split()
    assert out == ["0", "0"]


def test_parallel_jobs_in_one_process_attribution_limit(monkeypatch):
    """Batasan yang didokumentasikan: RSS agregat satu proses tidak bisa diatribusikan per job. Bila job A melampaui
    perkiraannya, kelebihannya mengurangi sisa cadangan job B. Pemakaian aktual cgroup tetap dihitung penuh, jadi
    total yang diperkirakan = terpakai + sisa; optimisme maksimal = kelebihan job A."""
    state = {"used": 100 * MB, "rss": 80 * MB}
    _fake_container(monkeypatch, 3000, state)
    resources.reserve("A", "render")                        # 700
    resources.reserve("B", "render")                        # 700, belum memakai apa pun
    state["rss"] += 900 * MB                                # A memakai 900 (200 di atas perkiraan)
    state["used"] += 900 * MB
    assert resources.reserved_bytes() == 500 * MB           # ideal: 700 (sisa B); optimis 200 = kelebihan A
    resources.release("A")
    resources.release("B")


def test_fail_after_is_read_from_environment(monkeypatch):
    import importlib

    monkeypatch.delenv("UNSERVABLE_FAIL_AFTER_SECONDS", raising=False)
    assert importlib.reload(config).UNSERVABLE_FAIL_AFTER_SECONDS == 0          # bawaan: tidak pernah gagal
    monkeypatch.setenv("UNSERVABLE_FAIL_AFTER_SECONDS", "900")
    assert importlib.reload(config).UNSERVABLE_FAIL_AFTER_SECONDS == 900        # diaktifkan admin
    monkeypatch.delenv("UNSERVABLE_FAIL_AFTER_SECONDS")
    importlib.reload(config)


def test_capable_worker_coming_back_online_resets_and_serves(client, monkeypatch):
    from sqlalchemy import update

    from app import db, jobs, monitor, worker

    monkeypatch.setattr(worker, "UNSERVABLE_FAIL_AFTER_SECONDS", 300)
    monkeypatch.setattr(worker, "_unservable_since", {})
    jid = _queued_job("uji-cap", age=3600)
    t0 = time.time()
    try:
        _worker("uji-cap-kecil", "uji-cap", SMALL)
        assert worker.handle_unservable(t0) == 0 and "uji-cap" in worker._unservable_since   # timer berjalan
        _worker("uji-cap-besar", "uji-cap")                                                # kembali online
        assert monitor.queue_capacity()["uji-cap"]["state"] == "siap"
        assert worker.handle_unservable(t0 + 10_000) == 0 and worker._unservable_since == {}
        with db.engine().begin() as c:                                                     # lalu offline lagi
            c.execute(update(db.workers).where(db.workers.c.name == "uji-cap-besar").values(heartbeat=t0 - 3600))
        assert monitor.queue_capacity()["uji-cap"]["state"] == "mampu_offline"
        assert worker.handle_unservable(t0 + 20_000) == 0 and jobs.get(jid)["status"] == "antre"
    finally:
        _drop_workers("uji-cap-kecil", "uji-cap-besar")
        jobs.request_cancel(jid)
