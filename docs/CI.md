# Continuous Integration (P5.3)

Workflow: `.github/workflows/ci.yml`. Dipicu oleh:
- `push` ke `v2/**`, `main`, `master`
- `pull_request` (semua branch tujuan)
- `workflow_dispatch` (manual; input `heavy=true` untuk menjalankan tes Whisper asli)

Prinsip: tanpa API berbayar, tanpa GPU, tidak mengunduh model AI (`HF_HUB_OFFLINE=1`), fixture kecil yang dibuat
saat tes (tidak ada footage pengguna), setiap job punya `timeout-minutes`, izin token hanya `contents: read`,
run lama pada branch yang sama dibatalkan (`concurrency`). Secret tidak pernah dicetak: pemindai hanya melaporkan
nama file, nomor baris, dan jenis temuan.

## 1. Struktur job

| Job | Batas | Isi | Gagal bila |
|---|---|---|---|
| `lint` | 10 mnt | ruff (`ruff.toml`: E4/E7/E9/F), bandit (`-ll`, baseline `.bandit-baseline.json`), `compileall`, `node --check web/*.js`, check-jsonschema untuk workflow, `docker compose config` | error sintaks/import tak terpakai/nama tak terdefinisi, temuan bandit BARU tingkat medium+, JS tidak valid, workflow/compose tidak valid |
| `security` | 15 mnt | detect-secrets (file saat ini, baseline `.secrets.baseline` yang sudah diaudit), `scripts/scan_secrets_history.py` (seluruh riwayat git), pip-audit atas `requirements.txt`, `scripts/check_licenses.py` di venv produksi bersih | secret baru, kerentanan dependensi yang diketahui, lisensi copyleft kuat / tidak dikenal yang belum ditinjau di `scripts/license_review.json` |
| `test-sqlite` | 30 mnt | `pytest` penuh (unit, integrasi, API, pembatalan job, upload resumable, lifecycle media, penjadwalan worker, migrasi) di SQLite | ada tes gagal |
| `test-postgres` | 30 mnt | `pytest` yang sama di service `postgres:16` sementara | ada tes gagal |
| `migrations` | 15 mnt | tepat satu head Alembic; SQL offline dapat dibuat; startup **ditolak** pada PostgreSQL kosong; `db-upgrade` → `db-status` → `db-check` (drift) → start OK; `tests/test_p5_migrations.py` (database lama P0–P4 berisi data, migrasi gagal = rollback, downgrade terlindungi, migrasi data antrean terpisah & idempoten) | salah satu langkah gagal |
| `heavy` | 30 mnt | hanya `workflow_dispatch` dengan `heavy=true`: `pytest -m heavy` (mengunduh Whisper `tiny` ±75 MB, CPU) | tes gagal |

Laporan yang diunggah sebagai artifact: `junit-sqlite.xml`, `junit-postgres.xml`, `licenses-python.md`
(tidak ada media/footage).

## 2. Rekomendasi branch protection (BELUM diterapkan)

Pengaturan repository **tidak diubah** oleh P5.3. Rekomendasi untuk pemilik repo
(Settings → Branches → Add rule, atau Rulesets), untuk branch default dan `v2/**`:

1. **Require status checks to pass before merging**, centang:
   `Lint & static checks`, `Keamanan & lisensi`, `Tes SQLite (unit, integrasi, API, migrasi)`,
   `Tes PostgreSQL (seluruh regresi + migrasi PG)`, `Validasi migrasi database`.
   Jangan jadikan `heavy` required (manual).
2. **Require branches to be up to date before merging** (tes dijalankan atas hasil gabungan terbaru).
3. **Require a pull request before merging**, minimal 1 approval; **Dismiss stale approvals** saat ada push baru.
4. **Block force pushes** dan **restrict deletions**.
5. Opsional: **Require conversation resolution**; aktifkan GitHub secret scanning & Dependabot alerts.

Dengan aturan 1–3, PR tidak dapat di-merge selama ada job CI yang gagal.

## 3. Menjalankan pemeriksaan yang sama secara lokal

```bash
pip install -r requirements-dev.txt
```

```bash
ruff check . && bandit -r app -ll -q -b .bandit-baseline.json && node --check web/*.js
```

```bash
detect-secrets-hook --baseline .secrets.baseline --exclude-files '(^|/)(tests/schema_snapshots|fonts|models)/' $(git ls-files)
```

```bash
python scripts/scan_secrets_history.py && pip-audit -r requirements.txt
```

Lisensi (di venv yang hanya berisi `requirements.txt`, agar alat dev tidak ikut terhitung):

```bash
python scripts/check_licenses.py --markdown docs/licenses-python.md
```

Tes SQLite dan PostgreSQL:

```bash
pytest -q
```

Untuk PostgreSQL, set `DATABASE_URL` ke database uji yang **kosong dan khusus tes** (format
`postgresql+psycopg://<pengguna>:<kata-sandi>@<host>:5432/<nama_db>`), lalu:

```bash
pytest -q
```

Tes berat (mengunduh Whisper `tiny`):

```bash
pytest -m heavy
```

Memperbarui baseline hanya setelah setiap temuan diaudit manual:
`detect-secrets scan --baseline .secrets.baseline` lalu `detect-secrets audit .secrets.baseline`.
Baseline bandit hanya untuk temuan yang sudah dicatat dan dijadwalkan perbaikannya (lihat bagian 5).

## 4. Durasi (simulasi lokal, 1 mesin, perintah identik dengan workflow)

| Job | Durasi lokal |
|---|---|
| lint | ±3 dtk (+ instalasi alat) |
| security | ±23 dtk (+ instalasi venv produksi) |
| migrations | ±5 dtk + tes migrasi |
| test-sqlite | ±254 dtk (187 lolos, 1 dilewati, 2 heavy tidak dipilih) |
| test-postgres | ±270 dtk (203 lolos, 2 heavy tidak dipilih) |

Job berjalan paralel; perkiraan total di GitHub Actions ±6–9 menit (instalasi dependensi & FFmpeg ±1–2 menit per
job). Angka di GitHub **belum terukur** sampai workflow dijalankan di remote.

## 5. Temuan yang diterima sementara (baseline)

| Sumber | Temuan | Status |
|---|---|---|
| bandit B310 | `app/ai.py`: `urlopen` tanpa pembatasan skema URL (`base_url` hanya diatur admin) | dijadwalkan P5.4 (validasi skema http/https) |
| detect-secrets | 14 temuan: contoh URL di dokumentasi & kredensial tiruan di tes | diaudit, bukan secret |
| lisensi | `mutagen` GPL-2.0+ lewat `yt-dlp[default]` | butuh keputusan pemilik (lihat `docs/LICENSES.md`) |
