"""M2.1 di BROWSER sungguhan (Chromium): kontrol Smart Framing + "Pratinjau hasil" (MP4 dari pipeline render).

Alur: unggah video -> analisis (transkrip tiruan) -> buka proyek -> mode bingkai Otomatis/Fokus Materi/Manual
(opsi yang relevan tampil, tersimpan di resep) -> daftar bagian bingkai + pilihan per bagian -> Pratinjau hasil
memutar MP4 360x640 yang dirender dari resep tersimpan.

    pytest -m browser tests/test_m2_browser_studio.py     ($BROWSER_ARTIFACTS: simpan tangkapan layar)
"""
import os
import socket
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api")
KEY = "kunci-uji-desktop-studio-0123456"  # pragma: allowlist secret


@pytest.fixture
def studio_server(monkeypatch, tmp_path, app_client, fake_transcript):
    import uvicorn

    from app import config, main, transcribe
    monkeypatch.setattr(main, "DESKTOP_MODE", True)
    monkeypatch.setattr(main, "DESKTOP_LAUNCH_KEY", KEY)
    monkeypatch.setattr(main, "EXPORT_DIR", tmp_path / "export")
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "export")
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    main.ensure_desktop_user()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(10)


def _recipe(page, pid):
    return page.request.get(f"/api/projects/{pid}/recipe").json()["recipe"]


def test_smart_framing_controls_and_preview_clip(studio_server, sample_video):
    base = studio_server
    shots = os.environ.get("BROWSER_ARTIFACTS")
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, base_url=base)
        page = ctx.new_page()
        page.goto(f"/api/desktop/enter?key={KEY}")
        res = page.request.post("/api/projects", multipart={
            "file": {"name": "kajian.mp4", "mimeType": "video/mp4", "buffer": sample_video.read_bytes()}})
        assert res.ok, res.text()
        pid, job = res.json()["project"]["id"], res.json()["job"]["id"]
        deadline = time.time() + 120
        while page.request.get(f"/api/jobs/{job}").json()["status"] not in ("selesai", "gagal") and time.time() < deadline:
            time.sleep(0.3)
        page.goto("/")
        page.click(f"li[data-id='{pid}']")
        page.wait_for_selector("#frameModeSeg button.active[data-v=auto]")
        page.wait_for_selector("#frameSections .fs-row", timeout=15000)
        assert page.is_hidden("#cropX") and page.is_visible("#frameDistance")
        page.click("#frameModeSeg button[data-v=material]")
        assert "papan" in page.inner_text("#frameModeHint").lower()
        page.click("#frameModeSeg button[data-v=manual]")
        assert page.is_visible("#cropX") and page.is_visible("#cropY") and page.is_visible("#frameZoom")
        page.fill("#frameZoom", "1.4")
        page.dispatch_event("#frameZoom", "change")
        deadline = time.time() + 10
        while _recipe(page, pid)["crop"]["mode"] != "manual" and time.time() < deadline:
            time.sleep(0.3)
        crop = _recipe(page, pid)["crop"]
        assert crop["mode"] == "manual" and crop["reframe"] == "manual" and abs(crop["zoom"] - 1.4) < 1e-6
        page.click("#frameModeSeg button[data-v=auto]")
        page.wait_for_selector("#frameSections .fs-row")
        page.select_option("#frameSections .fs-row select", "split")
        deadline = time.time() + 10
        while not _recipe(page, pid)["crop"]["sections"] and time.time() < deadline:
            time.sleep(0.3)
        assert _recipe(page, pid)["crop"]["sections"][0]["layout"] == "split"
        page.click("#previewClipBtn")
        page.wait_for_selector("#previewClipBox:not([hidden])", timeout=60000)
        page.wait_for_function("document.getElementById('previewClip').readyState >= 1", timeout=30000)
        assert page.evaluate("[document.getElementById('previewClip').videoWidth, document.getElementById('previewClip').videoHeight]") == [360, 640]
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(shots) / "m2-framing.png"), full_page=False)
        browser.close()
