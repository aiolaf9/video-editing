"""Kebijakan akses jaringan keluar (P5.4). Lihat docs/SECURITY_AUDIT_URL.md.

Dua kebijakan terpisah:
- URL dari pengguna (impor video dari link): hanya alamat IP publik. Diperiksa pada IP yang BENAR-BENAR dihubungi
  (guarded_getaddrinfo), sehingga redirect, URL manifest/segmen turunan, dan DNS rebinding ikut tertahan.
  Jaringan internal tepercaya (URL_IMPORT_TRUSTED_NETWORKS) hanya berlaku untuk impor yang diminta admin global.
- URL yang dikonfigurasi admin (base URL penyedia AI): http/https; host publik wajib https; http hanya untuk
  alamat lokal/privat (mis. Ollama).
"""
import ipaddress
import socket
import sys
import threading
from contextlib import contextmanager
from typing import Iterable, Iterator, Optional
from urllib.parse import urlsplit

from .config import URL_IMPORT_TRUSTED_NETWORKS

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_resolve = socket.getaddrinfo   # resolver asli; diganti di tes untuk mensimulasikan DNS
_guard = threading.local()
# Pemanggil getaddrinfo yang tidak pernah membawa URL pengguna: koneksi database dari thread unduhan (progress
# job ditulis ke DB) tidak boleh ikut diblokir, padahal host database memang alamat privat.
_PASSTHROUGH = ("psycopg.",)


class BlockedURL(ValueError):
    """URL/alamat tujuan ditolak kebijakan jaringan. Pesan aman ditampilkan ke pengguna."""


def _ip(value: str) -> IPAddress:
    return ipaddress.ip_address(value.split("%", 1)[0])   # buang zone id IPv6 (fe80::1%eth0)


def is_public_ip(ip: IPAddress) -> bool:
    """True hanya untuk alamat unicast global. Menolak loopback, privat (RFC1918, ULA), link-local (termasuk
    169.254.169.254 metadata cloud), CGNAT, multicast, reserved, unspecified, dan IPv4 yang dibungkus IPv6."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return is_public_ip(ip.ipv4_mapped)
        if ip in _NAT64:
            return is_public_ip(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip.sixtofour or ip.teredo:
            return False
    return ip.is_global and not ip.is_multicast


def parse_networks(spec: str) -> list:
    return [ipaddress.ip_network(part.strip(), strict=False) for part in spec.split(",") if part.strip()]


def _trusted(ip: IPAddress, networks: Iterable) -> bool:
    return any(ip.version == n.version and ip in n for n in networks)


def _allowed(ip: IPAddress, networks: Iterable) -> bool:
    return is_public_ip(ip) or _trusted(ip, networks)


def resolve(host: str, port: int) -> list[IPAddress]:
    try:
        infos = _resolve(host, port, 0, socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise BlockedURL(f"Nama host tidak dapat ditemukan: {host}") from exc
    return [_ip(info[4][0]) for info in infos]


def _split(url: str) -> tuple[str, str, int]:
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise BlockedURL("Link tidak valid.") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise BlockedURL("Link harus diawali http:// atau https://")
    if parts.username is not None or parts.password is not None:
        raise BlockedURL("Link tidak boleh berisi nama pengguna/kata sandi.")
    if not parts.hostname:
        raise BlockedURL("Link tidak berisi nama host.")
    return scheme, parts.hostname, port or (443 if scheme == "https" else 80)


# ------------------------------------------------------------------ URL dari pengguna (impor video)

def import_networks(trusted: bool) -> list:
    """Jaringan internal yang boleh dijangkau impor ini (hanya untuk impor yang diminta admin global)."""
    return parse_networks(URL_IMPORT_TRUSTED_NETWORKS) if trusted else []


def check_import_url(url: str, trusted: bool = False) -> None:
    """Pemeriksaan awal di API agar pengguna langsung mendapat pesan jelas. BUKAN satu-satunya pertahanan:
    setiap koneksi saat mengunduh diperiksa lagi oleh import_guard()."""
    _, host, port = _split(url)
    networks = import_networks(trusted)
    ips = resolve(host, port)
    if not ips or not all(_allowed(ip, networks) for ip in ips):
        raise BlockedURL("Link mengarah ke alamat jaringan internal/privat dan tidak diizinkan.")


def guarded_getaddrinfo(host, port, *args, **kwargs):
    """Pengganti socket.getaddrinfo. Di luar import_guard() perilakunya sama persis dengan aslinya. Di dalamnya,
    hanya alamat yang diizinkan yang dikembalikan, sehingga koneksi hanya bisa dibuka ke alamat yang lolos
    pemeriksaan (validasi & koneksi memakai hasil resolusi yang sama: aman terhadap DNS rebinding)."""
    infos = _resolve(host, port, *args, **kwargs)
    state = getattr(_guard, "state", None)
    if state is None or sys._getframe(1).f_globals.get("__name__", "").startswith(_PASSTHROUGH):
        return infos
    allowed = [info for info in infos if _ip(info[4][0]) in state["exempt"] or _allowed(_ip(info[4][0]), state["networks"])]
    if not allowed:
        state["blocked"].append(str(host))
        raise BlockedURL(f"Alamat {host} adalah jaringan internal/privat dan tidak diizinkan.")
    return allowed


def guard_active() -> bool:
    return getattr(_guard, "state", None) is not None


_installed = False
_install_lock = threading.Lock()


def install() -> None:
    global _installed
    with _install_lock:
        if not _installed:
            socket.getaddrinfo = guarded_getaddrinfo
            _installed = True


@contextmanager
def import_guard(trusted: bool = False, exempt_hosts: Iterable[str] = ()) -> Iterator[dict]:
    """Aktif untuk thread ini selama mengunduh. exempt_hosts: proxy keluar yang dikonfigurasi operator
    (proxy itulah yang wajib menyaring tujuan; lihat docs/SECURITY_AUDIT_URL.md)."""
    install()
    exempt = set()
    for host in exempt_hosts:
        exempt.update(resolve(host, 0))
    state = {"networks": import_networks(trusted), "exempt": exempt, "blocked": []}
    previous = getattr(_guard, "state", None)
    _guard.state = state
    try:
        yield state
    finally:
        _guard.state = previous


# ------------------------------------------------------------------ URL dari admin (penyedia AI)

def check_admin_url(url: str, resolve_host: bool = True) -> None:
    """base URL penyedia AI: https untuk host publik; http hanya bila host lokal/privat (Ollama, server internal)."""
    scheme, host, port = _split(url)
    if scheme == "https":
        return
    try:
        ips = [_ip(host)]
    except ValueError:
        if host.lower() == "localhost" or host.lower().endswith(".localhost"):
            return
        if not resolve_host:
            raise BlockedURL("http:// hanya untuk alamat lokal/jaringan internal; host publik wajib https://.")
        ips = resolve(host, port)
    if any(is_public_ip(ip) for ip in ips):
        raise BlockedURL("http:// hanya untuk alamat lokal/jaringan internal; host publik wajib https://.")


def proxy_host(proxy: Optional[str]) -> Optional[str]:
    return urlsplit(proxy).hostname if proxy else None
