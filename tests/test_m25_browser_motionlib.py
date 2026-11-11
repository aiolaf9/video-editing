"""M2.5 Motion Library di BROWSER sungguhan (Chromium): galeri per kategori dengan thumbnail & klip gerak dari engine
render, sekali klik menambahkan preset (posisi video / seluruh klip / beberapa rentang), kontrol per item tersimpan di
resep, intensitas ringan-seimbang-dinamis, hapus, dan undo/redo.

    pytest -m browser tests/test_m25_browser_motionlib.py     ($BROWSER_ARTIFACTS: simpan tangkapan layar)
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


def _fx(page, pid):
    return [o for o in (_recipe(page, pid)["overlays"] or []) if o["type"] == "fx"]


def test_motion_library_panel(studio_server, sample_video):  # noqa: F811
    base = studio_server
    shots = os.environ.get("BROWSER_ARTIFACTS")
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1100}, base_url=base)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"/api/desktop/enter?key={KEY}")
        res = page.request.post("/api/projects", multipart={
            "file": {"name": "kajian.mp4", "mimeType": "video/mp4", "buffer": sample_video.read_bytes()}})
        assert res.ok, res.text()
        pid, job = res.json()["project"]["id"], res.json()["job"]["id"]
        _until(lambda: page.request.get(f"/api/jobs/{job}").json()["status"] in ("selesai", "gagal"), 120)
        page.goto("/")
        page.click(f"li[data-id='{pid}']")
        page.wait_for_selector("#mlCats button[data-cat=titles]")
        # 6 kategori; galeri judul: thumbnail dari engine render, klip gerak saat disorot
        assert page.locator("#mlCats button").count() == 6
        page.click("#mlCats button[data-cat=titles]")
        page.wait_for_function("[...document.querySelectorAll('#mlGallery img')].filter(i => i.naturalWidth > 0).length >= 3",
                               timeout=90000)
        page.wait_for_selector(".ml-card[data-id='title.slam_stack']:not(.pending) img", timeout=90000)
        page.hover(".ml-card[data-id='title.slam_stack']")
        page.wait_for_function("document.querySelector(\".ml-card[data-id='title.slam_stack'] video\").readyState >= 2",
                               timeout=60000)
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            page.locator("#secMotionLib").screenshot(path=str(Path(shots) / "m25-galeri-judul.png"))
        # sekali klik di posisi video
        page.evaluate("document.getElementById('player').currentTime = 1.0")
        page.click(".ml-card[data-id='title.slam_stack']")
        assert _until(lambda: any(o["preset"] == "title.slam_stack" for o in _fx(page, pid)))
        # beberapa rentang sekaligus (kamera)
        page.click("#mlCats button[data-cat=camera]")
        page.click("#mlScope button[data-v=ranges]")
        page.fill("#mlRanges", "0-2.5, 4-6")
        page.click(".ml-card[data-id='cam.ken_burns']")
        t_click = time.time()
        assert _until(lambda: sum(o["preset"] == "cam.ken_burns" for o in _fx(page, pid)) == 2), (
            [(o["preset"], o["start"], o["end"]) for o in _fx(page, pid)], errors)
        # Galeri kamera baru dibuka (thumbnail masih dirender di server): simpan tidak boleh tertahan antrean gambar.
        assert time.time() - t_click < 5, time.time() - t_click
        # seluruh klip (caption)
        page.click("#mlCats button[data-cat=captions]")
        page.click("#mlScope button[data-v=clip]")
        page.click(".ml-card[data-id='cap.word_bounce']")
        assert _until(lambda: any(o["preset"] == "cap.word_bounce" for o in _fx(page, pid)))
        # kontrol item di tab Animasi: teks, warna, kecepatan -> tersimpan di params
        page.click("[data-tab=animTab]")
        row = page.locator(".plan-item", has_text="Tumpuk Hentak")
        row.locator("input[data-p=text]").fill("Sedekah membuka pintu rezeki")
        row.locator("input[data-p=speed]").fill("1.6")
        row.locator("input[data-p=speed]").dispatch_event("input")
        row.locator("select[data-p=easing]").select_option("inout")
        assert _until(lambda: next(o for o in _fx(page, pid) if o["preset"] == "title.slam_stack")["params"]
                      == {"text": "Sedekah membuka pintu rezeki", "speed": 1.6, "easing": "inout"})
        # intensitas klip
        page.click("#mlLevel button[data-v=dinamis]")
        assert _until(lambda: _recipe(page, pid)["motion"]["level"] == "dinamis")
        if shots:
            page.locator("#planList").screenshot(path=str(Path(shots) / "m25-daftar-animasi.png"))
        # hapus satu, lalu urungkan (Ctrl+Z / tombol) mengembalikannya
        n = len(_fx(page, pid))
        page.locator(".plan-item", has_text="Kata Memantul").locator("button[data-act=del]").click()
        assert _until(lambda: len(_fx(page, pid)) == n - 1)
        page.wait_for_selector("#undoBtn:not([disabled])")
        time.sleep(0.6)
        page.click("#undoBtn")
        assert _until(lambda: len(_fx(page, pid)) == n)
        # dibuka ulang: semua tetap ada
        page.reload()
        page.click(f"li[data-id='{pid}']")
        page.wait_for_selector("#mlLevel button.active[data-v=dinamis]")
        page.click("[data-tab=animTab]")
        assert page.locator(".plan-item", has_text="Ken Burns Halus").count() == 2
        assert not errors, errors
        browser.close()
