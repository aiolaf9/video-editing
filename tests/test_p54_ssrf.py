"""P5.4: impor video dari link tidak boleh menjangkau jaringan internal (SSRF).

Server "publik" uji berjalan di 127.0.0.2 dan diperlakukan sebagai IP publik lewat fixture `public_127_0_0_2`;
server "internal" di 127.0.0.1 mencatat setiap permintaan yang sampai (harus tetap nol)."""
import functools
import http.server
import ipaddress
import re
import socket
import threading

import pytest

from app import netpolicy, pipeline, storage
from conftest import _ffmpeg, login_client


class _Job:
    def update(self, progress=None, message=None):
        pass


# ------------------------------------------------------------------ klasifikasi alamat

@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.255.255.254", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
    "0.0.0.0", "224.0.0.1", "255.255.255.255", "198.18.0.1", "192.0.2.1",
    "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",   # IPv4 dibungkus IPv6
    "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe",                           # NAT64 menuju loopback/metadata
    "2002:7f00:1::1", "2001:0::1",                                      # 6to4 & Teredo
])
def test_internal_addresses_are_not_public(ip):
    assert not netpolicy.is_public_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111", "64:ff9b::808:808"])
def test_public_addresses(ip):
    assert netpolicy.is_public_ip(ipaddress.ip_address(ip))


def _dns(monkeypatch, table):
    """table: host -> daftar IP, atau fungsi tanpa argumen yang mengembalikan daftar IP (untuk DNS rebinding)."""
    real = netpolicy._resolve

    def fake(host, port, *a, **k):
        if host in table:
            ips = table[host]() if callable(table[host]) else table[host]
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                    for ip in ips]
        return real(host, port, *a, **k)

    monkeypatch.setattr(netpolicy, "_resolve", fake)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/v.mp4", "http://localhost/v.mp4", "http://[::1]/v.mp4", "http://[::ffff:127.0.0.1]/v.mp4",
    "http://2130706433/v.mp4", "http://0x7f.1/v.mp4", "http://017700000001/v.mp4",   # bentuk IPv4 tidak lazim
    "http://169.254.169.254/latest/meta-data/", "http://metadata.google.internal/computeMetadata/v1/",
    "http://10.0.0.5/v.mp4", "http://jahat.test/v.mp4", "http://ganda.test/v.mp4",
    "http://user:pw@example.com/v.mp4",  # pragma: allowlist secret
    "ftp://example.com/v.mp4", "file:///etc/passwd", "javascript:alert(1)", "http:///v.mp4",
])
def test_check_import_url_rejects(monkeypatch, url):
    _dns(monkeypatch, {"metadata.google.internal": ["169.254.169.254"],
                       "jahat.test": ["10.0.0.5"],                       # nama "publik" yang resolve ke IP privat
                       "ganda.test": ["93.184.216.34", "127.0.0.1"]})   # salah satu IP internal = ditolak
    with pytest.raises(netpolicy.BlockedURL):
        netpolicy.check_import_url(url)


def test_trusted_networks_only_for_trusted_imports(monkeypatch):
    monkeypatch.setattr(netpolicy, "URL_IMPORT_TRUSTED_NETWORKS", "10.20.0.0/16")
    netpolicy.check_import_url("http://10.20.1.2/v.mp4", trusted=True)
    with pytest.raises(netpolicy.BlockedURL):
        netpolicy.check_import_url("http://10.20.1.2/v.mp4", trusted=False)
    with pytest.raises(netpolicy.BlockedURL):
        netpolicy.check_import_url("http://10.99.1.2/v.mp4", trusted=True)   # di luar jaringan tepercaya


def test_guard_is_scoped_to_download_thread_and_lets_database_resolve():
    socket.getaddrinfo("127.0.0.1", 80)                  # di luar guard: perilaku normal
    with netpolicy.import_guard():
        with pytest.raises(netpolicy.BlockedURL):
            socket.getaddrinfo("127.0.0.1", 80)
        # Koneksi database (psycopg me-resolve host sendiri) dari thread yang sama tetap boleh.
        db_globals = {"__name__": "psycopg._conninfo_attempts", "socket": socket}
        exec("result = socket.getaddrinfo('127.0.0.1', 5432)", db_globals)
        assert db_globals["result"]
        other = {"__name__": "urllib3.util.connection", "socket": socket}
        with pytest.raises(netpolicy.BlockedURL):
            exec("socket.getaddrinfo('127.0.0.1', 80)", other)
        # Thread lain (mis. request web lain) tidak terpengaruh.
        seen = []
        t = threading.Thread(target=lambda: seen.append(socket.getaddrinfo("127.0.0.1", 80)))
        t.start()
        t.join()
        assert seen and seen[0]
    socket.getaddrinfo("127.0.0.1", 80)


# ------------------------------------------------------------------ API

@pytest.fixture(scope="module")
def editor(client):
    import time

    unit = client.post("/api/admin/units", json={"name": f"SSRF {time.time()}"}).json()
    email = f"ssrf{str(time.time()).replace('.', '')}@org.id"
    r = client.post("/api/admin/users", json={"email": email, "name": "e", "password": "password-123",  # pragma: allowlist secret
                                              "role": "editor", "unit_id": unit["id"]})
    assert r.status_code == 200, r.text
    return login_client(email, "password-123")


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/v.mp4", "http://[::1]/v.mp4", "http://169.254.169.254/",
                                 "http://192.168.0.10/v.mp4", "http://contoh.local@127.0.0.1/v.mp4"])
def test_api_rejects_internal_urls(editor, client, monkeypatch, url):
    monkeypatch.setattr(netpolicy, "URL_IMPORT_TRUSTED_NETWORKS", "127.0.0.0/8,192.168.0.0/16")
    before = len(editor.get("/api/projects").json())
    r = editor.post("/api/projects/from-url", json={"url": url})
    assert r.status_code == 400, r.text                  # editor: jaringan tepercaya admin tidak berlaku
    assert len(editor.get("/api/projects").json()) == before


# ------------------------------------------------------------------ unduhan nyata (yt-dlp)

class _Internal(http.server.SimpleHTTPRequestHandler):
    hits: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        _Internal.hits.append(self.path)
        super().do_GET()


class _Public(http.server.SimpleHTTPRequestHandler):
    internal = ""
    chain_hits: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        m = re.match(r"^/chain/(\d+)$", self.path)
        if self.path.startswith("/redir/"):
            return self._redirect(f"{_Public.internal}/{self.path.split('/', 2)[2]}")
        if m:
            _Public.chain_hits.append(self.path)
            return self._redirect(f"/chain/{int(m.group(1)) + 1}")
        super().do_GET()

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture(scope="module")
def servers(tmp_path_factory, sample_video):
    root = tmp_path_factory.mktemp("ssrf")
    _ffmpeg("-i", str(sample_video), "-t", "3", "-c", "copy", str(root / "single.mp4"))
    _ffmpeg("-i", str(root / "single.mp4"), "-map", "0:v", "-map", "0:a", "-c", "copy", "-f", "dash",
            "-seg_duration", "1", "-adaptation_sets", "id=0,streams=v id=1,streams=a", str(root / "manifest.mpd"))
    internal = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Internal, directory=str(root)))
    public = http.server.ThreadingHTTPServer(("127.0.0.2", 0), functools.partial(_Public, directory=str(root)))
    for srv in (internal, public):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    internal_url = f"http://127.0.0.1:{internal.server_address[1]}"
    _Public.internal = internal_url
    # Manifest DASH di server publik yang segmennya menunjuk ke server internal (URL turunan).
    mpd = (root / "manifest.mpd").read_text()
    (root / "evil.mpd").write_text(mpd.replace("<Period", f"<BaseURL>{internal_url}/</BaseURL>\n\t<Period", 1))
    try:
        yield {"public": f"http://127.0.0.2:{public.server_address[1]}", "internal": internal_url,
               "public_port": public.server_address[1]}
    finally:
        for srv in (internal, public):
            srv.shutdown()
            srv.server_close()


@pytest.fixture
def public_127_0_0_2(monkeypatch):
    real = netpolicy.is_public_ip
    monkeypatch.setattr(netpolicy, "is_public_ip", lambda ip: ip == ipaddress.ip_address("127.0.0.2") or real(ip))
    _Internal.hits = []


def _download(url, trusted=False):
    pid = storage.create_project("uji ssrf")["id"]
    try:
        return pid, pipeline.download(_Job(), pid, url, trusted=trusted), None
    except RuntimeError as exc:
        return pid, None, exc


def test_public_import_still_works(client, servers, public_127_0_0_2):
    for name in ("single.mp4", "manifest.mpd"):
        pid, source, err = _download(f"{servers['public']}/{name}")
        assert err is None and source and (storage.project_dir(pid) / source).stat().st_size > 0
    assert _Internal.hits == []


@pytest.mark.parametrize("case", ["direct", "redirect", "derived_manifest", "dns_rebinding", "public_name_private_ip"])
def test_download_never_reaches_internal_server(client, servers, public_127_0_0_2, monkeypatch, case):
    calls = {"n": 0}

    def rebinding():          # pemeriksaan pertama melihat IP publik, koneksi sesudahnya melihat IP internal
        calls["n"] += 1
        return ["127.0.0.2"] if calls["n"] == 1 else ["127.0.0.1"]

    _dns(monkeypatch, {"rebind.test": rebinding, "publik-palsu.test": ["127.0.0.1"]})
    port = servers["internal"].rsplit(":", 1)[1]
    url = {"direct": f"{servers['internal']}/single.mp4",
           "redirect": f"{servers['public']}/redir/single.mp4",
           "derived_manifest": f"{servers['public']}/evil.mpd",
           "dns_rebinding": f"http://rebind.test:{port}/single.mp4",
           "public_name_private_ip": f"http://publik-palsu.test:{port}/single.mp4"}[case]
    pid, source, err = _download(url)
    assert source is None and err is not None
    assert "internal" in str(err) and "127.0.0.1" not in str(err)   # alamat internal tidak diungkap
    assert _Internal.hits == []                                    # server internal tidak pernah dihubungi
    assert not list(storage.project_dir(pid).glob("source.*"))
    assert storage.load_project(pid)["status"] == "gagal"
    if case == "dns_rebinding":
        assert calls["n"] >= 2


def test_redirect_limit(client, servers, public_127_0_0_2):
    _Public.chain_hits = []
    pid, source, err = _download(f"{servers['public']}/chain/0")
    assert source is None and err is not None
    # Tanpa batas, requests mengikuti hingga 30 redirect per permintaan (yt-dlp bisa mencoba ulang beberapa kali).
    assert 0 < len(_Public.chain_hits) <= (pipeline.URL_IMPORT_MAX_REDIRECTS + 1) * 6, len(_Public.chain_hits)


def test_size_limit_without_content_length(client, servers, public_127_0_0_2, monkeypatch):
    monkeypatch.setattr(pipeline, "URL_IMPORT_MAX_MB", 0)    # batas 0 MB: berlaku juga untuk fragmen DASH
    for name in ("single.mp4", "manifest.mpd"):
        pid, source, err = _download(f"{servers['public']}/{name}")
        assert source is None and "batas" in str(err), err
        assert not list(storage.project_dir(pid).glob("source.*"))


def test_external_downloaders_are_refused():
    import yt_dlp

    from yt_dlp.downloader.external import FFmpegFD

    ydl = pipeline._safe_ydl(yt_dlp)({"quiet": True, "external_downloader": {"default": "native"}})
    with netpolicy.import_guard():
        with pytest.raises(yt_dlp.utils.DownloadError, match="eksternal"):
            ydl.dl("x.flv", {"url": "rtmp://127.0.0.1/app/x", "protocol": "rtmp", "ext": "flv"})
        # Delegasi diam-diam ke FFmpeg (mis. dari pengunduh HLS native) juga ditahan.
        with pytest.raises(yt_dlp.utils.DownloadError, match="eksternal"):
            FFmpegFD(ydl, ydl.params).real_download("x.mp4", {"url": "http://127.0.0.1/x.m3u8", "protocol": "m3u8"})


def test_admin_trusted_network_import(client, servers, trust_loopback, fake_transcript, monkeypatch):
    import time

    from app import transcribe

    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    r = client.post("/api/projects/from-url", json={"url": f"{servers['internal']}/single.mp4"})
    assert r.status_code == 200, r.text                  # admin global + jaringan tepercaya: diizinkan
    deadline = time.time() + 60
    while (job := client.get(f"/api/jobs/{r.json()['job']['id']}").json())["status"] in ("antre", "jalan"):
        assert time.time() < deadline
        time.sleep(0.3)
    assert job["status"] == "selesai", job
