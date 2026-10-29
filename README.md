# 🎬 Video Editing AI

Aplikasi lokal untuk mengubah video panjang menjadi klip pendek siap upload (TikTok, Reels, Shorts):
subtitle otomatis bergaya kekinian, klipper AI, crop vertikal, filter warna, dan backsound.

**Semua proses berjalan di komputer Anda. Tidak ada biaya API per video.**
Transkrip dibuat oleh Whisper secara lokal, editing oleh FFmpeg, dan pemilihan klip memakai algoritma lokal.

## Fitur (Tahap 1)

| Fitur | Keterangan |
|---|---|
| Ambil video | Upload file atau tempel link (YouTube, Instagram, TikTok, dll. via yt-dlp) |
| Subtitle otomatis | Whisper dengan timestamp per kata. Gaya: **Karaoke**, **Pop**, **Satu kata** (animasi), **Sederhana** |
| Klipper AI | Menilai tiap bagian dari energi suara, kepadatan bicara, kalimat pembuka yang memancing, dan akhir kalimat. Anda memilih sendiri klip mana yang dirender. |
| Pilih manual | Atur titik awal/akhir dari timeline atau dengan klik kata di transkrip |
| Format | 9:16, 1:1, 4:5, 16:9, atau asli. Bingkai bisa digeser agar wajah tetap di tengah. |
| Tune | Filter (Vivid, Cinematic, Hangat, Sejuk, Vintage, Hitam putih), kecerahan, kontras, saturasi, kecepatan 0.75×–2×, zoom-in perlahan, fade |
| Backsound | Upload musik, atur volume, *auto-ducking* (musik mengecil saat ada suara), normalisasi loudness standar media sosial |
| Bingkai otomatis | Melacak wajah (YuNet, lokal) sehingga bingkai 9:16 mengikuti pembicara secara halus |
| Animasi sesuai omongan | Kartu, kata kunci besar bergradasi, chips, checklist, dan punch-in zoom yang muncul tepat saat hal itu diucapkan. Bisa dibuat **Otomatis** (gratis), **Salin prompt ke Claude.ai** (gratis), atau **Claude API**. Semua elemen bisa diedit sebelum render. |
| Guidebook | Aturan gaya edit Anda sendiri, dipakai setiap kali Claude membuat rencana animasi agar hasil konsisten |
| Render massal | Centang beberapa klip, render sekaligus. Bisa pakai GPU NVIDIA/Intel/AMD/Mac (otomatis kembali ke CPU bila gagal). |

## Instalasi

### 1. Pasang Python 3.10+ dan FFmpeg

**Windows** (PowerShell):
```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
```
Tutup lalu buka lagi terminal setelah instalasi.

Untuk mengambil video dari **link YouTube**, pasang juga Deno (dibutuhkan yt-dlp):
```powershell
winget install DenoLand.Deno
```

**Mac**:
```bash
brew install python ffmpeg deno
```

**Linux (Ubuntu/Debian)**:
```bash
sudo apt install python3 python3-venv ffmpeg
```

### 2. Unduh aplikasi

```bash
git clone https://github.com/aiolaf9/video-editing.git
```

### 3. Jalankan

- **Windows**: klik dua kali `start.bat`
- **Mac/Linux**: jalankan `./start.sh`

Saat pertama kali dijalankan, skrip membuat virtual environment dan memasang dependensi (beberapa menit).
Setelah itu buka **http://localhost:8000** di browser.

> Saat menganalisis video pertama kali, model Whisper (~500 MB untuk `small`) diunduh otomatis satu kali.

## Cara pakai

1. Seret file video ke kotak upload, atau tempel link lalu klik **Ambil**.
2. Tunggu analisis: preview, transkrip, lalu pencarian momen terbaik.
3. Di tab **Klip pilihan AI**, klik klip untuk memutarnya. Centang klip yang Anda suka.
4. Atur gaya di panel kanan: format, filter, subtitle, dan musik. Subtitle bisa dilihat langsung di preview.
5. Klik **Render yang dicentang** atau **Render klip ini**. Hasilnya muncul di tab **Hasil render** dan bisa diunduh.

## Jika ada masalah

- **Proyek tertahan di "Menunggu giliran"**: pekerjaan sebelumnya masih berjalan. Klik proyek lain di daftar untuk melihat progresnya, dan lihat jendela terminal untuk pesan error.
- **Link YouTube gagal**: pastikan Deno terpasang, lalu perbarui yt-dlp: `.venv\Scripts\pip install -U "yt-dlp[default]"`.
- **Link Instagram gagal**: Instagram sering meminta login. Unduh videonya manual, lalu unggah filenya.

## Animasi dengan Claude: hemat token

Video tidak pernah dikirim ke Claude. Yang dikirim hanya **teks transkrip klip** (klip 30 detik ≈ 100 kata),
lalu Claude membalas rencana animasi berupa JSON. Semua gambar dan render dikerjakan laptop Anda.

- **Salin prompt untuk Claude.ai**: gratis dengan akun Claude biasa. Klik tombolnya, tempel di claude.ai,
  lalu tempel jawaban JSON-nya kembali ke aplikasi.
- **Claude API**: sekali klik, butuh API key dari https://console.anthropic.com. Pilih model di panel
  Animasi; Haiku 5.5 paling hemat, Opus 5.5 paling pintar. Fitur *fallback* otomatis aktif: bila permintaan
  ditolak filter keamanan, API mengulang di model cadangan.

## Pengaturan lanjutan

Atur lewat environment variable sebelum menjalankan:

| Variabel | Default | Keterangan |
|---|---|---|
| `WHISPER_MODEL` | `small` | `tiny`/`base` lebih cepat, `medium`/`large-v3` lebih akurat |
| `WHISPER_DEVICE` | `auto` | `cuda` untuk GPU NVIDIA, `cpu` untuk memaksa CPU |
| `JOB_WORKERS` | `1` | Jumlah render yang berjalan bersamaan |
| `VIDEO_APP_DATA` | `./data` | Lokasi penyimpanan proyek dan hasil render |

**GPU NVIDIA untuk Whisper** membutuhkan CUDA 12 dan cuDNN 9 (lihat [dokumentasi faster-whisper](https://github.com/SYSTRAN/faster-whisper#gpu)).
Tanpa itu aplikasi tetap jalan memakai CPU, hanya lebih lambat.

**Font subtitle**: taruh file `.ttf` di folder `fonts/` (lihat `fonts/README.md`).

## Struktur kode

```
app/
  main.py        API FastAPI + menyajikan UI
  pipeline.py    alur impor → analisis → render
  transcribe.py  Whisper lokal (faster-whisper)
  clipper.py     penilaian & pemilihan kandidat klip
  subtitles.py   pembuat subtitle ASS animasi
  render.py      penyusun perintah FFmpeg (crop, zoom, warna, musik, overlay)
  faces.py       pelacakan wajah untuk bingkai otomatis
  plan.py        rencana animasi: otomatis, prompt Claude.ai, Claude API
  overlays.py    menggambar kartu/kata kunci/chips/checklist (Pillow)
  media.py       probe, preview, energi audio
  jobs.py        antrean pekerjaan di latar belakang
web/             UI (HTML/CSS/JS tanpa build)
tests/           pytest
```

Menjalankan tes:
```bash
pip install -r requirements-dev.txt
pytest
```

## Rencana berikutnya

- Penilaian klip viral dengan Claude (membaca transkrip penuh)
- Teks di belakang subjek (hapus background), emoji, transisi, preset LUT
- Template animasi tambahan, B-roll otomatis, editor timeline multi-track

## Catatan

Gunakan hanya video milik sendiri atau yang Anda punya izin untuk diedit dan diunggah ulang.
