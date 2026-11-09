"""P2: PWA — manifest, ikon, service worker, header cache, halaman offline, akun tetap dikelola admin.

Kebijakan yang diuji: service worker hanya menyimpan berkas statis dari daftar SHELL (tanpa /api, video, atau data
pribadi), dan semua respons /api/* dikirim dengan Cache-Control: no-store (juga gambar mini & video pratinjau)."""
import json
import re
import struct
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web"


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def test_manifest_is_installable(app_client):
    r = app_client.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["name"] == "AI Video Studio" and m["short_name"] and m["start_url"] == "/" and m["scope"] == "/"
    assert m["display"] == "standalone" and m["id"] == "/"
    assert re.fullmatch(r"#[0-9a-f]{6}", m["theme_color"]) and re.fullmatch(r"#[0-9a-f]{6}", m["background_color"])
    sizes = {}
    for icon in m["icons"]:
        res = app_client.get(icon["src"])
        assert res.status_code == 200 and res.headers["content-type"] == "image/png", icon
        w, h = _png_size(WEB / icon["src"].lstrip("/"))
        assert icon["sizes"] == f"{w}x{h}"
        sizes.setdefault(icon.get("purpose", "any"), set()).add(w)
    assert {192, 512} <= sizes["any"] and 512 in sizes["maskable"]          # syarat pemasangan Chrome/Android
    assert _png_size(WEB / "icons" / "apple-touch-icon.png") == (180, 180)


@pytest.mark.parametrize("page", ["/", "/login.html", "/admin.html"])
def test_pages_link_manifest_and_apple_meta(app_client, page):
    html = app_client.get(page).text
    for needle in ['rel="manifest" href="/manifest.webmanifest"', 'name="theme-color"', 'rel="apple-touch-icon"',
                   'name="apple-mobile-web-app-capable" content="yes"', 'src="/pwa.js"', "viewport-fit=cover",
                   "AI Video Studio"]:
        assert needle in html, (page, needle)


def test_service_worker_headers_and_cache_policy(app_client):
    r = app_client.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache" and r.headers["service-worker-allowed"] == "/"
    js = r.text
    shell = json.loads("[" + re.search(r"const SHELL = \[(.*?)\];", js, re.S).group(1).rstrip().rstrip(",") + "]")
    assert "/offline.html" in shell and "/" in shell
    for path in shell:                                    # hanya berkas statis yang benar-benar ada
        assert not path.startswith("/api"), path
        assert not re.search(r"\.(mp4|webm|mov|m4a|mp3|jpg|json)$", path), path
        assert app_client.get(path).status_code == 200, path
    # /api, video (Range) & permintaan non-GET tidak pernah ditangani/dicache; tidak ada cache runtime lain.
    assert 'url.pathname.startsWith("/api/")' in js and 'request.headers.has("range")' in js
    assert 'request.method !== "GET"' in js
    assert "if (!STATIC.has(url.pathname) || url.search) return;" in js
    assert js.count("c.put(") == 1 and "caches.match(\"/offline.html\")" in js


def test_api_responses_are_never_cached(client, sample_video, fake_transcript, monkeypatch):
    from app import storage, transcribe
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    for path in ["/api/auth/me", "/api/projects", "/api/templates", "/api/editor/config"]:
        assert client.get(path).headers["cache-control"] == "no-store", path
    assert client.get("/api/auth/me", headers={"Origin": "x"}).headers["cache-control"] == "no-store"
    pid = storage.create_project("p2-cache")["id"]
    (storage.project_dir(pid) / "preview.mp4").write_bytes(sample_video.read_bytes())
    (storage.project_dir(pid) / "thumb.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    storage.update_project(pid, status="siap", source="preview.mp4", duration=8.0)
    for path in [f"/api/projects/{pid}", f"/api/projects/{pid}/recipe", f"/api/projects/{pid}/thumb",
                 f"/api/projects/{pid}/preview"]:
        r = client.get(path, headers={"Range": "bytes=0-99"} if path.endswith("preview") else {})
        assert r.status_code in (200, 206), (path, r.status_code)
        assert r.headers.get("cache-control") == "no-store", (path, r.status_code, r.headers.get("cache-control"))
    storage.delete_project(pid)


def test_static_pages_are_cacheable_and_contain_no_private_data(app_client):
    """Halaman yang disimpan service worker statis: tidak berisi data akun (data diambil lewat /api setelah login)."""
    for page in ["/", "/index.html", "/login.html", "/admin.html", "/offline.html"]:
        r = app_client.get(page)
        assert r.status_code == 200 and "no-store" not in r.headers.get("cache-control", "")
        assert "@" not in re.sub(r"<script.*?</script>|@font-face|@media", "", r.text, flags=re.S).replace(
            "&#64;", "") or page == "/admin.html", page
    off = app_client.get("/offline.html").text
    assert "Tidak ada koneksi" in off and "tidak disimpan di perangkat" in off


def test_accounts_still_managed_by_admin_only(app_client, client):
    """PWA tidak menambah pendaftaran mandiri: akun hanya dibuat Admin (Superadmin)."""
    login = app_client.get("/login.html").text.lower()
    assert "daftar" not in login and "register" not in login and "sign up" not in login
    for path in ["/api/auth/register", "/api/auth/signup", "/api/register"]:
        body = {"email": "x@y.id", "password": "abcdefgh1"}  # pragma: allowlist secret (endpoint harus tidak ada)
        assert app_client.post(path, json=body).status_code in (404, 405)
    import os
    import time

    from conftest import login_client
    unit = client.post("/api/admin/units", json={"name": f"P2 unit {time.time()}"}).json()
    email = f"p2ed{time.time_ns()}@org.id"
    assert client.post("/api/admin/users", json={"email": email, "name": "ed", "password": "password-123",  # pragma: allowlist secret
                                                 "role": "editor", "unit_id": unit["id"]}).status_code == 200
    editor = login_client(email, "password-123")                       # pragma: allowlist secret
    assert editor.post("/api/admin/users", json={"email": f"x{email}", "name": "x", "password": "password-123",  # pragma: allowlist secret
                                                 "role": "editor", "unit_id": unit["id"]}).status_code == 403
    assert os.environ["ADMIN_EMAIL"]


def test_icons_script_is_reproducible(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("make_icons", REPO / "scripts" / "make_icons.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "OUT", tmp_path)
    mod.main()
    for name in ["icon-192.png", "icon-512.png", "maskable-512.png", "apple-touch-icon.png", "favicon-32.png"]:
        assert _png_size(tmp_path / name) == _png_size(WEB / "icons" / name)
