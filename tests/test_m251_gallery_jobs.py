"""M2.5.1 regresi pekerjaan galeri Motion Library (CI run 43120628624): polling status saat sebuah preset SEDANG
dirender dulu mengantrekannya lagi (render ganda, status "antre" palsu). Kontrak:
  * satu preset dirender paling banyak sekali, berapa kali pun & dari berapa thread pun diminta;
  * status: belum -> antre -> proses -> siap | gagal, tidak pernah kembali ke "antre"/"belum" saat diproses;
  * setelah "siap"/"gagal", preset tidak ada lagi di antrean maupun daftar proses;
  * preset gagal tidak diantrekan ulang dan tidak menghentikan preset lain.
Render diganti tiruan yang ditahan Event (tanpa sleep) agar urutan kejadian deterministik."""
import threading

import pytest

from app import motion_gallery as g


@pytest.fixture
def fake(monkeypatch):
    state = {"calls": [], "done": set(), "fail": set(), "gate": {}, "started": {}}

    def render(pid):
        state["calls"].append(pid)
        state["started"].setdefault(pid, threading.Event()).set()
        state["gate"].setdefault(pid, threading.Event()).wait(30)
        if pid in state["fail"]:
            raise RuntimeError("ffmpeg gagal (tiruan)")
        state["done"].add(pid)

    def gate(pid):
        return state["gate"].setdefault(pid, threading.Event())

    def started(pid):
        return state["started"].setdefault(pid, threading.Event())
    monkeypatch.setattr(g, "render", render)
    monkeypatch.setattr(g, "cached", lambda pid: (pid, pid) if pid in state["done"] else None)
    monkeypatch.setattr(g, "_queue", [])
    monkeypatch.setattr(g, "_running", set())
    monkeypatch.setattr(g, "_failed", {})
    monkeypatch.setattr(g, "_worker", None)
    state["gate_of"], state["started_of"] = gate, started
    yield state
    for e in state["gate"].values():
        e.set()
    g.wait_idle(30)


def test_finished_job_renders_once_despite_polling_while_running(fake):
    assert g.status(["tr.iris"]) == {"tr.iris": "belum"}
    assert g.warm(["tr.iris"]) == 1
    assert fake["started_of"]("tr.iris").wait(10)
    for _ in range(20):                                   # polling UI selama render berjalan
        assert g.warm(["tr.iris"]) == 0
        assert g.status(["tr.iris"]) == {"tr.iris": "proses"}
        assert "tr.iris" not in g._queue
    fake["gate_of"]("tr.iris").set()
    assert g.wait_idle(30)
    assert g.status(["tr.iris"]) == {"tr.iris": "siap"}
    assert fake["calls"] == ["tr.iris"]
    assert "tr.iris" not in g._queue and "tr.iris" not in g._running
    assert g.warm(["tr.iris"]) == 0 and fake["calls"] == ["tr.iris"]          # sudah siap: tidak dirender lagi


def test_failed_job_is_reported_not_retried_and_does_not_block_others(fake):
    fake["fail"].add("tr.blinds")
    for pid in ("tr.blinds", "tr.swipe"):
        fake["gate_of"](pid).set()
    assert g.warm(["tr.blinds", "tr.swipe"]) == 2
    assert g.wait_idle(30)
    assert g.status(["tr.blinds", "tr.swipe"]) == {"tr.blinds": "gagal", "tr.swipe": "siap"}
    assert "ffmpeg gagal" in g._failed["tr.blinds"]
    assert g.warm(["tr.blinds"]) == 0 and fake["calls"] == ["tr.blinds", "tr.swipe"]
    assert not g._queue and not g._running


def test_concurrent_requests_render_each_preset_once(fake):
    ids = ["tr.iris", "tr.swipe", "tr.blinds", "gfx.ring"]
    for pid in ids:
        fake["gate_of"](pid).set()
    barrier = threading.Barrier(8)

    def client(k):
        barrier.wait()
        for _ in range(10):
            g.warm(ids[k % 4:] + ids[: k % 4])            # urutan berbeda per "klien"
            g.status(ids)
    threads = [threading.Thread(target=client, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert g.wait_idle(30)
    assert sorted(fake["calls"]) == sorted(ids), fake["calls"]          # tepat sekali per preset
    assert set(g.status(ids).values()) == {"siap"}
    assert not g._queue and not g._running


def test_status_endpoint_queues_once_and_reports_progress(client, fake):
    r1 = client.get("/api/motion-presets/status", params={"ids": "tr.iris"}).json()
    assert r1["tr.iris"] in ("antre", "proses")                          # baru diantrekan oleh permintaan ini
    assert fake["started_of"]("tr.iris").wait(10)
    for _ in range(10):
        assert client.get("/api/motion-presets/status", params={"ids": "tr.iris"}).json() == {"tr.iris": "proses"}
    fake["gate_of"]("tr.iris").set()
    assert g.wait_idle(30)
    assert client.get("/api/motion-presets/status", params={"ids": "tr.iris"}).json() == {"tr.iris": "siap"}
    assert fake["calls"] == ["tr.iris"]
