# Rencana Staging & Real-World Acceptance Test — Hostinger KVM 2

Status: **RENCANA UNTUK DIREVIEW.** Belum ada instalasi, deployment, perubahan DNS/firewall/cPanel, atau sentuhan
ke layanan produksi. Setiap tahap bertanda 🔒 baru dijalankan setelah disetujui tertulis.

| | |
|---|---|
| Versi aplikasi | `v2/phase-1` @ `2496391` (P0, P0.1, P1, P2; semua CI, Production image & Simulasi KVM 2 hijau) |
| Target | Hostinger KVM 2: 2 vCPU, 8 GB RAM, Ubuntu (VPS yang sama dengan layanan lain yang sudah berjalan) |
| Tidak boleh berubah | `editvideo.nurulhayat.org`, situs/layanan lain di VPS, DNS, cPanel, firewall, layanan produksi |
| Pelaksana di VPS | Sesi Claude Code di komputer Anda (punya kunci SSH). Sesi cloud ini tidak bisa menjangkau VPS. |

## Ringkasan alur

| Tahap | Isi | Mengubah VPS? | Persetujuan |
|---|---|---|---|
| 0 | Audit hanya-baca (`deploy/audit/vps_audit.sh`) + baseline layanan lain | Tidak | Sudah boleh dijalankan |
| 1 | Review hasil audit → pilih opsi akses HTTPS & sesuaikan rencana | Tidak | — |
| 2 | 🔒 Snapshot hPanel, (bila perlu) pasang Docker, deploy staging terisolasi | Ya | Wajib |
| 3 | 🔒 Uji end-to-end di VPS nyata | Hanya data staging | Wajib |
| 4 | 🔒 Benchmark 5 & 15 menit, dua pengguna | Hanya data staging | Wajib |
| 5 | 🔒 Uji HP fisik (Android Chrome, iPhone Safari) | Tidak | Wajib |
| 6 | Laporan hasil (`docs/STAGING_REPORT_TEMPLATE.md`) → keputusan lanjut / perbaiki / rollback | — | — |

---

## 1. Pemeriksaan non-destruktif (Tahap 0)

### 1.1 Skrip audit

`deploy/audit/vps_audit.sh` hanya membaca dan tidak menulis berkas. Keduanya dijaga tes `tests/test_staging_audit.py`:

- uji statis menolak `apt`, `systemctl start|stop|restart`, `docker run|pull|compose up`, `iptables -A`, `ufw allow`,
  `csf -r`, `rm`, `sed -i`, pengalihan ke berkas, `curl -X POST`, dan sejenisnya;
- uji jalan nyata membuktikan tidak ada berkas yang tercipta.

Isi laporan:

| Bagian | Isi |
|---|---|
| Sistem | OS, kernel, virtualisasi, cgroup v1/v2, sinkron waktu, reboot tertunda |
| CPU | jumlah vCPU, model, `vmstat` (termasuk **steal** dari hypervisor), PSI |
| RAM | total/tersedia, swap, 12 proses terbesar (nama saja, tanpa argumen yang bisa berisi rahasia) |
| Disk | ruang & inode, lokasi data Docker |
| Port & layanan | port yang mendengar + pemiliknya, khususnya 80/443/8001/18001/5432; layanan systemd; panel (cPanel/CyberPanel/Plesk/aaPanel/Hestia) & web server |
| Docker | versi, driver cgroup, container/jaringan/subnet yang ada, compose project, pemakaian disk |
| Firewall | ufw / firewalld / **CSF** (kunci `TESTING`, `DOCKER`, `TCP_IN`), jumlah aturan iptables/nftables, fail2ban, `ip_forward` |
| Backup | alat backup, cron berisi backup/dump. Snapshot hPanel diperiksa manual. |
| Jaringan | IP, DNS & sertifikat TLS `editvideo.nurulhayat.org` (hanya baca), akses keluar ke huggingface.co, pypi, Docker Hub, GitHub (HEAD) |
| Keamanan dasar | `PermitRootLogin`/`PasswordAuthentication`, jumlah akun shell, ulimit |
| Ringkasan | tanda bahaya otomatis (RAM < 5 GB tersedia, disk < 30 GB, port 80/443 terpakai, Docker belum ada, CSF, steal ≥ 10%, subnet bentrok) |

### 1.2 Cara menjalankan (dari komputer Anda, hanya baca)

PowerShell Windows, di folder repo. `tr -d '\r'` mencegah error akhir baris CRLF dari checkout Windows:

```
Get-Content deploy\audit\vps_audit.sh | ssh -i $HOME\.ssh\nurulhayat_root root@server.nurulhayat.org "tr -d '\r' | bash -s" > audit-vps.txt
```

Atau Linux/macOS/Git Bash:

```bash
ssh root@server.nurulhayat.org 'bash -s' < deploy/audit/vps_audit.sh > audit-vps.txt
```

Domain yang dicek bisa ditambah tanpa mengubah apa pun, mis. `AUDIT_DOMAINS="editvideo.nurulhayat.org nurulhayat.org"`.
Lalu lintas keluar bisa dimatikan dengan `AUDIT_NET=0`. Laporan berisi IP, nama host, dan nama proses: bagikan hanya
ke tim, jangan dipublikasikan.

### 1.3 Baseline layanan lain (sebelum apa pun dipasang)

Selama 15 menit, catat waktu respons setiap situs/layanan lain di VPS. Jalankan dari komputer Anda, bukan dari
VPS, agar tidak menambah beban:

```bash
for i in $(seq 1 30); do curl -s -o /dev/null -w '%{http_code} %{time_total}\n' https://SITUS-LAIN/; sleep 30; done > baseline-situs.txt
```

Ditambah satu laporan audit di jam sibuk dan satu di jam sepi. Angka p50/p95 dari baseline menjadi acuan kriteria
"tidak mengganggu layanan lain" (§8).

### 1.4 Hal yang dicek manual di hPanel

- Tanggal snapshot terakhir dan apakah slot snapshot tersedia.
- Backup mingguan Hostinger aktif.
- Firewall jaringan hPanel (aturan masuk), bila dipakai.

---

## 2. Rencana deployment staging terisolasi (🔒 Tahap 2)

### 2.1 Prinsip isolasi

| Aspek | Staging | Dampak ke layanan lain |
|---|---|---|
| Direktori | `/opt/avs-staging` (clone baru, commit dipatok) | tidak menyentuh direktori lain |
| Nama project Compose | `avs-staging` → container, volume & jaringan berawalan `avs-staging_` | terpisah dari container lain |
| Port publik | **tidak ada**. Web hanya di `127.0.0.1:18001` (atau port bebas lain menurut audit). Caddy tidak dijalankan bila 80/443 terpakai. | 80/443 milik web server yang ada tidak disentuh |
| CPU | total plafon 1,8 vCPU, 1 worker berat (tes `test_p01_kvm2_profile.py`) | ≥ 0,2 vCPU selalu tersisa |
| RAM | total batas ±3,9 GB; worker menunda job bila RAM host tersedia < 1 GB (`MEM_RESERVE_MB`) | aplikasi mengalah bila layanan lain butuh RAM |
| Disk | volume `avs-staging_*`; media sementara 2/5 jam + kuota unit; log container dirotasi | dipantau; batas di §8 |
| Data | database PostgreSQL staging sendiri; tidak ada data produksi | — |
| URL import | worker-io hanya lewat egress-proxy (`deploy/compose.staging.yml`), IP privat/metadata diblokir | tidak bisa menjangkau layanan internal VPS |

### 2.2 Prasyarat

- [ ] Audit dan baseline ditinjau.
- [ ] RAM tersedia ≥ 5 GB di jam sibuk.
- [ ] Disk kosong ≥ 30 GB.
- [ ] Steal CPU < 10%.
- [ ] **Snapshot hPanel dibuat tepat sebelum Tahap 2**, sebagai jaring pengaman terakhir.
- [ ] Opsi akses HTTPS dipilih (§3).
- [ ] Jendela waktu sepi disepakati (build image memakai CPU penuh beberapa menit).

### 2.3 Docker

**Bila Docker sudah ada**, pakai daemon yang ada; tidak ada perubahan sistem.

**Bila belum ada**, pemasangan Docker adalah perubahan sistem yang paling berisiko:
- Docker menambah aturan iptables dan bridge jaringan, serta mengaktifkan `ip_forward`.
- Bila audit menemukan **CSF**, restart CSF menghapus aturan Docker. Karena itu perlu pengaturan `DOCKER = "1"` di
  `csf.conf`, yang merupakan perubahan firewall dan butuh persetujuan terpisah.
- Bila ada ufw, port yang dipublikasikan Docker melewati ufw. Staging hanya memakai `127.0.0.1`, jadi tidak
  terbuka keluar.
- Langkah memakai paket resmi `download.docker.com` (versi dicatat). Bila risiko ini tidak diterima, alternatifnya
  adalah VPS staging terpisah.

### 2.4 Langkah (setelah disetujui; ringkas)

```bash
# Kode (commit dipatok, bukan cabang yang bergerak)
git clone -b v2/phase-1 https://github.com/aiolaf9/video-editing.git /opt/avs-staging
cd /opt/avs-staging && git checkout 2496391
cp deploy/kvm2.env.example .env && chmod 600 .env
# .env: COMPOSE_PROJECT_NAME=avs-staging, APP_PORT=18001, DOMAIN=<host staging>, POSTGRES_PASSWORD & ADMIN_* acak
# (openssl rand -hex 24), ALLOW_URL_IMPORT=admins, COOKIE_SECURE=1, WHISPER_MODEL=base, WHISPER_CHUNKED=1

F="-f docker-compose.yml -f deploy/compose.kvm2.yml -f deploy/compose.staging.yml -f deploy/compose.behind-proxy.yml"
docker compose $F config -q                      # validasi tanpa menjalankan apa pun
docker compose $F build                           # di jendela sepi; pantau load (§2.5)
docker compose $F up -d
docker compose $F ps && curl -s http://127.0.0.1:18001/api/health
```

- `compose.staging.yml` menambah hardening: filesystem read-only, `cap_drop ALL`, isolasi jaringan worker-io, dan
  egress-proxy.
- `compose.behind-proxy.yml` menonaktifkan Caddy dan mengikat web hanya ke `127.0.0.1`.

Berkas kecil yang **akan dibuat setelah rencana disetujui** (bukan sekarang):

| Berkas | Fungsi |
|---|---|
| `deploy/compose.logrotate.yml` | log container `json-file` maks 10 MB × 3, tanpa mengubah `daemon.json` |
| `deploy/compose.tunnel.yml` | opsi A §3: container `cloudflared` dengan versi dipatok |
| `deploy/staging.env.example` | `kvm2.env.example` + pengaturan staging di atas |
| `deploy/checks/staging_acceptance.py` | harness e2e & benchmark lewat API (pola `kvm2_load.py`) |
| `deploy/audit/monitor.sh` | pencatat hanya-baca selama uji: `docker stats`, `vmstat`, PSI, latensi situs lain |

### 2.5 Build image tanpa mengganggu

`docker build` memakai CPU penuh beberapa menit, karena build berjalan di daemon tanpa plafon Compose. Pilihan:

- **(a) Disarankan:** build di VPS saat jam sepi, dengan `monitor.sh` berjalan. Hentikan build (Ctrl-C) bila p95
  situs lain naik > 50% dari baseline.
- **(b)** Build di GitHub Actions lalu `docker save | gzip`, salin ke VPS, `docker load`. Tidak ada beban build di
  VPS, tetapi berkasnya ±0,5 GB dan perlu langkah tambahan.

### 2.6 Prosedur rollback

| Situasi | Perintah (di `/opt/avs-staging`) | Efek |
|---|---|---|
| Layanan lain melambat / RAM menipis | `docker compose $F stop` | staging berhenti seketika, data staging tetap |
| Hentikan staging, simpan data | `docker compose $F down` | container & jaringan dihapus, volume tetap |
| Hapus total staging | `docker compose $F down -v --rmi local && rm -rf /opt/avs-staging` | semua jejak staging hilang (setelah persetujuan) |
| Tunnel/subdomain | hentikan container tunnel / hapus record DNS staging (opsi B) | `editvideo.nurulhayat.org` tidak terpengaruh |
| Docker dipasang khusus staging | uninstall paket Docker + kembalikan catatan firewall/CSF | VPS kembali seperti sebelum Tahap 2 |
| Terakhir: sistem rusak | restore snapshot hPanel | ⚠️ seluruh VPS mundur ke waktu snapshot; perubahan situs lain setelahnya ikut hilang |

**Pemicu rollback otomatis (keputusan operator, tanpa menunggu):**
- 5xx di situs lain;
- p95 situs lain > 2× baseline selama ≥ 5 menit;
- RAM tersedia host < 700 MB selama ≥ 2 menit;
- swap aktif terus bertambah;
- disk kosong < 10 GB;
- konektivitas/firewall VPS terganggu.

---

## 3. Akses HTTPS tanpa mengubah `editvideo.nurulhayat.org`

| Opsi | Cara | Perubahan yang dibutuhkan | Kelebihan | Kekurangan |
|---|---|---|---|---|
| **A. Cloudflare Tunnel (quick tunnel)** | container `cloudflared` di project staging → `https://<acak>.trycloudflare.com` ke `web:8000` | tidak ada DNS, port masuk, firewall, atau cPanel; hanya koneksi keluar | paling sedikit sentuhan; HTTPS valid → PWA bisa dipasang di HP | URL acak berganti bila tunnel restart; lalu lintas & TLS lewat Cloudflare (pihak ketiga); tanpa SLA, untuk uji saja; batas 100 MB per permintaan (aman: potongan upload 8–64 MB) |
| **B. Subdomain staging baru**, mis. `staging-video.nurulhayat.org` | 1 record A baru ke IP VPS + sertifikat Let's Encrypt (Caddy bila 80/443 bebas; bila tidak, vhost di web server yang ada → `127.0.0.1:18001`) | **1 record DNS baru** (record `editvideo` tidak disentuh) + Caddy/vhost | paling mirip produksi, tanpa pihak ketiga, URL tetap | butuh persetujuan perubahan DNS; bila 80/443 terpakai perlu vhost di web server yang ada (bila itu cPanel → menyentuh cPanel) |
| **C. SSH tunnel** | `ssh -L 18001:127.0.0.1:18001 root@server` → `http://localhost:18001` | tidak ada | nol perubahan, privat | hanya desktop admin; HP fisik tidak bisa (PWA butuh HTTPS) |

**Rekomendasi:**
- **C** untuk pemeriksaan admin pertama.
- **A** untuk uji HP fisik dan benchmark dalam jendela uji singkat; tunnel dimatikan setelah selesai.
- **B** bila staging akan hidup lebih lama.

Keputusan diambil setelah audit: bila 80/443 bebas, B dengan Caddy staging. Bila panel memegang 80/443, A
(tanpa menyentuh panel). Data uji memakai video uji/izin, bukan konten sensitif, karena opsi A melewati Cloudflare.

---

## 4. Menjaga layanan lain tidak terganggu

1. **Batas keras container:**
   - total 1,8 vCPU / ±3,9 GB;
   - `MEM_RESERVE_MB=1024`, sehingga worker tidak mengambil job bila RAM host menipis;
   - satu job berat sekaligus.
2. **Tidak ada port publik baru**, tidak menyentuh 80/443 milik layanan lain, dan tidak mengubah konfigurasi web
   server/panel.
3. **Pemantauan selama setiap tahap:**
   - `monitor.sh` mencatat setiap 5 detik `docker stats`, `vmstat`, PSI memory/cpu/io, `df`, dan latensi situs lain
     (dari komputer Anda);
   - pemicu rollback di §2.6.
4. **Uji berat (benchmark)** hanya di jendela sepi yang disepakati; jendela itu diumumkan ke pemilik layanan lain.
5. **Sesudah tiap tahap**, jalankan ulang audit. Bandingkan RAM/disk/port dengan Tahap 0; selisih harus dijelaskan.

---

## 5. Uji end-to-end di VPS nyata (🔒 Tahap 3)

Lewat URL staging (opsi A/B), di browser desktop dan HP. Setiap butir dicatat di laporan beserta bukti
(tangkapan layar/log/ffprobe).

| # | Uji | Langkah | Lulus bila |
|---|---|---|---|
| E1 | Akun | Superadmin membuat unit + 2 editor (unit sama) + 1 editor unit lain | login berhasil; editor tidak bisa membuat akun; unit lain tidak melihat proyek |
| E2 | Upload | Video 5 menit 1080p (±200–400 MB) dari desktop; jeda & lanjutkan di tengah; putuskan Wi-Fi 30 dtk | selesai tanpa mulai dari nol; ukuran & sha256 sama dengan sumber |
| E3 | URL import | (a) URL MP4 publik yang dikendalikan (mis. berkas uji di hosting sendiri); (b) satu tautan YouTube; (c) `http://127.0.0.1/`, `http://169.254.169.254/` | (a) selesai; (b) selesai **atau** gagal dengan pesan jelas (IP datacenter sering diblokir; dicatat, bukan blocker); (c) ditolak |
| E4 | Whisper | Analisis video E2 (bahasa Indonesia) | selesai; transkrip berisi ucapan; status antre/berjalan jelas (tanpa "0%" yang tidak berubah) |
| E5 | Klip AI | Lihat kandidat klip (target 30 dtk) | ≥ 3 kandidat untuk video 5 menit; rentang di dalam durasi |
| E6 | Edit | Crop (geser), rasio 9:16, filter + penyesuaian warna, template subtitle, koreksi 1 segmen teks, animasi Otomatis | pratinjau berubah langsung; tidak ada job baru dari edit (cek daftar job) |
| E7 | Autosave | Edit → muat ulang halaman; buka di HP bersamaan, ubah dari dua perangkat | pengaturan kembali utuh; banner konflik muncul, tidak menimpa diam-diam |
| E8 | Preview | Putar, seek, putar klip | proxy 480p (atau salinan) mulai ≤ 3 dtk di Wi-Fi |
| E9 | Render | Render 30 dtk & 60 dtk dari resep; render massal 3 klip | MP4 1080×1920, durasi ±0,2 dtk, audio ada, subtitle terkoreksi tampil; frame sesuai pratinjau (cek visual) |
| E10 | Download | Unduh di desktop, Android, iPhone | berkas utuh & bisa diputar |
| E11 | Cleanup | Jendela uji khusus: `MEDIA_RETENTION_HOURS=0.1`, `MEDIA_MAX_HOURS=0.3` (hanya staging), lalu kembalikan | media terhapus sesuai jadwal; resep & riwayat tetap; peringatan tampil sebelum dihapus |
| E12 | Pemulihan | `docker compose $F kill worker-analisis` saat analisis; `restart` semua saat render antre | status "server tidak aktif" jelas; job berlanjut/diulang setelah pulih; tidak ada job menggantung |
| E13 | Backup | `deploy/backup.sh` (dengan `-p avs-staging`) + restore ke database sementara (`docs/BACKUP_RESTORE.md`) | dump valid; restore berisi akun & resep |
| E14 | Keamanan | Header `/api` `no-store`; HTTPS valid; percobaan akses proyek unit lain; ukuran upload > batas | sesuai; 404/413 dengan pesan jelas |

---

## 6. Benchmark (🔒 Tahap 4, jendela sepi)

### Bahan uji

| Nama | Isi |
|---|---|
| V5 / V15 | 5:00 & 15:00, 1080p30 H.264 + AAC, ucapan bahasa Indonesia asli (rekaman sendiri dengan izin, atau korpus FLEURS id seperti di CI) |
| VH | 1 menit rekaman iPhone **HEVC .mov** (format bawaan kamera iPhone), untuk uji kompatibilitas |

### Metrik per run

- **Waktu:**
  - durasi upload & throughput;
  - pratinjau siap;
  - menunggu di antrean;
  - **lama analisis & RTF** (lama analisis ÷ durasi video);
  - lama render klip 30 dtk & 60 dtk (9:16 + subtitle + filter, dengan/tanpa animasi).
- **Sumber daya:**
  - RAM puncak per container (`memory.peak`/`docker stats`);
  - RAM tersedia host minimum;
  - load average;
  - **steal**;
  - PSI memory/io;
  - pemakaian disk sebelum & sesudah cleanup.
- **Dampak:** latensi p50/p95 situs lain dibanding baseline, dan jumlah 5xx.

### Skenario

| ID | Skenario | Pengulangan |
|---|---|---|
| B1 | V5 sendirian: upload → analisis → render 30 & 60 dtk | 3× (median); run pertama dicatat terpisah karena mengunduh model) |
| B2 | V15 sendirian, sama dengan B1 | 3× |
| B3 | **Dua pengguna:** A menganalisis V15; bersamaan B mengunggah V5, mengedit, lalu meminta 2 render | 2× |
| B4 | **Dua pengguna render:** A & B masing-masing 2 render 30 dtk bersamaan | 2× |
| B5 | VH (HEVC iPhone): upload → pratinjau → render | 1× |

- **Harapan B3/B4:** satu worker berat memproses berurutan. Render didahulukan dari analisis yang belum mulai.
  Status di layar harus menjelaskan antrean, dan tidak ada job gagal.
- **Hasil:** JSON per run + ringkasan di laporan. Perkiraan simulasi CI tidak dipakai sebagai hasil.

---

## 7. Checklist HP fisik (🔒 Tahap 5)

Minimal 1 Android (Chrome terbaru) + 1 iPhone (Safari, iOS 16.4 ke atas). Catat model, versi OS & browser, dan
jaringan (Wi-Fi / 4G). Kolom hasil: Lulus / Gagal / Catatan + tangkapan layar.

| # | Uji | Android Chrome — harapan | iPhone Safari — harapan |
|---|---|---|---|
| M1 | Buka URL staging & login | HTTPS valid, tanpa peringatan | sama |
| M2 | Pasang PWA | tombol 📲 → prompt asli → ikon di layar utama | 📲 → petunjuk → Bagikan → Tambahkan ke Layar Utama → ikon muncul |
| M3 | Buka dari ikon | mode standalone (tanpa bilah alamat), tema gelap | standalone. Catatan: PWA iOS punya penyimpanan cookie sendiri, jadi login ulang sekali itu wajar. |
| M4 | Upload dari galeri | video kamera HP (Android: H.264/HEVC) | video kamera iPhone (**HEVC .mov**) & yang dipilih "Most Compatible" |
| M5 | Upload saat aplikasi ke latar belakang | pindah ke WhatsApp 30 dtk lalu kembali | sama; layar dikunci 30 dtk |
| M6 | Pemutaran **H.264** pratinjau | putar, seek, putar klip lancar | sama (inline, tidak memaksa layar penuh) |
| M7 | Seret timeline & crop dengan jari | pegangan mengikuti jari, pratinjau ikut | sama |
| M8 | Subtitle & template | pratinjau menempel di atas saat menggulir; template berubah langsung | sama |
| M9 | **Keyboard virtual** | koreksi teks transkrip: halaman tidak zoom, kolom tidak tertutup keyboard, tombol Simpan terjangkau | sama (perhatikan bilah bawah saat keyboard terbuka) |
| M10 | Autosave & **latar belakang** | ubah pengaturan → langsung kunci layar → buka di laptop: perubahan ada | sama |
| M11 | Render dari bilah bawah | job selesai, status jelas | sama |
| M12 | **Download** | berkas ke Downloads, bisa diputar di Galeri/Files | unduhan Safari → Files; Simpan Video ke Foto lewat Bagikan |
| M13 | Offline | mode pesawat → buka ikon → halaman "Tidak ada koneksi" | sama |
| M14 | Keluar akun | kembali ke login; tidak ada data proyek tersisa saat offline | sama |
| M15 | Orientasi & ukuran | potret & lanskap: tanpa gulir samping, tombol ≥ 44 px | sama |

---

## 8. Kriteria lulus/gagal

| Area | **Lulus** | **Lulus bersyarat** (boleh lanjut dengan tindak lanjut) | **Gagal** (perbaiki dulu) |
|---|---|---|---|
| Fungsional (E1–E14) | semua lulus | E3b (YouTube diblokir dari IP datacenter) atau ≤ 1 butir kosmetik | ada butir lain yang gagal |
| Kehilangan data | 0 (restart, kill worker, konflik) | — | ada data hilang / tertimpa diam-diam |
| Analisis V5 (median, model base) | ≤ 6 menit (RTF ≤ 1,2) | ≤ 10 menit | > 10 menit atau gagal |
| Analisis V15 (median) | ≤ 18 menit (RTF ≤ 1,2) | ≤ 30 menit | > 30 menit, gagal, atau OOM |
| Render klip 30 dtk | ≤ 2 menit | ≤ 4 menit | > 4 menit atau gagal |
| Simpan resep (autosave) | p95 ≤ 500 ms | ≤ 1 dtk | > 1 dtk atau error |
| Dua pengguna (B3/B4) | semua job selesai; status antre jelas; tidak ada timeout | waktu tunggu > 2× sendirian, tetapi selesai | ada job gagal / menggantung |
| OOM | 0 event OOM (Docker & kernel) | — | ≥ 1 |
| RAM host | tersedia ≥ 1 GB di setiap sampel | ≥ 700 MB sesaat (< 2 menit) | < 700 MB ≥ 2 menit / swap terus naik |
| Layanan lain | 0 tambahan 5xx; p95 naik ≤ 25% dari baseline saat benchmark | naik ≤ 50% | 5xx baru atau naik > 50% / rollback terpicu |
| Disk | kembali ke baseline + ≤ 3 GB setelah cleanup | ≤ 6 GB | terus bertambah |
| HP fisik (M1–M15) | semua lulus di kedua perangkat | ≤ 2 butir non-kritis gagal (mis. M15 lanskap) | M2, M4, M6, M10, atau M12 gagal di salah satu perangkat |

**Keputusan akhir:**
- semua "Lulus" → siap rencana produksi;
- ada "Lulus bersyarat" → lanjut dengan daftar tindak lanjut;
- ada "Gagal" → perbaiki, lalu uji ulang butir terkait.

## 9. Laporan

Isi `docs/STAGING_REPORT_TEMPLATE.md`:
- ringkasan audit;
- log deployment (perintah + waktu + versi);
- tabel E1–E14, B1–B5, M1–M15 dengan bukti;
- grafik/angka sumber daya;
- dampak ke layanan lain;
- daftar masalah dengan tingkat keparahan;
- keputusan.

Laporan menyebut dengan jelas apa yang diuji di **perangkat fisik**, apa yang di emulasi, dan apa yang belum diuji.
