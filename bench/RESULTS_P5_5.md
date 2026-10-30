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

### Run #2 — commit `2489710`

- **CI #6:** https://github.com/aiolaf9/video-editing/actions/runs/41064505431 — success.
- **Production image #2:** https://github.com/aiolaf9/video-editing/actions/runs/41064505606 — **failure**.

| Pemeriksaan | Hasil terukur |
|---|---|
| Digest image dasar | `python@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159`; proxy `debian@sha256:13e0a2e78aa0554ad5ff66e4e260c6dbcdff807eb1f39d43b9fb0288aae88b04`; image dibangun **dari digest tersebut** |
| Image produksi | id `sha256:fff03b89d321b808846c5f90394c615d8cb7f65841c471cc02d49126d97e1cf2`, 1.360.108.634 byte; proxy 168.301.741 byte |
| OS / Python / FFmpeg | Debian GNU/Linux 13 (trixie); Python 3.12.16; FFmpeg/FFprobe 7.1.5-0+deb13u1 (`--enable-gpl --enable-libx264`) |
| `pip check` / lisensi | bersih; 58 paket, 0 GPL/AGPL |
| Media (tanpa jaringan, read-only, tanpa capability) | 0 permintaan ke penjebak; MP4 normal 640x360 beraudio terbaca (48.298 sampel); 4 upaya baca berkas lokal ditolak |
| Regresi di image | **9 gagal**, 283 lolos: tes memanggil `pgrep` (paket procps) yang tidak ada di image produksi |
| Staging | tidak berjalan (langkah sebelumnya gagal) |

Analisis akar masalah: `pgrep` hanya dipakai tiga helper tes (`tests/test_jobcontrol.py`, `test_cancel_api.py`,
`test_p1_final.py`) untuk memeriksa apakah proses FFmpeg sudah mati; kode aplikasi tidak memakainya. Perbaikan:
helper `process_alive()` di `tests/conftest.py` membaca `/proc/*/cmdline` (regex, setara `pgrep -f`), tanpa menambah
paket ke image produksi. Diuji lokal dengan `pgrep` palsu yang selalu gagal: 28/28 lolos; helper mendeteksi proses
hidup (True) dan mati (False) sehingga asersi "proses sudah mati" tidak lolos secara kosong.

### Perubahan untuk run #3 (commit lokal)

- Fixture **terkendali** (`deploy/compose.ci-fixtures.yml`, `deploy/checks/fixture_server.py`): server media
  "publik" `fixture` (11.22.33.10) dan `internal-target` (10.250.0.0/24, nama DNS → IP privat) di port 80, sehingga
  penolakan proxy terhadap `internal-target` hanya mungkin berasal dari ACL alamat IP (bukan aturan port).
- E2E fixture (bukti utama): unduhan lewat proxy selesai, **sha256 identik** dengan sumber, berisi video+audio, tanpa
  berkas sementara; redirect ke nama privat/metadata/loopback gagal tanpa berkas; `internal-target` menerima 0
  permintaan; log squid `TCP_MISS/200` untuk fixture dan `TCP_DENIED/403` untuk tiap tujuan internal, serta tidak ada
  `TCP_MISS`/`TCP_TUNNEL` ke alamat internal. Situs pihak ketiga dijalankan terpisah (`continue-on-error`).
- Pemulihan (`deploy/checks/recovery_staging.py`): job unduhan yang **sedang berjalan** saat worker-io di-restart
  (SIGTERM) dan di-crash (SIGKILL), dengan `media-cleanup` dijalankan di tengah unduhan; wajib: job selesai, berkas
  identik, tepat 1 job unduhan per proyek, tanpa sisa berkas sementara, worker healthy lagi, job lain tidak berubah,
  tidak ada job `jalan` tertinggal.
- Workflow: `! cmd` diganti `if ...; then exit 1; fi` (negasi `!` tidak memicu `errexit`).

### Run #3 — commit `228b3c4`

- **CI #7:** https://github.com/aiolaf9/video-editing/actions/runs/41068238269 — success.
- **Production image #3:** https://github.com/aiolaf9/video-editing/actions/runs/41068238268 — **failure**
  (langkah e2e fixture).

| Pemeriksaan | Hasil terukur |
|---|---|
| Build dari digest, versi, lisensi, secret, media di image | sama dengan run #2 (image `sha256:813b3c5b8ac0…8268`, 1.360.108.634 byte) |
| Regresi di dalam image produksi (read-only, tanpa capability) | **292 lolos**, 1 dilewati (skip bawaan), 0 gagal |
| Compose staging: db healthy, migrate exit 0, health langsung & via Caddy, worker healthy, uid 1000, root fs read-only, `/data` & `/tmp` dapat ditulis | lolos |
| X-Forwarded-For palsu (dari worker & lewat Caddy) | tidak dipercaya |
| Isolasi worker-io (lapisan jaringan, termasuk fixture) | **42/42 sesuai** |
| E2E: 7 alamat/nama internal ditolak API (termasuk `internal-target` → 10.250.0.0/24, `[::ffff:10.250.0.2]`) | lolos; `internal-target` menerima 0 permintaan |
| E2E: impor `fixture/media/ok.mp4` & 3 redirect | **gagal**: semua job tetap `antre`, fixture tidak pernah menerima permintaan unduhan |

Analisis akar masalah (bug produksi, bukan bug uji): admission RAM (`resources.admit`, P3) mewajibkan RAM
tersedia ≥ kebutuhan job + `MEM_RESERVE_MB` (512). Di dalam container, RAM tersedia dihitung dari batas cgroup; untuk
`worker-io` (`mem_limit` 512 MB di `docker-compose.yml` produksi) hasilnya ±412 MB < 300 + 512 = 812 MB, sehingga
**unduhan dari link tidak pernah diproses pada deployment Docker**. Dibuktikan dengan menjalankan `admit()` versi
`228b3c4` terhadap angka container: `(False, 'RAM tersedia 412 MB < kebutuhan 812 MB (job menunggu)')`. Worker render
(1500 MB) dan analisis model `small` (3 GB) masih lolos dengan aturan lama.

Perbaikan (commit lokal): cadangan `MEM_RESERVE_MB` hanya diterapkan pada RAM host; di dalam batas container dipakai
ruang aman `CONTAINER_MEM_HEADROOM_MB` (bawaan 64 MB). Kedua syarat tetap wajib. Tes: io diterima di container 512 MB,
render ditolak bila tidak muat, batas compose render/analisis tetap muat, RAM host menipis tetap menahan job.
Workflow kini juga gagal bila ada worker berstatus `menunggu` saat idle, mencetak tabel `workers` & log worker-io bila
e2e/pemulihan gagal, dan punya `timeout-minutes` per langkah.

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
