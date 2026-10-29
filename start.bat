@echo off
REM Jalankan aplikasi di Windows (mode satu komputer): klik dua kali file ini
cd /d "%~dp0"
where ffmpeg >nul 2>nul || (echo FFmpeg belum terpasang. Jalankan: winget install Gyan.FFmpeg & pause & exit /b 1)
if not exist .venv python -m venv .venv
echo Memeriksa library (cepat bila sudah lengkap)...
.venv\Scripts\python -m pip install -q -r requirements.txt
if errorlevel 1 (echo Gagal memasang library. Periksa koneksi internet. & pause & exit /b 1)
REM Buat akun Admin pertama bila belum ada (ditanya email ^& password sekali saja)
.venv\Scripts\python -m app.manage ensure-admin
if errorlevel 1 (pause & exit /b 1)
start "" /b cmd /c "timeout /t 5 /nobreak >nul && start http://localhost:8000"
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
