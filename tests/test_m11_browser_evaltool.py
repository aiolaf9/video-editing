"""M1.1 di BROWSER sungguhan (Chromium): menu "🧪 Uji akurasi" aplikasi desktop, tanpa CMD.

Alur: masuk lewat kunci peluncuran -> tombol Uji akurasi (hanya desktop) -> isi folder -> Periksa (video tanpa .txt
disebut & dilewati) -> Mulai uji -> progres -> tabel WER/CER/waktu/RAM/istilah + tombol buka folder laporan.
Transkripsi per video (proses anak --eval-child) diganti tiruan: Whisper tidak tersedia di runner Linux uji ini;
jalur aslinya diuji di CI Windows (--eval dengan model sungguhan).

    pytest -m browser tests/test_m11_browser_evaltool.py     ($BROWSER_ARTIFACTS: simpan tangkapan layar)
"""
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api")
KEY = "kunci-uji-desktop-evaltool-0123"  # pragma: allowlist secret


@pytest.fixture
def desktop_server(monkeypatch, tmp_path, app_client):
    import uvicorn

    from app import config, main
    from desktop import nh_eval
    monkeypatch.setattr(main, "DESKTOP_MODE", True)
    monkeypatch.setattr(main, "DESKTOP_LAUNCH_KEY", KEY)
    monkeypatch.setattr(main, "EXPORT_DIR", tmp_path / "export")
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "export")
    monkeypatch.setenv("WHISPER_MODEL_ACCURATE", "small")
    monkeypatch.setenv("WHISPER_MODEL_HIGH", "large-v3-turbo")
    main.ensure_desktop_user()
    real = subprocess.run

    def fake_run(cmd, *a, **kw):
        if "--eval-child" not in cmd:
            return real(cmd, *a, **kw)
        media, q, out = cmd[cmd.index("--eval-child") + 1:][:3]
        time.sleep(0.4)
        wer = {"akurat": 0.162, "tinggi": 0.081}[q]
        Path(out).write_text(json.dumps({
            "file": Path(media).name, "quality": q, "model": transcribe_model(q), "duration": 60, "seconds": 12,
            "rtf": 0.2 if q == "akurat" else 0.45, "peak_ram_mb": 726 if q == "akurat" else 1911, "wer": wer,
            "cer": wer / 3, "ref_words": 120, "words": 118, "unsure_share": 0.1,
            "terms": {"terms_in_reference": 4, "terms_correct": 3, "term_accuracy": 0.75, "missed": ["zakat"]},
            "hypothesis": "teks"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def transcribe_model(q):
        return {"akurat": "small", "tinggi": "large-v3-turbo"}[q]

    monkeypatch.setattr(nh_eval.subprocess, "run", fake_run)
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
    folder = tmp_path / "Uji NH"
    folder.mkdir()
    for n in ("ceramah1", "ceramah2", "ceramah3"):
        (folder / f"{n}.mp4").write_bytes(b"x")
        (folder / f"{n}.txt").write_text("insya allah kita bayar zakat", encoding="utf-8")
    (folder / "belum-ada-teks.mp4").write_bytes(b"x")
    yield f"http://127.0.0.1:{port}", folder
    server.should_exit = True
    t.join(10)


def test_eval_dialog_runs_without_cmd(desktop_server):
    base, folder = desktop_server
    shots = os.environ.get("BROWSER_ARTIFACTS")
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 860})
        page.goto(f"{base}/api/desktop/enter?key={KEY}")
        page.wait_for_selector("#evalBtn:not([hidden])", timeout=20000)
        page.click("#evalBtn")
        page.fill("#evalFolder", str(folder))
        page.click("#evalScanBtn")
        page.wait_for_selector("#evalScan >> text=3 video siap diuji", timeout=10000)
        assert "belum-ada-teks.mp4" in page.inner_text("#evalScan")
        labels = page.inner_text("#evalModes")
        assert "Standar" in labels and "Kualitas Tinggi" in labels
        page.click("#evalStartBtn")
        page.wait_for_selector("#evalProgress:not([hidden])", timeout=10000)
        page.wait_for_selector(".eval-table", timeout=60000)
        table = page.inner_text(".eval-table")
        assert "16.2%" in table and "8.1%" in table and "75% dari" in table and "ya" in table
        assert page.is_visible("#evalOpenBtn") and "laporan-uji.md" in page.inner_text("#evalResult")
        assert (folder / "laporan-uji.md").exists()
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(shots) / "m11-uji-akurasi.png"))
        browser.close()
