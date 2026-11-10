# PyInstaller (onedir) untuk NH AI Clip Studio. Jalankan dari akar repo:  pyinstaller desktop/nh_clip_studio.spec
# Berkas unduhan (FFmpeg, model Whisper base) disiapkan workflow di build/desktop/ (lihat .github/workflows/desktop.yml).
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
STAGE = os.path.join(ROOT, "build", "desktop")


def need(path):
    if not os.path.exists(path):
        raise SystemExit(f"Berkas bundel belum disiapkan: {path}")
    return path


datas = [
    (os.path.join(ROOT, "web"), "web"),
    (os.path.join(ROOT, "fonts"), "fonts"),
    (os.path.join(ROOT, "models"), "models"),                      # YuNet (MIT)
    (os.path.join(ROOT, "app", "migrations"), os.path.join("app", "migrations")),   # Alembic membaca .py migrasi
    (os.path.join(ROOT, "app", "data"), os.path.join("app", "data")),               # kosakata bawaan (M1.1)
    (need(os.path.join(STAGE, "bin")), "bin"),                     # ffmpeg.exe, ffprobe.exe (GPLv3, lihat notices)
    (os.path.join(ROOT, "desktop", "THIRD_PARTY_NOTICES.md"), "."),
    (need(os.path.join(STAGE, "licenses")), "licenses"),
]
for model in ("base", "small"):              # Cepat & Akurat (desktop/launcher.py DESKTOP_MODELS)
    folder = f"whisper-{model}"
    datas.append((need(os.path.join(STAGE, folder, "model.bin")), folder))
    for name in ("config.json", "tokenizer.json", "vocabulary.txt", "vocabulary.json", "README.md", "SOURCE.json"):
        path = os.path.join(STAGE, folder, name)
        if os.path.exists(path):
            datas.append((path, folder))
datas += collect_data_files("faster_whisper")       # model VAD Silero (onnx)
datas += collect_data_files("webview")

hiddenimports = (collect_submodules("app") + collect_submodules("desktop") + collect_submodules("uvicorn")
                 + collect_submodules("alembic") + ["sqlalchemy.dialects.sqlite", "multipart", "python_multipart"])
binaries = collect_dynamic_libs("ctranslate2")

a = Analysis(
    [os.path.join(ROOT, "desktop", "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["yt_dlp", "anthropic", "psycopg", "psycopg_binary", "pytest", "playwright", "IPython", "matplotlib",
              "torch", "tensorflow"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = os.path.join(STAGE, "app.ico")
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NH-AI-Clip-Studio",
    console=False,
    icon=icon if os.path.exists(icon) else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="NH-AI-Clip-Studio")
