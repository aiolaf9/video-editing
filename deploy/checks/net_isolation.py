"""Uji isolasi jaringan dari DALAM container worker-io (staging, P5.5). Memakai soket mentah, bukan kode aplikasi,
sehingga yang diuji adalah lapisan jaringan (docs/EGRESS_DESIGN.md bagian 3), bukan guard Python.

    docker compose -f docker-compose.yml -f deploy/compose.staging.yml exec -T worker-io python - < deploy/checks/net_isolation.py
"""
import json
import os
import socket
import sys
from urllib.parse import urlsplit

PROXY = urlsplit(os.environ.get("DOWNLOAD_PROXY") or "http://egress-proxy:3128")
results = []


def tcp(host, port, timeout=5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "terhubung"
    except OSError as exc:
        return f"gagal: {type(exc).__name__}"


def via_proxy(request_line, timeout=20):
    """Kirim satu permintaan mentah ke proxy, kembalikan kode status HTTP (atau error)."""
    try:
        with socket.create_connection((PROXY.hostname, PROXY.port), timeout=timeout) as s:
            s.sendall(request_line.encode() + b"\r\n\r\n")
            head = s.recv(256).decode(errors="replace")
            return head.split(" ", 2)[1] if head.startswith("HTTP/") else f"aneh: {head[:40]!r}"
    except OSError as exc:
        return f"gagal: {type(exc).__name__}"


def expect(name, value, ok):
    results.append({"uji": name, "hasil": value, "sesuai": bool(ok)})


# Lapisan jaringan: hanya database & proxy yang terjangkau.
expect("db:5432 (antrean, wajib bisa)", r := tcp("db", 5432), r == "terhubung")
expect("egress-proxy:3128 (wajib bisa)", r := tcp(PROXY.hostname, PROXY.port), r == "terhubung")
for host, port in [("web", 8000), ("caddy", 443), ("worker-render", 8000), ("example.com", 443), ("1.1.1.1", 443),
                   ("169.254.169.254", 80)]:
    expect(f"{host}:{port} langsung (wajib gagal)", r := tcp(host, port), r != "terhubung")

# Lewat proxy: internet publik boleh, alamat internal/metadata/port lain ditolak proxy.
expect("proxy -> http://example.com/ (wajib 200/3xx)", r := via_proxy("GET http://example.com/ HTTP/1.1\r\nHost: example.com"),
       r[:1] in ("2", "3"))
expect("proxy CONNECT example.com:443 (wajib 200)", r := via_proxy("CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443"),
       r == "200")
for target in ["http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/", "http://127.0.0.1:3128/",
               "http://[::1]/", "http://2130706433/", "http://db:5432/", "http://example.com:22/"]:
    host = urlsplit(target).netloc
    expect(f"proxy -> {target} (wajib ditolak)", r := via_proxy(f"GET {target} HTTP/1.1\r\nHost: {host}"),
           r[:1] not in ("2", "3"))
expect("proxy CONNECT 169.254.169.254:443 (wajib ditolak)",
       r := via_proxy("CONNECT 169.254.169.254:443 HTTP/1.1\r\nHost: 169.254.169.254:443"), r != "200")

# CONNECT hanya ke 443; port/layanan lain ditolak.
for target in ["example.com:22", "example.com:80", "example.com:8443", "db:5432", "127.0.0.1:443", "[::1]:443"]:
    expect(f"proxy CONNECT {target} (wajib ditolak)", r := via_proxy(f"CONNECT {target} HTTP/1.1\r\nHost: {target}"),
           r != "200")

# IPv6 & bentuk alamat lain menuju jaringan internal.
for target in ["http://[::ffff:169.254.169.254]/", "http://[::ffff:127.0.0.1]/", "http://[fd00::1]/", "http://[fe80::1]/",
               "http://0x7f000001/", "http://0177.0.0.1/", "http://0/"]:
    host = urlsplit(target).netloc
    expect(f"proxy -> {target} (wajib ditolak)", r := via_proxy(f"GET {target} HTTP/1.1\r\nHost: {host}"),
           r[:1] not in ("2", "3"))

# Nama DNS publik yang resolve ke IP privat/metadata: squid harus memeriksa IP HASIL RESOLUSI. Wajib tepat 403
# (ACL); 503 berarti DNS gagal sehingga uji tidak membuktikan apa pun.
for target in ["http://169.254.169.254.nip.io/latest/meta-data/", "http://10.0.0.1.nip.io/", "http://127.0.0.1.nip.io:3128/",
               "http://localtest.me/"]:
    host = urlsplit(target).netloc
    expect(f"proxy -> {target} (resolve ke IP internal, wajib 403)",
           r := via_proxy(f"GET {target} HTTP/1.1\r\nHost: {host}"), r == "403")
expect("proxy CONNECT 169.254.169.254.nip.io:443 (wajib 403)",
       r := via_proxy("CONNECT 169.254.169.254.nip.io:443 HTTP/1.1\r\nHost: 169.254.169.254.nip.io:443"), r == "403")

print(json.dumps(results, indent=1, ensure_ascii=False))
bad = [r for r in results if not r["sesuai"]]
print(f"{len(results) - len(bad)}/{len(results)} sesuai")
sys.exit(1 if bad else 0)
