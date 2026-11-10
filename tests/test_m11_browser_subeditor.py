"""M1.1 di BROWSER sungguhan (Chromium) dengan proses web & worker nyata: editor subtitle visual.

Alur: kata tidak yakin ditandai -> klik teks, edit langsung, Enter (timing kata lain tetap) -> cari & ganti massal
dengan pratinjau -> saran istilah diterima satu klik (tidak ada yang berubah sebelum diterima) -> render ditahan
dialog "belum ditinjau" -> "Tinjau dulu" membuka filter -> tandai ditinjau -> render jalan tanpa dialog.

    pytest -m browser tests/test_m11_browser_subeditor.py     ($BROWSER_ARTIFACTS: simpan tangkapan layar)
"""
import json
import os
from pathlib import Path

import pytest
from browser_stack import ADMIN, Stack, _alive_queues, _wait
from conftest import _ffmpeg

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api")


def _seg(words):
    ws = [{"word": w, "start": s, "end": e, "p": p} for w, s, e, p in words]
    return {"start": ws[0]["start"], "end": ws[-1]["end"], "text": " ".join(w["word"] for w in ws), "words": ws}


TRANSCRIPT = {"language": "id", "segments": [
    _seg([("Assalamualaikum", 0.5, 1.2, 0.95), ("semuanya,", 1.3, 1.8, 0.97)]),
    _seg([("insyaallah", 2.0, 2.6, 0.31), ("kita", 2.7, 2.9, 0.97), ("bayar", 3.0, 3.3, 0.9), ("jakat", 3.4, 3.9, 0.28)]),
    _seg([("di", 4.2, 4.3, 0.99), ("nurul", 4.4, 4.7, 0.6), ("hayat.", 4.8, 5.2, 0.7)]),
]}


@pytest.fixture(scope="module")
def sub_stack(tmp_path_factory):
    import httpx
    root = tmp_path_factory.mktemp("m11-sub")
    s = Stack(root / "data")
    s.start_web()
    try:
        c = httpx.Client(base_url=s.base, timeout=60)
        _wait(lambda: c.get("/api/health").status_code == 200, 60, "web hidup", s)
        assert c.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}).status_code == 200
        s.api = c
        s.start_analysis()
        s.start_render()
        _wait(lambda: {"analisis", "render"} <= _alive_queues(c), 60, "worker hidup", s)
        video = root / "ceramah.mp4"
        _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=8", "-f", "lavfi", "-i",
                "sine=frequency=330:duration=8", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(video))
        with video.open("rb") as fh:
            res = c.post("/api/projects", files={"file": ("ceramah.mp4", fh, "video/mp4")}, data={"language": "id"})
        assert res.status_code == 200, res.text
        s.pid = res.json()["project"]["id"]
        _wait(lambda: c.get(f"/api/projects/{s.pid}").json()["status"] == "siap", 120, "analisis selesai", s)
        # Transkrip uji dengan probabilitas kata (worker tiruan tidak menjalankan Whisper).
        (root / "data" / "projects" / s.pid / "transcript.json").write_text(json.dumps(TRANSCRIPT))
        yield s
    finally:
        s.stop_all()


def _shots(tmp_path) -> Path:
    p = Path(os.environ.get("BROWSER_ARTIFACTS") or tmp_path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _server_seg(stack, i):
    return stack.api.get(f"/api/projects/{stack.pid}/transcript").json()["segments"][i]


def test_subtitle_editor_end_to_end(sub_stack, tmp_path):
    shots = _shots(tmp_path)
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(sub_stack.base + "/login.html")
        page.fill("#email", ADMIN[0])
        page.fill("#password", ADMIN[1])
        page.click("#loginBtn")
        page.wait_for_selector(f'#projectList li[data-id="{sub_stack.pid}"]', timeout=20000)
        page.click(f'#projectList li[data-id="{sub_stack.pid}"]')
        page.wait_for_selector("#transcriptBox .sub-row", timeout=30000, state="attached")
        page.click('.tab[data-tab="transcriptTab"]')

        # 1. kata tidak yakin ditandai (p < 0,5) + saran istilah dimuat
        page.wait_for_function("() => document.querySelector('#unsureCount').textContent === '(2)'")
        assert page.locator("#transcriptBox .w.unsure").count() == 2
        page.wait_for_selector(".sugg", timeout=10000)
        assert page.locator('.sugg:has-text("zakat")').count() == 1
        page.screenshot(path=str(shots / "m11-editor-awal.png"), full_page=False)

        # 1b. render ditahan: ada kata belum ditinjau di klip -> dialog; "Tinjau dulu" membuka filter
        page.evaluate("() => { setSelection(0, 6); }")
        page.click('.tab[data-tab="clipsTab"]')
        page.click("#renderBtn")
        page.wait_for_selector("#reviewDialog[open]", timeout=10000)
        assert "2 kata" in page.inner_text("#reviewText") and "jakat" in page.inner_text("#reviewList")
        page.screenshot(path=str(shots / "m11-dialog-tinjau.png"))
        page.click("#reviewGoBtn")
        page.wait_for_selector('.filter-row .chip.active[data-filter="unsure"]')
        assert page.locator("#transcriptBox .sub-row").count() == 1          # hanya baris yang perlu ditinjau
        assert not sub_stack.api.get(f"/api/projects/{sub_stack.pid}").json()["renders"]   # tidak ada render
        page.click('.filter-row .chip[data-filter="all"]')

        # 2. edit langsung: klik teks baris 3, ketik, Enter
        page.click('#transcriptBox .sub-row[data-s="2"] .sub-text')
        page.wait_for_selector('#transcriptBox .sub-row[data-s="2"] .sub-text.editing')
        page.keyboard.press("Control+A")
        page.keyboard.type("di Nurul Hayat Surabaya.")
        page.keyboard.press("Enter")
        page.wait_for_function("() => { const el = document.querySelector('#transcriptBox .sub-row[data-s=\"2\"] .sub-text');"
                               " return el && !el.classList.contains('editing') && el.textContent.includes('Surabaya')"
                               " && document.querySelector('#transcriptBox .sub-row[data-s=\"2\"]').classList.contains('edited'); }")
        seg2 = _server_seg(sub_stack, 2)
        assert seg2["text"] == "di Nurul Hayat Surabaya." and seg2["words"][0]["start"] == 4.2

        # 3. cari & ganti: pratinjau dulu, lalu ganti semua (dialog konfirmasi diterima)
        page.fill("#findInput", "insyaallah")
        page.click("#findBtn")
        page.wait_for_function("() => document.querySelector('#findResult').textContent.startsWith('1 baris')")
        assert _server_seg(sub_stack, 1)["text"].startswith("insyaallah")          # belum berubah
        page.fill("#replaceInput", "Insya Allah")
        page.click("#replaceAllBtn")
        page.wait_for_function("() => document.querySelector('#findResult').textContent.startsWith('1 baris diganti')")
        assert any("di 1 baris" in m for m in dialogs)
        seg1 = _server_seg(sub_stack, 1)
        assert seg1["text"] == "Insya Allah kita bayar jakat"
        assert [w["start"] for w in seg1["words"][2:]] == [2.7, 3.0, 3.4]          # kata lain tidak bergeser
        page.fill("#findInput", "")
        page.dispatch_event("#findInput", "input")

        # 4. saran istilah: terima "zakat" (satu klik)
        page.wait_for_selector('.sugg:has-text("zakat") .sugg-ok')
        page.click('.sugg:has-text("zakat") .sugg-ok')
        page.wait_for_function("() => document.querySelector('#transcriptBox .sub-row[data-s=\"1\"] .sub-text')"
                               ".textContent.includes('zakat')")
        assert _server_seg(sub_stack, 1)["text"] == "Insya Allah kita bayar zakat"

        # 5. waktu: geser awal baris 1 (+0,1 dtk)
        page.click('#transcriptBox .sub-row[data-s="0"] [data-act="start+"]')
        page.wait_for_function("() => document.querySelector('#transcriptBox .sub-row[data-s=\"0\"] .t-val')"
                               ".textContent.startsWith('00:00.6')")
        assert abs(_server_seg(sub_stack, 0)["start"] - 0.6) < 1e-6

        # 6. semua kata tidak yakin sudah dikoreksi pengguna: render jalan tanpa dialog
        assert page.inner_text("#unsureCount") == ""
        renders_before = len(sub_stack.api.get(f"/api/projects/{sub_stack.pid}").json()["renders"])
        page.click('.tab[data-tab="clipsTab"]')
        page.click("#renderBtn")
        page.wait_for_selector('.tab.active[data-tab="rendersTab"]', timeout=15000)
        assert not page.locator("#reviewDialog[open]").count()
        _wait(lambda: len(sub_stack.api.get(f"/api/projects/{sub_stack.pid}").json()["renders"]) > renders_before,
              180, "render selesai", sub_stack)
        page.click('.tab[data-tab="transcriptTab"]')
        page.click('.filter-row .chip[data-filter="all"]')
        page.screenshot(path=str(shots / "m11-editor-akhir.png"))
        assert not errors, errors
        browser.close()
