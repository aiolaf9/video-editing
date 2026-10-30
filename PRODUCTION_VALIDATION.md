# Daftar validasi produksi

Hal yang **belum** teruji penuh di lingkungan pengembangan dan wajib diperiksa di server produksi sebelum
dipakai luas. Perbarui tabel ini setiap kali satu butir diverifikasi.

| # | Butir | Status | Alasan belum teruji | Cara memverifikasi di server |
|---|---|---|---|---|
| 1 | **Whisper asli** (akurasi, RTF, RAM per worker analisis) | Belum | Model tidak dapat diunduh di lingkungan pengembangan (huggingface.co diblokir); benchmark P3 memakai beban CPU pengganti | `python bench/whisper_bench.py --check`, lalu `--media <rekaman ucapan nyata> --threads 2,4 --parallel 1,2`; pastikan RAM worker-analisis < `ANALYSIS_MEM` |
| 2 | **GPU sungguhan** (Whisper CUDA, NVENC, dekode hardware preview) | Belum | Tidak ada GPU; hanya diuji dengan `nvidia-smi` tiruan & kegagalan CUDA yang disimulasikan | NVIDIA Container Toolkit + `gpus: all`; `WHISPER_DEVICE=auto`, `PREVIEW_HWACCEL=cuda`; cek log "Memuat model Whisper ... di cuda", bandingkan `bench/preview_bench.py`, lalu matikan GPU dan pastikan jatuh ke CPU |
| 3 | **Build Dockerfile produksi** | Belum | Docker Hub menolak unduhan image dasar (429) di lingkungan pengembangan; uji Docker memakai image kosong + sistem file host | `docker compose build && docker compose up -d`; jalankan `bench/queue_bench.py --docker` atau uji upload/analisis/render manual; cek `docker stats` & OOMKilled |
| 4 | **Upload besar lewat internet + HTTPS (Caddy)** | Belum | Uji upload memakai loopback & throttling Chrome, bukan jaringan nyata | Unggah 1-2 GB dari luar kantor, putuskan Wi-Fi di tengah, lanjutkan; pastikan tidak ada timeout proxy (Caddy `read_timeout 30m` per potongan) |
| 5 | **Browser lain** (Safari/iOS, Firefox, Chrome Android) untuk upload bertahap | Belum | Uji UI otomatis hanya Chromium | Upload + jeda + refresh + lanjut di tiap browser; di HTTP non-HTTPS checksum potongan tidak aktif (WebCrypto butuh konteks aman) |
| 6 | **Beberapa proses web pada volume bersama** (kunci flock per sesi upload) | Sebagian | Diuji di satu mesin (proses uvicorn terpisah); belum pada volume jaringan (NFS) | Jangan taruh `/data` di NFS tanpa dukungan `flock`; untuk banyak mesin web, uji dua PATCH bersamaan ke sesi yang sama (harus 423) |
| 7 | **PostgreSQL produksi** di bawah beban nyata | Sebagian | Diuji PostgreSQL 16 lokal + benchmark 200 ribu job | Pantau `/metrics` (`ve_queue_*`), latensi Admin → Status; sesuaikan `DB_POOL_SIZE` × proses < `max_connections` |
| 8 | **Disk produksi & cadangan** | Sebagian | Disk penuh disimulasikan | Isi disk hingga mendekati `DISK_MIN_FREE_MB`; upload harus ditolak jelas (507), job berjalan tidak rusak |
| 9 | **Migrasi Alembic pada database produksi nyata** | Belum | Diuji pada database uji (snapshot P0-P4 berisi data & jalur kode lama sungguhan), bukan salinan data produksi | Pulihkan backup produksi ke server staging, `python -m app.manage db-status`, `db-upgrade`, `db-check`; ukur lama migrasi & cek aplikasi; latih prosedur pemulihan dari backup |
