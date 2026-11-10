"""Desktop M1: mode satu pengguna tanpa login (kunci peluncuran), penjaga Host, proyek tidak dihapus otomatis,
ekspor massal (klip x rasio) menjadi MP4 sungguhan, dan salinan ke folder ekspor."""
import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, jobs, lifecycle, main, pipeline, storage, transcribe
from app.config import PROJECTS_DIR

KEY = "kunci-uji-desktop-0123456789"  # pragma: allowlist secret


def _wait(c, job_id, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("selesai", "gagal", "batal"):
            return job
        time.sleep(0.3)
    raise AssertionError("job timeout")


@pytest.fixture
def desktop(monkeypatch, tmp_path, app_client):
    export = tmp_path / "Videos" / "NH AI Clip Studio"
    monkeypatch.setattr(main, "DESKTOP_MODE", True)
    monkeypatch.setattr(main, "DESKTOP_LAUNCH_KEY", KEY)
    monkeypatch.setattr(main, "EXPORT_DIR", export)
    monkeypatch.setattr(config, "EXPORT_DIR", export)
    main.ensure_desktop_user()
    return export


def _desktop_client():
    return TestClient(main.app, base_url="http://127.0.0.1:47811")


def test_enter_requires_launch_key(desktop):
    c = _desktop_client()
    assert c.get("/api/auth/me").status_code == 401
    assert c.get("/api/desktop/enter").status_code == 404
    assert c.get("/api/desktop/enter", params={"key": "salah"}).status_code == 404
    r = c.get("/api/desktop/enter", params={"key": KEY}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    me = c.get("/api/auth/me").json()
    assert me["desktop"] is True and me["email"] == config.DESKTOP_USER_EMAIL
    assert me["export_dir"] == str(desktop)
    # Peluncuran berikutnya menggantikan sesi lama (sesi tidak menumpuk).
    old = c.cookies.get("ve_session")
    c2 = _desktop_client()
    c2.get("/api/desktop/enter", params={"key": KEY}, follow_redirects=False)
    assert c2.get("/api/auth/me").status_code == 200
    c.cookies.set("ve_session", old)
    assert c.get("/api/auth/me").status_code == 401


def test_enter_disabled_outside_desktop(app_client, monkeypatch):
    monkeypatch.setattr(main, "DESKTOP_LAUNCH_KEY", KEY)
    r = TestClient(main.app).get("/api/desktop/enter", params={"key": KEY}, follow_redirects=False)
    assert r.status_code == 404                                          # versi web tidak berubah
    assert TestClient(main.app).get("/api/desktop/info").json() == {"desktop": False}


def test_desktop_rejects_foreign_host_login_and_sw(desktop):
    evil = TestClient(main.app, base_url="http://evil.example:47811")
    assert evil.get("/api/desktop/enter", params={"key": KEY}).status_code == 403   # DNS rebinding
    assert evil.get("/").status_code == 403
    c = _desktop_client()
    assert c.get("/api/desktop/info").json() == {"desktop": True}
    login = c.post("/api/auth/login", json={"email": config.DESKTOP_USER_EMAIL, "password": "apa-saja-123"})
    assert login.status_code == 404                                      # tidak ada login di desktop
    assert c.get("/sw.js").status_code == 404                            # tanpa cache offline PWA
    assert TestClient(main.app, base_url="http://localhost:5000").get("/api/desktop/info").status_code == 200


def test_desktop_cleanup_keeps_projects(desktop, fake_transcript):
    pid = storage.create_project("desktop-simpan", None)["id"]
    folder = PROJECTS_DIR / pid
    (folder / "source.mp4").write_bytes(b"\0" * 1024)
    (folder / "renders").mkdir(exist_ok=True)
    (folder / "renders" / "tmp_sisa").mkdir()
    storage.write_data(pid, "transcript", fake_transcript)
    storage.update_project(pid, status="siap", duration=8.0, source="source.mp4")
    far = time.time() + 400 * 86400                                      # lebih dari setahun kemudian
    lifecycle.desktop_cleanup(now=far)
    assert (folder / "source.mp4").exists() and storage.read_data(pid, "transcript")
    assert storage.load_project(pid)["status"] == "siap"
    storage.delete_project(pid)


def test_safe_export_names():
    assert pipeline._safe_name('Klip 1 (9:16)') == "Klip 1 (9 16)"
    assert pipeline._safe_name('a/b\\c:d*e?f"g<h>|') == "a b c d e f g h"
    assert pipeline._safe_name("CON") == "_CON"
    assert pipeline._safe_name("nul.txt") == "_nul.txt"
    assert pipeline._safe_name("  titik...  ") == "titik"
    assert len(pipeline._safe_name("x" * 300)) == 80


def _probe(path):
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                      "stream=width,height,codec_name", "-show_entries", "format=duration",
                                      "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout)


def test_render_batch_real_mp4_and_export_copies(desktop, sample_video, fake_transcript, monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    c = _desktop_client()
    c.get("/api/desktop/enter", params={"key": KEY}, follow_redirects=False)
    with sample_video.open("rb") as fh:
        res = c.post("/api/projects", files={"file": ("Ceramah Pagi.mp4", fh, "video/mp4")}).json()
    pid = res["project"]["id"]
    assert _wait(c, res["job"]["id"])["status"] == "selesai"
    clips = c.post(f"/api/projects/{pid}/clips/suggest", json={"target": 5}).json()
    assert clips and all("score" in k and k.get("reasons") for k in clips)   # rekomendasi dengan skor & alasan

    state = c.get(f"/api/projects/{pid}/recipe").json()
    rec = state["recipe"]
    rec["subtitles"]["style"] = "pop"
    v = c.put(f"/api/projects/{pid}/recipe", json={"version": state["version"], "recipe": rec}).json()["version"]
    v = c.put(f"/api/projects/{pid}/recipe", json={"version": v, "recipe": rec}).json()["version"]
    body = {"version": v, "aspects": ["9:16", "1:1", "16:9"],
            "clips": [{"start": 0.4, "end": 3.0, "title": "Klip #1"}, {"start": 3.0, "end": 6.5, "title": "Klip #2"}]}
    assert c.post(f"/api/projects/{pid}/render-batch", json={**body, "version": v - 1}).status_code == 409
    assert c.post(f"/api/projects/{pid}/render-batch",
                  json={**body, "aspects": ["9:16", "9:16"]}).status_code == 422          # rasio ganda

    monkeypatch.setattr(main, "MAX_PENDING_PER_USER", 5)
    before = jobs.pending_for_user(c.get("/api/auth/me").json()["id"])
    assert c.post(f"/api/projects/{pid}/render-batch", json=body).status_code == 429   # 6 > 5: tidak ada yang antre
    assert jobs.pending_for_user(c.get("/api/auth/me").json()["id"]) == before
    monkeypatch.setattr(main, "MAX_PENDING_PER_USER", 150)

    r = c.post(f"/api/projects/{pid}/render-batch", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 6
    for job in r.json()["jobs"]:
        done = _wait(c, job["id"])
        assert done["status"] == "selesai", done

    sizes = {"9x16": (1080, 1920), "1x1": (1080, 1080), "16x9": (1920, 1080)}
    renders = c.get(f"/api/projects/{pid}").json()["renders"]
    assert len(renders) == 6
    for item in renders:
        aspect = item["title"].rsplit("(", 1)[1].rstrip(")")
        info = _probe(PROJECTS_DIR / pid / "renders" / item["file"])
        stream = info["streams"][0]
        assert (stream["width"], stream["height"]) == sizes[aspect] and stream["codec_name"] == "h264"
        assert 2.0 < float(info["format"]["duration"]) < 4.2
    exported = sorted(p.name for p in (desktop / "Ceramah Pagi").glob("*.mp4"))
    assert exported == sorted(f"Klip #{n} ({a}).mp4" for n in (1, 2) for a in sizes)
    for p in (desktop / "Ceramah Pagi").glob("*.mp4"):
        assert _probe(p)["streams"][0]["codec_name"] == "h264"
    c.delete(f"/api/projects/{pid}")
    assert (desktop / "Ceramah Pagi").exists()       # menghapus proyek tidak menghapus hasil ekspor pengguna


def test_oslock_exclusive_between_handles(tmp_path):
    from app import oslock
    path = tmp_path / "x.part"
    path.write_bytes(b"x" * 64)
    with open(path, "r+b") as a, open(path, "r+b") as b:
        assert oslock.try_lock(a)
        assert not oslock.try_lock(b)
        oslock.unlock(a)
        assert oslock.try_lock(b)
        oslock.unlock(b)


def test_launcher_maps_bundled_models_to_quality_modes(tmp_path, monkeypatch):
    """M1.1 final: installer membundel HANYA whisper-small (mode Standar = "akurat"); tidak ada mode Cepat. Model base
    sisa instalasi lama tidak dipakai walau foldernya masih ada."""
    from desktop import launcher
    for name in ("base", "small"):
        (tmp_path / f"whisper-{name}").mkdir()
        (tmp_path / f"whisper-{name}" / "model.bin").write_bytes(b"x")
    monkeypatch.setattr(launcher, "resource_dir", lambda: tmp_path)
    for k in ("WHISPER_MODEL", "WHISPER_MODEL_ACCURATE", "WHISPER_MODEL_PATHS",
              "WHISPER_QUALITY_DEFAULT", "DESKTOP_MODE", "DESKTOP_LAUNCH_KEY", "VIDEO_APP_DATA", "EXPORT_DIR",
              "EMBEDDED_WORKERS", "MEDIA_LIFECYCLE", "AUTO_MIGRATE", "COOKIE_SECURE", "ALLOW_URL_IMPORT",
              "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "MAX_UPLOAD_MB"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WHISPER_MODEL_FAST", "base")          # sisa lingkungan lama: harus dibuang
    import os
    saved = dict(os.environ)
    try:
        launcher.configure_env(tmp_path / "data", tmp_path / "export")
        paths = json.loads(os.environ["WHISPER_MODEL_PATHS"])
        assert paths == {"small": str(tmp_path / "whisper-small")}
        assert "WHISPER_MODEL_FAST" not in os.environ and os.environ["WHISPER_MODEL_ACCURATE"] == "small"
        assert os.environ["WHISPER_MODEL"] == "small" and os.environ["WHISPER_QUALITY_DEFAULT"] == "akurat"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_selftest_wer_cer():
    from desktop import selftest
    r = selftest.wer_cer("Insya Allah kita bayar zakat.", "insya allah kita bayar jakat")
    assert r["wer"] == 0.2 and r["ref_words"] == 5 and 0 < r["cer"] < 0.1


def test_launcher_finds_optional_turbo_addon_in_data_dir(tmp_path, monkeypatch):
    """Paket "Kualitas tinggi" dipasang terpisah ke folder data (bukan installer utama)."""
    from desktop import launcher
    res = tmp_path / "res"
    for name in ("small",):
        (res / f"whisper-{name}").mkdir(parents=True)
        (res / f"whisper-{name}" / "model.bin").write_bytes(b"x")
    monkeypatch.setattr(launcher, "resource_dir", lambda: res)
    import os
    saved = dict(os.environ)
    try:
        launcher.configure_env(tmp_path / "data", tmp_path / "export")
        assert "WHISPER_MODEL_HIGH" not in os.environ                   # tanpa paket: tidak ada mode tinggi
        addon = tmp_path / "data" / "models" / "whisper-large-v3-turbo"
        addon.mkdir(parents=True)
        (addon / "model.bin").write_bytes(b"x")
        launcher.configure_env(tmp_path / "data", tmp_path / "export")
        assert os.environ["WHISPER_MODEL_HIGH"] == "large-v3-turbo"
        assert json.loads(os.environ["WHISPER_MODEL_PATHS"])["large-v3-turbo"] == str(addon)
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_nh_eval_term_check_and_report(tmp_path):
    from desktop import nh_eval
    t = nh_eval.term_check("Insya Allah kita bayar zakat dan sedekah. Zakat itu wajib.",
                           "insyaallah kita bayar jakat dan sedekah zakat itu wajib",
                           ["Allah", "Insya Allah", "zakat", "sedekah"])
    # "Allah" di dalam "Insya Allah" tidak dihitung lagi sebagai istilah terpisah
    assert t["terms_in_reference"] == 4 and t["terms_correct"] == 2 and set(t["missed"]) == {"Insya Allah", "zakat"}
    rep = {"system": {"os": "x", "cpu": "y", "cpu_threads": 8, "ram_total_gb": 8.0, "ram_available_gb": 4.0},
           "files": ["a.mp4"], "runs": 2,
           "summary": [{"quality": "akurat", "model": "small", "runs": 2, "wer_mean": 0.2, "wer_min": 0.2, "wer_max": 0.2,
                        "cer_mean": 0.05, "rtf_mean": 0.3, "peak_ram_mb_max": 1500, "runs_identical": True,
                        "term_accuracy": 0.5, "terms_in_reference": 4}], "rows": []}
    md = nh_eval.markdown(rep)
    assert "| akurat | small | 20.0% (20.0%–20.0%)" in md and "50% dari 4" in md and "RAM 8.0 GB" in md


def test_datacheck_snapshot_compare_detects_lost_or_changed_user_files(tmp_path):
    """Uji pembaruan M1 -> M1.1: berkas proyek/render & baris DB tidak boleh hilang atau berubah."""
    import sqlite3
    from desktop import datacheck
    d = tmp_path / "data"
    (d / "projects" / "p1" / "renders").mkdir(parents=True)
    (d / "projects" / "p1" / "source.mp4").write_bytes(b"v" * 100)
    (d / "projects" / "p1" / "renders" / "a.mp4").write_bytes(b"r" * 100)
    (d / "projects" / "p1" / "meta.json").write_text("{}")
    (d / "logs").mkdir()
    (d / "logs" / "desktop.log").write_text("x")
    con = sqlite3.connect(d / "app.db")
    con.executescript("create table projects(id text); insert into projects values('p1');"
                      "create table project_recipes(project_id text, version int, data text, updated real, updated_by text);"
                      "insert into project_recipes values('p1', 3, '{\"a\":1}', 0, null);"
                      "create table renders(id text); insert into renders values('r1');")
    con.commit()
    con.close()
    before = datacheck.snapshot(d)
    assert set(before["files"]) == {"projects/p1/source.mp4", "projects/p1/renders/a.mp4", "projects/p1/meta.json"}
    assert before["tables"] == {"projects": 1, "project_recipes": 1, "renders": 1}
    (d / "logs" / "desktop.log").write_text("baru")                   # log berubah: bukan data pengguna
    (d / "projects" / "p1" / "meta.json").write_text('{"x": 1}')       # metadata ditulis ulang: dicatat, tidak gagal
    res = datacheck.compare(before, datacheck.snapshot(d))
    assert res["ok"] and res["meta_rewritten"] == ["projects/p1/meta.json"]
    (d / "projects" / "p1" / "renders" / "a.mp4").unlink()
    con = sqlite3.connect(d / "app.db")
    con.execute("update project_recipes set data='{}'")
    con.commit()
    con.close()
    res = datacheck.compare(before, datacheck.snapshot(d))
    assert not res["ok"] and res["lost_files"] == ["projects/p1/renders/a.mp4"] and res["recipes_changed"] == ["p1"]


def test_datacheck_check_reads_every_project_and_render(monkeypatch):
    from desktop import datacheck, selftest

    class Fake:
        def __init__(self, base):
            pass

        def get(self, path):
            if path.startswith("/api/desktop/enter"):
                return 200, {}
            if path == "/api/projects":
                return 200, [{"id": "p1", "name": "M1"}]
            if path == "/api/projects/p1":
                return 200, {"renders": [{"file": "a.mp4", "available": True}, {"file": "b.mp4", "available": False}]}
            if path.endswith("/renders/a.mp4"):
                return 200, b"\0" * 5000
            if path.endswith("/transcript"):
                return 200, {"segments": [{"text": "x"}]}
            if path.endswith("/recipe"):
                return 200, {"version": 2}
            return 200, []

    monkeypatch.setattr(selftest, "Client", Fake)
    rep = datacheck.check("http://x", "k")
    assert rep["ok"] and rep["projects"] == 1
    assert rep["items"][0]["renders_readable"] == 1 and rep["items"][0]["recipe_version"] == 2


def _eval_folder(tmp_path):
    f = tmp_path / "Uji NH"
    f.mkdir()
    for n in ("a", "b"):
        (f / f"{n}.mp4").write_bytes(b"x")
        (f / f"{n}.txt").write_text("insya allah kita bayar zakat", encoding="utf-8")
    (f / "c.mp4").write_bytes(b"x")                       # tanpa rujukan -> dilewati
    (f / "d.mp4").write_bytes(b"x")
    (f / "d.txt").write_text("  ", encoding="utf-8")      # rujukan kosong -> dilewati
    return f


def _fake_child(monkeypatch, nh_eval, calls):
    real = subprocess.run

    def fake_run(cmd, *a, **kw):
        if "--eval-child" not in cmd:
            return real(cmd, *a, **kw)
        media, q, out = cmd[cmd.index("--eval-child") + 1:][:3]
        calls.append((Path(media).name, q))
        Path(out).write_text(json.dumps({"file": Path(media).name, "quality": q, "model": "small", "duration": 10,
                                         "seconds": 2, "rtf": 0.2, "peak_ram_mb": 700, "wer": 0.25, "cer": 0.1,
                                         "ref_words": 5, "words": 5, "unsure_share": 0.1,
                                         "terms": {"terms_in_reference": 2, "terms_correct": 1, "term_accuracy": 0.5,
                                                   "missed": ["zakat"]}, "hypothesis": "sama"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(nh_eval.subprocess, "run", fake_run)


def test_nh_eval_run_progress_cancel_and_report(tmp_path, monkeypatch):
    from desktop import nh_eval
    monkeypatch.setenv("WHISPER_MODEL_ACCURATE", "small")
    monkeypatch.delenv("WHISPER_MODEL_FAST", raising=False)
    monkeypatch.delenv("WHISPER_MODEL_HIGH", raising=False)
    folder = _eval_folder(tmp_path)
    info = nh_eval.scan(folder)
    assert info["pairs"] == ["a.mp4", "b.mp4", "d.mp4"] and info["missing_reference"] == ["c.mp4"]
    assert info["empty_reference"] == ["d.mp4"]
    calls, prog = [], []
    _fake_child(monkeypatch, nh_eval, calls)
    rep = nh_eval.run(folder, 2, ["cepat", "akurat"], progress=prog.append)    # "cepat" tidak tersedia -> diabaikan
    assert calls == [("a.mp4", "akurat")] * 2 + [("b.mp4", "akurat")] * 2
    assert prog[0]["total"] == 4 and prog[-1]["done"] == 4
    s = rep["summary"][0]
    assert s["quality"] == "akurat" and s["runs_identical"] is True and s["term_accuracy"] == 0.5
    assert rep["skipped_files"] == ["c.mp4", "d.mp4"] and not rep["cancelled"]
    assert (folder / "laporan-uji.md").exists() and (folder / "hasil" / "a.akurat.run2.txt").read_text() == "sama"
    # Dibatalkan setelah run pertama: tidak bisa menilai "2 run sama" -> None, laporan menandai pembatalan.
    calls.clear()
    rep = nh_eval.run(folder, 2, ["akurat"], stop=lambda: len(calls) >= 1)
    assert len(calls) == 1 and rep["cancelled"] and rep["summary"][0]["runs_identical"] is None
    assert "dibatalkan" in (folder / "laporan-uji.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        nh_eval.run(tmp_path, 1, ["akurat"])


def test_desktop_eval_api_runs_in_background(desktop, tmp_path, monkeypatch):
    from desktop import nh_eval
    from app import desktop_eval
    monkeypatch.setenv("WHISPER_MODEL_ACCURATE", "small")
    folder = _eval_folder(tmp_path)
    calls = []
    _fake_child(monkeypatch, nh_eval, calls)
    c = _desktop_client()
    c.get("/api/desktop/enter", params={"key": KEY})
    assert c.post("/api/desktop/eval/scan", json={"folder": str(tmp_path / "tidak-ada")}).status_code == 400
    assert c.post("/api/desktop/eval/scan", json={"folder": str(folder)}).json()["missing_reference"] == ["c.mp4"]
    assert c.post("/api/desktop/eval", json={"folder": str(tmp_path), "runs": 1}).status_code == 400
    assert c.post("/api/desktop/eval", json={"folder": str(folder), "runs": 9}).status_code == 422
    st = c.post("/api/desktop/eval", json={"folder": str(folder), "runs": 1, "qualities": ["akurat"]}).json()
    assert st["running"] is True
    deadline = time.time() + 30
    while desktop_eval.status()["running"] and time.time() < deadline:
        time.sleep(0.1)
    st = c.get("/api/desktop/eval").json()
    assert not st["running"] and st["error"] is None and st["summary"][0]["wer_mean"] == 0.25
    assert st["report"].endswith("laporan-uji.md") and st["done"] == st["total"] == 2
    assert c.post("/api/desktop/eval/open").json()["path"] == str(folder.resolve())
    assert c.post("/api/desktop/pick-folder").status_code == 501       # tanpa jendela pywebview -> kolom teks


def test_desktop_eval_api_hidden_outside_desktop(app_client):
    c = TestClient(main.app)
    assert c.get("/api/desktop/eval").status_code in (401, 404)
