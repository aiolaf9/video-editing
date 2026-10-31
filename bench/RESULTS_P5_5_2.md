# Hasil P5.5.2 — Validasi Media Nyata & AI

Status: **berjalan** (P5.5.2-A selesai; B sebagian; C lokal). P5.5.2 belum selesai sampai benchmark Whisper asli
& media di image produksi (workflow `Benchmark P5.5.2`) dijalankan.

## P5.5.2-A — Audit kemampuan & aset uji

### Inventaris fitur (dari kode, `v2/phase-1` @ `3e698b4`)

| Area | Tersedia | Catatan / gap |
|---|---|---|
| Transkripsi | faster-whisper (`app/transcribe.py`): model dari `WHISPER_MODEL` (tiny/base/small/…), CPU int8 atau GPU bila VRAM cukup, VAD, timestamp per kata, deteksi bahasa otomatis, prompt tanda baca (id/ms/jv/en) | GPU tidak tersedia di runner/sandbox |
| Analisis | probe → preview → pelacakan wajah → energi audio → transkrip → kandidat klip (`pipeline.analyze`) | |
| Render | satu rentang (trim) maks. 10 menit; aspect 9:16 / 1:1 / 4:5 / 16:9 / original; reframe otomatis (wajah) atau manual; preset warna, kecerahan/kontras/saturasi, speed 0,5–2×, zoom, fade, normalisasi audio; subtitle ASS (karaoke/kata) dibakar; musik + ducking; overlay & animasi dari rencana; encoder cpu/nvidia/intel/amd/mac (fallback CPU) | **Gap:** concat beberapa rentang/klip; voiceover terpisah (audio render = audio sumber + musik) |
| Preview | 480p, remux cepat bila memungkinkan, mode panjang, cache (`media.make_preview`) | |
| Upload | tus 1.0 (creation, termination, checksum), lanjut setelah putus/refresh, jeda/lanjut di UI (`web/app.js`), satu penulis per sesi, kedaluwarsa upload terputus | UI jeda/lanjut tidak diuji ulang di P5.5.2 (diuji di P4.3) |
| Format masuk | ekstensi mp4, mov, mkv, webm, avi, m4v, flv, wmv, 3gp, ts (validasi isi oleh FFprobe) | |
| Siklus media | masa simpan (`MEDIA_RETENTION_HOURS` 24, `MEDIA_MAX_HOURS` 72), kuota unit, lease job/upload, pembersihan yatim, advisory lock | |

### Prasyarat & blocker

| Prasyarat | Status |
|---|---|
| faster-whisper 1.2.1 / CTranslate2 4.8.4 terpasang | ada (venv produksi & image) |
| Model tiny/base/small | **BLOCKED di sandbox pengembangan**: `huggingface.co` ditolak proxy (403 CONNECT, kebijakan). Tersedia di runner GitHub → workflow `bench-p552.yml` (perlu persetujuan push & menjalankan) |
| Ground truth ucapan (FLEURS id_id & en_us, CC-BY 4.0) | sama: hanya dari runner |
| FFmpeg | sandbox 6.1.1 (tanpa libvmaf); image produksi 7.1.5 → angka resmi dari image |
| GPU | tidak ada (sandbox & runner `ubuntu-24.04`) → semua angka CPU |

### Aset & skrip (`bench/p552/`)

| Skrip | Isi |
|---|---|
| `assets.py` | 10 aset sintetis: MP4 H.264/AAC 1080p30 16:9 stereo (acuan), MOV, 1080×1920 9:16, 720p25 mono 44,1 kHz, 1080p 23,976, 1080p60, HEVC, WebM VP9/Opus, VFR (interval 1/60 & 2/60 dtk), 720p 30 menit. Penanda: flash putih 1 frame & beep 1 kHz 40 ms tiap 2 dtk (sejajar) |
| `media_bench.py` | preview (`media.make_preview`) & render (`pipeline.render`) per aset, N run di subprocess: waktu, CPU & puncak RSS FFmpeg, properti keluaran, offset/drift A/V, timing subtitle (kata DISUNTIKKAN, bukan Whisper), SSIM/PSNR |
| `fleurs_corpus.py` | id bersih (20 ucapan), id "dua pembicara" (24 ucapan pria/wanita bergantian, disusun), id + noise pink SNR 10 dB (diverifikasi: selisih RMS 10,0 dB), id ±10 menit, en bersih (20) |
| `whisper_quality.py` | Whisper asli lewat `app.transcribe.transcribe`: muat model, RTF, CPU, puncak RSS, bahasa terdeteksi, WER/CER (huruf kecil, tanpa tanda baca), selisih batas ucapan |
| `lifecycle_check.py` | upload tus berkas video nyata besar dengan putus & SIGKILL server, batal, kuota, lease/TTL, yatim |
| `summarize_media.py` | median & p95 (nearest-rank) per skenario |

Deteksi diverifikasi pada aset acuan: 30/30 pasangan flash–beep; subtitle 15/15 kata (deteksi luma maksimum di pita
bawah; versi awal dengan luma rata-rata tidak mendeteksi teks kecil — diperbaiki, lihat riwayat commit).

### Acceptance criteria (ditetapkan SEBELUM hasil)

| # | Kriteria | Ambang |
|---|---|---|
| W1 | WER id bersih (small) | ≤ 25 % (tiny/base: dicatat, bukan syarat) |
| W2 | RTF small, CPU 4 vCPU | ≤ 0,5 |
| W3 | Batas ucapan (small, timeline id) | median |selisih| ≤ 300 ms dan ≥ 80 % batas dalam 300 ms |
| W4 | Deteksi bahasa id (small, id bersih) | ≥ 95 % ucapan |
| W5 | Puncak RAM Whisper ≤ perkiraan admission (`_WHISPER_MEM`: tiny 700, base 900, small 1600 MB) | tidak melebihi |
| W6 | Hasil deterministik antar-run | teks identik |
| M1 | Semua format: probe, preview, render 9:16 & original berhasil | 100 % |
| M2 | Offset A/V | maks ≤ 1 frame (≤ 42 ms) di preview & render semua format |
| M3 | Drift A/V (aset 30 menit, klip di 1700–1730 dtk) | ≤ 1 frame |
| M4 | Subtitle | ≥ 95 % kata terdeteksi, selisih ≤ 1 frame |
| M5 | Kualitas render original | SSIM ≥ 0,98 |
| M6 | Kecepatan | render 1080p RTF ≤ 1,0; preview RTF ≤ 0,2 |
| M7 | RAM render | puncak RSS FFmpeg ≤ `RENDER_MEM_MB` 700 |
| M8 | Properti keluaran | resolusi sesuai aspect, fps = sumber (CFR), audio 48 kHz ada |
| U1 | Upload besar putus & server SIGKILL | lanjut dari offset server, sha256 identik, kirim ulang ≤ 1 potongan |
| U2 | Batal | berkas parsial terhapus, sesi tidak dapat dilanjutkan |
| U3 | Kuota | ditolak saat dibuat, sebelum data dikirim |
| U4 | TTL & lease | media dengan job aktif tidak dihapus; setelah job selesai & masa simpan lewat → dihapus |
| U5 | Yatim | folder tanpa proyek yang cukup tua dihapus |

Statistik: n = 3 per skenario (median; "p95" = nearest-rank, dengan n = 3 sama dengan nilai maksimum — bukan
estimasi persentil yang andal). Waktu antre tidak diukur di benchmark ini (fungsi dipanggil langsung); analisis
(Whisper) & render diukur terpisah.
