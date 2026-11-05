"""P0: AI lokal sebagai bawaan. Whisper, analisis klip, dan rekomendasi tetap lokal; rencana animasi memakai mode
aturan otomatis; AI eksternal (Claude/Gemini/...) opsional."""
import socket

import pytest

from app import ai, db, storage


@pytest.fixture
def fresh_ai_settings():
    saved = db.get_setting("ai")
    db.set_setting("ai", {})                     # instalasi baru: admin belum memilih penyedia apa pun
    yield
    db.set_setting("ai", saved or {})


def _ready_project(fake_transcript):
    pid = storage.create_project("p0-ai")["id"]
    storage.write_data(pid, "transcript", fake_transcript)
    storage.update_project(pid, status="siap", duration=8.0, source="source.mp4")
    return pid


def test_default_is_no_external_ai(client, fresh_ai_settings):
    me = client.get("/api/auth/me").json()["ai"]
    assert me == {"name": ai.PROVIDERS[ai.NONE]["name"], "enabled": False, "ready": False}
    s = client.get("/api/settings").json()
    assert s["provider"] == ai.NONE and ai.NONE in s["providers"]
    assert not s["providers"][ai.NONE]["needs_key"]


def test_auto_plan_works_offline_and_external_ai_is_refused_by_default(client, fresh_ai_settings, fake_transcript,
                                                                     monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("fitur inti tidak boleh menghubungi jaringan")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    pid = _ready_project(fake_transcript)
    r = client.post(f"/api/projects/{pid}/plan/auto", json={"start": 0, "end": 8})
    assert r.status_code == 200, r.text                                   # rencana otomatis: lokal, tanpa API
    r = client.post(f"/api/projects/{pid}/plan/ai", json={"start": 0, "end": 8})
    assert r.status_code == 400 and "Otomatis" in r.json()["detail"]      # AI eksternal tidak dipanggil
    r = client.post(f"/api/projects/{pid}/clips/suggest", json={"target": 5, "top_n": 3})
    assert r.status_code == 200                                           # rekomendasi klip: lokal
    storage.delete_project(pid)


def test_external_provider_still_optional(client, fresh_ai_settings):
    assert client.put("/api/settings", json={"provider": "gemini", "api_key": "uji-kunci-1234"}).status_code == 200  # pragma: allowlist secret
    me = client.get("/api/auth/me").json()["ai"]
    assert me["enabled"] and me["ready"] and me["name"] == "Gemini"
    assert client.put("/api/settings", json={"provider": ai.NONE}).status_code == 200
    assert not client.get("/api/auth/me").json()["ai"]["enabled"]


def test_none_provider_never_calls_out():
    assert ai.list_models(ai.NONE, {}) == []
    with pytest.raises(ai.AIError):
        ai.generate_plan(ai.NONE, {}, {"segments": []}, 0, 5, "")
