@echo off
REM Jalankan aplikasi di Windows: klik dua kali file ini
cd /d "%~dp0"
where ffmpeg >nul 2>nul || (echo FFmpeg belum terpasang. Jalankan: winget install Gyan.FFmpeg & pause & exit /b 1)
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
start "" /b cmd /c "timeout /t 5 /nobreak >nul && start http://localhost:8000"
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
