"""Desktop M1: peluncur dari kode sumber (lingkungan desktop, server uvicorn + worker bawaan di thread, MEDIA_LIFECYCLE=0)
menjalankan desktop.selftest sampai MP4 terverifikasi. Whisper diganti transkrip tetap (regresi tanpa unduh model);
Whisper base sungguhan diuji di workflow Windows (.github/workflows/desktop.yml)."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from desktop import launcher
d = Path(sys.argv[2])
key = launcher.configure_env(d / "data", d / "export")
launcher.setup_logging(d / "data")
from app import config, transcribe
assert config.DESKTOP_MODE and not config.MEDIA_LIFECYCLE and config.EMBEDDED_WORKERS
assert config.ALLOW_URL_IMPORT == "none"
sentences = ["Kenapa video pendek ini penting untuk dakwah kita semua?", "Ternyata cara membuatnya cukup mudah dan cepat.",
             "Pertama pilih bagian ceramah yang paling kuat.", "Rahasia kedua adalah subtitle yang jelas dibaca.",
             "Jangan lupa ajak penonton untuk berbagi kebaikan.", "Terima kasih sudah menonton sampai akhir video ini."]
words = " ".join(sentences).split()
def fake(*a, **k):
    segs, t = [], 0.4
    for i in range(0, len(words), 4):
        ws = []
        for w in words[i:i + 4]:
            ws.append({"start": round(t, 2), "end": round(t + 0.45, 2), "word": w}); t += 0.6
        t += 0.3
        segs.append({"start": ws[0]["start"], "end": ws[-1]["end"], "text": " ".join(x["word"] for x in ws), "words": ws})
    return {"language": "id", "segments": segs}
transcribe.transcribe = fake
srv = launcher.LocalServer(launcher.free_port()); srv.start()
from desktop import selftest
try:
    r = selftest.run(srv.base, key, Path(sys.argv[3]), d / "export", language="id")
finally:
    srv.stop()
r["server_stopped"] = not srv.thread.is_alive()
print("REPORT" + json.dumps(r))
'''


def test_launcher_selftest_from_source(tmp_path):
    video = tmp_path / "ucapan.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=40", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)
    out = subprocess.run([sys.executable, "-c", SCRIPT, str(ROOT), str(tmp_path / "app"), str(video)],
                         capture_output=True, text=True, timeout=900, cwd=tmp_path)
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("REPORT")), None)
    assert line, out.stdout[-3000:] + out.stderr[-3000:]
    report = json.loads(line[6:])
    assert report["ok"] and report["server_stopped"], report
    assert len(report["outputs"]) == 6                                 # 2 klip x 3 rasio
    assert {o["size"] for o in report["outputs"]} == {"1080x1920", "1080x1080", "1920x1080"}
    assert len(report["exported"]) == 6
    assert (tmp_path / "app" / "data" / "logs" / "desktop.log").exists()
    assert (tmp_path / "app" / "data" / "projects").exists()           # proyek tetap ada setelah aplikasi ditutup
