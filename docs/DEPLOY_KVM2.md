# Profil Hostinger KVM 2 (2 vCPU, 8 GB RAM, tanpa GPU) — uji terbatas

Profil ini untuk **pengujian terbatas** (1–3 pengguna aktif bersamaan, video ≤ 15 menit). Panduan umum: `DEPLOY.md`.

## Isi profil

| Berkas | Fungsi |
|---|---|
| `deploy/kvm2.env.example` | Templat `.env`: Whisper `base` + mode per potongan, batas 15 menit, 1 analisis + 1 render bersamaan, media 2/5 jam |
| `deploy/compose.kvm2.yml` | Batas RAM/CPU untuk database, web, dan Caddy (worker dibatasi lewat `.env`) |
| `deploy/compose.behind-proxy.yml` | Untuk VPS yang **sudah** punya web server di port 80/443: Caddy tidak dijalankan, aplikasi hanya di `127.0.0.1:8001` |

## Anggaran sumber daya

| Layanan | CPU (batas) | RAM (batas) |
|---|---|---|
| worker-analisis ×1 (Whisper base, chunked) | 1,5 vCPU (prioritas rendah) | 2 GB (terhitung ±0,75 GB untuk video 15 menit) |
| worker-render ×1 (FFmpeg) | 1,5 vCPU (prioritas tinggi) | 1,5 GB |
| worker-io ×1 | 0,25 vCPU | 0,5 GB |
| web (2 proses) | 0,75 vCPU | 0,75 GB |
| PostgreSQL | 0,5 vCPU | 0,5 GB |
| Caddy | 0,25 vCPU | 0,13 GB |
| **Batas total aplikasi** | | **±5,4 GB** — sisa ±2,5 GB untuk OS & layanan lain |

Tidak ada satu pekerjaan yang dapat memakai seluruh 2 vCPU. Saat CPU diperebutkan, render didahulukan
(`cpu_shares`). Pekerjaan baru ditunda bila RAM host tersedia kurang dari `MEM_RESERVE_MB` (1 GB).

**Perkiraan (belum diukur di KVM 2):** analisis video 15 menit ±5–10 menit dengan `base`; render klip 20 detik
±20–40 detik. Ukur dulu sebelum memakai `small` (lebih akurat, ±2× lebih lambat).

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
- **AI lokal:** Whisper, pemilihan klip, dan "⚡ Otomatis" tanpa API berbayar. AI eksternal opsional (menu Admin).
