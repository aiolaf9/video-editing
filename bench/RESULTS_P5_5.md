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

### Run #4 — commit `3e8de64`

- **CI #8:** https://github.com/aiolaf9/video-editing/actions/runs/41073736042 — success. Lint, keamanan & lisensi,
  migrasi: lolos; SQLite **299 lolos, 1 dilewati** (`test_lifecycle` advisory lock, khusus PostgreSQL); PostgreSQL
  **315 lolos, 0 dilewati** (tes advisory lock benar-benar berjalan di sini). Tes Whisper asli: manual (tidak dijalankan).
- **Production image #4:** https://github.com/aiolaf9/video-editing/actions/runs/41073735681 — success
  (artifact `kesiapan-staging`, ID 12987177141).

| Pemeriksaan | Hasil terukur |
|---|---|
| Build dari digest | python `sha256:aa943ce4…d159`, proxy `debian@sha256:13e0a2e7…8b04`; image `sha256:d7c27091…1af0`, 1.360.109.690 byte |
| Versi | Debian 13, Python 3.12.16, FFmpeg/FFprobe 7.1.5-0+deb13u1 (`--enable-gpl --enable-libx264`), uid 1000 |
| Lisensi / secret di image | 58 paket (54 izin, 4 catatan, 0 tolak); tidak ada secret/.env/data/media |
| Media tidak tepercaya (tanpa jaringan, read-only) | permintaan ke server penjebak: 0; MP4 normal 640x360 + audio |
| Regresi di dalam image (read-only, tanpa capability) | **299 lolos, 1 dilewati** (advisory lock PostgreSQL), 0 gagal |
| Compose staging | db healthy, migrate exit 0, `0006_uploads`, tanpa drift, uid 1000, root fs read-only, ketiga worker healthy |
| Worker tertahan admission saat idle | **tidak ada** (pemeriksaan baru, lolos) |
| X-Forwarded-For palsu | tidak dipercaya |
| Isolasi worker-io | **42/42 sesuai** |
| E2E fixture lewat Caddy (bukti utama) | **14 sesuai, 0 tidak sesuai, 0 tidak dapat diuji** |
| — unduhan `fixture/media/ok.mp4` | job `selesai`, sha256 `43510dc3…eff2` **identik dengan sumber**, video+audio, tanpa sisa `.part` |
| — 7 alamat internal (IPv4, IPv6 `::1`, `::ffff:10.250.0.2`, metadata, loopback, `db`, `web`) | 400 dari API |
| — redirect ke internal-target / metadata / loopback | job gagal dengan 403 dari proxy, tanpa berkas; internal-target menerima **0** permintaan |
| — log squid | `TCP_MISS/200` unduhan fixture: 2; `TCP_DENIED/403` internal-target: 2, metadata: 1, loopback: 1; tidak ada MISS/TUNNEL ke alamat internal |
| Pihak ketiga (pelengkap) | redirect httpbin → metadata ditolak 403 (sesuai); unduhan w3schools `mov_bbb.mp4` **selesai** tetapi pemeriksa menandai *tidak sesuai* karena berkas punya trek `data` (timecode) selain audio/video — pemeriksa terlalu ketat, bukan kegagalan aplikasi (lihat perbaikan) |
| Pemulihan SIGTERM (restart worker-io di progres 7,6 %) | job kembali `antre/0`, lalu `selesai`; sha256 identik dengan sumber; 1 job unduhan; worker healthy |
| Pemulihan SIGKILL (kill di progres 10,6 %) | job `jalan/1` → dipulihkan → `selesai`; sha256 identik; 1 job unduhan; worker healthy |
| Cleanup media di tengah unduhan | tidak menghapus apa pun (0 folder/berkas) |
| Job lain (selesai/gagal/antre) | tidak berubah; 0 job tertinggal `jalan`; worker terdaftar kembali |
| Restart seluruh stack | status 12 job identik sebelum/sesudah; migrate exit 0; Caddy health OK |
| Secret di log staging | 915 baris log, tanpa password database/admin |

Yang **tidak** dibuktikan run #4 (sehingga belum PASS): tidak ada pemeriksaan eksplisit OOM (`OOMKilled`/event OOM) dan
angka RAM host/container tidak dicatat; ditambahkan untuk run #5.

### Perubahan untuk run #5 (commit lokal, belum di-push)

**B — keamanan admission RAM**
- `_cgroup_memory`: cgroup v2 (`memory.max/current/stat`) lalu v1 (`memory/memory.limit_in_bytes/usage_in_bytes/stat`);
  "terpakai" dikurangi page cache tidak aktif (`inactive_file`/`total_inactive_file`), seperti `docker stats` — berkas
  unduhan besar yang baru ditulis tidak lagi membuat worker menganggap RAM habis. Tanpa batas (`max`/> 2^60) atau tanpa
  cgroup: aturan host. Diuji dengan berkas cgroup v1 & v2 tiruan.
- Cadangan host (`MEM_RESERVE_MB`) hanya untuk RAM host; container memakai `CONTAINER_MEM_HEADROOM_MB` (bawaan 64,
  **minimum 32**). Tidak dijumlahkan berdua (diuji).
- Job paralel di satu proses (mode tanam): klaim → cek RAM ulang di bawah `ADMISSION_LOCK` → cadangkan kebutuhan job
  selama `RAM_RAMP_SECONDS` (60 dtk; setelahnya pemakaian terukur yang dihitung, tidak dihitung dua kali). Job yang
  tidak muat lagi dikembalikan ke antrean dengan pesan "Menunggu RAM server tersedia (…)", percobaan tidak bertambah.
  Diuji: dua thread bersamaan di container 512 MB → tepat satu job io diterima.
- Kebutuhan `IO_MEM_MB` 300 diukur, bukan ditebak (FFmpeg lokal 7.1, media 1080p 120 dtk 8 Mbit/s, ±120 MB):
  gabung video+audio `+faststart` (FFmpegMergerPP, juga jalur DASH) puncak **54,6 MB**; fixup HLS `.ts`→mp4
  **63,6 MB**; demux m3u8 lokal **61,2 MB**. Proses worker + yt-dlp termuat ±78 MB (sudah terhitung sebagai "terpakai").
  Anggaran worker-io: 78 + 300 + 64 = 442 MB < 512 MB.

**C — job tidak boleh menggantung diam-diam**
1. Alasan penolakan: kolom `reason` worker (sudah ada) + pesan job yang dikembalikan; admin kini menampilkan
   "tertahan N mnt" di samping status.
2. Worker hidup-tetapi-tertahan dibedakan: `blocked`/`blocked_seconds` di monitoring, metrik `ve_worker_blocked` &
   `ve_worker_blocked_seconds`; `manage worker-health` gagal bila semua worker di container tertahan admission lebih
   dari `WORKER_BLOCKED_UNHEALTHY_SECONDS` (bawaan 1800; opsi `--max-blocked`).
3. Metrik lama tertahan: `ve_worker_blocked_seconds` (per worker), di samping `ve_queue_oldest_wait_seconds`.
4–5. Kapasitas yang tidak akan pernah cukup (`resources.capacity_shortfall`: kebutuhan + ruang aman > batas container,
   atau kebutuhan + cadangan > RAM host total) dicatat sekali sebagai ERROR; worker itu tidak mengambil antrean
   tersebut (worker lain mungkin mampu). Bila **tidak satu pun** worker hidup mampu, job antre yang lebih tua dari
   `UNSERVABLE_GRACE_SECONDS` (bawaan 300) digagalkan dengan pesan "Tidak ada worker dengan RAM cukup … Hubungi admin
   server." Antrean tanpa worker hidup tidak digagalkan (worker mungkin sedang dimulai). Contoh nyata yang kini
   tertangkap: `WHISPER_MODEL=medium` (3200 MB) dengan `ANALYSIS_MEM` bawaan 3 GB — sebelumnya analisis menunggu selamanya.
   Tanpa perubahan skema (data disimpan di kolom JSON `workers.system`).

**Workflow & pemeriksa**
- Langkah baru "RAM host & container, admission, dan tidak ada OOM": `memory.max/peak/events` dan `resources.memory()`
  di worker-io, worker-render, web; gagal bila ada event OOM Docker sejak stack dinyalakan atau container `OOMKilled`;
  worker-io wajib mendeteksi batas 512 MB dan menerima job io; tidak ada worker `menunggu`.
- Pemeriksa pihak ketiga: wajib video+audio; trek `data`/`subtitle` tambahan diperbolehkan. Fixture terkendali tetap
  persis {video, audio} dan sha256 identik.

Regresi lokal (isi commit lokal): SQLite **317 lolos, 1 dilewati** (advisory lock PostgreSQL); PostgreSQL (database
baru) **333 lolos, 0 dilewati**. Catatan jujur: satu percobaan PostgreSQL sebelumnya memakai database lama yang gagal
di-reset (perintah reset saya salah role) dan menghasilkan 4 kegagalan akibat data sisa; diulang dengan database baru.

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

### Status kriteria penerimaan P5.5.1 (setelah run #4, `3e8de64`)

| # | Kriteria | Status |
|---|---|---|
| 1 | CI umum berhasil | PASS (CI #8) |
| 2 | Workflow image berhasil, tanpa skenario dilewati | PASS (#4); satu-satunya tes dilewati di image = advisory lock PostgreSQL, dijalankan & lolos di job PostgreSQL CI |
| 3 | Image produksi terbangun dari digest resmi | PASS |
| 4 | Compose staging berjalan, worker healthy | PASS |
| 5 | Migrasi & health (langsung & via Caddy) | PASS |
| 6 | Worker IO: admitted, antre → jalan → selesai, berkas benar | PASS (e2e fixture & pemulihan); **tanpa OOM: NOT VERIFIED** (tidak ada pemeriksaan eksplisit di run #4) |
| 7 | Egress: unduhan fixture, sha256, redirect/DNS/IPv4/IPv6 internal ditolak, log squid, koneksi langsung diblokir | PASS (42/42, 14/14, bukti squid) |
| 8 | Pemulihan SIGTERM/SIGKILL, antrean, cleanup, integritas, tanpa duplikat/menggantung | PASS (6/6) |
| 9 | Media: regresi produksi penuh | PASS (299 di image, read-only) |
| 10 | Tidak ada kebocoran secret; hasil dicatat | PASS |
| B | Admission RAM host/container | perbaikan lokal + tes; bukti RAM/OOM staging: NOT VERIFIED sampai run #5 |
| C | Job tidak menggantung diam-diam | perbaikan lokal + tes; belum diuji di staging |
