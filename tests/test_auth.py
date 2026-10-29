import json
import os
import time

from conftest import login_client

from app import jobs, main, storage, transcribe


def _wait(c, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("selesai", "gagal"):
            return job
        time.sleep(0.3)
    raise AssertionError("job timeout")


def _setup_org(client):
    a = client.post("/api/admin/units", json={"name": f"Humas {time.time()}", "daily_render_quota": 1}).json()
    b = client.post("/api/admin/units", json={"name": f"Pemasaran {time.time()}"}).json()
    stamp = str(time.time()).replace(".", "")
    users = {}
    for key, role, unit in [("ea", "editor", a), ("ea2", "editor", a), ("eb", "editor", b), ("ua", "unit_admin", a)]:
        email = f"{key}{stamp}@org.id"
        r = client.post("/api/admin/users", json={"email": email, "name": key, "password": "password-123",
                                                  "role": role, "unit_id": unit["id"]})
        assert r.status_code == 200, r.text
        users[key] = login_client(email, "password-123")
        users[key + "_id"] = r.json()["id"]
    return a, b, users


def test_requires_login(app_client):
    assert app_client.get("/api/projects").status_code == 401
    assert app_client.get("/api/auth/me").status_code == 401
    assert app_client.get("/").status_code == 200          # halaman tetap bisa dibuka (lalu diarahkan ke login)
    r = app_client.post("/api/auth/login", json={"email": os.environ["ADMIN_EMAIL"], "password": "salah"})
    assert r.status_code == 401


def test_login_rate_limit(app_client):
    email = f"tidakada{time.time()}@org.id"
    codes = [app_client.post("/api/auth/login", json={"email": email, "password": "x"}).status_code for _ in range(9)]
    assert codes[:8] == [401] * 8 and codes[8] == 429


def test_cross_site_post_blocked(client):
    r = client.post("/api/auth/logout", headers={"Origin": "https://jahat.example"})
    assert r.status_code == 403


def test_unit_isolation_roles_and_quota(client, sample_video, fake_transcript, monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    unit_a, unit_b, u = _setup_org(client)

    me = u["ea"].get("/api/auth/me").json()
    assert me["role"] == "editor" and me["unit_id"] == unit_a["id"] and "password_hash" not in me

    with sample_video.open("rb") as fh:
        res = u["ea"].post("/api/projects", files={"file": ("rahasia unit.mp4", fh, "video/mp4")}).json()
    pid = res["project"]["id"]
    assert res["project"]["unit_id"] == unit_a["id"]
    assert _wait(u["ea"], res["job"]["id"])["status"] == "selesai"

    # Unit lain tidak bisa melihat apa pun.
    assert u["eb"].get(f"/api/projects/{pid}").status_code == 404
    assert u["eb"].get(f"/api/projects/{pid}/preview").status_code == 404
    assert u["eb"].get(f"/api/jobs/{res['job']['id']}").status_code == 404
    assert all(p["id"] != pid for p in u["eb"].get("/api/projects").json())
    # Rekan satu unit bisa melihat & mengedit, tapi tidak menghapus milik orang lain.
    assert any(p["id"] == pid for p in u["ea2"].get("/api/projects").json())
    assert u["ea2"].get(f"/api/projects/{pid}").json()["can_delete"] is False
    assert u["ea2"].delete(f"/api/projects/{pid}").status_code == 403

    # Kuota render unit A = 1 per hari.
    body = {"start": 0, "end": 4, "subtitles": {"enabled": False}, "reframe": "manual", "animations": False}
    job = u["ea2"].post(f"/api/projects/{pid}/render", json=body).json()
    assert _wait(u["ea2"], job["id"])["status"] == "selesai"
    r = u["ea"].post(f"/api/projects/{pid}/render", json=body)
    assert r.status_code == 429 and "Kuota render" in r.json()["detail"]
    unit_info = u["ea"].get("/api/unit").json()
    assert unit_info["renders_today"] == 1 and unit_info["can_edit"] is False

    # Guidebook: hanya Admin Unit; editor tidak boleh.
    assert u["ea"].put("/api/unit/guidebook", json={"guidebook": "x"}).status_code == 403
    assert u["ua"].put("/api/unit/guidebook", json={"guidebook": "Gaya resmi"}).json()["guidebook"] == "Gaya resmi"
    prompt = u["ea"].post(f"/api/projects/{pid}/plan/prompt", json={"start": 0, "end": 8}).json()["prompt"]
    assert "Gaya resmi" in prompt

    # Admin Unit: kelola pengguna unitnya saja.
    assert u["ua"].post("/api/admin/users", json={"email": f"x{time.time()}@org.id", "password": "password-123",
                                                  "role": "admin"}).status_code == 403
    made = u["ua"].post("/api/admin/users", json={"email": f"baru{time.time()}@org.id", "password": "password-123",
                                                  "role": "editor", "unit_id": unit_b["id"]}).json()
    assert made["unit_id"] == unit_a["id"]                      # dipaksa ke unit sendiri
    listed = {x["id"] for x in u["ua"].get("/api/admin/users").json()}
    assert u["eb_id"] not in listed and u["ea_id"] in listed
    assert u["ua"].put(f"/api/admin/users/{u['eb_id']}", json={"active": False}).status_code == 404
    assert u["ea"].get("/api/admin/users").status_code == 403
    assert u["ea"].get("/api/settings").status_code == 403

    # Admin Unit boleh menghapus proyek unitnya; admin pusat melihat semua unit.
    assert any(p["id"] == pid for p in client.get("/api/projects").json())
    assert u["ua"].delete(f"/api/projects/{pid}").json()["ok"]

    # Nonaktifkan akun: sesi langsung berakhir & tidak bisa login.
    client.put(f"/api/admin/users/{u['eb_id']}", json={"active": False})
    assert u["eb"].get("/api/projects").status_code == 401


def test_password_change_ends_old_sessions(client):
    email = f"ganti{time.time()}@org.id"
    unit = client.post("/api/admin/units", json={"name": f"Unit {time.time()}"}).json()
    client.post("/api/admin/users", json={"email": email, "password": "password-lama", "role": "editor",
                                          "unit_id": unit["id"]})
    a, b = login_client(email, "password-lama"), login_client(email, "password-lama")
    assert a.post("/api/auth/password", json={"old_password": "salah", "new_password": "x" * 9}).status_code == 400
    assert a.post("/api/auth/password", json={"old_password": "password-lama", "new_password": "pendek"}).status_code == 400
    assert a.post("/api/auth/password", json={"old_password": "password-lama", "new_password": "password-baru"}).status_code == 200
    assert a.get("/api/auth/me").status_code == 200        # sesi baru diberikan
    assert b.get("/api/auth/me").status_code == 401        # sesi lain berakhir
    login_client(email, "password-baru")


def test_claim_is_exclusive_under_concurrency():
    import threading

    pid = storage.create_project("antrean")["id"]
    ids = {jobs.submit("render", "render", pid, {}, "x", queue="uji")["id"] for _ in range(30)}
    claimed, lock = [], threading.Lock()

    def grab(n):
        while True:
            j = jobs.claim(("uji",), f"uji-{n}")
            if j is None:
                return
            if j["id"] in ids:
                with lock:
                    claimed.append(j["id"])
            jobs.finish(j["id"], result={})

    threads = [threading.Thread(target=grab, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(claimed) == sorted(ids)                   # setiap pekerjaan diambil tepat sekali
    storage.delete_project(pid)


def test_stale_jobs_requeued_then_failed():
    from sqlalchemy import update

    from app import db

    pid = storage.create_project("macet")["id"]
    jid = jobs.submit("render", "render", pid, {}, "x", queue="uji")["id"]

    def run_and_die():
        j = jobs.claim(("uji",), "mati")
        assert j and j["id"] == jid
        with db.engine().begin() as c:
            c.execute(update(db.jobs).where(db.jobs.c.id == jid).values(heartbeat=time.time() - 999))

    run_and_die()
    jobs.requeue_stale()
    assert jobs.get(jid)["status"] == "antre"
    run_and_die()
    jobs.requeue_stale()
    assert jobs.get(jid)["status"] == "gagal"
    storage.delete_project(pid)


def test_legacy_projects_migrated(client, tmp_path):
    folder = storage.PROJECTS_DIR / "abcdef123456"
    (folder / "renders").mkdir(parents=True, exist_ok=True)
    (folder / "renders" / "r1.mp4").write_bytes(b"x")
    (folder / "project.json").write_text(json.dumps({
        "id": "abcdef123456", "name": "Proyek lama", "status": "siap", "duration": 10.0, "width": 1280,
        "height": 720, "created": 1.0, "music": ["lagu.mp3"],
        "renders": [{"id": "r1r1r1r1r1r1", "file": "r1.mp4", "title": "Klip lama", "start": 0, "end": 5,
                     "aspect": "9:16", "size": 1, "created": 2.0}]}))
    main.migrate_legacy()
    p = client.get("/api/projects/abcdef123456").json()
    assert p["name"] == "Proyek lama" and p["renders"][0]["title"] == "Klip lama" and p["music"] == ["lagu.mp3"]
    assert not (folder / "project.json").exists()
