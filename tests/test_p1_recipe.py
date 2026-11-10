"""P1: resep editing per proyek di database — autosave & reload, dua perangkat, validasi, akses antar-unit,
kompatibilitas proyek lama, koreksi teks & overlay di resep, render dari resep (snapshot), template."""
import json
import subprocess
import time

import pytest
from conftest import login_client
from sqlalchemy import func, select, update

from app import db, jobs, lifecycle, pipeline, recipe, storage, transcribe
from app.config import PROJECTS_DIR


def _wait(c, job_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("selesai", "gagal", "batal"):
            return job
        time.sleep(0.3)
    raise AssertionError("job timeout")


def _project(fake_transcript, unit_id=None, plan_items=None):
    """Proyek siap tanpa analisis (cukup untuk API resep)."""
    pid = storage.create_project("p1-resep", unit_id)["id"]
    storage.write_data(pid, "transcript", fake_transcript)
    if plan_items is not None:
        storage.write_data(pid, "plan", plan_items)
    storage.update_project(pid, status="siap", duration=8.0, source="source.mp4")
    return pid


def _jobs_count():
    with db.engine().connect() as c:
        return c.execute(select(func.count()).select_from(db.jobs)).scalar()


def _org(client):
    stamp = str(time.time()).replace(".", "")
    a = client.post("/api/admin/units", json={"name": f"P1 A {stamp}"}).json()
    b = client.post("/api/admin/units", json={"name": f"P1 B {stamp}"}).json()
    users = {}
    for key, role, unit in [("ea", "editor", a), ("ea2", "editor", a), ("eb", "editor", b), ("ua", "unit_admin", a),
                            ("ub", "unit_admin", b)]:
        email = f"p1{key}{stamp}@org.id"
        r = client.post("/api/admin/users", json={"email": email, "name": key,
                                                  "password": "password-123",  # pragma: allowlist secret
                                                  "role": role, "unit_id": unit["id"]})
        assert r.status_code == 200, r.text
        users[key] = login_client(email, "password-123")
    return a, b, users


# ------------------------------------------------------------------ autosave & reload

def test_new_project_has_default_recipe_and_save_reload(client, fake_transcript):
    pid = _project(fake_transcript)
    r = client.get(f"/api/projects/{pid}/recipe").json()
    assert r["version"] == 0 and r["recipe"] == recipe.default().model_dump(mode="json")
    rec = r["recipe"]
    rec.update(aspect="1:1", selection={"start": 1.0, "end": 6.5}, target=45,
               checked=[{"start": 0.5, "end": 3.0}, {"start": 3.0, "end": 7.0}])
    rec["crop"]["x"] = 0.2
    rec["look"].update(color="bw", brightness=0.05, contrast=1.2, saturation=0.8)
    rec["subtitles"].update(style="pop", font="Poppins", color="#00FF00", position="top")
    saved = client.put(f"/api/projects/{pid}/recipe", json={"version": 0, "recipe": rec})
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1 and saved.json()["updated_by_name"]
    again = client.get(f"/api/projects/{pid}/recipe").json()          # "reload" / perangkat lain
    assert again["version"] == 1 and again["recipe"] == saved.json()["recipe"]
    assert again["recipe"]["subtitles"]["font"] == "Poppins" and again["recipe"]["crop"]["x"] == 0.2
    second = client.put(f"/api/projects/{pid}/recipe", json={"version": 1, "recipe": again["recipe"]})
    assert second.json()["version"] == 2


def test_autosave_never_starts_ffmpeg_jobs(client, fake_transcript):
    pid = _project(fake_transcript)
    before, version = _jobs_count(), 0
    rec = recipe.default().model_dump(mode="json")
    for i in range(25):                                   # 25 autosave beruntun (geser slider, ketik, dst.)
        rec["look"]["brightness"] = round(-0.25 + i * 0.02, 3)
        r = client.put(f"/api/projects/{pid}/recipe", json={"version": version, "recipe": rec})
        assert r.status_code == 200
        version = r.json()["version"]
    assert version == 25 and _jobs_count() == before


def test_saving_counts_as_editing_activity_but_survives_media_deletion(client, fake_transcript):
    pid = _project(fake_transcript)
    old = time.time() - 3600
    with db.engine().begin() as c:
        c.execute(update(db.project_media).where(db.project_media.c.project_id == pid).values(last_activity=old))
    r = client.put(f"/api/projects/{pid}/recipe", json={"version": 0, "recipe": recipe.default().model_dump()})
    assert r.status_code == 200
    assert lifecycle.info(pid)["last_activity"] > old + 3000          # memperpanjang masa simpan media
    assert lifecycle.delete_media(pid, "uji")
    r = client.put(f"/api/projects/{pid}/recipe", json={"version": 1, "recipe": recipe.default().model_dump()})
    assert r.status_code == 200 and r.json()["version"] == 2          # resep (metadata) tetap tersimpan
    assert not (PROJECTS_DIR / pid / "source.mp4").exists()


# ------------------------------------------------------------------ dua perangkat

def test_two_devices_conflict_is_detected_not_overwritten(client, fake_transcript):
    pid = _project(fake_transcript)
    hp = login_client(*_admin())                                       # sesi kedua (perangkat lain)
    laptop_view = client.get(f"/api/projects/{pid}/recipe").json()
    hp_view = hp.get(f"/api/projects/{pid}/recipe").json()
    a = laptop_view["recipe"]
    a["subtitles"]["color"] = "#FF0000"
    assert client.put(f"/api/projects/{pid}/recipe", json={"version": 0, "recipe": a}).json()["version"] == 1
    b = hp_view["recipe"]
    b["look"]["color"] = "warm"
    clash = hp.put(f"/api/projects/{pid}/recipe", json={"version": 0, "recipe": b})
    assert clash.status_code == 409 and clash.json()["conflict"]
    current = clash.json()["current"]
    assert current["version"] == 1 and current["recipe"]["subtitles"]["color"] == "#FF0000"
    assert client.get(f"/api/projects/{pid}/recipe").json()["recipe"]["look"]["color"] == "none"   # tidak tertimpa
    # Pengguna HP memilih "gabungkan / timpa dengan sadar": kirim ulang di atas versi terbaru.
    merged = current["recipe"]
    merged["look"]["color"] = "warm"
    ok = hp.put(f"/api/projects/{pid}/recipe", json={"version": current["version"], "recipe": merged})
    assert ok.status_code == 200 and ok.json()["version"] == 2
    final = client.get(f"/api/projects/{pid}/recipe").json()["recipe"]
    assert final["look"]["color"] == "warm" and final["subtitles"]["color"] == "#FF0000"


def test_concurrent_first_saves_only_one_wins(client, fake_transcript):
    pid = _project(fake_transcript)
    rec = recipe.default()
    recipe.save(pid, rec, 0, None)
    with pytest.raises(recipe.Conflict) as exc:
        recipe.save(pid, rec, 0, None)                    # perangkat lain juga mengira belum ada resep
    assert exc.value.current["version"] == 1


def test_server_side_changes_merge_with_device_edits(client, fake_transcript):
    """Koreksi teks/rencana animasi dari server tidak menimpa perubahan perangkat; versinya dikirim di header."""
    pid = _project(fake_transcript)
    v = client.put(f"/api/projects/{pid}/recipe", json={"version": 0, "recipe": {
        **recipe.default().model_dump(mode="json"), "aspect": "4:5"}}).json()["version"]
    r = client.put(f"/api/projects/{pid}/transcript", json={"segments": [{"index": 1, "text": "Karena editnya rapi"}]})
    assert r.status_code == 200 and int(r.headers["X-Recipe-Version"]) == v + 1
    state = client.get(f"/api/projects/{pid}/recipe").json()
    assert state["recipe"]["aspect"] == "4:5" and state["recipe"]["seg_edits"]["1"]["text"] == "Karena editnya rapi"


# ------------------------------------------------------------------ validasi & keamanan

@pytest.mark.parametrize("patch", [
    {"aspect": "2:1"},
    {"subtitles": {"font": "Arial,0,{\\b1}"}},              # injeksi ke berkas ASS
    {"subtitles": {"color": "red"}},
    {"look": {"brightness": 5}},
    {"crop": {"x": 2}},
    {"selection": {"start": 5, "end": 2}},
    {"checked": [{"start": 0, "end": 1}] * 31},
    {"text_fixes": {"satu": "x"}},
    {"overlays": [{"type": "script", "start": 0, "end": 1}]},
    {"audio": {"music": "../../etc/passwd"}},
    {"rahasia": True},                                      # field tak dikenal ditolak
    {"template_id": "<img>"},
])
def test_recipe_schema_is_validated(client, fake_transcript, patch):
    pid = _project(fake_transcript)
    rec = recipe.default().model_dump(mode="json")
    for k, v in patch.items():
        rec[k] = {**rec[k], **v} if isinstance(v, dict) and isinstance(rec.get(k), dict) else v
    r = client.put(f"/api/projects/{pid}/recipe", json={"version": 0, "recipe": rec})
    assert r.status_code == 422, (patch, r.text)
    assert client.get(f"/api/projects/{pid}/recipe").json()["version"] == 0


def test_recipe_size_is_capped(client, fake_transcript):
    pid = _project(fake_transcript)
    rec = recipe.default().model_dump(mode="json")
    rec["overlays"] = [{"type": "card", "start": i * 0.1, "end": i * 0.1 + 1, "text": "x" * 200, "label": "y" * 80}
                       for i in range(300)]
    rec["text_fixes"] = {str(i): "kata " * 300 for i in range(80)}
    r = client.put(f"/api/projects/{pid}/recipe", json={"version": 0, "recipe": rec})
    assert r.status_code in (413, 422)


def test_unit_isolation_for_recipe_and_render(client, fake_transcript):
    unit_a, unit_b, u = _org(client)
    pid = _project(fake_transcript, unit_id=unit_a["id"])
    assert u["ea"].put(f"/api/projects/{pid}/recipe", json={"version": 0, "recipe": recipe.default().model_dump()
                                                            }).status_code == 200
    assert u["ea2"].get(f"/api/projects/{pid}/recipe").json()["version"] == 1     # rekan satu unit
    for path, method, body in [("recipe", "get", None), ("recipe", "put", {"version": 1, "recipe": {}}),
                               ("render-recipe", "post", {"version": 1, "start": 0, "end": 3})]:
        r = getattr(u["eb"], method)(f"/api/projects/{pid}/{path}", **({"json": body} if body else {}))
        assert r.status_code == 404, (path, r.status_code)                          # unit lain: tidak terlihat


# ------------------------------------------------------------------ kompatibilitas proyek lama

def test_legacy_project_plan_and_transcript_still_work(client, fake_transcript):
    legacy = [{"id": "lama1", "type": "keyword", "start": 1.0, "end": 2.0, "text": "VIRAL", "label": "", "items": []}]
    pid = _project(fake_transcript, plan_items=legacy)
    assert client.get(f"/api/projects/{pid}/recipe").json()["recipe"]["overlays"] is None
    assert client.get(f"/api/projects/{pid}/plan").json()[0]["text"] == "VIRAL"         # dibaca dari plan.json
    r = client.put(f"/api/projects/{pid}/plan", json=legacy + [
        {"type": "zoom", "start": 3.0, "end": 4.0}])
    assert r.status_code == 200 and "X-Recipe-Version" in r.headers
    assert len(client.get(f"/api/projects/{pid}/recipe").json()["recipe"]["overlays"]) == 2
    assert storage.read_data(pid, "plan") == legacy                                    # berkas lama tidak diubah
    raw_before = json.dumps(storage.read_data(pid, "transcript"))
    fixed = client.put(f"/api/projects/{pid}/transcript", json={"segments": [{"index": 0, "text": "Kenapa ini viral"}]})
    assert fixed.json()["segments"][0]["text"] == "Kenapa ini viral"
    assert client.get(f"/api/projects/{pid}/transcript").json()["segments"][0]["text"] == "Kenapa ini viral"
    assert json.dumps(storage.read_data(pid, "transcript")) == raw_before              # transkrip mentah utuh
    assert client.put(f"/api/projects/{pid}/transcript", json={"segments": [{"index": 99, "text": "x"}]}
                      ).status_code == 422


def test_reanalyze_clears_text_fixes_but_keeps_other_settings(client, fake_transcript, monkeypatch):
    monkeypatch.setattr(main_module(), "_queue_job", lambda *a, **k: {"id": "palsu"})
    pid = _project(fake_transcript)
    client.put(f"/api/projects/{pid}/recipe", json={"version": 0, "recipe": {
        **recipe.default().model_dump(mode="json"), "aspect": "16:9", "text_fixes": {"0": "teks lama"}}})
    assert client.post(f"/api/projects/{pid}/reanalyze").status_code == 200
    state = client.get(f"/api/projects/{pid}/recipe").json()["recipe"]
    assert state["text_fixes"] == {} and state["aspect"] == "16:9"


def test_effective_transcript_ignores_fixes_for_missing_segments(fake_transcript):
    out = recipe.apply_text_fixes(fake_transcript, {"0": "Halo dunia", "40": "tidak ada"})
    assert out["segments"][0]["text"] == "Halo dunia" and len(out["segments"]) == len(fake_transcript["segments"])
    assert fake_transcript["segments"][0]["text"] != "Halo dunia"                      # masukan tidak diubah


# ------------------------------------------------------------------ render dari resep

def test_render_from_recipe_uses_saved_recipe_snapshot(client, sample_video, fake_transcript, monkeypatch):
    """Subtitle + crop + filter + template diubah, lalu render MP4 dari resep tersimpan: hasil memakai resep itu
    (rasio, hitam-putih, teks terkoreksi), dan job menyimpan snapshot sehingga edit berikutnya tidak mengubahnya."""
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("resep.mp4", fh, "video/mp4")}).json()
    pid = res["project"]["id"]
    assert _wait(client, res["job"]["id"])["status"] == "selesai"

    pop = next(t for t in client.get("/api/templates").json() if t["id"] == "builtin:pop")
    rec = client.get(f"/api/projects/{pid}/recipe").json()["recipe"]
    rec.update(aspect="1:1", template_id=pop["id"], subtitles=pop["data"]["subtitles"], animations=False)
    rec["crop"].update(x=0.0, reframe="manual", mode="manual")
    rec["look"]["color"] = "bw"
    v = client.put(f"/api/projects/{pid}/recipe", json={"version": 0, "recipe": rec}).json()["version"]
    v = int(client.put(f"/api/projects/{pid}/transcript", json={"segments": [
        {"index": 0, "text": "KOREKSI satu dua tiga"}]}).headers["X-Recipe-Version"])

    stale = client.post(f"/api/projects/{pid}/render-recipe", json={"version": v - 1, "start": 0.5, "end": 3.5})
    assert stale.status_code == 409 and stale.json()["current"]["version"] == v     # belum tersimpan / berubah

    seen = {}
    real_build = pipeline.build_ass

    def spy(transcript, *a, **k):
        seen["text"] = " ".join(w["word"] for s in transcript["segments"] for w in s.get("words", []))
        seen["opts"] = a[4]
        return real_build(transcript, *a, **k)

    monkeypatch.setattr(pipeline, "build_ass", spy)
    before = _jobs_count()
    job = client.post(f"/api/projects/{pid}/render-recipe", json={"version": v, "start": 0.5, "end": 3.5,
                                                                   "title": "Dari resep"})
    assert job.status_code == 200, job.text
    payload = json.loads(jobs.get(job.json()["id"])["payload"])
    assert payload["recipe_version"] == v and payload["request"]["aspect"] == "1:1"
    assert payload["request"]["seg_edits"]["0"]["text"] == "KOREKSI satu dua tiga"
    # Edit setelah render diajukan tidak mengubah render yang sudah antre.
    later = client.get(f"/api/projects/{pid}/recipe").json()
    later["recipe"]["aspect"] = "16:9"
    client.put(f"/api/projects/{pid}/recipe", json={"version": later["version"], "recipe": later["recipe"]})
    done = _wait(client, job.json()["id"])
    assert done["status"] == "selesai", done
    assert _jobs_count() == before + 1                                               # satu render, bukan per edit
    assert "KOREKSI" in seen["text"] and seen["opts"].style == "pop" and seen["opts"].font == "Poppins"

    item = client.get(f"/api/projects/{pid}").json()["renders"][0]
    out = PROJECTS_DIR / pid / "renders" / item["file"]
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                       "stream=width,height", "-of", "json", str(out)],
                                      capture_output=True, text=True, check=True).stdout)["streams"][0]
    assert (probe["width"], probe["height"]) == (1080, 1080)                         # rasio dari resep (bukan 16:9)
    frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.5", "-i", str(out), "-frames:v", "1", "-vf",
                            "scale=64:64,format=rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    top = frame[: 64 * 24 * 3]          # sepertiga atas: subtitle berwarna (template) ada di bawah, di atas filter
    spread = max(max(top[i:i + 3]) - min(top[i:i + 3]) for i in range(0, len(top), 3))
    assert spread <= 12                                                              # filter hitam-putih terpakai
    client.delete(f"/api/projects/{pid}")


# ------------------------------------------------------------------ template

def test_templates_access_rules(client):
    unit_a, unit_b, u = _org(client)
    data = {"subtitles": {**recipe.RecipeSubtitles().model_dump(), "style": "pop", "color": "#123456"}}
    names = lambda c: {t["name"] for t in c.get("/api/templates").json()}  # noqa: E731
    builtin = names(u["ea"])
    assert {"Karaoke kuning", "Pop hijau", "Satu kata besar"} <= builtin

    mine = u["ea"].post("/api/templates", json={"name": "Preset saya", "data": data})
    assert mine.status_code == 200 and mine.json()["scope"] == "personal"
    assert "Preset saya" in names(u["ea"]) and "Preset saya" not in names(u["ea2"])      # pribadi
    assert u["ea2"].delete(f"/api/templates/{mine.json()['id']}").status_code == 404

    assert u["ea"].post("/api/templates", json={"name": "Unit?", "scope": "unit", "data": data}).status_code == 403
    assert u["ea"].post("/api/templates", json={"name": "Global?", "scope": "global", "data": data}).status_code == 403
    shared = u["ua"].post("/api/templates", json={"name": "Gaya Humas", "scope": "unit", "data": data})
    assert shared.status_code == 200 and shared.json()["unit_id"] == unit_a["id"]
    assert "Gaya Humas" in names(u["ea2"]) and "Gaya Humas" not in names(u["eb"])         # per unit
    assert u["ub"].put(f"/api/templates/{shared.json()['id']}", json={"name": "Dibajak"}).status_code == 404
    assert u["ea2"].put(f"/api/templates/{shared.json()['id']}", json={"name": "Ubah"}).status_code == 403
    assert u["ua"].post("/api/templates", json={"name": "B?", "scope": "unit", "unit_id": unit_b["id"],
                                                "data": data}).status_code == 403
    edited = u["ua"].put(f"/api/templates/{shared.json()['id']}", json={"name": "Gaya Humas 2"})
    assert edited.status_code == 200 and "Gaya Humas 2" in names(u["ea"])

    glob = client.post("/api/templates", json={"name": f"Global {unit_a['id']}", "scope": "global", "data": data})
    assert glob.status_code == 200 and glob.json()["name"] in names(u["eb"])
    assert u["ua"].delete(f"/api/templates/{glob.json()['id']}").status_code == 403
    assert client.post("/api/templates", json={"name": "Untuk B", "scope": "unit", "unit_id": unit_b["id"],
                                               "data": data}).json()["name"] in names(u["eb"])
    assert u["ea"].put("/api/templates/builtin:pop", json={"name": "x"}).status_code == 403
    bad = u["ea"].post("/api/templates", json={"name": "x", "data": {"subtitles": {"font": "A,B"}}})
    assert bad.status_code == 422
    assert client.delete(f"/api/templates/{glob.json()['id']}").json()["ok"]


def test_personal_template_cap(client, monkeypatch):
    from app import edit_templates
    monkeypatch.setattr(edit_templates, "MAX_PERSONAL", 2)
    _, _, u = _org(client)
    data = {"subtitles": recipe.RecipeSubtitles().model_dump()}
    codes = [u["eb"].post("/api/templates", json={"name": f"t{i}", "data": data}).status_code for i in range(3)]
    assert codes == [200, 200, 409]


def test_editor_config_shares_looks_with_renderer(client):
    from app.render import COLOR_PRESETS
    cfg = client.get("/api/editor/config").json()
    assert set(cfg["looks"]) == set(COLOR_PRESETS) and cfg["output_sizes"]["9:16"] == [1080, 1920]


def _admin():
    import os
    return os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"]


def main_module():
    from app import main
    return main
