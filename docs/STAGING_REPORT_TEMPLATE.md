# Laporan Staging & Acceptance — AI Video Studio (KVM 2)

> Salin berkas ini ke `docs/STAGING_REPORT_<tanggal>.md` lalu isi. Jangan mencantumkan password, token, atau IP
> privat yang tidak perlu. Tandai setiap hasil: ✅ Lulus · 🟡 Lulus bersyarat · ❌ Gagal · ⏭ Tidak diuji (alasan).

## 0. Identitas

| | |
|---|---|
| Commit yang diuji | |
| Tanggal & jendela uji (WIB) | |
| Pelaksana | |
| Akses staging | A tunnel / B subdomain / C SSH |
| Snapshot hPanel sebelum Tahap 2 | tanggal / ID |

## 1. Ringkasan audit (Tahap 0)

| Item | Nilai | Catatan |
|---|---|---|
| OS / kernel / cgroup | | |
| vCPU / steal rata-rata | | |
| RAM total / tersedia (jam sibuk / sepi) | | |
| Disk kosong | | |
| Port 80/443 dipakai oleh | | |
| Panel / web server | | |
| Docker (versi / belum ada) | | |
| Firewall (ufw/CSF/firewalld) | | |
| Backup (hPanel / lainnya) | | |
| Tanda bahaya dari skrip | | |
| Baseline situs lain p50 / p95 | | |

## 2. Log deployment (Tahap 2)

| Waktu | Perintah / langkah | Hasil | Durasi |
|---|---|---|---|
| | | | |

Versi: Docker ___ · Compose ___ · image `video-editing` ID ___ · port lokal ___ · URL staging ___

## 3. Uji end-to-end (E1–E14)

| # | Hasil | Bukti (berkas/tangkapan) | Catatan |
|---|---|---|---|
| E1 Akun | | | |
| E2 Upload | | | |
| E3 URL import (a/b/c) | | | |
| E4 Whisper | | | |
| E5 Klip AI | | | |
| E6 Edit | | | |
| E7 Autosave & dua perangkat | | | |
| E8 Preview | | | |
| E9 Render | | | |
| E10 Download | | | |
| E11 Cleanup | | | |
| E12 Pemulihan | | | |
| E13 Backup & restore | | | |
| E14 Keamanan | | | |

## 4. Benchmark (B1–B5)

| Skenario | Upload | Pratinjau siap | Antre | Analisis (RTF) | Render 30 dtk | Render 60 dtk | RAM puncak worker | RAM host min | Steal | Hasil |
|---|---|---|---|---|---|---|---|---|---|---|
| B1 V5 (median 3×) | | | | | | | | | | |
| B2 V15 (median 3×) | | | | | | | | | | |
| B3 dua pengguna | | | | | | | | | | |
| B4 dua render | | | | | | | | | | |
| B5 HEVC iPhone | | | | | | | | | | |

Run pertama (unduh model): ___ · OOM: ___ · disk sebelum/sesudah cleanup: ___ / ___

## 5. Dampak ke layanan lain

| Situs/layanan | Baseline p50/p95 | Saat benchmark p50/p95 | 5xx baru | Hasil |
|---|---|---|---|---|
| | | | | |

## 6. HP fisik (M1–M15)

| Perangkat | Model | OS / browser | Jaringan |
|---|---|---|---|
| Android | | | |
| iPhone | | | |

| # | Android | iPhone | Catatan |
|---|---|---|---|
| M1 Login HTTPS | | | |
| M2 Pasang PWA | | | |
| M3 Buka standalone | | | |
| M4 Upload galeri (HEVC) | | | |
| M5 Upload + latar belakang | | | |
| M6 Pemutaran H.264 | | | |
| M7 Seret timeline & crop | | | |
| M8 Subtitle & template | | | |
| M9 Keyboard virtual | | | |
| M10 Autosave + latar belakang | | | |
| M11 Render | | | |
| M12 Download | | | |
| M13 Offline | | | |
| M14 Keluar akun | | | |
| M15 Orientasi & ukuran | | | |

## 7. Masalah

| # | Keparahan (kritis/tinggi/sedang/rendah) | Ringkasan | Langkah reproduksi | Tindak lanjut |
|---|---|---|---|---|
| | | | | |

## 8. Keputusan

- [ ] Lulus → siapkan rencana produksi
- [ ] Lulus bersyarat → tindak lanjut: ___
- [ ] Gagal → perbaiki & uji ulang: ___
- [ ] Rollback dilakukan? (alasan, perintah, waktu)

Cakupan: diuji di perangkat fisik ___ · hanya emulasi ___ · belum diuji ___
