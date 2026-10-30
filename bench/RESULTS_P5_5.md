# Hasil P5.5 — Validasi produksi akhir

## P5.5.1 — Build produksi & kesiapan staging

Lingkungan uji: GitHub Actions `ubuntu-24.04` (bukan server staging). Docker Hub tidak dapat diakses dari lingkungan
pengembangan (`429 Too Many Requests`). Image lokal bertag `python:3.12-slim`/`video-editing:latest` di lingkungan
pengembangan dibangun ulang secara lokal dengan CA proxy, sehingga **tidak dipakai** sebagai bukti.

### Run #1 — commit `ffed14e`

- **CI:** https://github.com/aiolaf9/video-editing/actions/runs/41060598481 — success.
- **Production image:** https://github.com/aiolaf9/video-editing/actions/runs/41060598515 — success, dengan catatan
  di bawah.

| Pemeriksaan | Hasil terukur |
|---|---|
| Image dasar | `python:3.12-slim` resmi (Debian 13 trixie). **Digest TIDAK tercatat**: BuildKit menarik image dasar ke cache build sehingga `docker image inspect` gagal → diperbaiki di run berikutnya (pull eksplisit + build dari digest) |
| Ukuran image | 1.360.107.570 byte (±1,36 GB) |
| Python / FFmpeg / FFprobe | Python 3.12.16; FFmpeg & FFprobe 7.1.5-0+deb13u1 (`--enable-gpl --enable-libx264`) |
| `pip check` | No broken requirements found |
| User | uid 1000 (`app`) di image dan di web, worker-analisis, worker-render, worker-io; `/app` tidak dapat ditulis user aplikasi |
| Lisensi di image | 58 paket: izin 54, catatan 4, 0 GPL/AGPL |
| Secret/berkas terlarang | history tanpa secret; tidak ada .env/.git/tests/data/media; detect-secrets atas `/app` bersih |
| FFmpeg & media tidak tepercaya | 0 permintaan ke server penjebak (playlist HLS/concat menyamar) |
| Compose produksi + overlay | db healthy, `migrate` exit 0, web healthy, `/api/health` langsung & via Caddy HTTPS benar, `db-check` OK (0006_uploads) |
| Worker | terdaftar: `analisis,render` ×2, `render` ×2, `io` ×1 |
| Volume | `/data` dapat ditulis user aplikasi |
| Isolasi worker-io | 18/18 sesuai: db & proxy terjangkau; web, caddy, worker lain, internet langsung, 1.1.1.1, metadata **gagal**; lewat proxy: example.com 200 & CONNECT 443 200; 169.254.169.254, 10/8, 127/8, ::1, IPv4 desimal, `db:5432`, port 22, CONNECT metadata → 403 |

Kekurangan run #1 yang ditemukan sendiri:
1. Digest image dasar tidak tercatat (lihat di atas).
2. Beberapa langkah memakai `| tee` tanpa `pipefail` sehingga kegagalan bisa tertutup; hasil run #1 sudah diperiksa
   manual dari log (semua langkah benar-benar lolos), tetapi workflow diperbaiki (`bash -eo pipefail`).
3. Belum diuji: unduhan publik end-to-end lewat proxy, redirect, nama DNS → IP privat, IPv6 tambahan, CONNECT port
   lain, restart tanpa merusak status job, render/subtitle/overlay di image, pembacaan berkas lokal, healthcheck
   worker, X-Forwarded-For, root filesystem read-only.

### Perubahan setelah run #1 (commit lokal, belum di-push)

| Area | Perubahan | Diuji oleh |
|---|---|---|
| Digest & reproducibility | `docker pull` eksplisit, digest dicatat, image dibangun **dari digest** (`ARG PYTHON_IMAGE`) | workflow |
| Masking kegagalan | `defaults.run.shell: bash -eo pipefail` | workflow |
| Media di image | MP4 normal beraudio terbaca; upaya baca berkas lokal (HLS `file://`, concat absolut/`../`) ditolak; dijalankan `--network none --read-only --cap-drop ALL` | `deploy/checks/media_trap.py` |
| Regresi di image | seluruh pytest (render, subtitle, overlay, musik, preview, upload, yt-dlp lokal, migrasi SQLite) di image produksi dengan root fs read-only & tanpa capability | workflow |
| Egress | +CONNECT ke port 22/80/8443/db/loopback; +IPv6 (`::ffff:`, ULA, link-local), IPv4 heksa/oktal/`0`; nama `*.nip.io`/`localtest.me` yang resolve ke IP internal wajib tepat 403 (squid memeriksa IP hasil resolusi) | `net_isolation.py` |
| E2E | impor video publik lewat Caddy → worker-io → squid (bukti `TCP_TUNNEL/200` di log squid); alamat internal & nama → IP privat ditolak API; redirect publik → metadata gagal dan tercatat `TCP_DENIED/403` dengan penanda unik | `e2e_staging.py` + log squid |
| Restart | restart db, web, worker-render, worker-io, proxy, caddy: status semua job identik, tidak ada job `jalan` tertinggal, health pulih | workflow |
| Healthcheck worker | `python -m app.manage worker-health` (heartbeat worker di container ini ≤ 60 dtk) | tes unit + workflow (status healthy) |
| X-Forwarded-For | `FORWARDED_ALLOW_IPS` (bawaan tetap `*`); staging: hanya subnet jaringan `edge` (Caddy). Uji: header palsu dari worker & dari klien lewat Caddy tidak muncul di log akses | workflow |
| Caddy | staging: `cap_drop: ALL` + `NET_BIND_SERVICE`, `no-new-privileges`, hanya jaringan `edge` | workflow (health via Caddy) |
| Root filesystem | staging: `read_only: true` + tmpfs `/tmp` & `/home/app` untuk migrate, web, semua worker | workflow (tulis `/app`,`/usr` gagal; `/data`,`/tmp` berhasil) |

### Tinjauan konfigurasi produksi (F)

| Item | Status di `docker-compose.yml` produksi | Di overlay staging | Rekomendasi |
|---|---|---|---|
| `--forwarded-allow-ips='*'` | `*` (tidak berubah) | subnet `edge` saja | promosikan setelah staging lolos |
| Kepercayaan Caddy → FastAPI | Caddy & semua service satu jaringan | Caddy hanya `edge`; web `default`+`edge` | sama |
| Capabilities / `no-new-privileges` | bawaan Docker | `cap_drop: ALL` (+`NET_BIND_SERVICE` Caddy) | sama |
| Secret antar-container | `.env` ke semua container aplikasi | worker-io tanpa API key/password admin/token metrics | lanjut: secret per service (Docker secrets) |
| Healthcheck worker | tidak ada | `worker-health` | sama |
| Root filesystem | dapat ditulis | `read_only` + tmpfs | sama |
| Volume | `appdata` milik uid 1000; `pgdata`/caddy milik root di container masing-masing | sama | role PostgreSQL terpisah untuk worker-io (P5.5.3) |
| Pinning dependensi | tag `python:3.12-slim`, `postgres:16-alpine`, `caddy:2-alpine`; Python `>=` | image dasar dibangun dari digest di CI | lihat strategi di bawah |

Strategi build yang dapat diulang (rekomendasi, belum diterapkan):
1. Pin digest semua image (`python`, `postgres`, `caddy`, proxy) di `.env`/compose; perbarui terjadwal.
2. Lockfile Python berhash (mis. `pip-compile --generate-hashes` dari `requirements.txt`) dipakai Dockerfile dengan
   `pip install --require-hashes`; versi acuan = `pip freeze` image run yang lolos (artifact workflow).
3. Paket Debian (FFmpeg 7.1.5-0+deb13u1) mengikuti digest image dasar; catat `ffmpeg -version` di setiap rilis.
4. Simpan image hasil build yang lolos di registry dengan digest; deploy berdasarkan digest, bukan tag.

### Status kriteria penerimaan P5.5.1 (setelah run #1)

| # | Kriteria | Status |
|---|---|---|
| 1 | CI umum berhasil | PASS (CI #5) |
| 2 | Workflow image berhasil | PASS untuk run #1, tetapi cakupannya belum lengkap |
| 3 | Image produksi benar-benar terbangun | PASS (identitas digest image dasar: FAIL di run #1) |
| 4 | Compose staging berjalan | PASS |
| 5 | Migrasi & health | PASS |
| 6 | Worker wajib aktif | PASS (registrasi); healthcheck worker: NOT VERIFIED |
| 7 | Isolasi jaringan uji positif & negatif | sebagian PASS (18/18); e2e unduhan publik, redirect, DNS→privat, IPv6 tambahan: NOT VERIFIED |
| 8 | FFmpeg tidak membuka jaringan yang tidak diizinkan | PASS (penjebak); media normal & baca berkas lokal di image: NOT VERIFIED |
| 9 | Tidak ada kebocoran secret | PASS (image); log staging: NOT VERIFIED |
| 10 | Hasil & batasan dicatat | PASS (dokumen ini) |
| C10 | Restart tanpa merusak status job | NOT VERIFIED |
