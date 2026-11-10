"""P1 di BROWSER sungguhan (Chromium) dengan proses web, worker analisis & worker render nyata:

1. autosave + reload: pengaturan (rasio, crop, filter, template, subtitle, rentang) tersimpan di server dan kembali
   persis setelah halaman dimuat ulang;
2. dua perangkat (dua sesi browser): perangkat kedua memuat resep yang sama; edit bersamaan memunculkan banner
   konflik (tidak menimpa diam-diam); "Muat versi terbaru" memasang versi server; perangkat yang diam ikut
   diperbarui;
3. render MP4 dari resep, lalu bandingkan frame hasil render dengan pratinjau di browser pada waktu yang sama:
   warna/crop di area tanpa subtitle mirip, crop yang salah jelas berbeda, dan subtitle berada di pita yang sama;
4. paritas preset warna: filter CSS pratinjau (lookCss di app.js, dari tabel app/looks.py) vs filter FFmpeg yang
   dipakai render, diukur per petak warna.

    pytest -m browser tests/test_p1_browser_editor.py     (butuh playwright + Chromium; $BROWSER_ARTIFACTS opsional)
"""
import base64
import io
import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
from browser_stack import ADMIN, Stack, _alive_queues, _wait
from conftest import _ffmpeg
from PIL import Image

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def editor_stack(tmp_path_factory):
    import httpx
    root = tmp_path_factory.mktemp("p1-editor")
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
        video = root / "sumber.mp4"
        # Bidang warna besar & diam (mudah dibandingkan) + bilah warna; 10 detik, ada suara.
        _ffmpeg("-f", "lavfi", "-i", "smptehdbars=size=1280x720:rate=25:duration=10", "-f", "lavfi", "-i",
                "sine=frequency=330:duration=10", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(video))
        s.source = video
        with video.open("rb") as fh:
            res = c.post("/api/projects", files={"file": ("editor p1.mp4", fh, "video/mp4")})
        assert res.status_code == 200, res.text
        s.pid = res.json()["project"]["id"]
        _wait(lambda: c.get(f"/api/projects/{s.pid}").json()["status"] == "siap", 120, "analisis selesai", s)
        yield s
    finally:
        s.stop_all()


def _shots(tmp_path) -> Path:
    p = Path(os.environ.get("BROWSER_ARTIFACTS") or tmp_path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _open(browser, stack, viewport=(1280, 900)):
    ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept("Preset uji UI") if d.type == "prompt" else d.accept())
    page.goto(stack.base + "/login.html")
    page.fill("#email", ADMIN[0])
    page.fill("#password", ADMIN[1])
    page.click("#loginBtn")
    page.wait_for_selector(f'#projectList li[data-id="{stack.pid}"]', timeout=20000)
    page.click(f'#projectList li[data-id="{stack.pid}"]')
    page.wait_for_function("() => { const v = document.querySelector('#player');"
                           " return v && v.videoWidth > 0 && v.readyState >= 2; }", timeout=30000)
    page.wait_for_function("() => document.querySelector('#saveState') && !document.querySelector('#saveState').hidden")
    return ctx, page


def _set(page, sel, value):
    """Ubah kontrol seperti pengguna (memicu input + change)."""
    page.eval_on_selector(sel, """(el, v) => { if (el.type === 'checkbox') el.checked = v; else el.value = v;
        el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); }""",
                          value)


def _saved(page, timeout=15000):
    page.wait_for_function("() => document.querySelector('#saveState').classList.contains('saved')", timeout=timeout)


def _recipe(stack):
    return stack.api.get(f"/api/projects/{stack.pid}/recipe").json()


def _png(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(np.float32)


def _grid(img: np.ndarray, n=24) -> np.ndarray:
    return np.asarray(Image.fromarray(img.astype(np.uint8)).resize((n, n), Image.BOX)).astype(np.float32)


def test_autosave_reload_two_devices_conflict(editor_stack, tmp_path):
    st = editor_stack
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx_a, a = _open(browser, st)
        a.click('#aspectSeg button[data-v="1:1"]')
        _set(a, "#templateSelect", "builtin:news")
        a.click("#frameModeSeg button[data-v=manual]")
        _set(a, "#cropX", "0.15")
        _set(a, "#colorSelect", "warm")
        _set(a, "#brightness", "0.1")
        _set(a, "#subStyle", "simple")
        _set(a, "#startInput", "1.5")
        _set(a, "#endInput", "6")
        _saved(a)
        r = _recipe(st)
        assert r["version"] >= 1
        rec = r["recipe"]
        assert rec["aspect"] == "1:1" and rec["template_id"] == "builtin:news"
        assert rec["subtitles"]["font"] == "Bebas Neue" and rec["subtitles"]["position"] == "top"
        assert rec["subtitles"]["style"] == "simple" and {k: rec["crop"][k] for k in ("x", "y", "reframe", "mode")} == {"x": 0.15, "y": 0.5, "reframe": "manual", "mode": "manual"}
        assert rec["look"]["color"] == "warm" and rec["look"]["brightness"] == 0.1
        assert rec["selection"] == {"start": 1.5, "end": 6.0}
        assert not [j for j in st.api.get(f"/api/projects/{st.pid}").json()["jobs"] if j["kind"] == "render"]

        # Muat ulang halaman: semua kembali dari server.
        a.reload()
        a.wait_for_selector(f'#projectList li[data-id="{st.pid}"]')
        a.click(f'#projectList li[data-id="{st.pid}"]')
        a.wait_for_function("() => document.querySelector('#saveState').classList.contains('saved')", timeout=20000)
        a.wait_for_function("() => document.querySelector('#startInput').value === '1.5'", timeout=20000)
        vals = a.evaluate("""() => ({aspect: document.querySelector('#aspectSeg button.active').dataset.v,
            tpl: document.querySelector('#templateSelect').value, font: document.querySelector('#subFont').value,
            style: document.querySelector('#subStyle').value, b: document.querySelector('#brightness').value,
            color: document.querySelector('#colorSelect').value, end: document.querySelector('#endInput').value,
            filter: document.querySelector('#player').style.filter})""")
        assert vals == {"aspect": "1:1", "tpl": "builtin:news", "font": "Bebas Neue", "style": "simple", "b": "0.1",
                        "color": "warm", "end": "6.0", "filter": vals["filter"]}
        assert "sepia" in vals["filter"] and "brightness(1.2" in vals["filter"]     # pratinjau filter langsung (b=0.1)

        # Perangkat kedua memuat resep yang sama.
        ctx_b, b = _open(browser, st, viewport=(390, 844))
        b.wait_for_function("() => document.querySelector('#colorSelect').value === 'warm'", timeout=15000)
        assert b.evaluate("document.querySelector('#aspectSeg button.active').dataset.v") == "1:1"

        # Edit bersamaan: A menyimpan dulu, B (versi lama) mendapat banner konflik, bukan menimpa.
        _set(a, "#contrast", "1.3")
        _saved(a)
        _set(b, "#saturation", "0.4")
        b.wait_for_selector("#conflictBanner:not([hidden])", timeout=15000)
        b.screenshot(path=str(_shots(tmp_path) / "p1-konflik-hp.png"), full_page=True)
        assert _recipe(st)["recipe"]["look"]["saturation"] == 1.0                     # tidak tertimpa diam-diam
        b.click("#conflictLoadBtn")
        b.wait_for_function("() => document.querySelector('#contrast').value === '1.3'", timeout=10000)
        assert b.is_hidden("#conflictBanner") and b.eval_on_selector("#saturation", "e => e.value") == "1"

        # B mengubah lagi (kini di atas versi terbaru) -> tersimpan; A yang diam ikut diperbarui.
        _set(b, "#saturation", "0.6")
        _saved(b)
        assert _recipe(st)["recipe"]["look"] == {"color": "warm", "brightness": 0.1, "contrast": 1.3, "saturation": 0.6}
        a.evaluate("checkRemoteRecipe()")
        a.wait_for_function("() => document.querySelector('#saturation').value === '0.6'", timeout=10000)

        # "Simpan perubahan saya": konflik diselesaikan dengan menimpa secara sadar, kolom lain tetap.
        _set(b, "#subColor", "#00ff00")
        _saved(b)
        _set(a, "#subSize", "1.4")
        a.wait_for_selector("#conflictBanner:not([hidden])", timeout=15000)
        a.click("#conflictKeepBtn")
        _saved(a)
        final = _recipe(st)["recipe"]
        assert final["subtitles"]["size"] == 1.4 and final["subtitles"]["color"] == "#FFFFFF"   # versi A dipakai
        ctx_b.close()
        ctx_a.close()
        browser.close()


def test_render_matches_preview(editor_stack, tmp_path):
    """Satu resep -> pratinjau di browser & MP4 hasil render; dibandingkan pada detik yang sama."""
    st = editor_stack
    shots = _shots(tmp_path)
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx, page = _open(browser, st, viewport=(1280, 1000))
        page.click('#aspectSeg button[data-v="9:16"]')
        page.click("#frameModeSeg button[data-v=manual]")
        _set(page, "#cropX", "0.3")
        _set(page, "#colorSelect", "none")
        _set(page, "#brightness", "0.05")
        _set(page, "#contrast", "1.2")
        _set(page, "#saturation", "0.6")
        _set(page, "#speedSelect", "1")
        _set(page, "#zoomCheck", False)
        _set(page, "#fadeCheck", False)
        _set(page, "#animEnabled", False)
        _set(page, "#subEnabled", True)
        _set(page, "#subStyle", "simple")
        _set(page, "#subPosition", "bottom")
        _set(page, "#subColor", "#ffffff")
        _set(page, "#subSize", "1.2")
        _set(page, "#startInput", "1")
        _set(page, "#endInput", "5")
        _saved(page)
        version = _recipe(st)["version"]

        page.click("#renderBtn")
        _wait(lambda: st.api.get(f"/api/projects/{st.pid}").json()["renders"], 120, "render selesai", st, 1)
        item = st.api.get(f"/api/projects/{st.pid}").json()["renders"][0]
        mp4 = tmp_path / "hasil.mp4"
        mp4.write_bytes(st.api.get(f"/api/projects/{st.pid}/renders/{item['file']}").content)
        assert _recipe(st)["version"] == version          # render tidak mengubah resep

        # Pratinjau pada detik 3.0 sumber (= detik 2.0 hasil render). Bingkai & tombol geser disembunyikan.
        page.add_style_tag(content="#cropFrame{border:0!important} #cropHandle{display:none!important}")
        page.mouse.move(5, 5)                                     # kontrol video bawaan Chrome menutupi bagian bawah
        page.evaluate("""() => new Promise((ok) => { const v = document.querySelector('#player'); v.pause(); v.controls = false;
            v.addEventListener('seeked', () => setTimeout(ok, 300), {once: true}); v.currentTime = 3.0; })""")
        page.evaluate("updateCaptionPreview()")
        frame_el = page.locator("#cropFrame")
        box = frame_el.bounding_box()
        w, h = int(box["width"]), int(box["height"])
        shot = frame_el.screenshot()
        (shots / "p1-pratinjau-subtitle.png").write_bytes(shot)
        with_caption = _png(shot)
        page.add_style_tag(content="#captionPreview{visibility:hidden!important}")
        no_caption = _png(frame_el.screenshot())
        (shots / "p1-pratinjau.png").write_bytes(frame_el.screenshot())

        def render_frame(path, t):
            vf = f"scale={w}:{h}:flags=area,format=rgb24"
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ss", str(t), "-frames:v", "1",
                                  "-vf", vf, "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
            return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(np.float32)

        rendered = render_frame(mp4, 2.0)
        Image.fromarray(rendered.astype(np.uint8)).save(shots / "p1-render.png")
        hh, ww = with_caption.shape[:2]
        rendered = np.asarray(Image.fromarray(rendered.astype(np.uint8)).resize((ww, hh))).astype(np.float32)

        # Pita subtitle: baris yang paling berbeda antara (render vs pratinjau tanpa caption) dan
        # (pratinjau dengan vs tanpa caption) harus berada di tempat yang sama.
        def band(diff):
            rows = diff.mean(axis=(1, 2))
            hot = np.where(rows > max(8.0, rows.max() * 0.35))[0]
            return (hot.min() / len(rows), hot.max() / len(rows)) if len(hot) else (None, None)

        prev_band = band(np.abs(with_caption - no_caption))
        rend_band = band(np.abs(rendered - no_caption))
        assert prev_band[0] is not None and rend_band[0] is not None, (prev_band, rend_band)
        overlap = min(prev_band[1], rend_band[1]) - max(prev_band[0], rend_band[0])
        assert prev_band[0] > 0.55 and rend_band[0] > 0.55, (prev_band, rend_band)        # posisi "bawah"
        assert overlap >= 0.5 * (prev_band[1] - prev_band[0]), (prev_band, rend_band)     # pita yang sama

        # Warna & crop di luar pita subtitle: mirip; crop yang salah (x=0.9) jelas berbeda.
        cut = int(hh * min(prev_band[0], rend_band[0])) - 4
        err = np.abs(_grid(rendered[:cut]) - _grid(no_caption[:cut])).mean()
        wrong = tmp_path / "salah.png"
        _ffmpeg("-ss", "3", "-i", str(st.source), "-frames:v", "1", "-vf",       # rumus crop_box untuk x=0.9
                "crop=404:720:788:0,scale=1080:1920,eq=brightness=0.05:contrast=1.2:saturation=0.6", str(wrong))
        wrong_img = np.asarray(Image.open(wrong).convert("RGB").resize((ww, hh))).astype(np.float32)
        wrong_err = np.abs(_grid(wrong_img[:cut]) - _grid(no_caption[:cut])).mean()
        print(f"\nPARITAS render vs pratinjau: galat rata-rata {err:.1f}/255 (crop salah: {wrong_err:.1f}); "
              f"pita subtitle pratinjau {prev_band}, render {rend_band}")
        assert err < 14, err
        assert wrong_err > err * 2, (err, wrong_err)
        ctx.close()
        browser.close()


def test_template_save_apply_update_delete_in_ui(editor_stack):
    st = editor_stack
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx, page = _open(browser, st)
        _set(page, "#subStyle", "pop")
        _set(page, "#subColor", "#ff00ff")
        _set(page, "#colorSelect", "vivid")
        page.click("#tplSaveBtn")                                   # prompt nama -> "Preset uji UI"
        page.wait_for_function("() => [...document.querySelectorAll('#templateSelect option')]"
                               ".some(o => o.textContent === 'Preset uji UI' && o.selected)", timeout=10000)
        mine = next(t for t in st.api.get("/api/templates").json() if t["name"] == "Preset uji UI")
        assert mine["scope"] == "personal" and mine["data"]["subtitles"]["color"] == "#FF00FF"
        assert mine["data"]["look"]["color"] == "vivid"
        _saved(page)
        assert _recipe(st)["recipe"]["template_id"] == mine["id"]
        assert page.is_visible("#tplUpdateBtn") and page.is_visible("#tplDeleteBtn")

        _set(page, "#subColor", "#00ffff")
        page.click("#tplUpdateBtn")
        _wait(lambda: next(t for t in st.api.get("/api/templates").json() if t["id"] == mine["id"]
                           )["data"]["subtitles"]["color"] == "#00FFFF", 10, "template diperbarui", st)

        _set(page, "#templateSelect", "builtin:karaoke")             # template bawaan: tidak bisa diubah/dihapus
        assert page.eval_on_selector("#subStyle", "e => e.value") == "karaoke"
        assert page.is_hidden("#tplUpdateBtn") and page.is_hidden("#tplDeleteBtn")
        _set(page, "#templateSelect", mine["id"])
        assert page.eval_on_selector("#subColor", "e => e.value") == "#00ffff"
        assert page.eval_on_selector("#subStyle", "e => e.value") == "pop"
        page.click("#tplDeleteBtn")                                 # confirm -> diterima
        _wait(lambda: all(t["id"] != mine["id"] for t in st.api.get("/api/templates").json()), 10, "terhapus", st)
        _saved(page)
        assert _recipe(st)["recipe"]["template_id"] is None
        ctx.close()
        browser.close()


CASES = [("none", 0, 1, 1), ("none", 0.1, 1, 1), ("none", -0.1, 1, 1), ("none", -0.1, 1.3, 1), ("none", 0.05, 1.2, 0.6),
         ("none", 0, 1, 0.5), ("vivid", 0, 1, 1),
         ("bw", 0, 1, 1), ("warm", 0, 1, 1), ("cool", 0, 1, 1), ("cinematic", 0, 1, 1), ("vintage", 0, 1, 1)]


def test_color_preset_parity_css_vs_ffmpeg(editor_stack, tmp_path):
    """lookCss() di app.js (yang dipakai pratinjau) vs filter FFmpeg render (COLOR_PRESETS + eq), per petak warna."""
    from app.render import COLOR_PRESETS
    st = editor_stack
    chart = tmp_path / "chart.png"
    rng = np.random.default_rng(7)
    colors = np.concatenate([rng.integers(20, 236, (40, 3)), np.array([[i * 28, i * 28, i * 28] for i in range(1, 9)])])
    img = np.zeros((6 * 32, 8 * 32, 3), np.uint8)
    for k, c in enumerate(colors):
        r, q = divmod(k, 8)
        img[r * 32:(r + 1) * 32, q * 32:(q + 1) * 32] = c
    Image.fromarray(img).save(chart)
    data_url = "data:image/png;base64," + base64.b64encode(chart.read_bytes()).decode()

    def patches(arr):
        return np.array([arr[r * 32 + 8:(r + 1) * 32 - 8, q * 32 + 8:(q + 1) * 32 - 8].reshape(-1, 3).mean(0)
                         for r in range(6) for q in range(8)])

    results = []
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx, page = _open(browser, st)
        page.evaluate("""(src) => { const i = document.createElement('img'); i.id = 'chartImg'; i.src = src;
            i.style.cssText = 'position:fixed;left:0;top:0;width:256px;height:192px;z-index:99999;image-rendering:pixelated';
            document.body.appendChild(i); }""", data_url)
        page.wait_for_function("() => document.querySelector('#chartImg').complete")
        for color, b, c, s in CASES:
            _set(page, "#colorSelect", color)
            _set(page, "#brightness", str(b))
            _set(page, "#contrast", str(c))
            _set(page, "#saturation", str(s))
            css = page.evaluate("lookCss()")
            page.evaluate("(f) => { document.querySelector('#chartImg').style.filter = f; }", css)
            preview = patches(_png(page.locator("#chartImg").screenshot()))
            vf = list(COLOR_PRESETS[color])
            if b or c != 1 or s != 1:
                vf.append(f"eq=brightness={b:.3f}:contrast={c:.3f}:saturation={s:.3f}")
            out = tmp_path / f"ff-{color}-{b}-{c}-{s}.png"
            _ffmpeg("-i", str(chart), "-vf", ",".join(vf + ["format=yuv420p"]), "-frames:v", "1", str(out))
            rendered = patches(np.asarray(Image.open(out).convert("RGB")).astype(np.float32))
            err = float(np.abs(preview - rendered).mean())
            results.append((color, b, c, s, round(err, 1), css))
        ctx.close()
        browser.close()
    print("\nPARITAS warna (galat rata-rata per kanal, 0-255):")
    for row in results:
        print("  ", row)
    (_shots(tmp_path) / "p1-paritas-warna.json").write_text(json.dumps(results, indent=1))
    for color, b, c, s, err, css in results:
        # eq (kecerahan/kontras/saturasi) & vivid: rumus padanan; hitam-putih: bobot luma CSS (Rec.709) berbeda dari
        # FFmpeg (BT.601); preset curves/colorbalance: perkiraan hasil penyetelan.
        limit = 8 if color in ("none", "vivid") else 12
        assert err <= limit, (color, b, c, s, err, css)
