# Hasil benchmark P3 (antrean & resource)

Mesin uji: 4 vCPU, 16 GB RAM, tanpa GPU, PostgreSQL 16 lokal. Worker sungguhan (subprocess),
FFmpeg sungguhan untuk preview/wajah/render. Whisper diganti beban CPU x264 dengan jumlah kerja tetap
(8 frame 720p per detik video, thread sama dengan Whisper pada kode yang diuji) karena model tidak dapat
diunduh di lingkungan uji. Angka = rata-rata 2 run (pembatalan: 1 run). Satuan detik kecuali disebut lain.

- **Sebelum** = commit `a19e135` (antrean `berat` FIFO, FFmpeg/Whisper memakai semua core).
- **Sesudah** = P3 (antrean analisis/render/io, penjadwalan adil, thread elastis, prioritas per antrean).

Kolom: `wait`/`lat` = waktu tunggu / waktu sampai selesai untuk job "kecil" (render unit B & C pada hol,
render unit B pada fair); `besar` = job unit A yang paling akhir selesai; `CPU` = rata-rata pemakaian CPU mesin.

## hol: 2 analisis video 120 dtk (unit A), 3 dtk kemudian 3+3 render 8 dtk (unit B, C)

| Konfigurasi | wait p50 | wait max | lat max | besar selesai | makespan | CPU % | RAM puncak MB | gagal |
|---|---|---|---|---|---|---|---|---|
| Sebelum, `berat:2` | 39.7 | 44.8 | 48.4 | 39.5 | 51.5 | 89.0 | 1585 | 0 |
| Sebelum, `berat:3` | 13.4 | 31.1 | 35.9 | 46.4 | 46.7 | 91.9 | 1801 | 0 |
| Sesudah, deployment lama `berat:2` | 41.0 | 46.3 | 50.6 | 40.2 | 53.8 | 86.4 | 1470 | 0 |
| Sesudah, analisis 1 + render 1 | 10.8 | 25.3 | 30.0 | 63.2 | 63.6 | 74.4 | 1372 | 0 |
| Sesudah, analisis 1 + render 2 | **7.1** | **18.1** | **25.6** | 61.6 | 61.8 | 76.8 | 1526 | 0 |

## fair: unit A mengirim 12 render sekaligus, 2 dtk kemudian unit B mengirim 2 render

| Konfigurasi | wait p50 | wait max | lat max | besar selesai | makespan | CPU % | RAM puncak MB | gagal |
|---|---|---|---|---|---|---|---|---|
| Sebelum, `berat:2` | 23.7 | 24.2 | 28.3 | 26.2 | 30.5 | 86.7 | 1587 | 0 |
| Sebelum, `berat:3` | 21.8 | 22.2 | 26.9 | 26.1 | 29.0 | 91.2 | 2022 | 0 |
| Sesudah, deployment lama `berat:2` | **3.5** | 8.0 | 12.2 | 32.7 | 32.9 | 83.9 | 1458 | 0 |
| Sesudah, analisis 1 + render 1 | 2.5 | 8.7 | 11.8 | 44.8 | 45.0 | 60.5 | 1219 | 0 |
| Sesudah, analisis 1 + render 2 | 3.8 | 8.5 | 13.0 | 33.7 | 33.9 | 82.8 | 1523 | 0 |

## cancel: beban hol, 12 dtk kemudian semua job unit A dibatalkan

| Konfigurasi | semua job A berhenti | render B/C wait p50 | lat max | gagal |
|---|---|---|---|---|
| Sebelum, `berat:2` | 1.87 dtk | 18.7 | 27.7 | 0 |
| Sesudah, analisis 1 + render 2 | 1.87 dtk | 6.8 | 20.2 | 0 |

## Cara mengulang

```bash
# database kosong per run, lalu:
python bench/queue_bench.py --code . --db "postgresql+psycopg://..." --scenario hol \
    --workers "analisis:1,render:2,io:1" --env HOST_HEAVY_WORKERS=3 --out hasil-1.json
python bench/compare.py "Sesudah=hasil-*.json"
```
Untuk "sebelum": `git worktree add /tmp/before a19e135` lalu `--code /tmp/before --workers "berat:2,unduh:1"`.
