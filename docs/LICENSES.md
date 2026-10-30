# Lisensi dependensi, model AI, dan komponen (P5.3)

Tujuan: memastikan aplikasi boleh dipakai sebagai **SaaS komersial**. Hasil dipisah menjadi **TERVERIFIKASI**
(bukti dibaca langsung dari file/metadata di repo atau paket terpasang) dan **BUTUH VALIDASI MANUAL** (bukti tidak
tersedia di lingkungan pengembangan, atau keputusan hukum/bisnis). Ini bukan nasihat hukum.

CI menjalankan `scripts/check_licenses.py` pada lingkungan berisi `requirements.txt` saja; hasil lengkap per paket
ada di `docs/licenses-python.md` (dibuat ulang otomatis).

## 1. Paket Python (dari metadata paket terpasang)

| Status | Paket | Lisensi | Catatan untuk SaaS |
|---|---|---|---|
| TERVERIFIKASI | 54 paket (dari 58) (FastAPI, SQLAlchemy, Alembic, faster-whisper, ctranslate2, numpy, OpenCV, Pillow, anthropic, yt-dlp, ...) | permisif (MIT/BSD/Apache-2.0/ISC/PSF/Unlicense) | bebas dipakai komersial |
| TERVERIFIKASI | psycopg, psycopg-binary | LGPL-3.0-only | dipakai tanpa modifikasi; bila image didistribusikan sertakan teks lisensi |
| TERVERIFIKASI | certifi, tqdm | MPL-2.0 (tqdm: MPL-2.0 AND MIT) | copyleft per-file; tidak dimodifikasi |
| DIHAPUS (P5.3) | mutagen | GPL-2.0-or-later | dulu ikut lewat `yt-dlp[default]`. Kini `requirements.txt` memasang `yt-dlp` + dependensi opsionalnya secara eksplisit tanpa mutagen (hanya dipakai postprocessor thumbnail yang tidak dipakai aplikasi). Bila mutagen/GPL lain masuk lagi, CI lisensi **gagal**. Bukti fungsi: `tests/test_ytdlp_local.py` (unduhan nyata file tunggal & DASH video+audio, versi yt-dlp-ejs cocok) |

## 2. Model AI

| Status | Model | Sumber | Lisensi | Bukti / yang harus diperiksa |
|---|---|---|---|---|
| TERVERIFIKASI | YuNet face detection (`models/face_detection_yunet_2023mar.onnx`) | OpenCV Zoo | MIT | `models/LICENSE-YuNet.txt` ikut di repo |
| **MANUAL** | Whisper `tiny`/`base`/`small`/`medium`/`large-v2`/`large-v3` (format CTranslate2) | Hugging Face `Systran/faster-whisper-<ukuran>` (pemetaan dari `faster_whisper.utils._MODELS` 1.2.1) | diharapkan MIT (bobot Whisper asli dirilis OpenAI dengan MIT) | Hugging Face tidak dapat diakses dari lingkungan pengembangan: buka kartu model **setiap ukuran yang dipakai** dan pastikan field lisensinya; jangan menganggap semua ukuran sama |
| **MANUAL** | Whisper `turbo` | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` (penerbit berbeda) | belum diverifikasi | periksa kartu model penerbit ini secara terpisah sebelum dipakai |
| **MANUAL** | `distil-large-v3` | `Systran/faster-distil-whisper-large-v3` (turunan Distil-Whisper) | belum diverifikasi | model turunan: periksa lisensi turunan & asalnya |
| Tidak dipakai | Qwen (bobot model) | – | – | aplikasi tidak mengunduh/menjalankan bobot Qwen. Nama "qwen" hanya preferensi pemilihan model saat memakai API Groq; lisensi Qwen berbeda per model & versi (sebagian Apache-2.0, sebagian lisensi Qwen sendiri), jadi **wajib** diperiksa per model bila kelak di-host sendiri |
| **MANUAL** | Model lewat API (Claude, Gemini, OpenAI, OpenRouter, Groq, DeepSeek) | penyedia | syarat layanan penyedia | tidak ada bobot di server; patuhi ToS penyedia (penggunaan komersial, data pelanggan) |
| **MANUAL** | Model Ollama lokal | pilihan admin | per model | periksa lisensi model yang dipasang admin |

Bawaan aplikasi: `WHISPER_MODEL=small`. CI tidak mengunduh model apa pun (`HF_HUB_OFFLINE=1`); tes khusus
`-m heavy` (dijalankan manual) mengunduh model `tiny` saja.

## 3. Font & aset

| Status | Aset | Lisensi | Bukti |
|---|---|---|---|
| TERVERIFIKASI | Poppins, Anton, Bebas Neue (`fonts/`) | SIL Open Font License 1.1 | `fonts/OFL-*.txt` di repo |

## 4. Komponen sistem (di luar Python)

| Status | Komponen | Lisensi | Catatan |
|---|---|---|---|
| **MANUAL** | FFmpeg (paket Debian/Ubuntu di image) | build `--enable-gpl` (menyertakan libx264) → GPL | SaaS tanpa distribusi umumnya tidak memicu kewajiban GPL; image yang dibagikan = distribusi. **Lisensi paten H.264/AAC** untuk encoding komersial perlu tinjauan hukum |
| **MANUAL** | yt-dlp (fitur "ambil dari link") | Unlicense | kode bebas; **syarat layanan platform sumber** (YouTube, Instagram, dll.) dan hak cipta konten perlu tinjauan hukum sebelum dibuka untuk pelanggan |
| TERVERIFIKASI (metadata) | OpenCV/PyAV wheel | Apache-2.0 / BSD; wheel menyertakan pustaka FFmpeg LGPL | tidak dimodifikasi |
| MANUAL (risiko rendah) | PostgreSQL, Caddy, image `python:3.12-slim` | PostgreSQL License, Apache-2.0, Debian (beragam) | standar untuk SaaS |

## 5. Daftar validasi komersial (wajib sebelum SaaS berbayar dibuka)

Centang hanya dengan bukti tertulis (tautan kartu model/teks lisensi/opini hukum) yang disimpan bersama catatan rilis.

| # | Item | Status | Yang harus dibuktikan |
|---|---|---|---|
| 1 | Paket Python produksi | TERVERIFIKASI otomatis (CI) | `scripts/check_licenses.py` lolos: 58 paket, 54 permisif, 4 copyleft lemah (psycopg, psycopg-binary LGPL; certifi, tqdm MPL), 0 GPL |
| 2 | Whisper per ukuran yang dipakai (bawaan `small`) | MANUAL | kartu model `Systran/faster-whisper-<ukuran>` menyatakan lisensi yang membolehkan penggunaan komersial; ulangi untuk setiap ukuran yang diaktifkan |
| 3 | Whisper `turbo` | MANUAL | kartu model `mobiuslabsgmbh/faster-whisper-large-v3-turbo` (penerbit berbeda) |
| 4 | `distil-large-v3` | MANUAL | lisensi model turunan & model asal |
| 5 | YuNet | TERVERIFIKASI | `models/LICENSE-YuNet.txt` (MIT) |
| 6 | Model AI lewat API / Ollama | MANUAL | ToS setiap penyedia untuk penggunaan komersial & data pelanggan; lisensi setiap model Ollama yang dipasang admin (cek per model & versi, bukan per keluarga) |
| 7 | FFmpeg di image | MANUAL | build Debian `--enable-gpl`: kewajiban GPL hanya bila image didistribusikan; bila image diberikan ke pihak lain sertakan sumber/penawaran sumber |
| 8 | Codec H.264 (libx264) & AAC | MANUAL (hukum) | kebutuhan lisensi paten (mis. pool paten AVC/AAC) untuk encoding komersial di yurisdiksi operasi |
| 9 | Font Poppins, Anton, Bebas Neue | TERVERIFIKASI | `fonts/OFL-*.txt` (OFL 1.1: boleh dipakai komersial; tidak boleh dijual terpisah) |
| 10 | Musik/aset yang diunggah pengguna | MANUAL (ToS) | syarat layanan mewajibkan pengguna memiliki hak atas konten yang diunggah |
| 11 | Fitur "ambil dari link" (yt-dlp) | MANUAL (hukum) | syarat layanan platform sumber & hak cipta konten; pertimbangkan membatasi ke konten milik pengguna |
