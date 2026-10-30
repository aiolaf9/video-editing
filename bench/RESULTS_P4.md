# Hasil benchmark P4 (upload & preview)

Mesin uji: 4 vCPU (Intel Xeon 2,1 GHz), 16 GB RAM, tanpa GPU. Angka = satu run per baris (diulang beberapa kali
selama pengembangan; variasi ±3%).

## Preview (`bench/preview_bench.py`)

Resep lama = `make_preview` sebelum P4 (commit `0b39237`), dijalankan lewat `run_ffmpeg` yang sama dengan anggaran
4 thread. SSIM = 60 dtk pertama terhadap sumber yang di-scale (lanczos) ke ukuran preview.

| Input | Resep | Waktu | Kecepatan | CPU | RAM FFmpeg puncak | Bitrate preview | SSIM |
|---|---|---|---|---|---|---|---|
| 1080p30, 5 mnt, 12 Mbps (setara footage HP) | lama | 48,1 dtk | 1,00× | 312% | 171 MB | 343 kbps | 0,9758 |
| | **baru** (transcode) | 46,4 dtk | 1,04× | 321% | 201 MB | 375 kbps | 0,9758 |
| | baru, analisis ulang (cache) | **0,15 dtk** | 320× | – | 0 | – | sama |
| | baru, mode video panjang (360p, dekode cepat) | 29,9 dtk | 1,61× | 282% | 178 MB | 306 kbps | 0,9737 |
| 720p30, 5 mnt, 3 Mbps (setara unduhan/ekspor medsos) | lama | 33,0 dtk | 1,00× | 226% | 141 MB | 409 kbps | 0,9853 |
| | **baru** (remux, tanpa transcode) | **0,81 dtk** | 40× | – | 53 MB | 2.938 kbps | 1,0 |
| | baru, analisis ulang (cache) | 0,12 dtk | 275× | – | 0 | – | sama |

Audit baseline: `make_preview` 1080p30 5 mnt = **49,2–50,7 dtk** (cocok dengan audit 50,4 dtk).

**Mengapa transcode 1080p tidak bisa 2× pada kualitas yang sama di CPU ini.** Dekode H.264 1080p saja = 27–32 dtk
(61% dari seluruh kerja CPU preview, diukur dengan rusage). Semua variasi yang diuji pada input & kualitas sama:

| Variasi (5 mnt 1080p) | Waktu | SSIM | Bitrate |
|---|---|---|---|
| baseline | 50,1 dtk | 0,9758 | 343 kbps |
| dekoder 8 thread + tanpa deblocking + superfast | 46,6 dtk | 0,9759 | 1.023 kbps |
| dekoder 8 thread + tanpa deblocking + veryfast CRF 27 | 47,5 dtk | 0,9735 | 504 kbps |
| + lewati frame non-referensi (±20 fps) | 35,0 dtk | 0,9748 | 1.130 kbps |
| skala fast_bilinear | 45,4 dtk | **0,9517** (turun) | – |

Percepatan besar hanya datang dari tidak melakukan transcode: cache (analisis ulang), remux untuk sumber yang sudah
ramah browser, atau dekoder hardware (`PREVIEW_HWACCEL`, belum diuji: tidak ada GPU).

**Trade-off remux:** preview mengikuti bitrate sumber (±3 Mbps, bukan ±0,4 Mbps) dan pelacakan wajah membaca 720p:
4,7 → 10,7 dtk. Tahap sebelum Whisper tetap jauh lebih cepat: 33,0 + 4,7 = 37,7 dtk → 0,8 + 10,7 = 11,5 dtk.

**Sinkron & timeline** (`tests/test_p4_preview.py`): kedipan & beep pada sumber dengan audio tertunda 200 ms dan
start_time 1,5 dtk: waktu kedipan preview = sumber (≤ 1 frame), waktu beep = sumber (≤ 25 ms), jarak gambar-suara
tetap, durasi sama (≤ 0,15 dtk) untuk mode transcode, remux, remux+start_time, dan video panjang (VFR).
Render final tetap dari sumber 1080p dengan timestamp yang sama.

## Upload (`bench/upload_bench.py`)

Server uvicorn sungguhan (proses baru per run, loopback), angka diukur pada proses server (`/proc/<pid>`).

| Ukuran | Metode | Waktu | MB/dtk | Ditulis ke disk | CPU server | RAM server puncak | Dikirim ulang bila putus di 50% |
|---|---|---|---|---|---|---|---|
| 100 MB | lama (multipart) | 1,40 dtk | 72 | 200 MB | 1,4 dtk | 96 MB | 50 MB (ulang dari nol) |
| | tus | 0,81 dtk | 124 | 100 MB | 0,5 dtk | 98 MB | – |
| | tus, putus 50% lalu lanjut | 1,88 dtk | 53 | 100 MB | 0,6 dtk | 98 MB | **0,04 MB** |
| 500 MB | lama | 6,61 dtk | 76 | 1.000 MB | 6,3 dtk | 96 MB | 250 MB |
| | tus | 2,99 dtk | 168 | 501 MB | 1,9 dtk | 98 MB | – |
| | tus, putus 50% lalu lanjut | 4,35 dtk | 115 | 501 MB | 2,6 dtk | 98 MB | **0,04 MB** |
| 2 GB | lama | **gagal (507)** | – | 2.048 MB | 17,0 dtk | 96 MB | 1.024 MB |
| | tus | 15,2 dtk | 135 | 2.050 MB | 10,0 dtk | 98 MB | – |
| | tus, putus 50% lalu lanjut | 16,5 dtk | 124 | 2.050 MB | 12,4 dtk | 98 MB | **0 MB** |

- Upload lama 2 GB ditolak "penyimpanan penuh" pada kondisi disk yang sama di mana tus berhasil: jalur lama butuh
  ruang 2× ukuran file sementara (spool multipart + salinan).
- RAM server datar (±2 MB) untuk semua ukuran pada kedua metode; tus menulis ke disk per 1 MB.
- Putus tanpa checksum (klien tus umum): byte yang sudah diterima disimpan. Dengan checksum (halaman web di HTTPS):
  potongan yang terputus dibuang dan dikirim ulang (maks. 1 potongan, 2–32 MB).

## Uji browser (Chromium, unggahan dibatasi 2 MB/dtk lewat DevTools)

| Langkah | Hasil |
|---|---|
| Mulai upload 30 MB | progres & kecepatan dari byte yang diterima server (20%, 1,8 MB/dtk, sisa ±12 dtk) |
| Jeda → Lanjutkan | berhenti di 6,3 MB, lanjut dari 6,3 MB |
| Offline | "Koneksi internet terputus. Upload dilanjutkan otomatis saat online." (48%) |
| Online lagi | lanjut otomatis dari 14,7 MB |
| Refresh halaman | "Belum selesai · 48% — Pilih file yang sama untuk melanjutkan" |
| Pilih file yang sama | lanjut dari 48% → "Selesai & terverifikasi" |
| Integritas | SHA-256 file di server = file asli; tidak ada sisa `.part`; tidak ada error JavaScript |
