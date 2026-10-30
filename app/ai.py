"""Penghubung ke penyedia AI pilihan pengguna untuk membuat rencana animasi.

- "claude"  : Anthropic Claude lewat SDK resmi (structured output, JSON dijamin valid)
- lainnya   : API "OpenAI-compatible" yang didukung hampir semua penyedia:
              Gemini, ChatGPT/OpenAI, OpenRouter, Groq, DeepSeek, Ollama (lokal), atau URL sendiri.
"""
import json
import ssl
import urllib.error
import urllib.request
from typing import Optional

from . import netpolicy, plan

PROVIDERS = {
    "claude": {"name": "Claude", "base_url": "", "needs_key": True,
               "key_url": "https://console.anthropic.com/settings/keys", "default_model": "claude-opus-5-5"},
    "gemini": {"name": "Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "needs_key": True, "key_url": "https://aistudio.google.com/apikey", "prefer": ["flash", "pro"]},
    "openai": {"name": "ChatGPT", "base_url": "https://api.openai.com/v1", "needs_key": True,
               "key_url": "https://platform.openai.com/api-keys", "prefer": ["mini", "gpt"]},
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "needs_key": True,
                   "key_url": "https://openrouter.ai/settings/keys", "prefer": []},
    "groq": {"name": "Groq", "base_url": "https://api.groq.com/openai/v1", "needs_key": True,
             "key_url": "https://console.groq.com/keys", "prefer": ["llama", "qwen"]},
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "needs_key": True,
                 "key_url": "https://platform.deepseek.com/api_keys", "prefer": ["chat"]},
    "ollama": {"name": "Ollama (lokal)", "base_url": "http://localhost:11434/v1", "needs_key": False,
               "key_url": "https://ollama.com/download", "prefer": []},
    "custom": {"name": "Lainnya (OpenAI-compatible)", "base_url": "", "needs_key": False, "key_url": "", "prefer": []},
}

# Model yang tidak cocok untuk tugas teks (gambar, suara, embedding, dll.).
_SKIP = ("embed", "image", "imagen", "tts", "audio", "vision", "whisper", "dall", "moderation", "realtime",
         "transcribe", "veo", "live", "guard", "search", "aqa")


class AIError(RuntimeError):
    """public: versi pesan untuk editor (tanpa isi respons penyedia/alamat internal); None = pesan lengkap aman."""

    def __init__(self, message: str, public: Optional[str] = None):
        super().__init__(message)
        self.public = public


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """Permintaan ke penyedia AI membawa API key: redirect tidak pernah diikuti (urllib bawaan meneruskan header
    Authorization ke host tujuan redirect, termasuk host lain)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AIError(f"Penyedia AI mengalihkan permintaan (HTTP {code}). Pengalihan tidak diikuti agar API key tidak "
                      "terkirim ke alamat lain; periksa Base URL.", public="Penyedia AI mengalihkan permintaan; "
                      "hubungi admin untuk memeriksa Base URL.")


def _opener() -> urllib.request.OpenerDirector:
    # Hanya http/https (tanpa file:, ftp:, data:), proxy dari lingkungan tetap dihormati, redirect ditolak.
    op = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler(), urllib.request.HTTPHandler(),
                    urllib.request.HTTPSHandler(context=ssl.create_default_context()),
                    urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPErrorProcessor(), _RefuseRedirect()):
        op.add_handler(handler)
    return op


def _scrub(text: str, key: Optional[str]) -> str:
    """Penyedia kadang menyalin API key ke pesan error: jangan pernah meneruskannya ke log atau pengguna."""
    return text.replace(key, "***") if key and len(key) >= 4 else text


def base_url(provider: str, cfg: dict) -> str:
    url = (cfg.get("base_url") or PROVIDERS[provider]["base_url"]).rstrip("/")
    if not url:
        raise AIError("Isi Base URL untuk penyedia ini (mis. https://api.contoh.com/v1).")
    return url


def _http(method: str, url: str, key: Optional[str], body: Optional[dict] = None, timeout: int = 180) -> dict:
    try:
        netpolicy.check_admin_url(url)
    except netpolicy.BlockedURL as exc:
        raise AIError(f"Base URL ditolak: {exc}", public="Pengaturan Base URL penyedia AI tidak valid; hubungi admin.") from exc
    headers = {"Content-Type": "application/json", "User-Agent": "video-editing-ai/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        # Skema dibatasi http/https oleh check_admin_url dan oleh _opener (tanpa handler file:/ftp:).
        with _opener().open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = _scrub(exc.read().decode("utf-8", "replace"), key)
        hint, full = _friendly(exc.code, raw)
        raise AIError(full, public=hint) from None
    except urllib.error.URLError as exc:
        raise AIError(_scrub(f"Tidak bisa terhubung ke {url}: {exc.reason}", key),
                      public="Tidak bisa terhubung ke penyedia AI; hubungi admin.") from None


def _friendly(code: int, raw: str) -> tuple[str, str]:
    """Ubah error HTTP penyedia AI menjadi pesan yang mudah dipahami (detail asli tetap disertakan)."""
    try:
        err = json.loads(raw)
        err = err[0] if isinstance(err, list) else err
        msg = (err.get("error") or {}).get("message") if isinstance(err.get("error"), dict) else err.get("error")
        msg = str(msg or err.get("message") or raw)
    except (ValueError, AttributeError):
        msg = raw
    msg = " ".join(msg.split())[:300]
    low = msg.lower()
    if code in (401, 403) or "api key" in low or "api_key" in low or "unauthorized" in low:
        hint = "API key ditolak: periksa kembali key-nya (salah, kedaluwarsa, atau belum diaktifkan)."
    elif code == 429 or "quota" in low or "rate limit" in low:
        hint = "Kuota atau batas pemakaian habis. Tunggu sebentar, atau cek paket/kuota di akun penyedia."
    elif code == 404 or "not found" in low or "model" in low and "exist" in low:
        hint = "Model tidak ditemukan. Klik 'Muat daftar model' lalu pilih salah satu."
    else:
        hint = f"Error {code}."
    return hint, f"{hint} (Pesan penyedia: {msg})"


def list_models(provider: str, cfg: dict) -> list[str]:
    key = cfg.get("api_key") or None
    if provider == "claude":
        import anthropic

        client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        return [m.id for m in client.models.list()]
    data = _http("GET", f"{base_url(provider, cfg)}/models", key, timeout=30)
    ids = [str(m.get("id", "")).removeprefix("models/") for m in data.get("data", [])]
    return sorted(i for i in ids if i and not any(s in i.lower() for s in _SKIP))


def pick_model(provider: str, models: list[str]) -> Optional[str]:
    """Pilih model teks yang wajar bila pengguna belum menentukan."""
    for word in PROVIDERS[provider].get("prefer", []):
        hits = [m for m in models if word in m.lower() and "preview" not in m.lower() and "lite" not in m.lower()]
        if hits:
            return sorted(hits)[-1]  # nama versi terbesar biasanya yang terbaru
    return models[0] if models else None


def _openai_compatible(provider: str, cfg: dict, model: str, system: str, user: str) -> str:
    url = f"{base_url(provider, cfg)}/chat/completions"
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"}}
    key = cfg.get("api_key") or None
    try:
        data = _http("POST", url, key, body)
    except AIError as exc:
        # Sebagian penyedia/model tidak mendukung mode JSON: ulangi tanpa itu.
        if "response_format" not in str(exc) and "json" not in str(exc).lower():
            raise
        body.pop("response_format")
        data = _http("POST", url, key, body)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError(_scrub(f"Format jawaban tidak dikenal: {str(data)[:300]}", key),
                      public="Format jawaban penyedia AI tidak dikenal.") from exc


def generate_plan(provider: str, cfg: dict, transcript: dict, start: float, end: float,
                  guidebook: str) -> tuple[list[dict], str]:
    """Kembalikan (elemen rencana, nama model yang dipakai)."""
    if provider not in PROVIDERS:
        raise AIError(f"Penyedia AI tidak dikenal: {provider}")
    info = PROVIDERS[provider]
    if info["needs_key"] and not cfg.get("api_key"):
        raise AIError(f"API key {info['name']} belum diisi. Buat di {info['key_url']}")

    model = (cfg.get("model") or "").strip() or info.get("default_model")
    if not model:
        model = pick_model(provider, list_models(provider, cfg))
        if not model:
            raise AIError("Tidak ada model yang tersedia. Isi nama model secara manual.")

    if provider == "claude":
        return plan.ask_claude(transcript, start, end, guidebook, cfg.get("api_key"), model), model

    user = plan.user_message(transcript, start, end, guidebook) + plan.JSON_INSTRUCTION
    reply = _openai_compatible(provider, cfg, model, plan.SYSTEM_PROMPT, user)
    try:
        return plan.parse_reply(reply), model
    except ValueError as exc:
        raise AIError(f"Jawaban {info['name']} bukan JSON yang valid: {exc}") from exc
