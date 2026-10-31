# Rencana P5.5.2 — Validasi Media Nyata & AI

Status: **rencana, belum dimulai** (menunggu evaluasi P5.5.1). Tidak ada fitur AI baru; yang diuji adalah alur yang
sudah ada dengan model dan media asli, di image produksi (`python:3.12-slim` dari digest), tanpa GPU kecuali terdeteksi.

## Prinsip
- Whisper asli (faster-whisper), bukan simulasi/stub. Model diunduh sekali ke cache yang di-pin (hash berkas dicatat).
- Media uji milik sendiri atau berlisensi jelas (CC0/CC-BY), tanpa data produksi; manifest berisi sumber, lisensi,
  sha256, durasi, codec.
- Setiap angka disertai lingkungan (CPU, jumlah core, RAM, batas container, versi model/FFmpeg) dan dijalankan ≥ 3 kali
  (median + p95). Status per butir: PASS / FAIL / BLOCKED / NOT VERIFIED.

## Korpus media (target)
| Kelompok | Isi |
|---|---|
| Bahasa | Indonesia (utama), Inggris; ucapan jelas, bising, musik latar, banyak pembicara |
| Durasi | 30 dtk, 5 mnt, 30 mnt, 2 jam |
| Format | MP4 H.264/AAC, MOV, MKV, WebM VP9/Opus, HEVC, VFR ponsel, rotasi metadata, audio mono 8 kHz–48 kHz, tanpa audio |
| Ground truth | transkrip manual + timestamp kata/kalimat untuk subset (≥ 20 mnt total) |

## Area & metrik
| # | Area | Metrik / kriteria lulus (usulan, ditetapkan sebelum run) |
|---|---|---|
| 1 | Transkripsi Whisper tiny/base/small (yang tersedia) | RTF (waktu proses / durasi) per model & panjang; WER & CER terhadap ground truth; puncak RAM (cgroup `memory.peak`); waktu muat model |
| 2 | Akurasi timestamp | median & p95 selisih awal/akhir segmen vs ground truth (ms); % segmen dalam ±300 ms |
| 3 | Video panjang & format | tingkat keberhasilan job per format; batas waktu analisis; tidak ada OOM; pesan galat yang jelas untuk format tidak didukung |
| 4 | Sinkronisasi audio-video | offset A/V hasil render (cross-correlation penanda klik/flash) ≤ 1 frame; drift pada video 2 jam |
| 5 | Render final & kualitas visual | waktu render per menit output, RTF render; VMAF/SSIM terhadap sumber (bila VMAF tersedia di FFmpeg image, bila tidak: SSIM/PSNR); ukuran berkas |
| 6 | Preview vs render final | selisih timeline (frame) & durasi; SSIM frame sampel; preview tidak dipakai sebagai sumber render |
| 7 | Subtitle & overlay | posisi/timing subtitle vs transkrip (ms); karakter non-ASCII; overlay/watermark posisi & alpha benar (pemeriksaan piksel) |
| 8 | Resumable upload | unggah 2–5 GB dengan pemutusan koneksi acak & restart server; sha256 identik; tanpa sisa berkas parsial |
| 9 | Storage TTL pada media asli | media terhapus tepat setelah masa simpan; job berjalan menahan lease; kuota unit kembali; tanpa berkas yatim |
| 10 | Antrean & latensi | p95 waktu tunggu & durasi per jenis job; tingkat keberhasilan job (%) di bawah beban campuran |

## Lingkungan & bukti
- Workflow terpisah (manual `workflow_dispatch`) di runner GitHub (CPU) untuk tiny/base/small; artifact: CSV metrik,
  manifest media, log. Media besar & 2 jam dijalankan di mesin staging terpisah bila runner tidak cukup (dicatat).
- Puncak RAM dari cgroup v2 container worker (`memory.peak`, `memory.events`), bukan rata-rata.
- Keterbatasan dicatat eksplisit (mis. tanpa GPU, korpus kecil, WER hanya untuk subset bertranskrip).

## Di luar cakupan P5.5.2
Fitur AI baru, model besar (medium/large) kecuali hanya untuk pengukuran RAM, pemisahan secret/role DB (P5.5.3),
deployment produksi.
