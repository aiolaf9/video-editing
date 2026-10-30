# Panduan Deploy ke Server Cloud

Panduan memasang aplikasi di server cloud supaya bisa dipakai banyak unit lewat browser, dengan login.

## Gambaran

```
Pengguna (browser) ──HTTPS──▶ Caddy ──▶ web (2 proses)  ──▶ PostgreSQL (data + antrean)
                                                                  │
                    ┌─────────────────────────────┬───────────────┴──────────┐
                    ▼                             ▼                          ▼
          worker-analisis × N            worker-render × N             worker-io
          (Whisper, wajah, preview)      (FFmpeg)                      (unduhan dari link)
```

- **web**: halaman & API, ringan. Tetap cepat walau banyak render berjalan.
- **worker-analisis / worker-render / worker-io**: tiga antrean terpisah. worker-render khusus render, sehingga
  transkripsi video panjang tidak pernah menahan render pendek; worker-analisis mengutamakan analisis dan ikut
  merender saat tidak ada analisis (konfigurasi "hibrida", paling seimbang di benchmark: `bench/RESULTS_P3.md`).
- Di setiap antrean, unit yang sedang paling sedikit dilayani didahulukan (adil antar-unit); render pendek
  didahulukan dari render panjang, dan job yang menunggu lama naik prioritasnya (tidak ada yang tertahan selamanya).
- Semua pekerjaan tersimpan di database: bila worker/server restart, pekerjaan diulang otomatis.

## 1. Pilih server

Untuk ratusan pengguna (puluhan aktif bersamaan), mulai dari:

| Kebutuhan | Rekomendasi awal |
|---|---|
| CPU / RAM | **8 vCPU, 16 GB RAM** |
| Disk | 200 GB SSD (media dihapus otomatis 24–72 jam; lihat "Penyimpanan media sementara") |
| OS | Ubuntu 22.04 / 24.04 |

Bisa dari penyedia mana pun (lokal maupun global). Bisa naik kelas kapan saja tanpa mengubah aplikasi.

## 2. Pasang Docker di server

```bash
curl -fsSL https://get.docker.com | sh
```

## 3. Ambil aplikasi & isi pengaturan

```bash
git clone -b claude/dreamy-carson-0mnbh2 https://github.com/aiolaf9/video-editing.git /opt/video-editing
```

```bash
cd /opt/video-editing && cp .env.example .env
```

Buka `.env` (misalnya `nano .env`) lalu isi minimal:

- `DOMAIN`: domain/subdomain yang diarahkan ke IP server (record DNS **A**), mis. `video.kantor.id`
- `POSTGRES_PASSWORD`: password acak, buat dengan `openssl rand -hex 24`
- `ADMIN_EMAIL` dan `ADMIN_PASSWORD`: akun Admin pertama

Belum punya domain? Isi `DOMAIN=:80` dan `COOKIE_SECURE=0`, lalu buka `http://IP-SERVER`.
(Untuk pemakaian sungguhan tetap disarankan memakai domain + HTTPS.)

## 4. Jalankan

```bash
docker compose up -d --build
```

Build pertama butuh beberapa menit. Cek status:

```bash
docker compose ps
```

Buka `https://DOMAIN-ANDA`, login dengan akun Admin, lalu:

1. **Admin → Unit**: buat unit (Humas, Pemasaran, dll.), atur kuota & guidebook gaya tiap unit.
2. **Admin → Pengguna**: buat akun untuk tiap anggota tim (Editor) dan penanggung jawab unit (Admin Unit).
3. **Admin → Pengaturan AI**: isi API key (mis. Gemini) bila ingin tombol 🤖 aktif.
4. Ganti password Admin di menu **Akun saya**.

Saat analisis pertama, model Whisper (~500 MB) diunduh sekali dan disimpan untuk seterusnya.

## Peran pengguna

| Peran | Bisa |
|---|---|
| **Admin** | Semua unit, semua pengguna, pengaturan AI, statistik |
| **Admin Unit** | Pengguna & guidebook unitnya, semua proyek unitnya (termasuk menghapus) |
| **Editor** | Membuat & mengedit proyek di unitnya; menghapus proyek miliknya sendiri |

Proyek satu unit tidak terlihat oleh unit lain.

## Mengatur kapasitas

Ubah di `.env`, lalu jalankan ulang `docker compose up -d`:

| Gejala | Tindakan |
|---|---|
| Antrean **render** sering panjang (Admin → Status, kolom "Tertua menunggu") | Naikkan `RENDER_WORKERS` (±1 per 2 vCPU) |
| Antrean **analisis** sering panjang | Naikkan `ANALYSIS_WORKERS` (butuh ±2–3 GB RAM per worker dengan model `small`) |
| Worker berstatus "menunggu resource" | RAM server kurang: kurangi worker analisis, pakai `WHISPER_MODEL=base`, atau tambah RAM |
| Pengguna mendapat pesan "antrean unit penuh" | Naikkan `MAX_QUEUED_PER_UNIT`, atau tambah worker bila antrean memang panjang |
| Transkrip terasa lama | `WHISPER_MODEL=base` (lebih cepat, sedikit kurang akurat), atau server dengan vCPU lebih banyak |
| Disk cepat penuh | Turunkan `MEDIA_RETENTION_HOURS`/`MEDIA_MAX_HOURS`, turunkan `MAX_UPLOAD_MB`, atur kuota media per unit |
| Satu unit memakai terlalu banyak | Atur kuota render/AI per unit di **Admin → Unit** |

Lihat juga "Kelas server" di bawah untuk nilai awal per ukuran server.

Uji beban di lingkungan pengembangan (4 vCPU, 4 proses web, 2 worker, PostgreSQL):
200 pengguna aktif + 40 render diajukan bersamaan → 40/40 render selesai, 0 error,
waktu respons halaman median 28 ms (p95 90 ms).

## Kelas server

Server **tanpa GPU** (CPU saja) adalah bawaan. Nilai di bawah adalah titik awal; pantau **Admin → Status**
lalu sesuaikan. `cpus` adalah batas keras per container; `cpu_shares` membuat render didahulukan dari
analisis saat CPU diperebutkan, tetapi analisis tetap boleh memakai CPU yang kosong.

| Kelas | Web | worker-analisis (analisis, lalu render) | worker-render | Pengaturan `.env` |
|---|---|---|---|---|
| **Kecil** 4 vCPU / 8 GB | `WEB_WORKERS=2` | 2 × (cpus 3, 2,5 GB) | 1 × (cpus 3, 1,5 GB) | `WHISPER_MODEL=base`, `MAX_QUEUED_PER_UNIT=15`, `MAX_QUEUE_DEPTH=100` |
| **Menengah** 8 vCPU / 16 GB | `WEB_WORKERS=2` | 2 × (cpus 4, 3 GB) | 2 × (cpus 3, 1,5 GB) | `WHISPER_MODEL=small`, `MAX_RUNNING_PER_UNIT=2` |
| **Besar** 16 vCPU / 32 GB | `WEB_WORKERS=4` | 3 × (cpus 4, 3,5 GB) | 3 × (cpus 3, 1,5 GB) | `MAX_RUNNING_PER_UNIT=2`, `DB_POOL_SIZE=5`, cek `max_connections` PostgreSQL |
| **GPU NVIDIA** (≥ 8 GB VRAM) | sama | 1 per GPU | sesuai CPU | `WHISPER_DEVICE=auto`, perlu NVIDIA Container Toolkit (`gpus: all`); belum diuji di lingkungan pengembangan |

Pilihan lain bila prioritas berbeda (terukur di 4 vCPU, `bench/RESULTS_P3.md`):
- **Render secepat mungkin**, analisis boleh lebih lambat: `ANALYSIS_WORKERS=1`, `RENDER_WORKERS=2`
  (tunggu render p95 ±10 dtk vs ±22 dtk, tetapi throughput saat banyak analisis turun ±25%).
- **RAM sangat terbatas**: `ANALYSIS_WORKERS=1` (satu model Whisper di memori), `RENDER_WORKERS=1`.
- Setiap worker-analisis memuat model Whisper sendiri: RAM analisis = jumlah worker × kebutuhan model.
  Ukur dengan `bench/whisper_bench.py` di server sebelum menambah worker analisis.

Isi `.env` sesuai kolom: `ANALYSIS_WORKERS`, `ANALYSIS_CPUS`, `ANALYSIS_MEM`, `RENDER_WORKERS`, `RENDER_CPUS`, `RENDER_MEM`.
Total `cpus` boleh melebihi jumlah vCPU (contoh kelas kecil: 3 + 3 pada 4 vCPU): batas itu mencegah satu worker
memakai semua core (selalu tersisa CPU untuk web & database), sedangkan `cpu_shares` mengatur siapa didahulukan.

Perlindungan yang selalu aktif:
- **RAM**: worker tidak mengambil job bila RAM host tersedia kurang dari kebutuhan job + `MEM_RESERVE_MB`, atau
  (di container dengan `mem_limit`) RAM container tersedia kurang dari kebutuhan job + `CONTAINER_MEM_HEADROOM_MB`
  (bawaan 64, minimum 32; page cache tidak aktif tidak dihitung terpakai, cgroup v1 & v2). Job menunggu di antrean
  (status worker "menunggu resource", admin menampilkan lama tertahan, metrik `ve_worker_blocked_seconds`) daripada
  proses dibunuh karena kehabisan memori. Worker yang tertahan lebih dari `WORKER_BLOCKED_UNHEALTHY_SECONDS` (1800)
  dilaporkan tidak sehat oleh healthcheck. Kebutuhan RAM job berjalan dicadangkan sampai job selesai (dikurangi RAM
  yang sudah benar-benar dipakai), sehingga job paralel di satu proses tidak melebihi kapasitas.
- **Kapasitas per antrean** (halaman admin, kolom "Kapasitas"; metrik `ve_queue_unservable`): siap; menunggu RAM/CPU
  (sementara); tidak ada worker hidup; worker yang mampu sedang offline; atau **tidak dapat diproses karena
  konfigurasi** — semua worker yang dikenal untuk antrean itu (hidup maupun offline) tidak akan pernah punya RAM cukup
  (mis. `WHISPER_MODEL=medium` dengan `ANALYSIS_MEM=3g`). Dalam kasus terakhir log worker mencatat ERROR dan job tetap
  menunggu dengan pesan "hubungi admin"; naikkan `mem_limit` atau perkecil kebutuhan. Job hanya digagalkan permanen bila
  `UNSERVABLE_FAIL_AFTER_SECONDS` > 0 (bawaan 0 = tidak pernah) dan kondisi itu teramati terus-menerus selama itu.
  Pengguna biasa hanya melihat pesan umum, tanpa angka RAM atau nama server.
- **CPU**: jumlah thread FFmpeg/OpenCV per job dibagi di antara job yang sedang berjalan; render didahulukan dari
  analisis (prioritas proses). Halaman web tetap responsif.
- **Antrean**: per unit paling banyak `MAX_QUEUED_PER_UNIT` job menunggu (pesan 429) dan per antrean
  `MAX_QUEUE_DEPTH` (pesan 503, coba lagi nanti), sehingga lonjakan tidak membuat antrean tak terbatas.
- **GPU** tidak pernah diasumsikan. Dipakai hanya bila `nvidia-smi` mendeteksi GPU dengan VRAM bebas cukup;
  encoder NVENC yang dipilih pengguna tanpa GPU otomatis diganti CPU.

## Monitoring

**Admin → Status** (admin pusat) menampilkan: CPU, load, RAM, disk, GPU; per antrean jumlah menunggu/berjalan,
umur job tertua, waktu tunggu p50/p95, durasi p95, selesai/gagal 1 jam terakhir; per worker status, job, CPU & RAM
(termasuk FFmpeg yang dijalankannya), jumlah thread, dan kabar terakhir. Worker yang tidak melapor > ±1 menit
ditandai "tidak merespons" (job-nya otomatis diulang setelah 2 menit).

Untuk Prometheus/Grafana, isi `METRICS_TOKEN` di `.env` lalu ambil `https://domain/metrics` dengan header
`Authorization: Bearer <token>` (metrik `ve_queue_*`, `ve_worker_*`, `ve_cpu_percent`, `ve_memory_*`, `ve_disk_free_bytes`).

## Upload & preview

**Upload bertahap (protokol tus 1.0.0).** Browser mengirim video per potongan (mulai 2 MB, menyesuaikan kecepatan
sampai 32 MB). Bila koneksi putus, upload melanjutkan sendiri dari byte terakhir yang sudah diterima server; setelah
browser di-refresh, pengguna cukup memilih file yang sama. Setiap potongan diperiksa checksum SHA-256 (di HTTPS)
dan file akhir diverifikasi (ukuran & isi video) sebelum dinyatakan selesai. File ditulis langsung ke folder
proyek (sekali tulis, tanpa salinan sementara) dan kuota unit direservasi di awal, sekali saja walau dilanjutkan.

| Pengaturan | Bawaan | Arti |
|---|---|---|
| `UPLOAD_RESUME_HOURS` | 12 | Upload terputus bisa dilanjutkan selama ini sejak aktivitas terakhir; lewat itu file setengah jadi & reservasi kuota dibersihkan |
| `MAX_ACTIVE_UPLOADS_PER_USER` | 4 | Upload belum selesai per pengguna (mencegah penimbunan ruang) |
| `UPLOAD_CHUNK_MAX_MB` | 64 | Batas satu potongan |

Endpoint upload lama (`POST /api/projects`, satu request) tetap ada untuk klien API, tetapi butuh ruang disk 2x
ukuran file sementara (spool + salinan). Halaman web memakai upload bertahap.

**Preview.** Render final selalu memakai video sumber asli; preview hanya untuk diputar di browser & pelacakan wajah.
- Sumber yang sudah ramah browser (H.264 8-bit, AAC, tinggi <= `PREVIEW_COPY_MAX_HEIGHT` 720, bitrate <=
  `PREVIEW_COPY_MAX_KBPS` 4000) tidak di-transcode: cukup remux (±1 dtk untuk video 5 menit).
- Selain itu transcode 480p (`PREVIEW_HEIGHT`). Video lebih panjang dari `PREVIEW_LONG_MINUTES` (20) memakai 360p dan
  dekode cepat (fps sedikit lebih rendah) agar analisis tidak tertahan lama.
- Preview tidak dibuat ulang selama video sumber dan resepnya sama (analisis ulang memakai preview yang ada).
- Server dengan GPU/Intel QuickSync: `PREVIEW_HWACCEL=auto` (atau `cuda`/`vaapi`/`qsv`); bila gagal otomatis kembali ke CPU.
  Di CPU, 61% waktu preview 1080p adalah decode H.264 yang tidak bisa dipercepat tanpa decoder hardware.

## Penyimpanan media sementara

Video sumber, preview, dan hasil render bersifat **sementara**:

- dihapus otomatis `MEDIA_RETENTION_HOURS` (bawaan 24 jam) setelah aktivitas terakhir pengguna,
  dan paling lama `MEDIA_MAX_HOURS` (bawaan 72 jam) sejak upload;
- membuka atau memantau proyek tidak memperpanjang masa simpan; mengedit, merender, atau mengunduh memperpanjangnya;
- media yang sedang dipakai tidak pernah dihapus: pekerjaan antre/berjalan (termasuk yang sedang dipulihkan
  setelah worker mati), upload yang sedang ditulis, dan file yang sedang diunduh. Setelah pemakaian terakhir
  selesai, media yang sudah lewat masa simpan langsung dihapus;
- riwayat hasil render tetap tercatat, tetapi file yang sudah dihapus tidak ditawarkan untuk diunduh;
- akun, unit, tema, Guidebook, dan riwayat proyek tetap tersimpan.

Upload baru ditolak dengan pesan jelas bila sisa disk di bawah `DISK_MIN_FREE_MB` / `DISK_MIN_FREE_PCT`
atau kuota media unit terlampaui. Kuota ditegakkan dengan ukuran file sebenarnya dan reservasi atomik,
sehingga beberapa upload bersamaan dari unit yang sama tidak bisa bersama-sama melewati kuota. Pemakaian per unit terlihat di **Admin → Unit** dan **Admin → Status**.

**Proyek dari versi sebelumnya** tidak dihapus otomatis. Periksa dulu (tidak mengubah apa pun):

```bash
docker compose exec web python -m app.manage media-migrate
```

Bila daftar sudah sesuai, masukkan ke siklus sementara dengan masa tenggang 72 jam
(pengguna sempat mengunduh hasil render yang masih dibutuhkan):

```bash
docker compose exec web python -m app.manage media-migrate --apply --grace-hours 72
```

Menjalankan pembersihan sekarang (biasanya otomatis oleh worker setiap `CLEANUP_INTERVAL` detik):

```bash
docker compose exec web python -m app.manage media-cleanup
```

## Update aplikasi

Skema database dikelola **Alembic** (lihat `docs/DATABASE_MIGRATIONS.md`). Web & worker menolak start bila skema
database belum sesuai versi aplikasi; service `migrate` menaikkannya sekali sebelum web & worker start.

```bash
cd /opt/video-editing
./deploy/backup.sh                      # backup database dulu
git pull
docker compose build
docker compose run --rm migrate         # migrasi skema (satu transaksi; gagal = database tetap versi lama)
docker compose up -d
docker compose exec web python -m app.manage db-status
```

Database dari versi sebelum P5 (tanpa catatan versi) dikenali otomatis dan dinaikkan tanpa menyentuh data.
Data (akun, proyek, video) tersimpan di volume Docker dan tidak hilang saat update. Bila migrasi gagal, aplikasi
lama tetap bisa dijalankan pada database itu; prosedur pemulihan ada di `docs/DATABASE_MIGRATIONS.md`.

## Backup

Backup database harian (akun, unit, proyek, rencana animasi):

```bash
crontab -e
```

Tambahkan baris:

```
0 2 * * * cd /opt/video-editing && ./deploy/backup.sh >> backups/backup.log 2>&1
```

Simpan juga salinan folder `backups/` di luar server (mis. object storage). Backup **tidak** mencakup
file video: media bersifat sementara dan dihapus otomatis, jadi pengguna perlu mengunduh hasil render sendiri.

## Perintah berguna

Lihat log:

```bash
docker compose logs -f web worker
```

Reset password pengguna dari server:

```bash
docker compose exec web python -m app.manage reset-password email@kantor.id
```

Daftar pengguna:

```bash
docker compose exec web python -m app.manage list-users
```

## Keamanan

- Password disimpan dengan hash *scrypt*; sesi login memakai cookie HttpOnly.
- Login dibatasi (8 kali gagal per email / 15 menit).
- API key AI hanya tersimpan di server dan tidak pernah dikirim ke browser.
- Buka hanya port 80 & 443 di firewall server; database tidak terbuka ke internet.
- Ambil video dari link (YouTube, dll.) bawaannya hanya untuk Admin & Admin Unit (`ALLOW_URL_IMPORT`).
  Unduhan hanya boleh menuju IP publik (anti-SSRF, `docs/SECURITY_AUDIT_URL.md`). Sebagai lapisan tambahan,
  batasi juga jaringan keluar container `worker` di tingkat host/firewall: blokir 169.254.169.254 (metadata
  cloud), jaringan privat, dan port database/Redis lain.
  Pastikan konten yang diolah memang milik/izin organisasi.
