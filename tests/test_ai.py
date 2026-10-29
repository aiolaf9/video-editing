import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import ai


class FakeOpenAI(BaseHTTPRequestHandler):
    """Server tiruan berformat OpenAI-compatible (seperti Gemini/ChatGPT/Ollama)."""
    calls: list = []
    reject_json_mode = False

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        FakeOpenAI.calls.append(("GET", self.path, self.headers.get("Authorization"), None))
        self._send(200, {"data": [{"id": "models/gemini-9-flash"}, {"id": "models/gemini-9-pro"},
                                  {"id": "models/text-embedding-9"}, {"id": "models/gemini-9-flash-lite"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenAI.calls.append(("POST", self.path, self.headers.get("Authorization"), body))
        if FakeOpenAI.reject_json_mode and "response_format" in body:
            return self._send(400, {"error": {"message": "response_format json_object is not supported"}})
        reply = '```json\n{"items": [{"type": "keyword", "start": 1, "end": 2.5, "text": "VIRAL", "label": "", "items": []}]}\n```'
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": reply}}]})


@pytest.fixture
def fake_server():
    FakeOpenAI.calls = []
    FakeOpenAI.reject_json_mode = False
    srv = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_openai_compatible_with_auto_model_pick(fake_server, fake_transcript, monkeypatch):
    monkeypatch.setitem(ai.PROVIDERS["gemini"], "base_url", fake_server)
    items, model = ai.generate_plan("gemini", {"api_key": "AIza-test"}, fake_transcript, 0, 8, "Santai")
    assert model == "gemini-9-flash"  # flash dipilih, lite/embedding dilewati
    assert items[0]["text"] == "VIRAL"
    method, path, auth, body = FakeOpenAI.calls[-1]
    assert (method, path, auth) == ("POST", "/v1/chat/completions", "Bearer AIza-test")
    assert body["model"] == "gemini-9-flash" and body["response_format"] == {"type": "json_object"}
    assert "Santai" in body["messages"][1]["content"] and "Kenapa" in body["messages"][1]["content"]


def test_retries_without_json_mode_and_custom_url(fake_server, fake_transcript):
    FakeOpenAI.reject_json_mode = True
    items, model = ai.generate_plan("custom", {"base_url": fake_server, "model": "qwen2.5:3b"}, fake_transcript, 0, 8, "")
    assert model == "qwen2.5:3b" and items[0]["type"] == "keyword"
    posts = [c for c in FakeOpenAI.calls if c[0] == "POST"]
    assert len(posts) == 2 and "response_format" not in posts[1][3] and posts[1][2] is None


def test_clear_errors(fake_transcript):
    with pytest.raises(ai.AIError, match="API key Gemini"):
        ai.generate_plan("gemini", {}, fake_transcript, 0, 8, "")
    with pytest.raises(ai.AIError, match="Base URL"):
        ai.generate_plan("custom", {"model": "x"}, fake_transcript, 0, 8, "")
    with pytest.raises(ai.AIError, match="Tidak bisa terhubung"):
        ai.generate_plan("custom", {"model": "x", "base_url": "http://127.0.0.1:9/v1"}, fake_transcript, 0, 8, "")


def test_settings_api_per_provider(client):
    client.put("/api/settings", json={"provider": "gemini", "api_key": "AIza-abcd9999", "model": ""})
    client.put("/api/settings", json={"provider": "ollama", "base_url": "http://localhost:11434/v1", "model": "qwen2.5:3b"})
    s = client.get("/api/settings").json()
    assert s["provider"] == "ollama"
    assert s["providers"]["gemini"]["key_hint"] == "...9999"
    assert s["providers"]["ollama"]["model"] == "qwen2.5:3b"
    assert "AIza-abcd9999" not in json.dumps(s)
    assert client.put("/api/settings", json={"provider": "nope"}).status_code == 400
    client.put("/api/settings", json={"provider": "claude"})
