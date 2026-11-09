# Profil Hostinger KVM 2 (2 vCPU, 8 GB RAM, tanpa GPU) — uji terbatas

Profil ini untuk **pengujian terbatas** (1–3 pengguna aktif bersamaan, video ≤ 15 menit). Panduan umum: `DEPLOY.md`.

## Isi profil

| Berkas | Fungsi |
|---|---|
| `deploy/kvm2.env.example` | Templat `.env`: Whisper `base` + mode per potongan, batas 15 menit, satu worker berat, media 2/5 jam |
| `deploy/compose.kvm2.yml` | Batas RAM/CPU untuk database, web, dan Caddy (worker dibatasi lewat `.env`) |
| `deploy/compose.behind-proxy.yml` | Untuk VPS yang **sudah** punya web server di port 80/443: Caddy tidak dijalankan, aplikasi hanya di `127.0.0.1:8001` |

## Anggaran sumber daya

Batas `cpus` Docker adalah **plafon per container**, bukan jatah. Karena itu jumlah seluruh plafon layanan yang
berjalan bersamaan dibuat **≤ 1,8 vCPU**: walau semua layanan aplikasi sibuk sekaligus, minimal 0,2 vCPU selalu
tersisa untuk OS dan layanan lain di VPS. (Profil P0 sebelumnya berjumlah 4,75 vCPU: analisis dan render yang
berjalan bersamaan bisa memakan seluruh 2 vCPU.)

| Layanan | CPU (plafon) | RAM (batas) |
|---|---|---|
| worker berat ×1: render (didahulukan) lalu analisis, satu job sekaligus | 1,2 vCPU | 2 GB |
| worker-io ×1 (unduhan dari link) | 0,15 vCPU | 0,5 GB |
| web (2 proses) | 0,2 vCPU | 0,75 GB |
| PostgreSQL | 0,15 vCPU | 0,5 GB |
| Caddy | 0,1 vCPU | 0,13 GB |
| **Total** | **1,8 vCPU** (sisa ≥ 0,2) | **±3,9 GB** (sisa ±4 GB untuk OS, cache disk & layanan lain) |
| `migrate` (hanya sebelum layanan lain mulai) | 0,5 vCPU | 0,5 GB |

**Mengapa satu worker berat.** Di 2 vCPU, Whisper dan FFmpeg yang berjalan bersamaan hanya saling memperlambat;
total waktunya tidak lebih cepat. Satu worker berat membuat setiap job memakai jatah CPU penuhnya, RAM puncak
lebih kecil (tidak ada model Whisper + FFmpeg sekaligus), dan render tetap didahulukan dari analisis yang belum
dimulai. Konsekuensinya: render yang diajukan saat analisis sedang berjalan menunggu analisis itu selesai, dan
statusnya menjelaskan hal ini di layar. Bila nanti VPS lebih besar, kembali ke dua worker
(`RENDER_WORKERS=1`, `ANALYSIS_QUEUES=analisis`) dan hitung ulang total plafon.

**Diuji otomatis:**
- `tests/test_p01_kvm2_profile.py` menghitung total plafon dari `docker compose config` (profil ini, dengan dan
  tanpa Caddy). Perubahan yang melewati 1,8 vCPU atau 4,5 GB gagal di CI. Tes ini juga memastikan batas RAM worker
  berat ≥ kebutuhan analisis video 15 menit dan render menurut model memori aplikasi.
- `.github/workflows/kvm2.yml` (Simulasi KVM 2) menjalankan seluruh stack di cgroup bertopi **2 vCPU / 8 GB tanpa
  swap**, bersama container "layanan lain" yang memegang 3 GB RAM. Bebannya video ucapan 15 menit (Whisper base asli)
  dan tiga render. Lulus bila tidak ada OOM, semua job selesai, dan layanan lain tetap mendapat CPU.

## Kecepatan: belum dapat dijanjikan

Belum ada pengukuran di VPS KVM 2 sungguhan. Waktu di simulasi CI berasal dari runner GitHub (prosesor dan
penjadwalan berbeda), sehingga **hanya indikatif**. Jangan menjanjikan waktu analisis/render kepada pengguna sebelum
benchmark di VPS nyata (langkah ini dilakukan setelah deploy uji disetujui). Perkiraan kasar: analisis video 15 menit
dengan `base` pada 1,2 vCPU mungkin 10–20 menit; render klip 20 detik mungkin 1–2 menit.

## Langkah (setelah disetujui, di VPS uji — bukan server produksi)

```bash
git clone -b v2/phase-1 https://github.com/aiolaf9/video-editing.git /opt/video-editing
cd /opt/video-editing && cp deploy/kvm2.env.example .env && chmod 600 .env
```

Isi `DOMAIN`, `POSTGRES_PASSWORD` (`openssl rand -hex 24`), `ADMIN_EMAIL`, `ADMIN_PASSWORD` di `.env`.

**A. Port 80/443 kosong** (Caddy mengurus HTTPS):

```bash
docker compose -f docker-compose.yml -f deploy/compose.kvm2.yml up -d --build
```

**B. VPS sudah punya web server** (Nginx/Apache/panel): aplikasi di `127.0.0.1:8001`, web server yang ada
meneruskan subdomain ke sana (`proxy_pass http://127.0.0.1:8001;`, `client_max_body_size 2100m;`,
`proxy_read_timeout 1800s;`), HTTPS diurus web server tersebut:

```bash
docker compose -f docker-compose.yml -f deploy/compose.kvm2.yml -f deploy/compose.behind-proxy.yml up -d --build
```

Cek: `docker compose ps` dan `curl -s https://DOMAIN/api/health`.

## Perilaku penting

- **Worker tidak aktif → pekerjaan baru ditolak** dengan pesan jelas (HTTP 503), termasuk sebelum upload besar
  dimulai. Upload yang sudah berjalan tidak diputus; job-nya disimpan dan menunggu dengan status
  "Server pemrosesan sedang tidak aktif … dilanjutkan otomatis". Matikan dengan `REQUIRE_LIVE_WORKER=0`.
- **Media sementara:** 2 jam setelah aktivitas terakhir, paling lama 5 jam sejak upload; peringatan 30 menit
  sebelum dihapus. Pembersihan berjalan di worker (juga langsung setelah restart) dan cadangan di proses web.
- **Batas pemulihan job (`MEDIA_JOB_GRACE_HOURS`, bawaan 1 jam):** job yang masih antre, dibatalkan, atau
  worker-nya mati hanya menahan media sampai masa simpan + 1 jam (memberi waktu worker pulih). Setelah itu job
  diakhiri "gagal (kedaluwarsa)" dengan pesan jelas, lalu file dihapus. Media tidak pernah tersimpan tanpa batas
  waktu.
- **Toleransi khusus untuk pekerjaan yang benar-benar berjalan:** job yang prosesnya hidup (heartbeat < 2 menit,
  FFmpeg/Whisper aktif) tidak dihentikan dan filenya tidak dihapus meski lewat 5 jam. Batasnya adalah batas waktu
  job itu sendiri (render: menurut panjang klip; analisis: menurut durasi video; maksimum `JOB_TIMEOUT_MAX`).
  Begitu job selesai, media langsung dihapus. File yang masih dibuka proses di mesin yang sama juga tidak dihapus.
- **AI lokal:** Whisper, pemilihan klip, dan "⚡ Otomatis" tanpa API berbayar. AI eksternal opsional (menu Admin).
