# Hasil P5.5 — Validasi produksi akhir

## P5.5.1 — Build produksi & kesiapan staging

Status keseluruhan: **BELUM TERVERIFIKASI** untuk build image produksi. Docker Hub menolak lingkungan pengembangan
(`429 Too Many Requests` pada `registry-1.docker.io` untuk `python:3.12-slim`) dan tidak ada server staging.
Validasi image dipindahkan ke workflow GitHub Actions `.github/workflows/image.yml` (image resmi dari Docker Hub),
yang baru berjalan setelah push disetujui.

Catatan kejujuran: daemon Docker lokal menyimpan image bertag `python:3.12-slim` dan `video-editing:latest` dari
sesi sebelumnya. `docker history` menunjukkan image `python:3.12-slim` itu **dibangun ulang secara lokal** (CA dan
variabel proxy lingkungan pengembangan disisipkan), jadi bukan image resmi. Image tersebut **tidak dipakai** sebagai
bukti apa pun.

### Audit Dockerfile & compose

| # | Temuan | Tingkat | Tindakan P5.5.1 | Status |
|---|---|---|---|---|
| F1 | Image dasar `python:3.12-slim` & dependensi Python tidak dipin (tag & `>=`): build tidak dapat diulang persis | Sedang | workflow mencatat digest image dasar & `pip freeze` di image (artifact); usulan: pin digest + lockfile berhash setelah staging | terbuka (usulan) |
| F2 | `.dockerignore` tidak mengecualikan `.env*`, backup, dump, media | Sedang | diperketat; Dockerfile hanya menyalin `app web fonts models` | selesai |
| F3 | Semua service di satu jaringan; worker-io (konten luar) bisa menjangkau web, Caddy, worker lain, internet langsung | Tinggi | overlay `deploy/compose.staging.yml`: worker-io hanya di `dl-db` & `dl-egress` (internal), proxy keluar squid | disiapkan, uji di workflow |
| F4 | `env_file` membagikan semua secret (API key AI, password admin, token metrics) ke semua container termasuk worker-io | Sedang | overlay mengosongkan secret yang tidak dibutuhkan worker-io | disiapkan |
| F5 | Container aplikasi tanpa `cap_drop`/`no-new-privileges` | Sedang | overlay: `cap_drop: [ALL]`, `no-new-privileges` untuk migrate, web, semua worker, proxy | disiapkan |
| F6 | Caddy berjalan dengan kapabilitas bawaan (root, port 80/443) | Rendah | usulan: `cap_drop: ALL` + `cap_add: NET_BIND_SERVICE` setelah diuji | terbuka |
| F7 | Worker tanpa healthcheck container (keaktifan hanya dari heartbeat tabel `workers`) | Rendah | workflow memeriksa tabel `workers`; healthcheck worker diusulkan di P5.5.3 | terbuka |
| F8 | Mode proxy: worker-io di jaringan internal tidak dapat me-resolve nama internet → semua impor link akan ditolak | Tinggi (fungsi) | `check_import_url(resolve_host=False)` saat `DOWNLOAD_PROXY` diisi: IP literal & nama internal tetap ditolak aplikasi, nama lain diperiksa proxy | selesai + tes |
| F9 | Pembatasan protokol FFmpeg hanya mengandalkan bawaan versi FFmpeg | Sedang | `-protocol_whitelist file,pipe` eksplisit pada setiap input FFmpeg & ffprobe | selesai + tes |
| F10 | `uvicorn --forwarded-allow-ips='*'`: container di jaringan default selain Caddy bisa memalsukan X-Forwarded-For | Rendah | usulan: batasi ke alamat Caddy setelah jaringan staging final | terbuka |
| F11 | Root filesystem container dapat ditulis selain `/app` (bukan `read_only`) | Rendah | usulan `read_only: true` + tmpfs setelah diuji di staging; `/app` sudah tidak dapat ditulis user aplikasi (diperiksa workflow) | terbuka |

Yang sudah benar: image berjalan sebagai user `app` (uid 1000); `/data` volume milik user aplikasi; berkas sementara
aplikasi di `DATA_DIR/tmp` (volume, bukan layer image); migrasi lewat service `migrate` sebelum web & worker
(diverifikasi CI job *migrations*); healthcheck web ke `/api/health`.

### Kriteria penerimaan (P5_5_PLAN area 1 & 2)

| Kriteria | Bukti | Status |
|---|---|---|
| Build sukses dari image resmi; digest dasar tercatat | workflow `image.yml` langkah build | BELUM TERVERIFIKASI (menunggu push) |
| Ukuran image tercatat | sama | BELUM TERVERIFIKASI |
| `pip check` bersih di image | sama | BELUM TERVERIFIKASI (lokal di venv: bersih) |
| 0 paket GPL/AGPL di image | `check_licenses.py` di dalam image | BELUM TERVERIFIKASI (venv produksi CI: lolos) |
| User non-root uid 1000 di web & semua worker | workflow (`id -u` tiap service) | BELUM TERVERIFIKASI (Dockerfile: `USER app`) |
| Tidak ada secret/.env/data/media di image | workflow (history, daftar berkas, detect-secrets) | BELUM TERVERIFIKASI (konteks build diperketat) |
| `migrate` exit 0 sebelum web/worker; `db-check` tanpa drift | workflow + CI job *migrations* | sebagian (CI *migrations* lolos; dengan image produksi: menunggu) |
| `/api/health` 200 ≤ 60 dtk (langsung & lewat Caddy HTTPS) | workflow | BELUM TERVERIFIKASI |
| Worker analisis, render, io terdaftar | workflow (tabel `workers`) | BELUM TERVERIFIKASI |
| Versi Python/FFmpeg/FFprobe image tercatat; FFmpeg tidak membuka jaringan dari media tidak tepercaya (B1) | workflow (`media_trap.py`) | BELUM TERVERIFIKASI (lokal FFmpeg 6.1.1: 0 permintaan) |
| Isolasi worker-io (B4, B5) | workflow (`net_isolation.py`) | BELUM TERVERIFIKASI |
| Startup ditolak bila skema tertinggal | CI job *migrations* | TERVERIFIKASI (CI run #4) |
