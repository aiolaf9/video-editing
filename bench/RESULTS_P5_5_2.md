# Hasil P5.5.2 — Validasi Media Nyata & AI

Status: **berjalan** (P5.5.2-A selesai; B sebagian; C lokal). P5.5.2 belum selesai sampai benchmark Whisper asli
& media di image produksi (CI, dispatch manual `bench=smoke|full`) dijalankan.

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
| Model tiny/base/small | **BLOCKED di sandbox pengembangan**: `huggingface.co` ditolak proxy (403 CONNECT, kebijakan). Tersedia di runner GitHub → job `bench-*` di `ci.yml` (dispatch manual) |
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

**Cara menjalankan di runner.** GitHub hanya bisa men-dispatch workflow yang sudah terdaftar (pernah berjalan karena
suatu event). Branch default repo ini (`claude/dreamy-carson-0mnbh2`) tidak berisi workflow apa pun, dan workflow
baru yang hanya `workflow_dispatch` tidak pernah terdaftar: dispatch `bench-p552.yml` ditolak 404. `ci.yml` sudah
terdaftar dan dispatch dengan `ref=v2/phase-1` terbukti memakai kode branch itu (CI #13, `head_sha` = `429920a`);
input divalidasi terhadap berkas di ref tersebut (input asing ditolak "Unexpected inputs provided"). Karena itu
benchmark ditempatkan sebagai job terpisah di `ci.yml`: input `bench` (none/smoke/full, bawaan none), `bench_models`
(tiny,base,small) & `bench_runs` (1–10, divalidasi). Push biasa tidak menjalankannya; run manual punya grup
`concurrency` sendiri sehingga tidak dibatalkan oleh push.

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

## P5.5.2-B — Whisper asli

| Butir | Status | Bukti |
|---|---|---|
| Akses model di runner GitHub | PASS | CI #13 (dispatch `heavy=true`, ref `v2/phase-1` @ `429920a`): https://github.com/aiolaf9/video-editing/actions/runs/41171196141 — job "Tes khusus Whisper asli (manual)": **2 passed**, 0 dilewati (340 tes lain di luar `-m heavy`). Model tiny diunduh dari Hugging Face & dimuat sebagai `faster_whisper.WhisperModel` (tes memeriksa tipe objek — bukan tiruan); transkripsi end-to-end berjalan. faster-whisper 1.2.1, CTranslate2 4.8.4, huggingface-hub 2.4.0 (log pip) |
| Waktu unduh/muat, durasi transkripsi, CPU, RAM tiny | NOT VERIFIED | job ini hanya mencatat total pytest 5,36 dtk (2 tes); rincian diukur `whisper_quality.py` |
| WER/CER, RTF, timestamp, bahasa, SNR 10 dB (tiny/base/small) | **BLOCKED** | job `bench-*` di `ci.yml` (commit lokal `805e4ca`) menunggu persetujuan push; sandbox tidak dapat mengunduh model/dataset (403 kebijakan) |
| Revisi model | NOT VERIFIED | dicatat otomatis oleh `whisper_quality.py` (`model_revision`) saat benchmark berjalan |

## P5.5.2-B — Media (SINTETIS, lokal)

Lingkungan: sandbox pengembangan, Intel Xeon @ 2,10 GHz, 4 vCPU, 16 GB RAM, tanpa GPU, **FFmpeg 6.1.1-3ubuntu5**
(BUKAN FFmpeg 7.1.5 image produksi), venv produksi (requirements.txt). Tidak ada proses benchmark lain selama run
(upload/lifecycle dijalankan sesudahnya). `media_bench.py` mode full: 10 aset × 3 operasi × 3 run = **90 baris, exit 0,
0 error**. n = 3 per skenario: "p95" = nearest-rank = nilai maksimum. Media sintetis (testsrc2 + flash + beep): hasil
ini membuktikan sinkronisasi, timing, properti keluaran & stabilitas pipeline, **bukan** kualitas visual konten nyata.

| Aset | Preview: waktu median/p95 (dtk), RTF, mode | Render 9:16 + subtitle: median/p95, RTF | Render original: median/p95, RTF, SSIM | Puncak RSS FFmpeg (MB) |
|---|---|---|---|---|
| MP4 H.264/AAC 1080p30 16:9 (acuan) | 5,11 / 5,13; 0,085; transcode | 8,74 / 8,85; 0,29 | 6,56 / 6,69; 0,22; 0,99966 | 152 / 327 / 316 |
| MOV H.264/AAC 1080p30 | 5,17 / 5,25; 0,086; transcode | 8,56 / 8,78; 0,29 | 6,11 / 6,75; 0,20; 0,99966 | 152 / 327 / 316 |
| 1080×1920 9:16 | 3,70 / 4,08; 0,062; transcode | 6,53 / 6,73; 0,22 | 6,47 / 6,54; 0,22; 0,99967 | 125 / 327 / 316 |
| 720p25 mono 44,1 kHz | 0,17 / 0,22; 0,003; copy | 7,15 / 7,22; 0,24 | 2,67 / 3,01; 0,09; 0,99969 | 72 / 304 / 182 |
| 1080p 23,976 | 4,36 / 4,51; 0,073; transcode | 7,23 / 7,41; 0,24 | 5,56 / 5,69; 0,19; 0,99964 | 152 / 328 / 317 |
| 1080p60 | 9,95 / 10,03; 0,166; transcode | 14,42 / 14,87; 0,48 | 10,93 / 11,20; 0,36; 0,99958 | 148 / 327 / 316 |
| HEVC/AAC 1080p30 | 5,88 / 6,03; 0,098; transcode | 9,36 / 9,41; 0,31 | 6,69 / 7,08; 0,22; 0,99932 | 171 / 342 / 330 |
| WebM VP9/Opus 720p30 | 7,62 / 7,74; 0,127; transcode | 9,75 / 9,82; 0,33 | 4,61 / 4,85; 0,15; 0,98858 | 115 / 289 / 167 |
| VFR (rata-rata 36 fps) | 0,20 / 0,21; 0,003; copy | 12,79 / 13,28; 0,43 | 4,93 / 5,10; 0,16; 0,99226 | 72 / 304 / 183 |
| 720p30 30 menit (klip 1700–1730 dtk) | 2,81 / 3,00; 0,0016; copy | 8,48 / 8,68; 0,28 | 2,97 / 3,08; 0,10; 0,99971 | 72 / 305 / 186 |

RTF render = waktu render / durasi klip (30 dtk); RTF preview = waktu / durasi sumber. Waktu = fungsi aplikasi
dipanggil langsung (tanpa antrean). Puncak RSS = proses FFmpeg (RUSAGE_CHILDREN), per operasi preview/9:16/original.

**Sinkronisasi A/V** (flash 1 frame & beep 40 ms tiap 2 dtk; resolusi deteksi: 1 frame video, 2 ms audio):
- Semua 30 skenario: semua penanda berpasangan (preview 30/30; aset 30 menit 900/900; render 15/15).
- Offset absolut 0 ms untuk 8 dari 10 aset (preview & render). WebM VP9/Opus: +6 ms (preview) / +8 ms (render)
  konstan, kemungkinan pre-skip Opus saat dikodekan ulang ke AAC — < 1 frame. 1080p 23,976: offset absolut 10–40 ms
  dengan pola gergaji pada **sumbernya sendiri** (flash jatuh pada frame pertama setelah batas 2 dtk karena 2 dtk
  bukan kelipatan frame); dibandingkan per penanda dengan sumber, pipeline menggeser 0 ms (preview) dan 10 ms
  (render, efek trim di tengah frame) — < 1 frame (41,7 ms). Angka "drift 59,94 ms/mnt" dari ringkasan absolut adalah
  kemiringan pola gergaji aset, bukan drift pipeline. `media_bench.py` kini juga melaporkan `sync_vs_source`.
- Aset 30 menit, klip di 1700–1730 dtk: offset 0 ms → tidak ada drift setelah 28 menit.
- Kontrol positif detektor: audio digeser +100 ms → terdeteksi +98 ms; −40 ms → −40 ms.

**Subtitle** (kata disuntikkan ke transkrip dengan waktu diketahui; menguji timing subtitle & render, BUKAN Whisper):
33 render 9:16, **495/495 kata terdeteksi** (0 false negative), **0 onset palsu**, **0 frame terang** di luar jendela
tampil aplikasi (awal kata → akhir kata + 0,25 dtk, toleransi 1 frame). Selisih maks 33,3 ms @30 fps, 20,5 ms
@23,976, 16,7 ms @60 = ≤ 1 frame. Versi awal detektor (luma rata-rata) tidak mendeteksi teks kecil, dan jendela FP
awal (+0,7 dtk) lebih pendek dari aturan tampil aplikasi (0,75 dtk) — keduanya diperbaiki sebelum angka di atas.

**Trim:** durasi keluaran = 30,0 dtk (23,976: 30,03 = pembulatan frame). Timecode bawaan testsrc pada frame detik ke-5
keluaran menunjukkan 00:00:15.000 = sumber 10 + 5 dtk. **Concat beberapa rentang: feature gap.**

**Properti keluaran:** resolusi sesuai aspect (9:16 → 1080×1920; original = sumber); fps CFR = sumber; audio render
AAC 48 kHz. Perilaku yang dicatat: VFR → keluaran CFR 60 fps (r_frame_rate sumber), sehingga render 9:16 VFR lebih
lambat (RTF 0,43); sumber mono tetap mono di render; preview mode copy mempertahankan audio sumber (44,1 kHz).

**Inspeksi visual terbatas** (6 frame: 9:16 dari 16:9, HEVC, VFR, WebM; original 9:16 & 16:9): crop di tengah,
subtitle di posisi bawah, tanpa artefak blok/warna/komposisi yang terlihat. Pola uji sintetis — kualitas konten nyata
(wajah, teks halus, gerak cepat) BELUM dinilai.

## P5.5.2-C — Upload & siklus media (lokal)

`lifecycle_check.py`, server uvicorn sungguhan (SQLite, `EMBEDDED_WORKERS=0`), berkas video NYATA hasil encode
1080p 10 menit, 612.232.338 byte (sha256 `f6e25a28…`), loopback lokal (throughput tidak mewakili jaringan nyata):

| Skenario | Hasil |
|---|---|
| Socket diputus di ±27 % (di tengah potongan 16 MB) | server menyimpan 8,35 MB dari potongan itu; lanjut dari offset server (176.124.602) |
| Server SIGKILL di ±62 %, lalu dinyalakan ulang | offset server tetap 377.451.194; lanjut tanpa kehilangan data |
| Hasil | state `selesai`, 583,9 MB terkirim, **0 MB dikirim ulang**, **sha256 identik**, ffprobe 600,0 dtk, 7,4 dtk (78,6 MB/dtk loopback) |
| Batal (DELETE) di tengah upload | 204; HEAD sesudahnya 410; berkas `.part` 1 → 0 |
| Kuota unit 100 MB | ditolak saat dibuat (507) sebelum data dikirim. Catatan UX: pesan "0 dari 105 MB" (kuota MiB ditampilkan MB desimal) & tidak menyebut ukuran berkas (612 MB) |
| Lease/job aktif & TTL (`lifecycle.sweep(now = +30 hari)`, jam terkendali di lingkungan uji) | job analisis masih antre → media **tidak dihapus**; setelah job selesai → `expired: 1`, media terhapus |
| Folder yatim (umur 7 hari, tanpa proyek) | dihapus (1 folder, 1 MB) |

Jeda/lanjut di UI tidak diuji ulang (P4.3). Upload lewat jaringan nyata (latensi, putus Wi-Fi) belum diukur.

## Status kriteria (saat ini)

| # | Status | Catatan |
|---|---|---|
| W1–W6 | **BLOCKED** | menunggu push `805e4ca` & dispatch `bench=smoke` (tiny) lalu `full` (base, small) |
| M1 | PASS (sintetis, FFmpeg 6.1.1) | 10 aset × 3 operasi, 90/90 run |
| M2 | PASS (sintetis) | ≤ 8 ms absolut; ≤ 10 ms relatif sumber (23,976) |
| M3 | PASS (sintetis) | 0 ms di 1700–1730 dtk |
| M4 | PASS (sintetis) | 495/495, 0 FP, ≤ 1 frame |
| M5 | PASS (sintetis) | SSIM min 0,9886 (WebM) |
| M6 | PASS (sintetis, 4 vCPU) | render RTF maks 0,48 (1080p60 9:16); preview RTF maks 0,166 |
| M7 | PASS | puncak RSS FFmpeg maks 342 MB |
| M8 | PASS dengan catatan | VFR → CFR 60 fps; mono tetap mono |
| M1–M8 di image produksi (FFmpeg 7.1.5) | NOT VERIFIED | job `bench-media` |
| U1–U5 | PASS (lokal) | jaringan nyata & UI jeda: NOT VERIFIED |
| Kualitas visual konten nyata | NOT VERIFIED | belum ada korpus video nyata berlisensi |

### Feature gap (bukan kegagalan validasi)
Concat beberapa rentang/klip; voiceover terpisah; watermark/logo (tidak ada sama sekali — overlay yang ada hanya animasi rencana: zoom, keyword, card, chips, checklist).

## Run benchmark di runner (CI #15, dispatch `bench=smoke, bench_models=tiny, bench_runs=1`, `2bb0360`)

https://github.com/aiolaf9/video-editing/actions/runs/41174704790 — job CI wajib sukses; CI #14 (push yang sama):
semua job benchmark & heavy **skipped** (push tidak menjalankan benchmark).

**Whisper tiny: FAIL (bug workflow, bukan model/dataset).** Langkah "Korpus FLEURS" gagal dengan
`huggingface_hub.errors.OfflineModeIsEnabled`: `HF_HUB_OFFLINE=1` di tingkat workflow (agar regresi tidak mengunduh)
tidak ditimpa untuk langkah di host. Whisper tidak sempat berjalan. Perbaikan: `HF_HUB_OFFLINE: "0"` di env job
`bench-whisper` (commit lokal, menunggu persetujuan push).

**Media di image produksi (runner ubuntu-24.04, FFmpeg image = digest run #6), smoke n = 1, 4 aset: 12/12 run sukses.**

| Aset / operasi | Image: waktu (dtk), RTF, RSS MB | Lokal 6.1.1 median | A/V (ms) image | Subtitle image |
|---|---|---|---|---|
| Acuan 1080p30 — preview | 6,15; 0,102; 181 | 5,11 | 0 | – |
| Acuan — render 9:16 | 9,22; 0,307; 382 | 8,74 | 0 | 15/15, ≤ 33,3 ms, 0 FP |
| Acuan — original (SSIM 0,99966) | 6,66; 0,222; 368 | 6,56 | 0 | – |
| 9:16 sumber — preview / 9:16 / original | 3,99 / 6,70 / 6,74 | 3,70 / 6,53 / 6,47 | 0 | 15/15, 0 FP |
| WebM VP9/Opus — preview / 9:16 / original | 4,09 / 8,64 / 3,10 | 7,62 / 9,75 / 4,61 | +6 / +8 / +8 (= lokal) | 15/15, 0 FP |
| VFR — preview / 9:16 / original | 0,17 / 9,60 / 3,54 | 0,20 / 12,79 / 4,93 | 0 | 15/15, ≤ 16,7 ms, 0 FP |

Perbedaan hardware (runner vs sandbox) & versi FFmpeg: angka waktu **tidak** dipakai sebagai bukti optimasi.
Temuan: (1) **VFR** — FFmpeg 7.1.5 (image) mempertahankan 36 fps rata-rata pada render; FFmpeg 6.1.1 lokal menjadi
CFR 60 fps. Sinkronisasi 0 ms di keduanya. (2) Puncak RSS render di image 346–382 MB (lokal 304–327), < 700 MB.
(3) WebM render original: SSIM 0,9886 tetapi PSNR 21,6 dB — kemungkinan perbandingan frame bergeser 1 frame
(timestamp awal VP9); metode ukur kualitas untuk WebM NOT VERIFIED.
Mode full (10 aset × 3 run, termasuk 30 menit) di image: belum dijalankan.

## Target produk: tahap produksi video (hanya tahap yang tersedia diukur)

Target produk: produksi video lapangan dari ±4–12 jam menjadi ≤ 30 menit. P5.5.2 TIDAK mengklaim alur Field-to-Final
tersedia atau target 30 menit tercapai.

| Tahap | Tersedia? | Ukuran saat ini |
|---|---|---|
| Upload | ya (tus) | 612 MB dalam 7,4 dtk via loopback lokal — jaringan nyata NOT VERIFIED |
| Analisis AI (Whisper) | ya | BLOCKED (benchmark belum berjalan) |
| Penyusunan timeline | sebagian (kandidat klip otomatis `clipper.suggest`; satu rentang per render) | belum diukur |
| Preview | ya | RTF 0,003–0,17 (sintetis) |
| Review manual | UI ada; waktu manusia | tidak diukur (bukan metrik sistem) |
| Render final | ya (satu klip ≤ 10 menit) | RTF 0,10–0,48 (sintetis, 4 vCPU) |
| Export | unduh berkas render | belum diukur terpisah |

## Backlog (dicatat, TIDAK dikerjakan di P5.5.2)
- Pesan kuota upload membingungkan (MiB vs MB; ukuran berkas tidak disebut).
- Concat beberapa rentang/klip.
- Voiceover terpisah.
- Multi-watermark/logo.
- Review & Finalize: thumbnail, judul, subtitle dapat diedit, caption postingan, pengeditan manual.
- AI Field-to-Final untuk multi-footage.
- Mode frame rate render eksplisit (`-fps_mode`), agar keluaran VFR sama antar-versi FFmpeg.

## Run CI #17 (dispatch smoke tiny setelah `ad0f43b`)

https://github.com/aiolaf9/video-editing/actions/runs/41176036325 — job CI wajib sukses.
**Whisper tiny: FAIL — penyebab BARU, bukan mode offline.** Akses Hugging Face kini berhasil (unduhan FLEURS
id_id ±11 dtk, peringatan "unauthenticated requests" saja). Gagal saat menyusun `id_dialog`: `No files to concat` —
daftar ucapan pria/wanita kosong (kolom gender TSV FLEURS tidak terbaca seperti diasumsikan; set *clean* berhasil
disusun). Perbaikan:
- Baris `DIAGNOSTIK` di log: jumlah baris TSV, jumlah kolom, nama kolom (TSV FLEURS tanpa header; dipetakan dari
  urutan terdokumentasi bila 7 kolom), ucapan yang cocok dengan audio, sebaran gender mentah & terbaca, contoh baris
  dengan teks transkripsi diganti panjangnya saja.
- Fallback bila gender tidak terbaca: 24 ucapan berurutan, `speaker_source=unknown`, `two_speakers_verified=false`.
  Set ini HANYA untuk akurasi transkripsi audio gabungan; bukan percakapan dua pembicara terverifikasi. Evaluasi
  pergantian pembicara/diarization: NOT VERIFIED selama identitas pembicara tidak terverifikasi (aplikasi juga tidak
  memiliki fitur diarization).
- Audio gabungan kosong/senyap ditolak (durasi < 1 dtk atau RMS < −60 dB, termasuk −inf); jumlah ucapan & durasi tiap
  set dicatat di `corpus.json` dan baris `KORPUS` di log.
- Diuji lokal: `fetch()` asli dengan modul huggingface_hub tiruan (TSV 7 kolom) → diagnostik benar; `main()` dengan
  ucapan tanpa gender → fallback `unknown`, 24 ucapan, 105,6 dtk; ucapan senyap → berhenti dengan pesan jelas.

## Investigasi temuan media smoke

**PSNR WebM 21,6 dB vs SSIM 0,9886 — artefak metode ukur, bukan bug aplikasi.**
1. Trim aplikasi tepat: frame pertama render identik (PSNR ∞) dengan frame sumber #300 (t = 10,000 dtk); frame #299
   berbeda (3,88 dB).
2. Pengukur lama memotong sumber dengan `-ss` di input; sumber WebM memiliki `start_time` −0,007 dtk (pre-skip Opus)
   dan timestamp Matroska 1 ms (0,033 / 0,067 vs 1/30 dtk persis di render). Filter `psnr`/`ssim` memasangkan frame
   per timestamp, sehingga tiap frame ke-3 dibandingkan dengan frame tetangga (pola ±35 dB) dan rata-rata PSNR
   (berbasis MSE) jatuh.
3. Perbaikan pengukur (`quality()`): sumber dipotong dengan `trim` pada timestamp hasil decode, kedua aliran disampel
   ke grid 60 fps (pembulatan terdekat) sebelum dibandingkan, plus median PSNR per frame & jumlah frame < 30 dB.
   Hasil ulang pada render lokal YANG SAMA (`9e03b509c6a1.mp4`, 900 frame; FFmpeg 6.1.1 sandbox): WebM dari SSIM
   0,9886 / PSNR 21,6 dB menjadi **SSIM 0,9994 / PSNR 53,0 dB** (median 52,8; 0 frame < 30 dB); acuan tidak berubah
   (0,99966 / 55,3 dB); 23,976 fps 0,99965 / 55,2 dB. Frame dibandingkan 1800 dari 1800 yang diharapkan (30 dtk ×
   grid 60 fps) di ketiga render — tidak ada frame hilang. Ini **pengukuran lokal yang dikoreksi**, belum hasil
   benchmark media full di image produksi.
4. VFR: tetap 15 frame < 30 dB (= satu per flash; median 51,6 dB). Sumber VFR memberi jeda 2/60 dtk setelah frame
   flash; konversi ke CFR menduplikasi frame tersebut dan penempatan duplikat ambigu pada pembanding. Sinkronisasi
   0 ms; inspeksi visual tanpa artefak. Kualitas VFR via PSNR: NOT VERIFIED.

**Perilaku VFR FFmpeg 6.1.1 vs 7.1.5.** `render.build_command` tidak menetapkan `-fps_mode`/`-r` (preview menetapkan
`-fps_mode vfr`, `media.py:206`), sehingga mode frame keluaran render mengikuti bawaan versi FFmpeg: 6.1.1 → CFR
60 fps (r_frame_rate sumber), 7.1.5 → VFR rata-rata 36 fps. Sinkronisasi A/V & subtitle benar di keduanya; tidak ada
bukti bug. Backlog: tetapkan mode frame render secara eksplisit agar keluaran sama antar-versi FFmpeg.

## Whisper tiny ASLI — smoke (CI #19, `b2d6cc3`, `bench=smoke, tiny, runs=1`)

https://github.com/aiolaf9/video-editing/actions/runs/41176712848 — semua job sukses; artifact
`bench-whisper-tiny` (ID 13033186906). Runner GitHub `ubuntu-24.04`, di dalam image produksi (digest run #6), CPU int8,
`WHISPER_DEVICE=cpu`, cache model 75 MB. Korpus FLEURS split `test`, lisensi CC-BY 4.0.

**Diagnostik korpus:** id_id 687 baris TSV, 7 kolom (id, file_name, raw_transcription, transcription, phonemes,
num_samples, gender), 687 cocok dengan audio; kolom gender **"MALE" untuk seluruh 687 baris** (metadata gender FLEURS
Indonesia tidak informatif) → **fallback dipakai**: `id_dialog`/`id_noisy_snr10` = 24 ucapan berurutan,
`speaker_source=unknown`, `two_speakers_verified=false`. en_us 647 baris, gender 235 pria / 412 wanita.
Set: id_clean 20 ucapan 214,7 dtk; id gabungan 24 ucapan 268,9 dtk; id panjang 50 ucapan 597,0 dtk; en_clean 20 ucapan
198,9 dtk. Normalisasi teks: NFKC, huruf kecil, tanda baca dihapus; CER tanpa spasi.

| Set (tiny, n = 1) | WER | CER | RTF | Muat model (dtk) | Puncak RSS (MB) | Bahasa terdeteksi | Batas ucapan: median |selisih| awal/akhir; dalam 300 ms |
|---|---|---|---|---|---|---|---|
| id bersih (20 ucapan) | 44,2 % | 15,6 % | 0,080 | 1,67 (unduh+muat, run pertama) | 348 | id 19, ms 1 | – |
| id gabungan (bersih) | 68,4 % | 40,7 % | 0,086 | 0,64 | 513 | id | 140 / 180 ms; 15/24 & 11/24 |
| id gabungan + noise SNR 10 dB | 79,7 % | 55,0 % | 0,037 | 0,65 | 506 | id | 406 / 395 ms; 8/24 & 5/24 |
| id panjang (±10 mnt) | 58,6 % | 34,7 % | 0,064 | 0,60 | **835** | id | 201 / 210 ms; 26/50 & 22/50 |
| en bersih (20 ucapan) | 14,7 % | 7,8 % | 0,083 | 0,61 | 314 | en 20 | – |

Hasil deterministik (hash teks sama per set). Catatan: (1) tiny tidak memenuhi ambang kualitas yang ditetapkan untuk
small (W1 ≤ 25 %) — tiny hanya dicatat; (2) **puncak RSS tiny 835 MB pada audio 10 menit melebihi perkiraan admission
tiny 700 MB** (`_WHISPER_MEM`) — temuan untuk kriteria W5, belum diubah; (3) RTF noisy lebih rendah karena keluaran
lebih sedikit (WER naik), bukan lebih efisien; (4) pergantian pembicara/diarization: NOT VERIFIED (fallback);
(5) revisi model tercatat di artifact (`model_revision`), belum dibaca dari log.

## Whisper base & small ASLI — full (CI #20, `b2d6cc3`, `bench=full, base,small, runs=3`)

https://github.com/aiolaf9/video-editing/actions/runs/41177453255 — job base (12 mnt) & small (25 mnt) sukses, di
runner terpisah; artifact `bench-whisper-base` (13033608358), `bench-whisper-small` (13033194866). Image produksi,
CPU int8, cache model base 142 MB, small 464 MB. Korpus sama dengan smoke (fallback `speaker_source=unknown` untuk set
gabungan). Nilai = **median 3 run** (rentang); n = 3 → "p95" = maksimum. RINGKASAN di log hanya memuat WER run 1,
sehingga angka di bawah dihitung dari baris per-run.

| Model / set | WER | CER | RTF | Muat (dtk) | Puncak RSS MB | Bahasa |
|---|---|---|---|---|---|---|
| **small** id bersih (20 ucapan, 214,7 dtk) | **14,1 %** (14,1–14,1) | 4,5 % | 0,320 (0,308–0,324) | 1,28 (5,14 run 1: unduh) | 911 (maks 1113) | id 20/20 |
| small id gabungan bersih (268,9 dtk) | 24,1 % (23,1–26,8) | 10,4 % (9,6–13,0) | 0,309 | 1,28 | 1434 | id |
| small id gabungan + SNR 10 dB | 60,7 % (58,4–61,4) | 48,9 % (45,4–51,9) | 0,221 | 1,27 | 1429 | id |
| small id panjang (597 dtk) | 30,0 % (21,3–33,8) | 18,6 % (10,4–23,4) | 0,368 (0,326–0,398) | 1,27 | **1521** | id |
| small en bersih (198,9 dtk) | 8,2 % | 5,1 % | 0,311 | 1,26 | 856 | en 20/20 |
| **base** id bersih | 28,7 % | 9,0 % | 0,175 | 0,86 (1,68 run 1) | 400 (maks 422) | id 20/20 |
| base id gabungan bersih | 46,6 % | 26,4 % | 0,139 | 0,86 | 605 | id |
| base id gabungan + SNR 10 dB | 77,4 % | 60,8 % | 0,053 | 0,86 | 546 | id |
| base id panjang | 38,0 % (37,8–42,4) | 18,8 % (18,8–23,4) | 0,140 | 0,85 | **862** | id |
| base en bersih | 10,8 % (10,8–18,5) | 5,7 % (5,7–11,9) | 0,238 | 0,88 | 465 | en 20/20 |

Batas ucapan (run 1; median |selisih| awal/akhir; dalam 300 ms): small gabungan 180/110 ms, 15/24 & 12/24; small
panjang 197/220 ms, 33/50 & 25/50; small noisy 347/320 ms, 5/24 & 6/24; base gabungan 180/150 ms, 14/24 & 11/24;
base panjang 172/280 ms, 36/50 & 22/50. Acuan batas = awal/akhir suara per ucapan (silencedetect −35 dB) — perkiraan
dengan ketidakpastian puluhan ms (napas, jeda dalam ucapan); bukan timestamp kata manual.

**Bersih vs SNR 10 dB (audio sama):** small WER 24,1 % → 60,7 %; base 46,6 % → 77,4 %; tiny 68,4 % → 79,7 %. RTF
lebih rendah pada noisy karena keluaran lebih sedikit (bukan lebih efisien).

**Temuan:** (1) **tidak deterministik** — WER berbeda antar-run pada set panjang/noisy (small id panjang 21,3–33,8 %)
dan base en bersih; dugaan: temperature fallback faster-whisper (sampling acak bila ambang kualitas gagal) — NOT
VERIFIED; (2) **RAM vs perkiraan admission** (`_WHISPER_MEM`): tiny 835 > 700 MB (melebihi), base 862 / 900 MB (96 %),
small 1521 / 1600 MB (95 %) pada audio 10 menit — RSS naik dengan durasi audio, sehingga video lebih panjang (batas
analisis aplikasi) dapat melampaui perkiraan; belum diukur > 10 menit; (3) id panjang & gabungan jauh lebih buruk
daripada ucapan tunggal (14,1 % → 24–30 %).

### Status kriteria Whisper (ambang ditetapkan sebelum hasil)

| # | Kriteria | Status | Bukti |
|---|---|---|---|
| W1 | WER id bersih (small) ≤ 25 % | **PASS** | 14,1 % (3/3 run sama); gabungan 24,1 %; panjang 30,0 % (di luar definisi W1) |
| W2 | RTF small ≤ 0,5 (4 vCPU) | **PASS** | maks 0,398 |
| W3 | Batas ucapan: median ≤ 300 ms DAN ≥ 80 % dalam 300 ms | **FAIL** | median 110–220 ms (bersih) memenuhi; persentase hanya 50–66 % (bersih), 21–25 % (noisy). Ketidakpastian acuan silencedetect dicatat |
| W4 | Deteksi bahasa id (small) ≥ 95 % | **PASS** | 20/20 (tiny 19/20, 1 → ms) |
| W5 | Puncak RAM ≤ perkiraan admission | **FAIL** | tiny 835 > 700 MB; base/small dalam batas tetapi ≥ 95 % |
| W6 | Hasil deterministik | **FAIL** | WER berbeda antar-run (lihat temuan 1) |
| – | Pergantian pembicara / diarization | **NOT VERIFIED** | gender FLEURS id seluruhnya "MALE"; set gabungan = fallback `unknown`; aplikasi tidak punya diarization |

## Media full di image produksi (CI #20, `b2d6cc3`, `bench=full, runs=3`)

https://github.com/aiolaf9/video-editing/actions/runs/41177453255 — job "Benchmark preview & render (full)" sukses
(95 mnt), artifact `bench-media` (13036276009). Runner `ubuntu-24.04`, FFmpeg image produksi (digest run #6), pengukur
kualitas versi `b2d6cc3` (diselaraskan per waktu). **30 skenario × 3 run = 90/90, 0 error.** Median waktu (dtk) / RTF;
puncak RSS FFmpeg maks (MB).

| Aset | Preview | Render 9:16 + subtitle | Render original (SSIM / PSNR dB) |
|---|---|---|---|
| MP4 1080p30 (acuan) | 6,11 / 0,102 / 183 MB | 9,01 / 0,300 / 380 MB | 6,45 / 0,215 / 369 MB (0,99966 / 55,3) |
| MOV 1080p30 | 6,13 / 0,102 | 9,01 / 0,300 | 6,50 / 0,217 (0,99966 / 55,3) |
| 1080×1920 9:16 | 3,95 / 0,066 | 6,61 / 0,220 | 6,56 / 0,219 (0,99967 / 55,3) |
| 720p25 mono 44,1 kHz | 0,16 / 0,003 (copy) | 7,04 / 0,235 | 2,52 / 0,084 (0,99969 / 55,5) |
| 1080p 23,976 | 4,98 / 0,083 | 7,39 / 0,246 | 5,59 / 0,186 (0,99965 / 55,2) |
| 1080p60 | 11,76 / **0,196** | 16,09 / **0,536** / 398 MB | 11,85 / 0,395 (0,99958 / 54,3) |
| HEVC 1080p30 | 6,73 / 0,112 | 9,61 / 0,320 | 6,78 / 0,226 / **467 MB** (0,99934 / 53,4) |
| WebM VP9/Opus 720p | 4,09 / 0,068 | 8,50 / 0,283 | 3,01 / 0,100 (**0,99940 / 53,0**) |
| VFR (36 fps rata-rata) | 0,16 / 0,003 (copy) | 9,49 / 0,317 (keluaran 36 fps) | 3,47 / 0,116 (0,99954 / 54,3) |
| 720p 30 menit (klip 1700–1730 dtk) | 0,60 / 0,0003 (copy) | 8,43 / 0,281 | 3,01 / 0,100 (0,99971 / 55,6) |

- Sinkronisasi: semua penanda berpasangan (preview 30/30; 30 menit 900/900; render 15/15). Perubahan offset A/V oleh
  pipeline (relatif sumber): 0 ms untuk 8 aset; WebM +6/+8 ms; 23,976 fps 0 (preview) / 10 ms (render). Tidak ada
  drift setelah 28 menit. Semua < 1 frame.
- Subtitle: 30 render 9:16, 450/450 kata, 0 onset palsu, 0 frame terang di luar jendela tampil, maks 33,3 ms
  (30 fps) / 20,5 ms (23,976) / 16,7 ms (60 fps) = ≤ 1 frame.
- Variasi antar-run waktu ≤ 2 % (n = 3; "p95" = maksimum).
- Lokal (FFmpeg 6.1.1, sandbox Xeon 2,1 GHz 4 vCPU) vs runner image: sinkronisasi, subtitle & SSIM sama; waktu berbeda
  karena hardware & versi FFmpeg berbeda — bukan bukti optimasi. Perbedaan perilaku: render VFR = 36 fps (7.1.5) vs
  CFR 60 fps (6.1.1); RSS render image 326–467 MB vs lokal 289–342 MB.

## Status akhir kriteria P5.5.2

| # | Kriteria | Status | Bukti |
|---|---|---|---|
| W1 | WER id bersih small ≤ 25 % | **PASS** | 14,1 % |
| W2 | RTF small ≤ 0,5 | **PASS** | maks 0,398 |
| W3 | Batas ucapan ≥ 80 % dalam 300 ms | **FAIL** | 50–66 % (median 110–220 ms) |
| W4 | Deteksi bahasa id ≥ 95 % (small) | **PASS** | 20/20 |
| W5 | Puncak RAM ≤ perkiraan admission | **FAIL** | tiny 835 > 700 MB; base/small ≥ 95 % dari perkiraan |
| W6 | Deterministik | **FAIL** | WER berbeda antar-run pada set panjang/noisy |
| – | Diarization / dua pembicara | **NOT VERIFIED** | metadata gender FLEURS id tidak informatif; fitur tidak ada |
| M1 | Semua format diproses | **PASS** | 90/90 di image produksi |
| M2 | A/V ≤ 1 frame | **PASS** | ≤ 10 ms relatif sumber |
| M3 | Drift 30 menit ≤ 1 frame | **PASS** | 0 ms |
| M4 | Subtitle ≥ 95 %, ≤ 1 frame, tanpa FP | **PASS** | 450/450, 0 FP |
| M5 | SSIM render original ≥ 0,98 | **PASS** | min 0,99934 |
| M6 | Render RTF ≤ 1,0; preview RTF ≤ 0,2 | **PASS** | 0,536 / 0,196 (preview 1080p60 mendekati batas) |
| M7 | RAM render ≤ 700 MB | **PASS** | maks 467 MB |
| M8 | Properti keluaran | **PASS** | resolusi/fps/48 kHz sesuai; VFR antar-versi berbeda (backlog fps_mode) |
| U1–U5 | Upload & siklus media | **PASS (lokal)** | jaringan nyata & UI jeda: NOT VERIFIED |
| – | Kualitas visual konten nyata | **NOT VERIFIED** | hanya media sintetis |
| – | Alur Field-to-Final & target 30 menit | **NOT VERIFIED** | tidak tersedia (bukan cakupan P5.5.2) |

## Remediasi W3 / W5 / W6 / noise (investigasi; default produksi TIDAK diubah)

### W5 — memory admission (P0): akar masalah terukur
**Audit kode** (`app/transcribe.py` → `WhisperModel.transcribe`, faster-whisper 1.2.1, `vad_filter=True`):
1. `media.decode_audio` menahan stdout FFmpeg (bytes) + salinan float32 seluruh audio;
2. VAD Silero dijalankan atas **seluruh** audio (dipad);
3. `collect_chunks` + `np.concatenate` menyalin seluruh audio ucapan;
4. `self.feature_extractor(audio)` menghitung STFT + mel untuk **seluruh** audio ucapan sekaligus.
Perkiraan admission `_WHISPER_MEM` (tiny 700, base 900, small 1600 MB) hanya mencakup model — tidak ada komponen
yang bergantung durasi, padahal aplikasi tidak membatasi durasi video yang dianalisis (hanya ukuran upload 2 GB &
batas waktu job 6 jam ≈ 115 menit video).

**Pengukuran lokal tanpa model** (`bench/p552/whisper_mem_components.py`, jalur kode faster-whisper & aplikasi yang
sama, kasus terburuk seluruh audio = ucapan, puncak VmHWM di atas RSS awal):

| Durasi audio | Audio float32 | Setelah decode | Setelah VAD | Setelah fitur (total) |
|---|---|---|---|---|
| 1 mnt | 3,7 MB | +7 MB | +56 MB | **+96 MB** |
| 10 mnt | 36,6 MB | +73 MB | +249 MB | **+656 MB** |
| 30 mnt | 110 MB | +221 MB | +545 MB | **+1907 MB** |
| 60 mnt | 220 MB | +443 MB | +902 MB | **+3718 MB** |

≈ **62 MB per menit audio** (mayoritas STFT/mel atas seluruh audio). Cocok dengan runner: tiny 348 MB (ucapan
pendek) → 835 MB (10 menit). Konsekuensi: video 60 menit melampaui `mem_limit` worker-analisis 3 GB untuk model
**mana pun** → risiko OOM, bukan antre.

**Opsi perbaikan (usulan, belum diimplementasikan):**
| Opsi | Efek | Trade-off |
|---|---|---|
| A. Transkripsi per jendela (mis. 5–10 menit, dipotong di jeda VAD, timestamp di-offset) | memori tetap ≈ model + ±330–660 MB, tidak tumbuh dengan durasi | konteks antar-jendela hilang (`condition_on_previous_text`), perlu uji WER & timestamp di batas jendela |
| B. Admission sadar durasi: `kebutuhan = tetap(model) + per_menit × menit + margin`, durasi dari probe (sudah ada) | job besar menunggu/ditolak jelas, bukan OOM | tetap tidak bisa memproses video panjang di container 3 GB tanpa A |
| C. Batas durasi analisis (tolak > N menit dengan pesan jelas) | perlindungan cepat | membatasi produk |
Rekomendasi: A + B (C sebagai pengaman sementara). Angka `tetap(model)` & per menit nyata per model diukur di runner
(eksperimen `mem`), termasuk dua job serentak di container 3 GB (eksperimen `conc`).

### W6 — determinisme (P1): hipotesis dari audit kode
Aplikasi memakai default faster-whisper: `temperature=[0.0, 0.2, …, 1.0]` (fallback), `best_of=5`, `beam_size=5`,
`condition_on_previous_text=True`, fallback bila `compression_ratio > 2,4` atau `avg_logprob < −1,0`. Pada
temperature > 0 decoding memakai **sampling** (`num_hypotheses=best_of`, `sampling_temperature`) dan aplikasi tidak
memanggil `ctranslate2.set_random_seed` → hasil dapat berbeda antar-run tepat pada audio sulit (panjang/noisy), sesuai
pola di CI #20. Eksperimen `det`: mode default / seed tetap / `temperature=0`, 3 run per set, dengan jumlah fallback
dicatat dari log faster-whisper. Pertanyaan yang dijawab: apakah determinisme penuh dapat dijamin (temp0, satu mesin
& versi) atau hanya repeatability (seed).

### W3 — timestamp (P1): rencana pemisahan
Metrik W3 sebelumnya = batas UCAPAN (acuan silencedetect −35 dB), bukan timestamp KATA. Eksperimen `gt`: (1) sebaran
acuan pada ambang −30/−35/−40/−45 dB untuk 20 ucapan tunggal (kualitas acuan); (2) selisih kata pertama/terakhir vs
awal/akhir suara pada ucapan tunggal. Timestamp kata terverifikasi membutuhkan sampel beranotasi manual — belum
tersedia (NOT VERIFIED); alignment tambahan (mis. forced alignment wav2vec2/MMS) hanya diusulkan bila (1)–(2)
menunjukkan kesalahan nyata, karena menambah dependensi besar.

### Audio lapangan (E)
Eksperimen `noise` (small, decoding temp0 agar perbedaan bukan karena sampling) pada set SNR 10 dB: asli, `afftdn`,
highpass+lowpass, `afftdn`+loudnorm, dan ambang VAD 0,35/0,65 — WER, RTF, RSS, waktu praproses. Mekanisme koreksi
transkrip oleh pengguna: belum ada di aplikasi (backlog, bukan P5.5.2).

### Cara menjalankan
CI manual `bench=remediasi` (job `bench-remediation`, matrix mem/conc/det/gt/noise, tiap eksperimen di runner sendiri,
image produksi, korpus FLEURS). Push biasa tidak menjalankannya.
