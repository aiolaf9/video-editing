import time

from fastapi.testclient import TestClient

from storymaker import media


def wait(c, pid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = c.get(f"/api/projects/{pid}").json()
        if not p["running"]:
            return p
        time.sleep(0.5)
    raise AssertionError("timeout")


def test_full_flow(videos, patch_whisper):
    from storymaker.server import app

    c = TestClient(app)
    pid = c.post("/api/projects", json={"name": "Air Bersih", "target": 40}).json()["id"]
    # minimal 3 video
    files = [("files", (videos[0].name, open(videos[0], "rb"), "video/mp4"))]
    c.post(f"/api/projects/{pid}/clips", files=files)
    assert c.post(f"/api/projects/{pid}/analyze", json={"planner": "heuristic"}).status_code == 400
    files = [("files", (v.name, open(v, "rb"), "video/mp4")) for v in videos[1:]]
    r = c.post(f"/api/projects/{pid}/clips", files=files).json()
    assert not r["errors"] and len(r["added"]) == 2

    assert c.post(f"/api/projects/{pid}/analyze", json={"planner": "heuristic"}).status_code == 200
    p = wait(c, pid)
    assert p["job"]["error"] == "", p["job"]
    plan = p["plan"]
    assert plan["sections"][0]["role"] == "hook"
    assert plan["sections"][0]["items"][0]["text"].startswith("Bayangkan")
    assert p["hooks"]

    # koreksi subtitle & edit rencana
    uid = plan["sections"][0]["items"][0]["ref"]
    r = c.put(f"/api/projects/{pid}/utterances/{uid}", json={"text": "Bayangkan, selama 10 tahun warga berjalan 3 km untuk air bersih."})
    assert r.status_code == 200
    raw = {"title": "Air Bersih untuk Desa", "sections": [
        {"role": s["role"], "items": [{"kind": i["kind"], "ref": i["ref"], "broll": i["broll"]} for i in s["items"]]}
        for s in plan["sections"]]}
    r = c.put(f"/api/projects/{pid}/plan", json=raw)
    assert r.status_code == 200 and r.json()["title"] == "Air Bersih untuk Desa"

    assert c.get(f"/api/projects/{pid}/prompt").json()["prompt"].count("|") > 10

    assert c.post(f"/api/projects/{pid}/render", json={"transition": "fade"}).status_code == 200
    p = wait(c, pid)
    assert p["job"]["error"] == "", p["job"]
    out = p["outputs"][0]
    path = f"/api/projects/{pid}/files/outputs/{out['file']}"
    assert c.get(path).status_code == 200
    from storymaker import storage
    info = media.probe(storage.project_dir(pid) / "outputs" / out["file"])
    assert (info["width"], info["height"]) == (1080, 1920)
    assert abs(info["duration"] - out["duration"]) < 0.6
    assert info["has_audio"]

    # path traversal ditolak
    assert c.get(f"/api/projects/{pid}/files/../project.json").status_code == 404
