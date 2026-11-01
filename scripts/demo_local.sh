#!/usr/bin/env bash
# Demo prototipe lokal/terisolasi (bukan untuk publik): ./scripts/demo_local.sh
#
# Hanya untuk lingkungan demo, TIDAK mengubah bawaan aplikasi:
#   - WHISPER_CHUNKED=1  transkripsi per potongan ucapan + saringan gap terbaru (bawaan aplikasi tetap 0)
#   - MAX_VIDEO_SECONDS=900  video > 15 menit ditolak dengan pesan jelas (bawaan aplikasi tanpa batas)
#   - data terpisah di ./data-demo, server hanya mendengar 127.0.0.1 (tidak terbuka ke jaringan)
# Model Whisper asli ("small") diunduh sekali dari Hugging Face saat analisis pertama (butuh internet).
set -euo pipefail
cd "$(dirname "$0")/.."
command -v ffmpeg >/dev/null || { echo "FFmpeg belum terpasang. Lihat README.md"; exit 1; }
PY=${PYTHON:-.venv/bin/python}
if [ ! -x "$PY" ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -q -r requirements.txt
  PY=.venv/bin/python
fi
export WHISPER_CHUNKED=1
export MAX_VIDEO_SECONDS=900
export WHISPER_MODEL=${WHISPER_MODEL:-small}
export VIDEO_APP_DATA=${VIDEO_APP_DATA:-$PWD/data-demo}
export EMBEDDED_WORKERS=1
PORT=${PORT:-8000}
mkdir -p "$VIDEO_APP_DATA"
# Akun admin: dari ADMIN_EMAIL/ADMIN_PASSWORD (dibuat aplikasi saat start) atau ditanya sekali di terminal.
if [ -z "${ADMIN_EMAIL:-}" ] || [ -z "${ADMIN_PASSWORD:-}" ]; then
  "$PY" -m app.manage ensure-admin
fi
echo "Demo: buka http://127.0.0.1:$PORT (hanya komputer ini). Batas video 15 menit."
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
