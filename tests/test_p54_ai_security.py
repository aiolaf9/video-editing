"""P5.4: API key penyedia AI tidak boleh bocor (redirect lintas host, skema, pesan error)."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import ai, netpolicy

KEY = "sk-UJI-rahasia-9f8e7d6c"


class _Recorder(BaseHTTPRequestHandler):
    """Host kedua (127.0.0.2): mencatat setiap permintaan yang sampai, termasuk header Authorization."""
    hits: list = []

    def log_message(self, *a):
        pass

    def _any(self):
        _Recorder.hits.append((self.command, self.path, self.headers.get("Authorization")))
        body = json.dumps({"data": [{"id": "curian"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _any


def _serve(host, handler):
    srv = HTTPServer((host, 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def two_hosts():
    _Recorder.hits = []
    other = _serve("127.0.0.2", _Recorder)
    target = f"http://127.0.0.2:{other.server_port}/curi"

    class Redirector(BaseHTTPRequestHandler):
        code = 302
        hits: list = []

        def log_message(self, *a):
            pass

        def _any(self):
            Redirector.hits.append((self.command, self.headers.get("Authorization")))
            if self.headers.get("Content-Length"):
                self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(Redirector.code)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = _any

    first = _serve("127.0.0.1", Redirector)
    yield f"http://127.0.0.1:{first.server_port}/v1", Redirector
    for srv in (first, other):
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_to_other_host_never_carries_authorization(two_hosts, code):
    base, redirector = two_hosts
    redirector.code = code
    with pytest.raises(ai.AIError, match="mengalihkan") as err:
        ai.list_models("custom", {"base_url": base, "api_key": KEY})
    assert redirector.hits and redirector.hits[0][1] == f"Bearer {KEY}"   # host asli memang menerima key
    assert _Recorder.hits == []                                           # host tujuan redirect tidak dihubungi
    assert KEY not in str(err.value)


def test_chat_completion_redirect_refused(two_hosts, fake_transcript):
    base, redirector = two_hosts
    redirector.code = 307
    with pytest.raises(ai.AIError, match="mengalihkan"):
        ai.generate_plan("custom", {"base_url": base, "api_key": KEY, "model": "m"}, fake_transcript, 0, 8, "")
    assert _Recorder.hits == []


def test_api_key_echoed_by_provider_is_scrubbed():
    class Echo(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"error": {"message": f"Incorrect API key provided: {self.headers['Authorization']}"}}).encode()
            self.send_response(401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = _serve("127.0.0.1", Echo)
    try:
        with pytest.raises(ai.AIError) as err:
            ai.list_models("custom", {"base_url": f"http://127.0.0.1:{srv.server_port}/v1", "api_key": KEY})
    finally:
        srv.shutdown()
        srv.server_close()
    assert "API key ditolak" in str(err.value) and KEY not in str(err.value)
    assert err.value.public and "Incorrect" not in err.value.public       # editor tidak melihat isi respons


def _fake_dns(monkeypatch, table):
    real = netpolicy._resolve

    def fake(host, port, *a, **k):
        if host in table:
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                    for ip in table[host]]
        return real(host, port, *a, **k)

    monkeypatch.setattr(netpolicy, "_resolve", fake)


@pytest.mark.parametrize("url", [
    "http://8.8.8.8/v1",                    # host publik tanpa TLS
    "http://api.penyedia.test/v1",          # nama publik (resolve ke IP publik) tanpa TLS
    "file:///etc/passwd", "ftp://127.0.0.1/", "gopher://127.0.0.1/",
    "https://user:pass@api.penyedia.test/v1",  # pragma: allowlist secret
    "http:///tanpa-host",
])
def test_admin_url_policy_rejects(monkeypatch, url):
    _fake_dns(monkeypatch, {"api.penyedia.test": ["93.184.216.34"]})
    with pytest.raises(netpolicy.BlockedURL):
        netpolicy.check_admin_url(url)
    with pytest.raises(ai.AIError, match="Base URL ditolak"):
        ai._http("GET", url, KEY)


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1", "https://8.8.8.8/v1",
    "http://localhost:11434/v1", "http://127.0.0.1:11434/v1", "http://[::1]:11434/v1",
    "http://10.1.2.3:8000/v1", "http://192.168.1.10/v1", "http://ollama.lan:11434/v1",
])
def test_admin_url_policy_allows(monkeypatch, url):
    _fake_dns(monkeypatch, {"ollama.lan": ["192.168.1.20"]})
    netpolicy.check_admin_url(url)


def test_local_ollama_still_works(fake_transcript):
    """Ollama lokal (http, tanpa key) yang dikonfigurasi admin tetap berfungsi."""
    calls = []

    class Ollama(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            calls.append(("GET", self.path, self.headers.get("Authorization")))
            self._send({"data": [{"id": "qwen2.5:3b"}]})

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(("POST", self.path, self.headers.get("Authorization")))
            reply = '{"items": [{"type": "keyword", "start": 1, "end": 2, "text": "LOKAL", "label": "", "items": []}]}'
            self._send({"choices": [{"message": {"content": reply}}]})

    srv = _serve("127.0.0.1", Ollama)
    try:
        cfg = {"base_url": f"http://localhost:{srv.server_port}/v1"}
        assert ai.list_models("ollama", cfg) == ["qwen2.5:3b"]
        items, model = ai.generate_plan("ollama", cfg, fake_transcript, 0, 8, "")
    finally:
        srv.shutdown()
        srv.server_close()
    assert model == "qwen2.5:3b" and items[0]["text"] == "LOKAL"
    assert calls[-1][:2] == ("POST", "/v1/chat/completions")
    assert all(c[2] is None for c in calls)               # tanpa key: tidak ada header Authorization


def test_settings_reject_public_http_and_drop_key_on_base_url_change(client, monkeypatch):
    _fake_dns(monkeypatch, {"api.penyedia.test": ["93.184.216.34"]})
    r = client.put("/api/settings", json={"provider": "custom", "base_url": "http://api.penyedia.test/v1"})
    assert r.status_code == 400 and "https" in r.json()["detail"]
    r = client.put("/api/settings", json={"provider": "openrouter", "api_key": KEY, "model": "m"})
    assert r.json()["providers"]["openrouter"]["has_key"]
    # Base URL diganti tanpa key baru: key lama tidak ikut terkirim ke alamat baru.
    r = client.put("/api/settings", json={"provider": "openrouter", "model": "m", "base_url": "https://lain.test/v1"})
    assert r.status_code == 200 and not r.json()["providers"]["openrouter"]["has_key"]
    assert KEY not in r.text
    client.put("/api/settings", json={"provider": "openrouter", "model": "", "base_url": ""})
    client.put("/api/settings", json={"provider": "claude"})


def test_env_key_never_sent_to_custom_base_url(monkeypatch):
    from app import main

    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    assert main._provider_cfg({"providers": {}}, "openai")["api_key"] == KEY
    cfg = main._provider_cfg({"providers": {"openai": {"base_url": "https://lain.test/v1"}}}, "openai")
    assert not cfg.get("api_key")
