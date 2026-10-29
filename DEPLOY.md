# Panduan Deploy ke Server Cloud

Panduan memasang aplikasi di server cloud supaya bisa dipakai banyak unit lewat browser, dengan login.

## Gambaran

```
Pengguna (browser) ──HTTPS──▶ Caddy ──▶ web (2 proses)  ──▶ PostgreSQL
                                             │ antrean di database
                                             ▼
                                worker × N (Whisper + FFmpeg)   worker-unduh
```

- **web**: halaman & API, ringan. Tetap cepat walau banyak render berjalan.
- **worker**: mengerjakan analisis (transkrip) dan render satu per satu dari antrean.
  Bila antrean sering panjang, tambah jumlah worker.
- Semua pekerjaan tersimpan di database: bila worker/server restart, pekerjaan diulang otomatis.

## 1. Pilih server

Untuk ratusan pengguna (puluhan aktif bersamaan), mulai dari:

| Kebutuhan | Rekomendasi awal |
|---|---|
| CPU / RAM | **8 vCPU, 16 GB RAM** |
| Disk | 200 GB SSD (video besar; atur `RETENTION_DAYS` agar file lama terhapus) |
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
| Antrean analisis/render sering panjang (lihat **Admin → Status**) | Naikkan `HEAVY_WORKERS` (±1 per 2–3 vCPU) |
| Transkrip terasa lama | `WHISPER_MODEL=base` (lebih cepat, sedikit kurang akurat), atau server dengan vCPU lebih banyak |
| Disk cepat penuh | Turunkan `RETENTION_DAYS`, turunkan `MAX_UPLOAD_MB` |
| Satu unit memakai terlalu banyak | Atur kuota render/AI per unit di **Admin → Unit** |

Aturan praktis: `HEAVY_WORKERS × WHISPER_THREADS ≈ jumlah vCPU`.

Uji beban di lingkungan pengembangan (4 vCPU, 4 proses web, 2 worker, PostgreSQL):
200 pengguna aktif + 40 render diajukan bersamaan → 40/40 render selesai, 0 error,
waktu respons halaman median 28 ms (p95 90 ms).

## Update aplikasi

```bash
cd /opt/video-editing && git pull && docker compose up -d --build
```

Data (akun, proyek, video) tersimpan di volume Docker dan tidak hilang saat update.

## Backup

Backup database harian (akun, unit, proyek, rencana animasi):

```bash
crontab -e
```

Tambahkan baris:

```
0 2 * * * cd /opt/video-editing && ./deploy/backup.sh >> backups/backup.log 2>&1
```

Simpan juga salinan folder `backups/` di luar server (mis. object storage). File video ada di volume
`video-editing_appdata`; sertakan dalam snapshot disk dari penyedia cloud bila hasil render perlu diamankan.

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
  Pastikan konten yang diolah memang milik/izin organisasi.
