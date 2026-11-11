"""M2.5.1 Area Dilindungi di BROWSER sungguhan (Chromium): gambar kotak & poligon di atas video, area tersimpan di
resep (koordinat sumber), safe-zone memindahkan grafis menjauhi area, pisah di sini + gambar ulang untuk subjek
bergerak, hapus + urungkan, buka ulang proyek.

    pytest -m browser tests/test_m251_browser_protect.py     ($BROWSER_ARTIFACTS: simpan tangkapan layar)
"""
import os
import time
from pathlib import Path

import pytest

from tests.test_m2_browser_studio import KEY, studio_server  # noqa: F401  (fixture)

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api")


def _recipe(page, pid):
    return page.request.get(f"/api/projects/{pid}/recipe").json()["recipe"]


def _until(fn, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.3)
    return fn()


def _video_box(page):
    """Persegi video yang tampil (object-fit: contain) dalam koordinat halaman."""
    return page.evaluate("""() => { const r = displayedVideoRect(); const w = document.getElementById('playerWrap')
        .getBoundingClientRect(); return {x: w.left + r.x, y: w.top + r.y, w: r.w, h: r.h}; }""")


def test_protected_areas_in_editor(studio_server, sample_video):  # noqa: F811
    shots = os.environ.get("BROWSER_ARTIFACTS")
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(viewport={"width": 1440, "height": 1100}, base_url=studio_server).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"/api/desktop/enter?key={KEY}")
        res = page.request.post("/api/projects", multipart={
            "file": {"name": "ternak.mp4", "mimeType": "video/mp4", "buffer": sample_video.read_bytes()}})
        pid, job = res.json()["project"]["id"], res.json()["job"]["id"]
        _until(lambda: page.request.get(f"/api/jobs/{job}").json()["status"] in ("selesai", "gagal"), 120)
        # hook di posisi bawaan (sepertiga atas) sebagai grafis yang harus menyingkir
        page.request.put(f"/api/projects/{pid}/plan", data=[
            {"type": "hook", "text": "Ternak sehat panen berkah", "start": 0.0, "end": 3.0}])
        page.goto("/")
        page.click(f"li[data-id='{pid}']")
        page.wait_for_function("document.getElementById('player').videoWidth > 0", timeout=20000)
        page.click("[data-tab=animTab]")
        page.evaluate("document.getElementById('player').currentTime = 0.5")
        # kotak: seret di sepertiga atas-tengah video (tempat hook berada)
        page.click("#protectRectBtn")
        v = _video_box(page)
        page.mouse.move(v["x"] + v["w"] * 0.35, v["y"] + v["h"] * 0.12)
        page.mouse.down()
        page.mouse.move(v["x"] + v["w"] * 0.65, v["y"] + v["h"] * 0.55, steps=8)
        page.mouse.up()
        assert _until(lambda: len(_recipe(page, pid)["protect"]) == 1)
        area = _recipe(page, pid)["protect"][0]
        assert abs(area["box"][0] - 0.35) < 0.02 and abs(area["box"][2] - 0.30) < 0.02 and area["start"] <= 0.6
        page.fill("#protectList li[data-id] input[data-k=label]", "Sapi")
        page.dispatch_event("#protectList li[data-id] input[data-k=label]", "change")
        assert _until(lambda: _recipe(page, pid)["protect"][0]["label"] == "Sapi")
        # safe-zone memakai area: hook dipindah/diperkecil, alasannya menyebut area dilindungi
        lay = lambda: page.request.get(f"/api/projects/{pid}/motion-layout",  # noqa: E731
                                       params={"start": 0, "end": 8}).json()["items"]
        assert _until(lambda: any("area dilindungi" in (x.get("reason") or "") or x.get("action") in ("dipindah", "diperkecil")
                                  for x in lay().values()), 15), lay()
        # subjek bergerak: pisah di 2,0 dtk lalu gambar ulang bagian kedua di kiri bawah
        page.evaluate("document.getElementById('player').currentTime = 2.0")
        page.click("#protectList li[data-id] button[data-act=split]")
        page.wait_for_selector("#protectLayer.drawing")
        v = _video_box(page)
        page.mouse.move(v["x"] + v["w"] * 0.05, v["y"] + v["h"] * 0.6)
        page.mouse.down()
        page.mouse.move(v["x"] + v["w"] * 0.30, v["y"] + v["h"] * 0.95, steps=6)
        page.mouse.up()
        assert _until(lambda: len(_recipe(page, pid)["protect"]) == 2
                      and _recipe(page, pid)["protect"][1]["box"][0] < 0.1)
        a, b = _recipe(page, pid)["protect"]
        assert abs(a["end"] - 2.0) < 0.05 and abs(b["start"] - 2.0) < 0.05 and b["label"] == "Sapi"
        # poligon: 4 klik + klik ganda
        page.evaluate("document.getElementById('player').currentTime = 5.0")
        page.click("#protectPolyBtn")
        v = _video_box(page)
        for fx, fy in ((0.7, 0.2), (0.9, 0.25), (0.85, 0.6), (0.72, 0.55)):
            page.mouse.click(v["x"] + v["w"] * fx, v["y"] + v["h"] * fy)
        page.mouse.dblclick(v["x"] + v["w"] * 0.72, v["y"] + v["h"] * 0.55)
        assert _until(lambda: len(_recipe(page, pid)["protect"]) == 3)
        poly = _recipe(page, pid)["protect"][2]
        assert len(poly["points"]) >= 3 and abs(poly["box"][0] - 0.7) < 0.02
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            page.evaluate("document.getElementById('player').currentTime = 5.5")
            time.sleep(0.5)
            page.locator("#playerWrap").screenshot(path=str(Path(shots) / "m251-area-video.png"))
            page.locator("#protectBox").screenshot(path=str(Path(shots) / "m251-area-daftar.png"))
        # hapus lalu urungkan
        page.click("#protectList li[data-id] >> nth=2 >> button[data-act=del]")
        assert _until(lambda: len(_recipe(page, pid)["protect"]) == 2)
        time.sleep(0.6)
        page.click("#undoBtn")
        assert _until(lambda: len(_recipe(page, pid)["protect"]) == 3)
        # buka ulang: area tetap ada dan tampil di daftar
        page.reload()
        page.click(f"li[data-id='{pid}']")
        page.click("[data-tab=animTab]")
        page.wait_for_selector("#protectList li[data-id]")
        assert page.locator("#protectList li[data-id]").count() == 3
        assert not errors, errors
        browser.close()
