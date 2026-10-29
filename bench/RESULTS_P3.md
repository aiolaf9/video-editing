# Hasil benchmark P3 (final)

**Lingkungan:** 4 vCPU, 16 GB RAM, tanpa GPU, PostgreSQL 16. Setiap worker berjalan sebagai **container Docker
sungguhan** dengan `--cpus`, `--memory` (tanpa swap) dan `--cpu-shares` seperti `docker-compose.yml`
(image kosong + sistem file host di-mount read-only karena Docker Hub membatasi unduhan image di lingkungan uji).
FFmpeg sungguhan untuk preview, pelacakan wajah, dan render.

**Penting:** Whisper diganti beban CPU tetap (x264, 8 frame 720p per detik video, jumlah thread = yang akan dipakai
Whisper). Angka analisis di bawah **bukan** performa Whisper nyata. Ukur Whisper asli dengan
`bench/whisper_bench.py` di server yang memiliki model (lihat bagian akhir).

- *Lama* = commit `a19e135` (sebelum P3): antrean `berat` FIFO, FFmpeg/Whisper memakai semua core, tanpa batas container
  (sama dengan compose lama).
- *Baru* = P3 final. Worker baru: analisis `@cpus 3 / 3 GB / shares 256`, render `@cpus 3 / 1,5 GB / shares 1024`,
  io `@0,5 / 512 MB / 512`. `A+R` = worker analisis yang ikut merender saat tidak ada analisis.
- Jumlah & jadwal job identik untuk semua konfigurasi; n = jumlah run (rata-rata). Satuan detik.
- *siap-jalan/core p95* = thread yang siap jalan per core (ukuran oversubscription CPU).
- *bocor* = proses FFmpeg tertinggal + job menggantung + job yang dijalankan dua kali (harus 0).

Skenario: **hol** = 2 analisis video 120 dtk (unit A), 3 dtk kemudian 3+3 render 8 dtk (unit B, C).
**fair** = unit A 12 render sekaligus, 2 dtk kemudian unit B 2 render. **mixed** = 3 unit, 2 analisis + 14 render
datang bertahap selama 15 dtk. **cancel** = beban hol, 12 dtk kemudian semua job unit A dibatalkan.

### hol

| Konfigurasi | n | render tunggu p50 | p95 | analisis selesai | makespan | job/mnt | CPU % | siap-jalan/core p95 | RAM worker maks MB | OOM | bocor |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Lama berat:2 (compose lama) | 2 | 35.7 | 40.6 | 34.9 | 47.3 | 10.2 | 87.9 | 4.0 | 476.5 | 0 | 0 |
| Lama berat:3 | 2 | 12.4 | 28.5 | 42.1 | 42.5 | 11.3 | 93.6 | 5.1 | 464.5 | 0 | 0 |
| 1A+1R | 2 | 7.3 | 17.3 | 57.2 | 57.4 | 8.4 | 72.2 | 2.8 | 401 | 0 | 0 |
| 1A+2R (rekomendasi P3 awal) | 2 | 5.2 | 10.2 | 56.5 | 56.9 | 8.5 | 72.2 | 3.4 | 394.5 | 0 | 0 |
| 2A+1R | 2 | 9.1 | 22.1 | 45.0 | 45.0 | 10.7 | 91.8 | 3.9 | 401.5 | 0 | 0 |
| Hibrida 1(A+R)+1R | 2 | 7.6 | 17.8 | 56.8 | 56.9 | 8.4 | 71.9 | 2.6 | 401 | 0 | 0 |
| Hibrida 1(A+R)+2R | 2 | 5.4 | 10.1 | 59.2 | 59.4 | 8.1 | 71.2 | 3.5 | 394 | 0 | 0 |
| **Hibrida 2(A+R)+1R (rekomendasi)** | 2 | 9.5 | 21.7 | 44.6 | 45.0 | 10.7 | 91.3 | 3.9 | 401.5 | 0 | 0 |
| Kode final, hibrida 2+1 (konfirmasi) | 1 | 9.3 | 21.5 | 42.5 | 42.7 | 11.2 | 93.4 | 4.0 | 402 | 0 | 0 |

### fair

| Konfigurasi | n | unit kecil tunggu p50 | p95 | unit besar selesai | makespan | job/mnt | CPU % | siap-jalan/core p95 | OOM | bocor |
|---|---|---|---|---|---|---|---|---|---|---|
| Lama berat:2 (compose lama) | 2 | 20.7 | 21.2 | 23.2 | 26.9 | 31.2 | 86.8 | 3.9 | 0 | 0 |
| Lama berat:3 | 2 | 18.5 | 20.2 | 22.7 | 26.1 | 32.2 | 92.5 | 5.0 | 0 | 0 |
| 1A+1R | 2 | 1.5 | 6.9 | 39.8 | 40.1 | 21.0 | 60.8 | 1.6 | 0 | 0 |
| 1A+2R (rekomendasi P3 awal) | 2 | 2.2 | 6.1 | 28.0 | 28.1 | 29.9 | 86.5 | 3.0 | 0 | 0 |
| 2A+1R | 2 | 1.7 | 7.4 | 41.0 | 41.1 | 20.5 | 60.5 | 1.8 | 0 | 0 |
| Hibrida 1(A+R)+1R | 2 | 2.1 | 5.6 | 28.6 | 28.9 | 29.1 | 85.6 | 2.9 | 0 | 0 |
| Hibrida 1(A+R)+2R | 2 | 3.0 | 7.4 | 26.6 | 27.1 | 30.9 | 90.8 | 3.6 | 0 | 0 |
| **Hibrida 2(A+R)+1R (rekomendasi)** | 2 | 2.9 | 6.8 | 27.1 | 27.6 | 30.4 | 92.2 | 3.8 | 0 | 0 |
| Kode final, hibrida 2+1 (konfirmasi) | 1 | 2.9 | 6.8 | 27.2 | 27.4 | 30.7 | 93.4 | 4.5 | 0 | 0 |

### mixed

| Konfigurasi | n | render tunggu p50 | p95 | analisis selesai | fairness (Jain) | makespan | job/mnt | CPU % | RAM mesin MB | siap-jalan/core p95 | OOM | bocor |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Lama berat:2 (compose lama) | 2 | 38.0 | 41.5 | 38.7 | 0.9 | 59.3 | 16.2 | 89.2 | 1739.0 | 4.1 | 0 | 0 |
| Lama berat:3 | 2 | 23.7 | 38.6 | 51.0 | 1.0 | 58.4 | 16.5 | 94.9 | 2107.5 | 5.2 | 0 | 0 |
| 1A+1R | 2 | 11.5 | 28.6 | 63.3 | 0.9 | 65.4 | 14.7 | 80.4 | 1546.5 | 3.0 | 0 | 0 |
| 1A+2R (rekomendasi P3 awal) | 2 | 7.6 | 18.5 | 67.4 | 0.7 | 69.7 | 13.8 | 79.5 | 1740.0 | 3.8 | 0 | 0 |
| 2A+1R | 2 | 15.1 | 37.3 | 55.6 | 0.9 | 57.2 | 16.8 | 92.2 | 1813.0 | 4.1 | 0 | 0 |
| Hibrida 1(A+R)+1R | 2 | 12.2 | 30.2 | 66.2 | 0.9 | 68.2 | 14.1 | 79.6 | 1501.0 | 3.0 | 0 | 0 |
| Hibrida 1(A+R)+2R | 2 | 5.8 | 17.3 | 66.3 | 0.7 | 68.7 | 14.0 | 79.2 | 1787.5 | 3.9 | 0 | 0 |
| **Hibrida 2(A+R)+1R (rekomendasi)** | 2 | 14.2 | 33.1 | 54.5 | 0.8 | 57.0 | 16.9 | 92.4 | 1788.0 | 4.1 | 0 | 0 |
| Kode final, hibrida 2+1 (konfirmasi) | 1 | 14.6 | 35.9 | 57.1 | 0.8 | 59.2 | 16.2 | 93.8 | 1915.0 | 4.0 | 0 | 0 |

### cancel

| Konfigurasi | n | semua job A berhenti (dtk) | job berjalan berhenti (dtk) | render B/C tunggu p50 | makespan | bocor |
|---|---|---|---|---|---|---|
| Lama berat:2 (compose lama) | 1 | 0.6 | 0.6 | 13.4 | 23.9 | 0 |
| Lama berat:3 | 1 | 0.6 | 0.6 | 9.3 | 20.8 | 0 |
| 1A+1R | 1 | 0.4 | 0.4 | 7.1 | 21.1 | 0 |
| 1A+2R (rekomendasi P3 awal) | 1 | 0.3 | 0.3 | 5.4 | 17.0 | 0 |
| 2A+1R | 1 | 0.4 | 0.4 | 9.3 | 23.7 | 0 |
| Hibrida 1(A+R)+1R | 1 | 0.3 | 0.3 | 7.3 | 19.5 | 0 |
| Hibrida 1(A+R)+2R | 1 | 1.2 | 1.2 | 5.0 | 16.9 | 0 |
| **Hibrida 2(A+R)+1R (rekomendasi)** | 1 | 1.1 | 1.1 | 8.6 | 19.8 | 0 |
| Kode final, hibrida 2+1 (konfirmasi) | 1 | 1.2 | 1.2 | 9.3 | 20.9 | 0 |

**Catatan fairness (mixed):** indeks Jain dihitung dari rata-rata tunggu render per unit. Unit C mengirim 6 render,
unit lain 4. Penjadwal adil melayani unit ringan lebih cepat dan kelebihan unit C terakhir, sehingga Jain turun
walau tidak ada unit yang dirugikan. Ukuran yang lebih bermakna, yaitu rata-rata tunggu **unit terburuk**:
lama `berat:2` 38,2 dtk, lama `berat:3` 26,8 dtk, hibrida 2+1 23,4 dtk.

## Kesimpulan

| | Lama `berat:3` | 1A+2R (P3 awal) | **Hibrida 2(A+R)+1R** |
|---|---|---|---|
| Throughput saat banyak analisis (hol, job/mnt) | 11,3 | 8,5 | **10,7** |
| Throughput campuran (mixed, job/mnt) | 16,5 | 13,8 | **16,9** |
| Unit kecil di belakang unit besar (fair, tunggu p50) | 18,5 | 2,2 | **2,9** |
| Render di belakang analisis (hol, tunggu p50 / p95) | 12,4 / 28,5 | **5,2 / 10,2** | 9,5 / 21,7 |
| Oversubscription CPU (siap-jalan/core p95, mixed) | 5,2 | 3,8 | 4,1 |

- Hibrida 2+1 mengembalikan throughput ke tingkat sebelum P3 (bahkan sedikit di atasnya pada beban campuran)
  dengan tetap mempertahankan keadilan antar-unit dan render yang tidak tertahan analisis.
- Trade-off yang tidak bisa dihindari pada 4 vCPU: dengan satu worker render khusus, render pendek di tengah
  analisis menunggu sedikit lebih lama (p95 ±22 dtk) dibanding dua worker render khusus (p95 ±10 dtk), tetapi
  konfigurasi dua render khusus kehilangan ±25% throughput saat banyak analisis. Hibrida dipilih sebagai bawaan;
  1A+2R tersedia bila responsivitas render lebih penting.
- 56 run + 4 run konfirmasi: 0 OOM, 0 proses FFmpeg tertinggal, 0 job ganda, 0 job menggantung.
- Pembatalan di bawah beban: semua job berhenti dalam 0,3–1,2 dtk (lama: 0,6 dtk).

## Biaya database penjadwalan & monitoring (`bench/db_bench.py`)

PostgreSQL, 200.000 job riwayat (30 hari) + 1.000 menunggu, 50 unit, 12 worker terdaftar. Latensi p50 / p95 (ms):

| Operasi | Seberapa sering | Dengan indeks P3 final | Tanpa indeks `(queue, finished)` |
|---|---|---|---|
| ambil + selesaikan job | per job | 6,4 / 8,6 | 6,5 / 8,0 |
| ambil saat antrean kosong | tiap ±1 dtk per worker menganggur | 2,5 / 3,3 | 2,7 / 4,0 |
| laporan worker | tiap 10 dtk per worker | 0,7 / 1,2 | 0,9 / 1,4 |
| cek kapasitas antrean | per pengajuan job | 2,0 / 2,5 | 2,0 / 2,6 |
| snapshot monitoring | tiap 10 dtk saat Status dibuka / scrape | **9,9 / 13,3** | 53,1 / 69,5 |

Membaca monitoring tidak lagi menulis ke database (dulu: DELETE worker lama di setiap baca), dan jumlah query
per snapshot tetap (diuji: tidak bertambah dengan jumlah job/unit).

## Validasi Docker

- Di dalam container `--cpus 2 --memory 1g`, aplikasi membaca batas cgroup: 2 CPU, 1024 MB.
- Worker analisis di container 800 MB (Whisper `small` butuh ±2,1 GB termasuk cadangan): worker **menunda**
  dengan alasan "RAM tersedia 751 MB < kebutuhan 2112 MB", job tetap di antrean, container tidak di-OOM-kill,
  `docker stop` keluar bersih (exit 0).
- RAM puncak per container worker selama benchmark: 290–420 MB (batas 1,5–3 GB).

## Whisper asli

Pemeriksaan kapasitas lingkungan uji (`python bench/whisper_bench.py --check --model small`): faster-whisper
1.x & ctranslate2 4.8.4 terpasang, RAM 14,5 GB, disk 20 GB, **model belum ada di cache dan huggingface.co tidak
dapat dijangkau dari lingkungan ini**, jadi Whisper asli belum diukur. Di server (dengan rekaman ucapan nyata):

```bash
python bench/whisper_bench.py --check --model small
python bench/whisper_bench.py --model small --media contoh-ucapan.mp4 --threads 2,4 --parallel 1,2 --out whisper.json
# lalu antrean dengan Whisper asli:
python bench/queue_bench.py --code . --db "postgresql+psycopg://..." --scenario mixed --real-whisper --docker \
    --workers "analisis+render:2@3/3g/256,render:1@3/1500m/1024,io:1@0.5/512m/512"
```
(`--real-whisper` memakai video uji sintetis; untuk angka yang mewakili, ganti `long.mp4` di cache benchmark
dengan rekaman ucapan nyata.)

## Cara mengulang

```bash
# image kosong untuk mode --docker
mkdir -p /tmp/empty && tar -C /tmp/empty -cf - . | docker import - ve-hostroot:empty
# satu run (database KOSONG khusus benchmark):
python bench/queue_bench.py --code . --db "postgresql+psycopg://.../bench" --scenario mixed --docker \
    --workers "analisis+render:2@3/3g/256,render:1@3/1500m/1024,io:1@0.5/512m/512" --out runs/H21-mixed-1.json
# kode sebelum P3: git worktree add /tmp/before a19e135, lalu --code /tmp/before --workers "berat:3,unduh:1"
python bench/compare.py runs "Hibrida=H21" "Lama=B3"
```
