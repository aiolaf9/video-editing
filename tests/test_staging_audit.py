"""Skrip audit VPS staging (deploy/audit/vps_audit.sh) harus HANYA MEMBACA: tidak boleh ada perintah yang memasang,
menghentikan, menyalakan, atau mengubah apa pun di server, dan tidak boleh menulis berkas."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "audit" / "vps_audit.sh"

FORBIDDEN = [
    r"\bapt(-get)?\s", r"\bdpkg\s+-(i|r|P)", r"\bsnap\s+(install|remove)", r"\bpip3?\s+install",
    r"\bsystemctl\s+(start|stop|restart|reload|enable|disable|mask|unmask|daemon-reload|kill)",
    r"\bservice\s+\S+\s+(start|stop|restart|reload)",
    r"\bdocker\s+(run|pull|push|rm|rmi|stop|start|restart|kill|exec|build|create|update|prune|load|save|login)\b",
    r"\bdocker\s+(network|volume|image|container|system|builder)\s+(create|rm|prune|connect|disconnect)",
    r"\bdocker\s+compose\s+(up|down|build|pull|restart|stop|start|rm|run|exec|create)",
    r"\biptables\s+-(A|I|D|F|X|N|R|Z|P\s)", r"\bnft\s+(add|delete|flush|insert|replace)",
    r"\bufw\s+(allow|deny|reject|limit|enable|disable|reset|delete|reload)", r"\bcsf\s+-[a-z]*[rxfadetq]",
    r"\bfirewall-cmd\s+--(add|remove|reload|permanent|set)", r"\bfail2ban-client\s+(set|stop|start|reload|unban)",
    r"(^|[\s;|&])(rm|mv|cp|chmod|chown|mkdir|touch|ln|truncate|dd|mkfs|mount|umount|useradd|usermod|passwd|"
    r"reboot|shutdown|poweroff|kill|pkill|killall)\s",
    r"\bsed\s+-i", r"\btee\b", r"\bcrontab\s+-(e|r)\b", r"\bsysctl\s+-w", r"\bswapon\s+/", r"\bswapoff\b",
    r"\bcurl\b[^\n]*\s-(X|d|F|T)\s", r"\bcurl\b[^\n]*--(data|upload-file|request)",
]


def _code_lines():
    for n, line in enumerate(SCRIPT.read_text().splitlines(), 1):
        code = line.split(" #", 1)[0] if not line.lstrip().startswith("#") else ""
        if code.strip():
            yield n, code


def test_script_has_no_mutating_commands():
    hits = [(n, pat, code.strip()) for n, code in _code_lines() for pat in FORBIDDEN if re.search(pat, code)]
    assert not hits, hits


def test_script_writes_no_files():
    """Satu-satunya pengalihan keluaran: ke /dev/null, ke stdout/stderr, atau masukan dari /dev/null."""
    bad = []
    for n, code in _code_lines():
        for m in re.finditer(r"(\d?)>>?\s*(\S+)", code):
            target = m.group(2)
            if target.startswith("&") or target == "/dev/null" or target.startswith("/dev/null"):
                continue
            if re.match(r"=|\d", target):          # perbandingan seperti -ge / 2>&1 sudah tertangani
                continue
            bad.append((n, code.strip()))
    assert not bad, bad


@pytest.mark.skipif(shutil.which("bash") is None, reason="butuh bash")
def test_script_runs_read_only_and_reports_all_sections(tmp_path):
    home, cwd = tmp_path / "home", tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "AUDIT_NET": "0", "LANG": "C"}
    out = subprocess.run(["bash", str(SCRIPT)], cwd=cwd, env=env, capture_output=True, text=True, timeout=240)
    assert out.returncode == 0, out.stderr[-2000:]
    for section in ["1. Sistem", "2. CPU", "3. RAM & swap", "4. Disk", "5. Port & layanan", "6. Docker",
                    "7. Firewall", "8. Backup", "9. Jaringan keluar", "10. Keamanan dasar", "RINGKASAN"]:
        assert f"========== {section}" in out.stdout, section
    assert "Selesai. Skrip tidak mengubah apa pun di server." in out.stdout
    assert list(cwd.iterdir()) == [] and list(home.iterdir()) == []          # tidak ada berkas yang ditulis
    for secret_hint in ["POSTGRES_PASSWORD=", "ADMIN_PASSWORD=", "API_KEY="]:
        assert secret_hint not in out.stdout                                 # tidak membuang variabel lingkungan
