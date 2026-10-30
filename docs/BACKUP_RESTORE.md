# Backup & restore PostgreSQL

Diuji di P5.3: backup format *custom* (`pg_dump --format=custom`, sama seperti `deploy/backup.sh`), restore ke
database kosong dengan `pg_restore`, lalu `db-status` = terbaru, `db-check` = sama dengan model, jumlah baris
(proyek, job, user) identik.

Yang dicadangkan: seluruh database (akun, unit, Guidebook, proyek, rencana animasi, riwayat job & render, sesi
upload). **Tidak** termasuk file video/hasil render: media bersifat sementara (dihapus otomatis 24-72 jam).

## Backup

```bash
cd /opt/video-editing
./deploy/backup.sh                    # -> backups/db-YYYYmmdd-HHMM.dump (14 terakhir disimpan)
```

Jadwalkan harian (cron, lihat DEPLOY.md) dan **wajib** sebelum setiap update aplikasi / migrasi skema. Salin
folder `backups/` ke luar server (object storage).

## Uji backup tanpa mengganggu produksi (disarankan rutin)

```bash
docker compose exec -T db createdb -U videoapp videoapp_verify
docker compose exec -T db pg_restore -U videoapp -d videoapp_verify --no-owner --exit-on-error < backups/db-XXXX.dump
docker compose run --rm -e DATABASE_URL="postgresql+psycopg://videoapp:${POSTGRES_PASSWORD}@db:5432/videoapp_verify" \
    migrate python -m app.manage db-check
docker compose exec -T db dropdb -U videoapp videoapp_verify
```

## Restore (mengganti database produksi)

1. Hentikan aplikasi agar tidak ada penulisan: `docker compose stop web worker-analisis worker-render worker-io`
2. Backup keadaan sekarang dulu (bila masih bisa): `./deploy/backup.sh`
3. Kosongkan & isi ulang database:

   ```bash
   docker compose exec -T db psql -U videoapp -d postgres \
       -c "DROP DATABASE videoapp WITH (FORCE)" -c "CREATE DATABASE videoapp OWNER videoapp"
   docker compose exec -T db pg_restore -U videoapp -d videoapp --no-owner --exit-on-error < backups/db-XXXX.dump
   ```

4. Periksa versi skema: `docker compose run --rm migrate python -m app.manage db-status`
   - sama dengan aplikasi → lanjut;
   - lebih lama → `docker compose run --rm migrate` (upgrade);
   - lebih baru (backup dari versi aplikasi yang lebih baru) → jalankan versi aplikasi yang sesuai.
5. Start: `docker compose up -d`, lalu cek Admin → Status.

Catatan: media (video) proyek yang ada di backup tetapi filenya sudah terhapus akan tampil "media dihapus"; ini
sesuai kebijakan penyimpanan sementara.
