#!/usr/bin/env bash
# Jalankan aplikasi di Mac/Linux: ./start.sh
set -e
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "FFmpeg belum terpasang. Lihat README.md"; exit 1; }
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
echo "Buka http://localhost:8000 di browser"
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
