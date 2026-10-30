# Migrasi database (P5)

Skema database dikelola dengan **Alembic**. `metadata.create_all()` tidak lagi dipakai untuk membuat atau
mengubah tabel di aplikasi; semua perubahan skema adalah revisi Alembic yang teruji di SQLite dan PostgreSQL.

## 1. Audit skema (P5.1)

Sebelum P5, skema dibuat `create_all()` saat aplikasi start, ditambah perbaikan ad-hoc
(`ADDED_COLUMNS`: `ALTER TABLE ... ADD COLUMN`, `ADDED_INDEXES`: `CREATE INDEX IF NOT EXISTS`). Akibatnya
database produksi bisa berada di salah satu keadaan berikut, tanpa catatan versi:

| Revisi Alembic | Setara commit | Isi perubahan | Cara dibuat sebelum P5 |
|---|---|---|---|
| `0001_baseline` | `20ac1c9` (dasar multi-unit, sebelum P1) | units, users, sessions, login_failures, projects, renders, music, jobs, settings, usage_events (+ indeksnya) | `create_all` |
| – | `fc4f8ea` (P1) | tidak ada perubahan skema | – |
| `0002_media_lifecycle` | `7c8127a` (P2.1) | tabel `project_media`; kolom `units.storage_quota_mb` | `create_all` + `ALTER` |
| `0003_media_leases` | `a19e135` (P2.5) | tabel `media_leases` | `create_all` |
| `0004_scheduling` | `0080aa0` (P3) | kolom `jobs.priority`; tabel `workers`; indeks `ix_jobs_queue_status`, `ix_jobs_queue_started` | `create_all` + `ALTER` + `CREATE INDEX` |
| `0005_metrics_index` | `0b39237` (P3 final) | indeks `ix_jobs_queue_finished` | `CREATE INDEX` |
| `0006_uploads` | `55366de` (P4) | tabel `uploads` | `create_all` |

Revisi dibuat per keadaan nyata agar database dari commit mana pun bisa dikenali tepat dan dinaikkan tanpa
langkah yang bertabrakan (mis. P2.1 sudah punya `project_media` tetapi belum `media_leases`).

Data aplikasi lama sebelum database (folder `project.json`, `settings.json`) tidak termasuk migrasi skema: tetap
diimpor oleh `storage.import_legacy_projects` seperti sebelumnya.

## 2. Rancangan

- **Lokasi**: `app/migrations/` (ikut ter-copy ke image Docker bersama paket `app`). `alembic.ini` di root repo
  hanya untuk pengembang (`alembic history`, `alembic revision`).
- **Revisi ditulis tangan** (bukan autogenerate) dengan tipe, panjang, nullable, default, FK, dan nama indeks persis
  seperti `app/db.py`. Tes "drift" membandingkan hasil migrasi dengan `db.metadata` di kedua database: model dan
  migrasi tidak boleh berbeda.
- **SQLite**: `render_as_batch=True` (ALTER terbatas di SQLite memakai salin-tabel bila perlu).
- **Database lama tanpa `alembic_version`** (dibuat sebelum P5): aplikasi mendeteksi revisi tertinggi yang semua
  cirinya ada (tabel/kolom/indeks pada tabel di atas), mencatatnya (*stamp*), lalu menjalankan revisi sisanya.
  Data tidak disentuh.
- **Validasi saat start** (web & worker), `app/migrate.py::ensure_schema()`:
  - versi = head → jalan;
  - versi lebih lama → bila `AUTO_MIGRATE` aktif, upgrade (dengan backup SQLite & kunci agar satu proses saja);
    bila tidak, aplikasi berhenti dengan pesan jelas;
  - versi tidak dikenal (database lebih baru dari aplikasi, mis. aplikasi di-rollback) → berhenti, tidak menebak.
  - `AUTO_MIGRATE` bawaan: aktif untuk SQLite (satu komputer), **mati untuk PostgreSQL** (produksi). Di Docker
    Compose, service `migrate` menjalankan `python -m app.manage db-upgrade` sekali sebelum web & worker start.
- **Kegagalan migrasi**: PostgreSQL menjalankan seluruh upgrade dalam satu transaksi (DDL transaksional) → gagal =
  kembali utuh ke versi sebelumnya. SQLite juga transaksional, ditambah salinan cadangan otomatis sebelum upgrade.
- **Migrasi destruktif dilindungi**: setiap `downgrade()` yang menghapus tabel/kolom menolak berjalan kecuali
  diminta eksplisit (`python -m app.manage db-downgrade <rev> --allow-data-loss`). Revisi upgrade di P5 hanya
  menambah (tabel, kolom nullable, indeks).

## 3. Prosedur operasional

### Backup

```bash
# PostgreSQL (Docker Compose): sebelum setiap update aplikasi
./deploy/backup.sh                                   # pg_dump ke backups/
# SQLite (satu komputer): otomatis disalin ke data/backups/app-<versi>-<waktu>.db sebelum upgrade
```

### Upgrade

```bash
git pull
docker compose build
docker compose run --rm migrate                      # = python -m app.manage db-upgrade (dengan kunci)
docker compose up -d
python -m app.manage db-status                       # versi database vs versi aplikasi
```

### Pemulihan bila upgrade gagal

1. PostgreSQL: transaksi otomatis dibatalkan; `db-status` menunjukkan versi lama. Perbaiki penyebab (lihat log),
   ulangi `db-upgrade`. Aplikasi versi lama tetap bisa dijalankan pada database itu.
2. Bila database rusak karena hal lain: hentikan aplikasi dan pulihkan dari backup `pg_restore` (langkah teruji di
   `docs/BACKUP_RESTORE.md`), lalu jalankan aplikasi versi yang sesuai dengan backup tersebut.
3. SQLite: hentikan aplikasi, salin `data/backups/app-<versi>-<waktu>.db` menjadi `data/app.db`.

### Kembali ke versi aplikasi sebelumnya (rollback)

Aplikasi lama menolak start pada database yang lebih baru. Pilihan: pulihkan backup sebelum upgrade (aman), atau
`python -m app.manage db-downgrade <revisi> --allow-data-loss` (menghapus tabel/kolom yang ditambahkan setelah
revisi itu beserta datanya).

## 4. Migrasi data (bukan skema)

Perubahan **isi** data tidak dicampur dengan revisi Alembic. Aturannya:

- Revisi Alembic hanya mengubah struktur (tabel, kolom, indeks) dan tidak membaca/mengubah baris data.
- Migrasi data ditulis di kode aplikasi sebagai fungsi yang **idempoten**, hanya menyentuh data yang perlu
  (mis. `jobs.migrate_legacy_queues`: job AKTIF di antrean lama `berat`/`unduh` dipindah ke `analisis`/`render`/`io`;
  riwayat job tidak diubah), dipanggil saat start setelah skema terbaru, dan memiliki tes sendiri
  (`tests/test_p5_migrations.py::test_queue_data_migration_is_separate_and_idempotent`).
- Bila kelak migrasi data besar diperlukan (jutaan baris, tidak boleh saat start), buat perintah `app.manage`
  terpisah dengan mode dry-run (contoh: `media-migrate`), dokumentasikan, dan jalankan setelah backup.

## 5. Syarat sebelum deployment publik

Migrasi **wajib** diuji pada **salinan database produksi** (restore backup ke server staging, `db-status`,
`db-upgrade`, `db-check`, uji aplikasi) sebelum deployment publik. Tercatat sebagai butir 9 di
`PRODUCTION_VALIDATION.md`.
