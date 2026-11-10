"""M1.1: kosakata (bawaan/global/unit), saran istilah yang menunggu konfirmasi (tidak pernah diterapkan otomatis),
dan mode kualitas transkripsi yang diteruskan dari upload sampai Whisper."""
import json
import time

import pytest

from app import db, recipe, storage, transcribe, vocab


def _seg(words):
    ws = [{"word": w, "start": s, "end": e, "p": p} for w, s, e, p in words]
    return {"start": ws[0]["start"], "end": ws[-1]["end"], "text": " ".join(w["word"] for w in ws), "words": ws}


TR = {"language": "id", "segments": [
    _seg([("insyaallah", 0.0, 0.6, 0.9), ("kita", 0.7, 0.9, 0.95), ("bayar", 1.0, 1.3, 0.9), ("jakat,", 1.4, 1.9, 0.3)]),
    _seg([("program", 2.0, 2.4, 0.9), ("nurul", 2.5, 2.8, 0.6), ("hayat", 2.9, 3.2, 0.6), ("dua", 3.3, 3.5, 0.2)]),
    _seg([("Alhamdulillah", 4.0, 4.8, 0.99)]),
]}


@pytest.fixture
def clean_vocab():
    with db.engine().begin() as c:
        c.execute(db.settings.delete().where(db.settings.c.key.like("vocab:%")))
    yield
    with db.engine().begin() as c:
        c.execute(db.settings.delete().where(db.settings.c.key.like("vocab:%")))


@pytest.fixture
def project():
    pid = storage.create_project("m11-kosakata", None)["id"]
    storage.write_data(pid, "transcript", json.loads(json.dumps(TR)))
    storage.update_project(pid, status="siap", duration=6.0, source="source.mp4")
    yield pid
    storage.delete_project(pid)


def test_suggestions_never_change_transcript_until_accepted(client, project, clean_vocab):
    client.put("/api/vocab", json={"entries": [{"term": "Insya Allah", "variants": ["insyaallah"], "kind": "arab"}]})
    sug = client.get(f"/api/projects/{project}/transcript/suggestions").json()
    by_term = {s["term"]: s for s in sug}
    assert by_term["Insya Allah"]["reason"] == "ejaan"                 # varian terdaftar
    assert by_term["zakat"]["reason"] == "mirip" and by_term["zakat"]["replacement"] == "zakat,"
    assert by_term["Nurul Hayat"]["found"] == "nurul hayat"
    assert "duha" not in by_term                                         # istilah pendek tidak ditebak dari "dua"
    assert "Alhamdulillah" not in by_term                                # sudah benar
    # belum ada yang berubah
    assert client.get(f"/api/projects/{project}/transcript").json()["segments"][0]["text"].startswith("insyaallah")

    r = client.post(f"/api/projects/{project}/transcript/suggestions",
                    json={"accept": [by_term["zakat"]["id"]], "ignore": [by_term["Insya Allah"]["id"]]})
    assert r.status_code == 200, r.text
    seg0 = r.json()["segments"][0]
    assert seg0["text"] == "insyaallah kita bayar zakat,"                # yang ditolak tidak diterapkan
    assert [w["start"] for w in seg0["words"]] == [0.0, 0.7, 1.0, 1.4]   # timing kata tetap
    left = {s["term"] for s in client.get(f"/api/projects/{project}/transcript/suggestions").json()}
    assert "zakat" not in left and "Insya Allah" not in left and "Nurul Hayat" in left   # penolakan diingat
    stale = client.post(f"/api/projects/{project}/transcript/suggestions", json={"accept": [by_term["zakat"]["id"]]})
    assert stale.status_code == 409


def test_vocab_scopes_and_defaults(client, clean_vocab):
    terms = {e["term"] for e in vocab.effective(None)}
    assert "Nurul Hayat" in terms and "zakat" in terms
    r = client.put("/api/vocab", json={"use_defaults": False, "entries": [{"term": "Sedekah Subuh", "kind": "program"}]})
    assert r.status_code == 200
    assert [e["term"] for e in vocab.effective(None)] == ["Sedekah Subuh"]
    got = client.get("/api/vocab").json()
    assert got["scope"] == "global" and got["entries"][0]["kind"] == "program" and got["defaults"]
    bad = client.put("/api/vocab", json={"entries": [{"term": "satu dua tiga empat lima enam"}]})
    assert bad.status_code == 422
    assert vocab.hotwords([{"term": "zakat", "kind": "istilah"}, {"term": "X", "kind": "program"}]) == ["X", "zakat"]


def test_editor_cannot_change_vocab(client, clean_vocab):
    from conftest import login_client
    stamp = str(time.time()).replace(".", "")
    unit = client.post("/api/admin/units", json={"name": f"Vocab {stamp}"}).json()
    email = f"ed{stamp}@org.id"
    client.post("/api/admin/users", json={"email": email, "name": "ed", "role": "editor", "unit_id": unit["id"],
                                          "password": "password-123"})  # pragma: allowlist secret
    ed = login_client(email, "password-123")  # pragma: allowlist secret
    assert ed.put("/api/vocab", json={"entries": []}).status_code == 403
    assert ed.get("/api/vocab").json()["can_edit"] is False


def test_quality_flows_from_upload_to_whisper(client, sample_video, monkeypatch, fake_transcript, clean_vocab):
    seen = {}

    def fake(path, duration, language=None, progress=None, *, quality=None, vocabulary=None):
        seen.update(quality=quality, vocabulary=vocabulary, language=language)
        return json.loads(json.dumps(fake_transcript))

    monkeypatch.setattr(transcribe, "transcribe", fake)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("q.mp4", fh, "video/mp4")},
                          data={"language": "id", "quality": "akurat"}).json()
    deadline = time.time() + 120
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{res['job']['id']}").json()
        if job["status"] in ("selesai", "gagal"):
            break
        time.sleep(0.3)
    assert job["status"] == "selesai", job
    assert seen["quality"] == "akurat" and seen["language"] == "id"
    assert seen["vocabulary"] is None            # bawaan: kosakata TIDAK jadi hotwords (eval M1.1: WER naik)
    pid = res["project"]["id"]
    seen.clear()
    r = client.post(f"/api/projects/{pid}/reanalyze", params={"language": "id", "quality": "cepat"})
    assert r.status_code == 200
    deadline = time.time() + 120
    while time.time() < deadline and "quality" not in seen:
        time.sleep(0.3)
    assert seen.get("quality") == "cepat"
    client.delete(f"/api/projects/{pid}")


def test_unknown_quality_is_ignored(client):
    from app import main
    assert main._quality("akurat") == "akurat" and main._quality("x") is None and main._quality(None) is None


def test_transcribe_info_lists_modes(client, monkeypatch):
    me = client.get("/api/auth/me").json()
    assert me["transcribe"]["qualities"] == []                           # versi web: tidak berubah
    monkeypatch.setenv("WHISPER_MODEL_FAST", "base")
    monkeypatch.setenv("WHISPER_MODEL_ACCURATE", "small")
    info = client.get("/api/auth/me").json()["transcribe"]
    assert [q["id"] for q in info["qualities"]] == ["cepat", "akurat"]
    fast, acc = info["qualities"]
    assert fast["model"] == "base" and acc["model"] == "small" and acc["ram_mb"] > fast["ram_mb"]


def test_job_memory_uses_quality_model(monkeypatch):
    from app import resources
    monkeypatch.setenv("WHISPER_MODEL_FAST", "base")
    monkeypatch.setenv("WHISPER_MODEL_ACCURATE", "small")
    monkeypatch.setattr(resources, "project_duration", lambda pid: 600.0)
    fast = resources.job_need_mb({"task": "analyze", "queue": "analisis", "payload": '{"quality": "cepat"}'})
    acc = resources.job_need_mb({"task": "analyze", "queue": "analisis", "payload": {"quality": "akurat"}})
    assert acc > fast
    r = recipe.default()
    assert r.seg_edits == {} and r.reviewed == [] and r.vocab_ignored == []


def test_whisper_hints_opt_in_and_only_user_terms(client, clean_vocab):
    assert vocab.whisper_terms(None) == []                               # bawaan mati
    client.put("/api/vocab", json={"entries": [{"term": "Sedekah Subuh", "kind": "program"}], "whisper_hints": False})
    assert vocab.whisper_terms(None) == []
    client.put("/api/vocab", json={"entries": [{"term": "Sedekah Subuh", "kind": "program"}], "whisper_hints": True})
    assert vocab.whisper_terms(None) == ["Sedekah Subuh"]                # tanpa daftar bawaan
    assert client.get("/api/vocab").json()["whisper_hints"] is True
