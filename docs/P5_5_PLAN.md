# Rencana P5.5 — Validasi produksi akhir (belum dimulai)

Prinsip:
- P5.5 **tidak selesai berdasarkan unit test**. Setiap area harus punya bukti dari staging: log, angka, tangkapan
  layar, atau keluaran perintah, disimpan di `bench/RESULTS_P5_5.md`.
- Tanpa GPU kecuali benar-benar tersedia. Tanpa API AI berbayar dalam pengujian otomatis.
- Tanpa perubahan pada deployment produksi. Hasil akhir: keputusan go/no-go per area.

## 0. Lingkungan staging

- **Server:** kelas **Menengah** (8 vCPU / 16 GB, disk SSD ≥ 200 GB) sesuai `DEPLOY.md`.
- **Image:** dibangun dari `Dockerfile` (`python:3.12-slim` resmi, digest dicatat).
- **Database:** PostgreSQL 16 resmi.
- **HTTPS:** Caddy dengan domain staging.
- **Data:** salinan database produksi yang sudah dianonimkan, atau data sintetis bila belum ada produksi. Media
  uji dibuat sendiri / berlisensi; **tanpa footage pengguna**.
- **Pengukuran:** `/metrics`, `docker stats` (sampel 5 dtk), `bench/*.py`, log JSON.

## 1. Area, langkah, dan kriteria penerimaan

| # | Area | Langkah | Kriteria penerimaan (semua wajib) |
|---|---|---|---|
| 1 | Build Dockerfile produksi | `docker compose build --pull`; catat digest image dasar; `docker image inspect`; jalankan `pip check` & `python scripts/check_licenses.py` di dalam image | build sukses dari image resmi; ukuran image tercatat; `pip check` bersih; 0 paket GPL/AGPL; container berjalan sebagai user non-root `app` (uid 1000, sesuai Dockerfile) |
| 2 | Deploy staging | `docker compose up -d` dengan service `migrate` | `migrate` selesai exit 0 sebelum web/worker; `db-check` tanpa drift; `/api/health` 200 dalam ≤ 60 dtk; startup ditolak bila skema tertinggal (uji: kembalikan revisi lalu start) |
| 3 | Whisper asli | `bench/whisper_bench.py --check`, lalu `--media` 3 rekaman ucapan Indonesia (1, 10, 30 menit) model `small`, `--threads 2,4` | transkrip tidak kosong; WER sampel ≤ 25 % terhadap transkrip manual 2 menit; RTF (waktu proses / durasi) ≤ 1,0 pada 4 thread; RAM puncak worker-analisis < `ANALYSIS_MEM` (3 GB); tidak OOMKilled |
| 4 | FFmpeg end-to-end | upload → analisis → render 9:16 dengan subtitle, musik, overlay, zoom; 5 video beragam (HP portrait berotasi, 4K, tanpa audio, VFR, 60 fps) | 5/5 render selesai; hasil dapat diputar (`ffprobe` valid, durasi ±0,2 dtk, A/V sync ≤ 80 ms); versi FFmpeg image tercatat; file playlist/concat yang menyamar sebagai video tidak menghasilkan koneksi keluar (uji dengan server penjebak) |
| 5 | Upload besar & resumable | file 2 GB lewat internet (bukan LAN) via HTTPS; putus jaringan 2× di tengah; refresh browser; lanjutkan; Chrome, Firefox, Safari/iOS | upload selesai tanpa mulai dari nol (byte terkirim ulang ≤ 1 potongan per putus); checksum file = sumber; sesi kedaluwarsa tidak bisa dilanjutkan (409/410); kuota tidak bocor (reservasi dilepas saat batal) |
| 6 | Storage TTL & cleanup | `MEDIA_RETENTION_HOURS`/`MEDIA_MAX_HOURS` dipendekkan di staging; buat proyek aktif & tidak aktif; jalankan sweep | media tidak aktif terhapus ≤ 1 siklus cleanup setelah TTL; media dengan lease aktif (upload/analisis/render) **tidak** terhapus; pemakaian kuota unit turun sesuai; tidak ada file yatim di `/data` (skrip inventaris) |
| 7 | Concurrent users, render, analisis | `bench/queue_bench.py --docker` + skenario: 20 pengguna di 4 unit, 10 analisis + 20 render dikirim bersamaan | 0 job hilang/duplikat; fairness: tidak ada unit menunggu > 2× rata-rata; p95 tunggu render pendek ≤ 5 mnt saat analisis panjang berjalan; latensi API p95 ≤ 500 ms selama beban; tidak ada OOMKilled |
| 8 | Restart worker & recovery | `docker kill` worker saat analisis/render/unduhan; restart web saat upload; restart db saat antre | job yang terputus kembali ke antrean atau gagal jelas dalam ≤ 2× heartbeat; tidak ada job "jalan" selamanya; file setengah jadi dibersihkan; upload berlanjut setelah web kembali |
| 9 | Backup & restore | `deploy/backup.sh` di staging; restore ke database baru sesuai `docs/BACKUP_RESTORE.md`; migrasi salinan data produksi (butir 9 `PRODUCTION_VALIDATION.md`) | restore sukses; jumlah baris tabel inti identik; `db-check` OK; aplikasi berjalan di atas hasil restore; waktu restore (RTO) & umur backup (RPO) tercatat dan ≤ target pemilik |
| 10 | Resource CPU/RAM/disk/GPU | ukur selama area 3–8; GPU hanya bila tersedia (`WHISPER_DEVICE=auto`, `PREVIEW_HWACCEL=cuda`) | tidak ada container melampaui `mem_limit`; CPU host ≤ 90 % rata-rata 5 mnt saat beban puncak; disk penuh ditolak jelas (507) sebelum `DISK_MIN_FREE_MB`; tanpa GPU: jalur CPU berfungsi; dengan GPU: log memakai cuda dan fallback ke CPU berhasil saat GPU dimatikan |
| 11 | Keamanan jaringan & isolasi tenant | terapkan rancangan `docs/EGRESS_DESIGN.md` **di staging**; jalankan tabel uji bagian 3; uji isolasi unit: editor unit A mencoba mengakses proyek/job/upload/media unit B lewat API (ID ditebak) | semua uji egress sesuai tabel; 0 akses lintas unit (404/403 untuk semua endpoint); SSRF ke metadata/privat ditolak dari UI; API key tidak muncul di log/respons (grep log staging); header keamanan & cookie `Secure` di HTTPS |
| 12 | Lisensi untuk distribusi komersial | lengkapi daftar `docs/LICENSES.md` bagian 5: kartu model Whisper yang dipakai, lisensi FFmpeg di image (`ffmpeg -L`), paten codec, ToS penyedia AI & platform sumber; SBOM image (`pip-licenses`/`syft` bila diizinkan) | 0 butir "MANUAL" tanpa bukti tertulis; keputusan tertulis untuk FFmpeg/H.264 dan yt-dlp; daftar atribusi siap dimasukkan ke halaman "Tentang"/dokumen distribusi |

## 1a. Item staging tambahan dari penerimaan P5.4 (wajib, area 4 & 11)

Uji SSRF di Python **bukan pengganti** isolasi jaringan; item ini diuji pada container nyata.

| # | Item | Bukti yang diminta | Alat |
|---|---|---|---|
| B1 | FFmpeg/FFprobe memproses media tidak tepercaya tanpa koneksi jaringan tidak sah | 0 permintaan ke server penjebak, di image produksi | `deploy/checks/media_trap.py` (workflow *Production image*) |
| B2 | PyAV/faster-whisper tidak membuka URL dari berkas pengguna | audio di-decode FFmpeg (dibatasi) lalu diberikan sebagai array | `tests/test_p55_untrusted_media.py` + uji Whisper asli P5.5.2 |
| B3 | Protokol input FFmpeg dibatasi eksplisit | `-protocol_whitelist file,pipe` pada setiap `-i` & ffprobe | `app/media.py` (`local_inputs`), tes unit |
| B4 | worker-io tidak dapat menjangkau layanan internal yang tidak diizinkan | koneksi langsung ke web/caddy/worker lain/internet/metadata gagal; hanya db & proxy | `deploy/checks/net_isolation.py` di container worker-io |
| B5 | Egress proxy menolak IP privat, metadata cloud, port lain, dan tujuan redirect berbahaya (setiap hop lewat proxy) | 403 untuk 169.254.169.254, 10/8, 127/8, ::1, IPv4 desimal, port 22; 200 untuk internet publik | `deploy/checks/net_isolation.py` |
| B6 | PostgreSQL & layanan internal tepercaya tetap berfungsi | migrasi exit 0, `db-check` OK, worker io/analisis/render terdaftar di tabel `workers`, impor admin dari jaringan tepercaya (P5.5.3) | workflow *Production image* |

## 2. Urutan & keluaran

1. Area 1–2 (build & deploy), lalu 11 (egress) sebelum data uji apa pun masuk.
2. Area 3, 4, 5, 6 (fungsi inti).
3. Area 7, 8, 10 (beban & ketahanan).
4. Area 9 (backup/restore & migrasi salinan produksi).
5. Area 12 (lisensi).

**Keluaran:**
- `bench/RESULTS_P5_5.md`: angka & bukti per area;
- `PRODUCTION_VALIDATION.md`: status diperbarui;
- daftar temuan dengan tingkat keparahan dan rekomendasi go/no-go.
