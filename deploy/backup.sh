#!/usr/bin/env bash
# Backup database (akun, unit, proyek, rencana) ke folder backups/. Jalankan harian via cron:
#   0 2 * * * cd /opt/video-editing && ./deploy/backup.sh >> backups/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
stamp=$(date +%Y%m%d-%H%M)
docker compose exec -T db pg_dump -U videoapp -d videoapp --format=custom > "backups/db-$stamp.dump"
# Simpan 14 backup terakhir saja.
ls -1t backups/db-*.dump | tail -n +15 | xargs -r rm --
echo "Backup selesai: backups/db-$stamp.dump"
