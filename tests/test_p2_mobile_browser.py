"""P2 di browser: PWA (service worker, installability, cache, offline) dan editor di HP dengan SENTUHAN.

PENTING (jujur soal cakupan): ini EMULASI, bukan HP sungguhan.
- Chromium dengan profil perangkat Playwright (Pixel 7, iPhone 14: ukuran layar, rasio piksel, user agent,
  layar sentuh). Sentuhan dikirim sebagai event sentuh asli lewat Chrome DevTools Protocol.
- WebKit (mesin Safari) dengan profil iPhone 14 hanya bila Playwright WebKit terpasang (di CI); WebKit Linux
  biasanya tidak bisa memutar H.264, sehingga pemeriksaan yang butuh video dilewati di sana.
Safari iOS/Chrome Android di perangkat fisik, pemasangan ke layar utama sungguhan, dan perilaku latar belakang
OS belum diuji.

    pytest -m browser tests/test_p2_mobile_browser.py
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
from browser_stack import ADMIN, Stack, _alive_queues, _wait
from conftest import _ffmpeg

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api")
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def mstack(tmp_path_factory):
    import httpx
    root = tmp_path_factory.mktemp("p2-mobile")
    s = Stack(root / "data")
    s.start_web()
    try:
        c = httpx.Client(base_url=s.base, timeout=60)
        _wait(lambda: c.get("/api/health").status_code == 200, 60, "web hidup", s)
        assert c.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}).status_code == 200
        s.api = c
        s.start_analysis()
        s.start_render()
        _wait(lambda: {"analisis", "render"} <= _alive_queues(c), 60, "worker analisis & render hidup", s)
        video = root / "hp.mp4"
        _ffmpeg("-f", "lavfi", "-i", "smptehdbars=size=1280x720:rate=25:duration=12", "-f", "lavfi", "-i",
                "sine=frequency=330:duration=12", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(video))
        with video.open("rb") as fh:
            res = c.post("/api/projects", files={"file": ("editor hp.mp4", fh, "video/mp4")})
        s.pid = res.json()["project"]["id"]
        _wait(lambda: c.get(f"/api/projects/{s.pid}").json()["status"] == "siap", 120, "analisis selesai", s)
        yield s
    finally:
        s.stop_all()


def _shots(tmp_path) -> Path:
    p = Path(os.environ.get("BROWSER_ARTIFACTS") or tmp_path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _login(page, st):
    page.goto(st.base + "/login.html")
    page.fill("#email", ADMIN[0])
    page.fill("#password", ADMIN[1])
    page.click("#loginBtn")
    page.wait_for_selector(f'#projectList li[data-id="{st.pid}"]', timeout=20000)


def _recipe(st):
    return st.api.get(f"/api/projects/{st.pid}/recipe").json()


def _saved(page, timeout=15000):
    page.wait_for_function("() => document.querySelector('#saveState').classList.contains('saved')", timeout=timeout)


def _center(page, sel):
    b = page.locator(sel).bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


class Touch:
    """Seret dengan jari: event sentuh asli lewat CDP (Chromium) atau PointerEvent sentuh (WebKit)."""

    def __init__(self, page, engine):
        self.page, self.engine = page, engine
        self.cdp = page.context.new_cdp_session(page) if engine == "chromium" else None

    def drag(self, sel, dx, dy=0.0, steps=10):
        x0, y0 = _center(self.page, sel)
        if self.cdp:
            send = self.cdp.send
            send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
            for i in range(1, steps + 1):
                send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [
                    {"x": x0 + dx * i / steps, "y": y0 + dy * i / steps}]})
                time.sleep(0.03)
            send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        else:
            self.page.evaluate("""([sel, x0, y0, dx, dy, steps]) => {
                const el = document.querySelector(sel);
                const ev = (t, x, y) => el.dispatchEvent(new PointerEvent(t, {bubbles: true, cancelable: true,
                    pointerId: 7, pointerType: 'touch', isPrimary: true, clientX: x, clientY: y}));
                ev('pointerdown', x0, y0);
                for (let i = 1; i <= steps; i++) ev('pointermove', x0 + dx * i / steps, y0 + dy * i / steps);
                ev('pointerup', x0 + dx, y0 + dy); }""", [sel, x0, y0, dx, dy, steps])


def _engines():
    out = ["chromium"]
    if os.environ.get("P2_WEBKIT", "auto") != "0":
        out.append("webkit")
    return out


# ------------------------------------------------------------------ PWA

def test_pwa_installable_service_worker_cache_policy_and_offline(mstack, tmp_path):
    st = mstack
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(**pw.devices["Pixel 7"])
        page = ctx.new_page()
        _login(page, st)
        page.wait_for_function("() => navigator.serviceWorker && navigator.serviceWorker.controller !== null "
                               "|| (location.reload(), false)", timeout=20000, polling=2000)
        page.wait_for_function("() => navigator.serviceWorker.controller !== null", timeout=20000)
        _login(page, st) if "login" in page.url else None
        page.wait_for_selector(f'#projectList li[data-id="{st.pid}"]', timeout=20000)

        cdp = ctx.new_cdp_session(page)
        assert cdp.send("Page.getInstallabilityErrors")["installabilityErrors"] == []   # bisa dipasang (Chrome)
        manifest = cdp.send("Page.getAppManifest")
        assert manifest["url"].endswith("/manifest.webmanifest") and not manifest.get("errors")

        # Pakai aplikasi seperti biasa: buka proyek, putar pratinjau (permintaan Range), simpan resep, render list.
        page.click(f'#projectList li[data-id="{st.pid}"]')
        page.wait_for_function("() => document.querySelector('#player').readyState >= 1", timeout=30000)
        page.evaluate("() => { const v = document.querySelector('#player'); v.muted = true; return v.play().catch(() => 0); }")
        time.sleep(1.5)
        page.evaluate("() => document.querySelector('#player').pause()")
        page.select_option("#colorSelect", "warm")
        _saved(page)
        page.click('.tab[data-tab="rendersTab"]')

        shell = json.loads("[" + re.search(r"const SHELL = \[(.*?)\];", (REPO / "web" / "sw.js").read_text(),
                                           re.S).group(1).rstrip().rstrip(",") + "]")
        cached = page.evaluate("""async () => { const out = [];
            for (const k of await caches.keys()) { const c = await caches.open(k);
              for (const r of await c.keys()) out.push(new URL(r.url).pathname + new URL(r.url).search); }
            return out; }""")
        assert cached, "service worker tidak menyimpan berkas statis"
        assert set(cached) <= set(shell), sorted(set(cached) - set(shell))
        assert not [u for u in cached if u.startswith("/api") or re.search(r"\.(mp4|jpg|json)$", u)]
        idb = page.evaluate("async () => indexedDB.databases ? (await indexedDB.databases()).length : 0")
        assert idb == 0                                                   # tidak ada database lokal
        keys = page.evaluate("() => Object.keys(localStorage)")
        assert all(k == "ve-settings" or k.startswith("ve-upload:") for k in keys), keys

        # Offline: server tidak terjangkau -> halaman offline dari cache, tanpa data proyek. (Emulasi offline
        # Playwright tidak berlaku untuk permintaan service worker, jadi server web dimatikan sungguhan.)
        st.kill("web")
        page.goto(st.base + "/")
        page.wait_for_selector("#offlineCard", timeout=10000)
        assert "tidak ada koneksi" in page.inner_text("body").lower() and st.pid not in page.content()
        page.screenshot(path=str(_shots(tmp_path) / "p2-offline.png"))
        st.start_web()
        _wait(lambda: st.api.get("/api/health").status_code == 200, 60, "web hidup lagi", st)
        page.goto(st.base + "/")
        page.wait_for_selector(f'#projectList li[data-id="{st.pid}"]', timeout=20000)

        # Keluar akun: Cache Storage dibersihkan.
        page.click("#logoutBtn")
        page.wait_for_url("**/login.html", timeout=10000)
        left = page.evaluate("async () => { let n = 0; for (const k of await caches.keys()) n += (await (await caches.open(k)).keys()).length; return n; }")
        assert left <= len(shell)          # SW memasang ulang shell statis saja; tidak ada data pribadi
        ctx.close()
        browser.close()


def test_install_button_per_platform(mstack):
    """Tombol pasang: tersembunyi di Chromium sampai beforeinstallprompt; iPhone/Mac Safari mendapat petunjuk."""
    st = mstack
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(**pw.devices["iPhone 14"])          # UA iPhone (profil emulasi)
        page = ctx.new_page()
        _login(page, st)
        page.wait_for_selector("#installBtn:not([hidden])", timeout=10000)
        page.click("#installBtn")
        assert "Tambahkan ke Layar Utama" in page.inner_text("#installHelpText")
        page.click("#installHelpClose")
        ctx.close()
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        _login(page, st)
        assert page.is_hidden("#installBtn")
        page.evaluate("""() => { const e = new Event('beforeinstallprompt'); e.prompt = () => { window.__prompted = true; };
            e.userChoice = Promise.resolve({outcome: 'dismissed'}); window.dispatchEvent(e); }""")
        page.wait_for_selector("#installBtn:not([hidden])")
        page.click("#installBtn")
        page.wait_for_function("() => window.__prompted === true")
        ctx.close()
        browser.close()


# ------------------------------------------------------------------ editor di HP

@pytest.mark.parametrize("engine,device", [("chromium", "Pixel 7"), ("chromium", "iPhone 14"), ("chromium", "Galaxy S9+"),
                                           ("webkit", "iPhone 14")])
def test_mobile_editor_touch_flow(mstack, tmp_path, engine, device):
    st = mstack
    shots = _shots(tmp_path)
    tag = f"{engine}-{device.replace(' ', '')}"
    with playwright_api.sync_playwright() as pw:
        bt = getattr(pw, engine)
        if engine == "webkit" and (os.environ.get("P2_WEBKIT") == "0" or not Path(bt.executable_path).exists()):
            pytest.skip("Playwright WebKit tidak terpasang di lingkungan ini (dijalankan di CI)")
        browser = bt.launch()
        ctx = browser.new_context(**pw.devices[device])
        page = ctx.new_page()
        _login(page, st)
        vw = page.evaluate("innerWidth")
        page.tap(f'#projectList li[data-id="{st.pid}"]')
        page.wait_for_function("() => document.body.classList.contains('project-open')")
        page.wait_for_function("() => !document.querySelector('#saveState').hidden", timeout=20000)
        plays_h264 = page.evaluate("document.createElement('video').canPlayType('video/mp4; codecs=\"avc1.42E01E\"') !== ''")
        if plays_h264:
            page.wait_for_function("() => document.querySelector('#player').videoWidth > 0", timeout=30000)
        page.wait_for_function("() => +document.querySelector('#endInput').value > 0", timeout=20000)

        # Tata letak HP: daftar proyek disembunyikan, pratinjau selebar layar, tanpa gulir samping, tombol besar.
        lay = page.evaluate("""() => { const r = (s) => document.querySelector(s).getBoundingClientRect();
            return {scroll: document.documentElement.scrollWidth, sidebar: getComputedStyle(document.querySelector('.sidebar')).display,
              player: r('#playerWrap').width, bar: [...document.querySelectorAll('#mobileBar button')].map(b => {
                const q = b.getBoundingClientRect(); return [Math.round(q.width), Math.round(q.height)]; }),
              barBottom: r('#mobileBar').bottom, vh: innerHeight}; }""")
        assert lay["scroll"] <= vw and lay["sidebar"] == "none" and lay["player"] >= vw - 1, lay
        assert len(lay["bar"]) == 4 and all(w >= 44 and h >= 44 for w, h in lay["bar"]), lay
        assert abs(lay["barBottom"] - lay["vh"]) <= 1, lay
        page.screenshot(path=str(shots / f"p2-{tag}-editor.png"))

        touch = Touch(page, engine)
        # Seret pegangan akhir klip di timeline dengan jari -> rentang berubah & tersimpan.
        before = _recipe(st)["recipe"]["selection"] or {"start": 0, "end": float(page.input_value("#endInput"))}
        tl = page.locator("#timeline").bounding_box()
        dur = st.api.get(f"/api/projects/{st.pid}").json()["duration"]
        target = min(dur - 0.5, before["end"] + 3.0)
        hx, _ = _center(page, '#tlSel .tl-h[data-h="end"]')
        touch.drag('#tlSel .tl-h[data-h="end"]', tl["x"] + tl["width"] * target / dur - hx)
        _saved(page)
        sel = _recipe(st)["recipe"]["selection"]
        assert abs(sel["end"] - target) < 0.4 and abs(sel["start"] - before["start"]) < 0.05, (before, sel, target)

        # Geser bingkai crop dengan jari (mode manual).
        if page.is_checked("#reframeCheck"):
            page.tap("#reframeCheck")
        if plays_h264:
            x0 = float(page.input_value("#cropX"))
            touch.drag("#cropHandle", -60 if x0 > 0.5 else 60)
            _saved(page)
            assert abs(_recipe(st)["recipe"]["crop"]["x"] - x0) > 0.05

        # Pintasan "Subtitle": bagian subtitle terlihat di bawah pratinjau yang menempel.
        page.tap('#mobileNav button[data-target="secSubtitle"]')
        page.wait_for_timeout(800)
        pos = page.evaluate("""() => ({player: document.querySelector('#playerWrap').getBoundingClientRect(),
            sub: document.querySelector('#secSubtitle').getBoundingClientRect(), vh: innerHeight})""")
        assert abs(pos["player"]["top"]) <= 1, pos                                  # pratinjau tetap di atas
        assert pos["player"]["bottom"] - 2 <= pos["sub"]["top"] < pos["vh"] * 0.75, pos

        page.select_option("#templateSelect", "builtin:pop")
        page.fill("#subColor", "#00ff88") if engine == "chromium" else page.eval_on_selector(
            "#subColor", "e => { e.value = '#00ff88'; e.dispatchEvent(new Event('change', {bubbles: true})); }")
        page.eval_on_selector("#subColor", "e => e.dispatchEvent(new Event('change', {bubbles: true}))")
        _saved(page)
        rec = _recipe(st)["recipe"]
        assert rec["template_id"] == "builtin:pop" and rec["subtitles"]["style"] == "pop"
        assert rec["subtitles"]["color"] == "#00FF88"
        page.screenshot(path=str(shots / f"p2-{tag}-subtitle.png"))

        # Koreksi teks subtitle lewat tab Transkrip.
        page.tap('.tab[data-tab="transcriptTab"]')
        page.tap("#transcriptBox .seg-edit >> nth=0")
        page.fill("#transcriptBox textarea.seg-text", "Koreksi dari HP")
        page.tap("#transcriptBox .seg-save")
        _wait(lambda: _recipe(st)["recipe"]["text_fixes"].get("0") == "Koreksi dari HP", 10, "koreksi tersimpan", st)

        # Aplikasi ke latar belakang (pindah aplikasi / layar dikunci): perubahan langsung disimpan, tanpa menunggu.
        page.eval_on_selector("#saturation", "e => { e.value = '0.7'; e.dispatchEvent(new Event('change', {bubbles: true})); }")
        page.evaluate("""() => { Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
            document.dispatchEvent(new Event('visibilitychange')); }""")
        _wait(lambda: _recipe(st)["recipe"]["look"]["saturation"] == 0.7, 3, "tersimpan saat latar belakang", st, 0.1)
        page.evaluate("() => { delete document.visibilityState; }")

        # Render dari bilah bawah.
        renders = len(st.api.get(f"/api/projects/{st.pid}").json()["renders"])
        page.tap("#mbRender")
        _wait(lambda: len(st.api.get(f"/api/projects/{st.pid}").json()["renders"]) > renders, 120, "render dari HP",
              st, 1)
        proj = st.api.get(f"/api/projects/{st.pid}").json()
        job = max((j for j in proj["jobs"] if j["kind"] == "render"), key=lambda j: j["created"])
        assert job["status"] == "selesai", job
        item = max(proj["renders"], key=lambda r: r["created"])            # render dari perangkat ini
        out = tmp_path / f"{tag}.mp4"
        out.write_bytes(st.api.get(f"/api/projects/{st.pid}/renders/{item['file']}").content)
        probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                                           str(out)], capture_output=True, text=True, check=True).stdout)
        assert abs(float(probe["format"]["duration"]) - (sel["end"] - sel["start"])) < 0.6   # rentang hasil seret
        page.screenshot(path=str(shots / f"p2-{tag}-render.png"))

        # Kembali ke daftar proyek.
        page.tap("#backBtn")
        page.wait_for_function("() => !document.body.classList.contains('project-open')")
        assert page.is_visible(f'#projectList li[data-id="{st.pid}"]')
        ctx.close()
        browser.close()
