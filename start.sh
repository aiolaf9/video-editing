#!/usr/bin/env bash
# Jalankan aplikasi di Mac/Linux (mode satu komputer): ./start.sh
set -e
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "FFmpeg belum terpasang. Lihat README.md"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
echo "Memeriksa library (cepat bila sudah lengkap)..."
.venv/bin/python -m pip install -q -r requirements.txt
# Buat akun Admin pertama bila belum ada (ditanya email & password sekali saja)
.venv/bin/python -m app.manage ensure-admin
echo "Buka http://localhost:8000 di browser"
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
