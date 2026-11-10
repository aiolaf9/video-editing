# Desktop M1 — NH AI Clip Studio untuk Windows

Implementasi M1 dari `docs/DESKTOP_SPEC.md`. Branch `desktop/m1` (dari `v2/phase-1`); versi web tetap sama
(mode desktop hanya aktif bila `DESKTOP_MODE=1`, yang diatur oleh peluncur desktop).

## Arsitektur

```
NH-AI-Clip-Studio.exe (PyInstaller onedir, tanpa Python terpisah)
 └─ desktop/launcher.py
     ├─ atur lingkungan desktop (sebelum app diimpor)
     ├─ server lokal FastAPI di 127.0.0.1:47811 (port lain bila terpakai), thread uvicorn
     │    └─ worker AI (Whisper base bawaan, YuNet) & FFmpeg berjalan otomatis (EMBEDDED_WORKERS=1)
     └─ jendela pywebview (Microsoft Edge WebView2) -> /api/desktop/enter?key=<kunci acak per peluncuran>
          (tanpa WebView2: browser bawaan + jendela kecil "tutup untuk keluar")
```

| Hal | Desktop |
|---|---|
| Login | tidak ada. Satu pengguna lokal; sesi dibuat hanya dengan kunci acak per peluncuran (cookie HttpOnly, SameSite=Strict). `/api/auth/login` dimatikan. |
| Jaringan | server hanya di 127.0.0.1; Host selain 127.0.0.1/localhost ditolak (anti DNS rebinding). Impor link dimatikan; `HF_HUB_OFFLINE=1`. Tanpa telemetri, tanpa API berbayar. |
| Data | `%LOCALAPPDATA%\NH AI Clip Studio` (database SQLite, proyek, log `logs\desktop.log`). |
| Ekspor | setiap render juga disalin ke `Videos\NH AI Clip Studio\<proyek>\<judul klip> (<rasio>).mp4` (tidak menimpa). Tombol "📂 Buka folder ekspor". |
| Penyimpanan | **proyek tidak dihapus otomatis** (`MEDIA_LIFECYCLE=0`; aturan 2/5 jam versi web tidak berlaku). Hanya cache/berkas sementara (upload terputus, sisa render, temp) yang dibersihkan saat aplikasi dibuka lalu tiap jam. Uninstall tidak menghapus proyek maupun hasil ekspor. |
| Ekspor massal | tab Klip: centang klip, pilih rasio 9:16 / 1:1 / 16:9, "⬇ Ekspor klip dicentang" -> `POST /api/projects/{id}/render-batch` (klip x rasio, dari resep tersimpan = sama dengan pratinjau). Batas: 30 klip x 5 rasio; ditolak utuh (429) bila melebihi batas antrean, tidak setengah jalan. |
| Satu salinan | kunci berkas: membuka aplikasi kedua kali menampilkan pesan dan keluar. |
| PWA | service worker tidak dipakai di jendela desktop (`/sw.js` 404). |
| Branding | placeholder: nama "NH AI Clip Studio", ikon sementara (ikon versi web), penerbit "NH (placeholder)". **Tidak ada data rekening/QR donasi** di M1. |

## Bundel

FFmpeg 7.1.1 (gyan.dev essentials, sha256 dikunci; libass/libx264/freetype/fribidi diverifikasi), model Whisper
base CTranslate2 (lisensi MIT dicek otomatis, revisi dicatat), YuNet, font OFL, Python 3.12 + dependensi
(`desktop/requirements-desktop.txt`). Lisensi: `desktop/THIRD_PARTY_NOTICES.md` (FFmpeg GPL v3: tinjau sebelum
distribusi publik).

## Build & uji (GitHub Actions, `.github/workflows/desktop.yml`, runner `windows-2022`)

1. Unduh & verifikasi bundel; `pip-licenses` gagal bila ada paket GPL/AGPL/komersial di aplikasi.
2. Sampel video ucapan dibuat offline dengan suara SAPI Windows (bahasa Inggris, ±90 dtk).
3. `selftest` dari kode sumber.
4. PyInstaller -> **selftest exe beku dengan koneksi keluar diblokir firewall** (aturan dibuktikan memblokir) dan
   PATH tanpa Python/FFmpeg.
5. Inno Setup -> `NH-AI-Clip-Studio-Setup.exe` -> pasang diam-diam per pengguna -> selftest aplikasi terpasang
   (folder bawaan) -> buka GUI seperti pengguna (tangkapan layar `gui-window.png`), uji satu salinan -> uninstall
   (proyek tetap ada).
6. Artefak: `NH-AI-Clip-Studio-Setup` (installer) dan `desktop-test-reports` (laporan JSON, log, tangkapan layar,
   lisensi).

`selftest` (`desktop/selftest.py`): masuk tanpa login -> upload -> analisis (Whisper + YuNet) -> transkrip >= 10 kata
-> >= 2 rekomendasi klip dengan skor & alasan -> subtitle (gaya pop, font Poppins bawaan) di resep -> ekspor massal
2 klip x 3 rasio -> ffprobe: H.264 + audio, 1080x1920 / 1080x1080 / 1920x1080 -> 6 salinan di folder ekspor.

Lokal (Linux, tanpa unduh model): `tests/test_desktop_m1.py` dan `tests/test_desktop_launcher.py`.

Pasang manual (beta internal): unduh artefak dari halaman run Actions, jalankan `NH-AI-Clip-Studio-Setup.exe`.
Installer belum ditandatangani: Windows SmartScreen dapat menampilkan "Windows protected your PC" — pilih
"More info" -> "Run anyway" hanya bila berkas berasal dari artefak resmi build ini (cocokkan sha256 di ringkasan run).
Jangan menonaktifkan antivirus atau proteksi Windows.

## Batasan M1

- Runner GitHub bukan Windows bersih (ada Python, VC++ runtime, Edge/WebView2). DLL VC++ runtime disalin ke bundel
  bila tidak ikut; uji di Windows 10/11 bersih belum dilakukan.
- Tanpa tanda tangan kode (ditunda untuk beta internal): SmartScreen/antivirus dapat memberi peringatan.
- Tanpa WebView2 Runtime (jarang di Windows 10 lama), aplikasi terbuka di browser bawaan.
- Whisper base di CPU: akurasi bahasa Indonesia di bawah model small; GPU tidak dibundel (CUDA tidak disertakan).
- Template NH (logo, CTA, rekening donasi), B-roll, motion lanjutan: M2+.
- Tidak ada pembaruan otomatis; versi baru = pasang ulang installer (data tetap).
