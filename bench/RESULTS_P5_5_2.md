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
