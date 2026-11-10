# NH AI Clip Studio — komponen pihak ketiga

NH AI Clip Studio (beta internal) dibundel dengan komponen gratis/sumber terbuka berikut. Teks lisensi lengkap ada
di folder `licenses/`, `fonts/`, dan `models/` di folder instalasi. Daftar paket Python beserta lisensinya dibuat
otomatis saat build: `licenses/python-packages.md`. Status: **diperiksa untuk beta internal; tinjau ulang sebelum
distribusi publik** (lihat catatan GPL di bawah).

| Komponen | Versi / sumber | Lisensi | Dipakai untuk |
|---|---|---|---|
| FFmpeg (build "essentials" gyan.dev) | 7.1.1, `ffmpeg.exe` + `ffprobe.exe`, sha256 zip dikunci di workflow | **GPL v3** (build memakai libx264, libass, dll.) | render & probe video, subtitle (libass) |
| libass, FreeType, FriBidi, HarfBuzz, x264 | di dalam FFmpeg di atas | ISC, FTL/GPL-2, LGPL-2.1, MIT, GPL-2+ | subtitle & encode H.264 |
| Model Whisper small (format CTranslate2) | `Systran/faster-whisper-small`, revisi dicatat di `whisper-small/SOURCE.json` | MIT (bobot asli OpenAI Whisper: MIT) | transkripsi lokal (model standar) |
| faster-whisper, CTranslate2 | PyPI | MIT | menjalankan Whisper |
| Silero VAD (di faster-whisper) | bawaan paket | MIT | deteksi ucapan |
| onnxruntime, tokenizers, huggingface_hub | PyPI | MIT, Apache-2.0, Apache-2.0 | dependensi faster-whisper (offline) |
| PyAV + pustaka FFmpeg miliknya | PyPI (dependensi faster-whisper) | BSD-3 + LGPL | membaca audio |
| YuNet face detector | `models/face_detection_yunet_2023mar.onnx` | MIT (`models/LICENSE-YuNet.txt`) | reframe wajah |
| OpenCV (headless) | PyPI | Apache-2.0 | deteksi wajah |
| Font Anton, Bebas Neue, Poppins | `fonts/` | SIL OFL 1.1 (`fonts/OFL-*.txt`) | subtitle |
| pywebview, pythonnet | PyPI | BSD-3, MIT | jendela aplikasi (Microsoft Edge WebView2) |
| FastAPI, Starlette, uvicorn, pydantic | PyPI | MIT, BSD-3, BSD-3, MIT | server lokal |
| SQLAlchemy, Alembic | PyPI | MIT | database lokal (SQLite) |
| Pillow, NumPy | PyPI | MIT-CMU (HPND), BSD-3 | gambar & audio |
| Python | 3.12 (dibundel PyInstaller) | PSF | runtime |
| PyInstaller bootloader | 6.x | GPL-2+ **dengan pengecualian** yang mengizinkan distribusi program apa pun | peluncur exe |

## Catatan GPL (FFmpeg)

`ffmpeg.exe`/`ffprobe.exe` adalah program terpisah yang dipanggil sebagai proses (tidak ditautkan ke kode aplikasi).
Lisensi GPL v3 berlaku untuk berkas tersebut. Kode sumber FFmpeg yang tepat untuk build ini dirujuk di
`licenses/FFmpeg-README-build-and-source.txt` (commit FFmpeg dan skrip build gyan.dev). **Sebelum distribusi publik**
(di luar beta internal), sediakan arsip kode sumber yang sesuai bersama installer atau tawaran tertulis sesuai GPL v3
pasal 6, atau ganti dengan build FFmpeg LGPL (tanpa x264) bila diperlukan.

## Yang tidak dibundel

Tidak ada API AI berbayar, tidak ada telemetri, tidak ada akun cloud. Pustaka impor link (yt-dlp), klien API AI, dan
driver PostgreSQL dari versi web tidak disertakan di aplikasi desktop.
