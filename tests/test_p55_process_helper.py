"""P5.5.1: helper process_alive() (pengganti `pgrep -f`, karena image produksi tidak memuat procps)."""
import os
import subprocess
import sys
import time
import uuid

from conftest import process_alive


def _marker():
    return f"penanda-{uuid.uuid4().hex}"


def _state(pid):
    with open(f"/proc/{pid}/stat") as fh:
        return fh.read().rsplit(")", 1)[1].split()[0]


def test_detects_live_process_and_not_after_it_is_reaped():
    m = _marker()
    proc = subprocess.Popen(["sh", "-c", f"sleep 30 # {m}"])
    try:
        time.sleep(0.3)
        assert process_alive(m)
    finally:
        proc.kill()
        proc.wait()
    assert not process_alive(m)


def test_zombie_is_not_counted_as_alive():
    m = _marker()
    proc = subprocess.Popen(["sh", "-c", f"exit 0 # {m}"])     # sengaja tidak di-wait: menjadi zombie
    deadline = time.time() + 5
    while _state(proc.pid) != "Z" and time.time() < deadline:
        time.sleep(0.05)
    try:
        assert _state(proc.pid) == "Z"
        assert not process_alive(m)                               # cmdline zombie kosong, sama seperti pgrep -f
    finally:
        proc.wait()


def test_matches_command_line_not_pid_so_reused_pids_are_not_confused():
    """Pencocokan berdasarkan penanda unik di command line, dibaca ulang setiap pemanggilan (tanpa cache PID):
    proses lain yang kebetulan memakai PID lama tidak dihitung sebagai proses yang dicari."""
    old, new = _marker(), _marker()
    first = subprocess.Popen(["sh", "-c", f"sleep 30 # {old}"])
    first.kill()
    first.wait()
    second = subprocess.Popen(["sh", "-c", f"sleep 30 # {new}"])
    try:
        time.sleep(0.3)
        assert not process_alive(old) and process_alive(new)
    finally:
        second.kill()
        second.wait()


def test_works_without_any_external_tools():
    """Dijalankan dengan PATH kosong: tidak memanggil pgrep/ps sama sekali."""
    m = _marker()
    proc = subprocess.Popen(["sh", "-c", f"sleep 30 # {m}"])
    try:
        time.sleep(0.3)
        code = (f"import sys; sys.path.insert(0, {os.path.dirname(__file__)!r}); from conftest import process_alive; "
                f"sys.exit(0 if process_alive({m!r}) else 1)")
        env = {**os.environ, "PATH": ""}
        assert subprocess.run([sys.executable, "-c", code], env=env, timeout=30).returncode == 0
    finally:
        proc.kill()
        proc.wait()


def test_detects_leftover_ffmpeg_subprocess():
    m = _marker()
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=5",
                             "-metadata", f"comment={m}", "-f", "null", "-"], stdin=subprocess.DEVNULL)
    try:
        time.sleep(0.5)
        assert process_alive(f"comment={m}")
    finally:
        proc.kill()
        proc.wait()
    assert not process_alive(f"comment={m}")
