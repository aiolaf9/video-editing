"""P0.1: profil Hostinger KVM 2 (2 vCPU, 8 GB) tidak boleh menjanjikan lebih dari kapasitas mesin.

Konfigurasi akhir dihitung oleh `docker compose config` (penggabungan resmi docker-compose.yml + overlay + .env
contoh), bukan disalin ulang di tes. Batas `cpus` Docker adalah plafon per container: jumlah seluruh plafon layanan
yang berjalan bersamaan harus <= anggaran agar OS & layanan lain di VPS selalu kebagian CPU, walau semua layanan
aplikasi sibuk sekaligus. Simulasi beban nyata (RAM/OOM): .github/workflows/kvm2.yml.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOST_CPUS, HOST_RAM_GB = 2.0, 8.0
APP_CPU_BUDGET = 1.8          # minimal 0,2 vCPU selalu tersisa untuk OS & layanan lain
APP_RAM_BUDGET_GB = 4.5       # sisa >= 3,5 GB untuk OS, cache disk, dan layanan lain

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="butuh docker compose (CLI saja)")


def _config(tmp_path, *overlays):
    for f in ("docker-compose.yml", *overlays):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / f, tmp_path / f)
    shutil.copy(REPO / "deploy/kvm2.env.example", tmp_path / ".env")
    args = ["docker", "compose", "--project-directory", str(tmp_path), "--env-file", str(tmp_path / ".env")]
    for f in ("docker-compose.yml", *overlays):
        args += ["-f", str(tmp_path / f)]
    out = subprocess.run(args + ["config", "--format", "json"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["services"]


def _long_running(services):
    """Layanan yang berjalan bersamaan (migrate selesai sebelum web & worker dimulai)."""
    out = {}
    for name, svc in services.items():
        replicas = (svc.get("deploy") or {}).get("replicas", 1)
        if name == "migrate" or replicas == 0:
            continue
        out[name] = (svc, replicas)
    return out


def _mem_gb(value) -> float:
    return int(value) / 2**30


@pytest.mark.parametrize("overlays", [("deploy/compose.kvm2.yml",),
                                      ("deploy/compose.kvm2.yml", "deploy/compose.behind-proxy.yml")],
                         ids=["caddy", "di-belakang-web-server"])
def test_total_cpu_and_ram_limits_fit_the_vps_with_headroom(tmp_path, overlays):
    services = _config(tmp_path, *overlays)
    running = _long_running(services)
    missing = [n for n, (s, _) in running.items() if not s.get("cpus") or not s.get("mem_limit")]
    assert not missing, f"layanan tanpa batas CPU/RAM: {missing}"
    cpus = sum(float(s["cpus"]) * r for s, r in running.values())
    ram = sum(_mem_gb(s["mem_limit"]) * r for s, r in running.values())
    assert cpus <= APP_CPU_BUDGET, {n: float(s["cpus"]) * r for n, (s, r) in running.items()}
    assert ram <= APP_RAM_BUDGET_GB, {n: _mem_gb(s["mem_limit"]) * r for n, (s, r) in running.items()}
    assert HOST_CPUS - cpus >= 0.2 - 1e-9 and HOST_RAM_GB - ram >= 3.5
    for name in running:   # migrate selesai lebih dulu: plafonnya tidak bertumpuk dengan layanan lain
        if name not in ("db", "caddy"):
            assert services[name]["depends_on"]["migrate"]["condition"] == "service_completed_successfully", name


def test_single_heavy_worker_renders_first_then_analyses(tmp_path):
    services = _config(tmp_path, "deploy/compose.kvm2.yml")
    heavy = services["worker-analisis"]
    assert heavy["deploy"]["replicas"] == 1 and services["worker-render"]["deploy"]["replicas"] == 0
    command = " ".join(heavy["command"])
    assert "--queues render,analisis" in command                    # render didahulukan, satu job berat sekaligus
    assert float(heavy["cpus"]) < HOST_CPUS                          # tidak pernah memakai seluruh CPU
    env = heavy["environment"]
    assert env["WHISPER_MODEL"] == "base" and env["WHISPER_CHUNKED"] == "1" and env["WHISPER_DEVICE"] == "cpu"
    assert env["MAX_VIDEO_SECONDS"] == "900"


def test_heavy_worker_memory_covers_analysis_and_render_needs(tmp_path):
    """Batas RAM worker berat >= kebutuhan analisis video 15 menit (Whisper base, per potongan) dan render menurut
    model memori yang sama dengan admission control (app/config.analysis_need_mb, TASK_MEM_MB), plus headroom."""
    import sys
    services = _config(tmp_path, "deploy/compose.kvm2.yml")
    heavy = services["worker-analisis"]
    limit_mb = int(heavy["mem_limit"]) / 2**20
    env = {k: v for k, v in heavy["environment"].items() if k.startswith(("WHISPER_", "MAX_VIDEO"))}
    code = ("import json; from app import config as c; print(json.dumps({'analisis': c.analysis_need_mb("
            "c.MAX_VIDEO_SECONDS), 'render': c.TASK_MEM_MB['render'], 'headroom': c.CONTAINER_MEM_HEADROOM_MB}))")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True,
                         env={"PATH": "/usr/bin:/bin", "VIDEO_APP_DATA": str(tmp_path / "data"), **env})
    need = json.loads(out.stdout.strip().splitlines()[-1])
    for task in ("analisis", "render"):
        assert need[task] + need["headroom"] <= limit_mb, (task, need, limit_mb)
