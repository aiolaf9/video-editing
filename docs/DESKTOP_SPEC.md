# Spesifikasi NH AI Clip Studio Desktop

Status: **SPESIFIKASI UNTUK DIREVIEW**, belum diimplementasikan. Dokumen ini dibuat dari basis kode AI Video
Studio (`v2/phase-1` @ `99e1ca1`). Spesifikasi desktop sebelumnya tidak ditemukan di repo; bila ada versi lain,
dokumen ini perlu digabung dengannya.

**Tujuan.** Aplikasi Windows offline untuk tim Nurul Hayat dengan pengalaman mendekati OpusClip:

> impor video → klip AI → caption animasi → editing → ekspor MP4 massal

Semuanya berjalan di komputer sendiri, tanpa API berbayar, tanpa dependensi berlisensi komersial, dan bisa dipasang
di banyak komputer.

**Prinsip prioritas.**
- MVP yang berfungsi lebih dulu. Installer tidak ditunda demi efek motion.
- Fitur hanya dijanjikan bila benar-benar ada model/komponen lokal untuk menjalankannya.

Legenda:

| Tanda | Arti |
|---|---|
| ✅ | **Sudah tersedia** di kode sekarang (diuji di CI) |
| 🎯 | **Wajib sebelum Rilis 1** |
| 🔜 | **Penyempurnaan berikutnya** (Rilis 2+) |
| ⚠️ | Catatan/batasan jujur |

---

## 0. Bentuk aplikasi desktop (keputusan teknis yang diusulkan)

Basis kode yang sama dengan versi server, dalam **mode satu pengguna**. Mode lokal sudah ada: SQLite, worker di
dalam proses, tanpa Docker.

| Bagian | Pilihan | Lisensi | Alasan |
|---|---|---|---|
| Jendela aplikasi | **pywebview** + Microsoft Edge WebView2 (bawaan Windows 10/11) | BSD-3 | UI web yang sudah ada tampil sebagai jendela aplikasi; tanpa Electron (±150 MB lebih kecil) |
| Runtime | Python + seluruh library, dibekukan dengan **PyInstaller** (mode folder) | GPL-2 + *bootloader exception* (aplikasi bebas lisensinya) | tidak perlu Python/pip di komputer pengguna |
| Installer | **Inno Setup** | lisensi Inno Setup (gratis, termasuk penggunaan organisasi) | instalasi per-mesin/per-pengguna, mode senyap `/VERYSILENT` untuk banyak komputer, uninstaller |
| Video | **FFmpeg** dibundel (build Windows dengan libx264) | GPL (karena x264) | lihat §7: distribusi internal + teks lisensi + tautan sumber |
| Transkripsi | faster-whisper + CTranslate2, model **base** dibundel (opsi **small** sebagai komponen installer) | MIT | offline sejak instalasi; tidak mengunduh model saat pertama jalan |
| Wajah | YuNet ONNX via OpenCV | MIT / Apache-2.0 | sudah dipakai untuk auto-reframe |
| Caption | ASS + **libass** (render FFmpeg) dan **libass-wasm (JavascriptSubtitlesOctopus)** untuk pratinjau | ISC / MIT | mesin render caption yang **sama** di pratinjau & MP4 → animasi identik |
| Grafis | Pillow (PNG lapisan) + filter FFmpeg (overlay, xfade, zoom/crop berekspresi) | HPND / GPL-LGPL | sudah dipakai untuk kartu, kata kunci, chips, checklist |
| Data | `%LOCALAPPDATA%\NH AI Clip Studio` (proyek, cache, database SQLite) | — | uninstall tidak menghapus proyek kecuali diminta |

⚠️ Pemeriksaan aktual tetap diperlukan untuk setiap bagian ini sebelum rilis. Lihat checklist lisensi di §7.

### Evaluasi Remotion

Teks lisensinya diperiksa 10 Nov 2026 di `remotion-dev/remotion/LICENSE.md`:
- **Gratis untuk organisasi nirlaba** ("a non-profit or not-for-profit organization").
- Juga gratis untuk perorangan dan perusahaan dengan ≤ 3 karyawan.
- Lisensinya *source-available*, bukan open source.
- Kodenya tidak boleh dijual, disewakan, atau disublisensikan.

| Aspek | Penilaian |
|---|---|
| Biaya | Kemungkinan gratis untuk Nurul Hayat sebagai yayasan/LAZ nirlaba, **dengan konfirmasi tertulis status nirlaba oleh pimpinan/legal** |
| Teknis | Render tiap frame lewat Chromium headless + Node.js: lambat di PC kantor tanpa GPU, menambah ±300 MB ke installer, butuh keahlian React |
| Risiko | Lisensi bisa berubah di versi berikutnya; status nirlaba harus tetap terpenuhi |

**Keputusan:** Remotion **tidak diwajibkan**. Rilis 1 memakai libass + Pillow + FFmpeg (semuanya bebas biaya dan
sudah terbukti di aplikasi). Remotion bisa ditambahkan di Rilis 2+ sebagai **renderer opsional** untuk motion
graphics yang sangat kompleks, hanya setelah eligibility nirlaba dikonfirmasi tertulis.

---

## 1. Advanced Animated Captions

| Fitur | Status | Rincian |
|---|---|---|
| Karaoke per kata | ✅ | kata aktif berwarna, timestamp per kata dari Whisper |
| Gaya pop & muncul per kata | ✅ | `pop` (kata aktif membesar), `word` (satu kata dengan animasi skala), `simple` |
| Font, warna, warna sorotan, posisi (atas/tengah/bawah), ukuran, HURUF BESAR, kata per baris | ✅ | Arial, Poppins, Anton, Bebas Neue, Impact (font OFL dibundel) |
| Preset/template subtitle | ✅ | 5 bawaan + preset pribadi/unit (P1) |
| Pratinjau tanpa render ulang | ✅ | perkiraan CSS; posisi dan warna diukur cocok dengan MP4 (P1) |
| **Outline** (warna & tebal), **bayangan** (jarak, blur, warna), **kotak latar** semi-transparan | 🎯 | kini outline hitam tetap; menjadi pengaturan ASS (`\bord`, `\shad`, `\3c`, `\4c`, BorderStyle 3) |
| **Animasi caption**: pop, **bounce**, **slide**, **fade**, **zoom** (per kata & per baris) | 🎯 | tag ASS libass: `\t(\fscx\fscy)` bertahap untuk bounce, `\move` untuk slide, `\fad` untuk fade, skala untuk zoom; dipilih per preset |
| **Highlight otomatis kata penting** | 🎯 | warna/ukuran khusus untuk kata pemancing, angka, nama, kata yang diucapkan paling keras, dan kata non-umum (daftar kata umum bahasa Indonesia); bisa ditambah/dihapus per kata |
| **Koreksi teks dengan sinkronisasi waktu** | 🎯 | kini: kata yang jumlahnya berubah dibagi rata dalam segmen. Rilis 1: pencocokan per kata (`difflib`) mempertahankan waktu kata yang tidak berubah, hanya kata baru yang diinterpolasi; geser batas kata di timeline |
| **Pratinjau animasi = MP4 final** | 🎯 | pratinjau memakai **libass-wasm** dengan berkas ASS yang **sama** dengan render (bukan tiruan CSS). Uji: selisih piksel frame pratinjau vs MP4 per gaya animasi |
| Preset modern short-form | 🎯 | ±10 preset siap pakai (mis. "Bold Kuning", "Bounce Hijau", "Slide Minimal", "Kotak Hitam", "Highlight Merah") termasuk 5 preset Nurul Hayat (§5) |
| Emoji otomatis, efek per huruf, caption 2 bahasa | 🔜 | — |

## 2. Motion Graphics Engine

| Fitur | Status | Rincian |
|---|---|---|
| Punch-in zoom pada penekanan, zoom-in perlahan, fade in/out | ✅ | dari rencana animasi otomatis (lokal) |
| Kartu poin, kata kunci besar, chips, checklist (animasi masuk) | ✅ | PNG Pillow + overlay FFmpeg |
| Bingkai mengikuti wajah (pan otomatis) | ✅ | YuNet + jalur kamera halus |
| **Hook di awal video** (judul 2–3 detik, animasi masuk) | 🎯 | teks dari kalimat hook klip (bisa diedit), gaya dari template |
| **Lower-third nama narasumber** | 🎯 | nama + jabatan, masuk/keluar slide, waktu bisa diatur |
| **Logo Nurul Hayat** (watermark + animasi masuk sederhana fade/slide) | 🎯 | PNG/SVG→PNG dari brand kit, posisi pojok, opasitas |
| **Outro + CTA donasi** | 🎯 | kartu penutup 3–5 detik: logo, ajakan, rekening/URL/QR (gambar), animasi masuk sederhana |
| Pan & zoom manual (titik kunci awal-akhir) | 🎯 | ekspresi crop/scale FFmpeg (sudah dipakai untuk zoom) |
| **Kinetic typography** penuh (kata beterbangan, susunan dinamis) | 🔜 | ASS per kata (`\move`, `\t`, `\frz`) di atas mesin Rilis 1 |
| **Animasi angka** (hitung naik "1.250 paket") | 🔜 | event ASS per frame (tetap libass, jadi pratinjau = MP4) |
| **Transisi antaradegan** (xfade: fade, slide, wipe, zoom) | 🔜 | butuh klip multi-potongan (§3 jump cut); filter `xfade` FFmpeg |
| Animasi logo kompleks (Lottie) | 🔜 | evaluasi `rlottie` (MIT) atau Remotion opsional; tidak di Rilis 1 |

## 3. AI Smart Editing

Semuanya lokal, tanpa LLM berbayar.

| Fitur | Status | Rincian |
|---|---|---|
| Rekomendasi beberapa klip dari satu video | ✅ | kandidat tidak saling tumpang tindih |
| **Skor daya tarik dengan alasan** | ✅ | skor 0–100 dari energi suara, kepadatan bicara, kalimat pembuka pemancing, akhir kalimat, kecocokan durasi; alasan tertulis ("pembuka memancing rasa penasaran", "padat bicara", "ada jeda panjang") |
| Identifikasi hook | ✅ dasar | pertanyaan & kata pemancing (Indonesia + Inggris) di awal klip |
| Auto-reframe mengikuti wajah | ✅ | 9:16, 1:1, 4:5 |
| Pilihan durasi | ✅ | ±15/30/60/90 detik |
| **Preset platform** (Shorts ≤ 60 dtk, Reels ≤ 90 dtk, TikTok, YouTube 16:9) | 🎯 | durasi + rasio + zona aman caption per platform |
| **Hook lebih baik** | 🎯 | daftar frasa hook bahasa Indonesia (dakwah/sosial: "tahukah", "bayangkan", "kisah", angka di awal), skor 3 detik pertama terpisah, label "Hook kuat/sedang/lemah" |
| **Deteksi jeda panjang** | 🎯 | penanda di timeline (VAD Silero sudah dipakai di transkripsi) + potong otomatis jeda di **awal/akhir** klip |
| Potong jeda **di dalam** klip (jump cut) | 🔜 | butuh klip multi-potongan (daftar potongan + `concat`); dasar untuk transisi & B-roll |
| Deteksi bagian kurang relevan | 🔜 | ⚠️ tanpa LLM: kata pengisi ("eh", "anu", "jadi gini"), pengulangan, jeda; LLM lokal (mis. model berlisensi Apache-2.0 via llama.cpp) hanya bila lisensi & performa PC memadai. Tidak dijanjikan di Rilis 1. |
| Reframe mengikuti **subjek non-wajah** | 🔜 | pelacak objek OpenCV (Apache-2.0); wajah tetap prioritas |

⚠️ Skor OpusClip memakai model AI cloud. Skor di sini heuristik lokal yang transparan alasannya. Akurasinya perlu
dievaluasi dengan editor Nurul Hayat (target: ≥ 2 dari 3 kandidat teratas dinilai layak).

## 4. B-Roll & Visual Storytelling

| Fitur | Status | Rincian |
|---|---|---|
| **Overlay gambar** (foto, logo, QR) dengan waktu & posisi | 🎯 | dasar logo/CTA (§2); gambar dari brand kit atau berkas lokal |
| **Pustaka B-roll lokal** | 🔜 | folder pustaka; impor video/foto; tag manual + kata dari nama berkas; gambar mini |
| **Rekomendasi B-roll dari transkrip** | 🔜 | tahap 1: kata kunci transkrip (stemming Sastrawi, MIT) ↔ tag pustaka. Tahap 2 (opsional): embedding teks multibahasa lokal berlisensi Apache-2.0, bila lolos uji performa CPU |
| **Pengaturan kapan B-roll muncul** | 🔜 | ditempel ke kata/segmen; mode layar penuh (cutaway) atau gambar-dalam-gambar; durasi & transisi |
| **Overlay video** | 🔜 | bersama klip multi-potongan |
| Generasi B-roll dengan AI | ❌ **tidak dijanjikan** | tidak ada model lokal yang layak & legal untuk PC kantor saat ini |

## 5. Template Nurul Hayat

| Fitur | Status | Rincian |
|---|---|---|
| Template subtitle | ✅ | dasar template (P1) |
| **Brand kit yang bisa diubah** | 🎯 | logo (terang/gelap), warna primer/sekunder, font, teks CTA, rekening/URL/QR donasi, nama lembaga; satu berkas yang bisa diekspor/impor ke komputer lain |
| **5 template** | 🎯 | Dokumentasi kegiatan sosial · Kisah penerima manfaat · Fundraising · Tanggap bencana · Edukasi zakat |
| Isi tiap template | 🎯 | preset caption + warna/filter + hook pembuka + lower-third + logo + outro CTA + durasi/platform bawaan |
| Berbagi template antar komputer | 🎯 | ekspor/impor berkas `.nhtemplate`, karena desktop offline tidak punya server |
| Template animasi intro bermerek kompleks | 🔜 | setelah mesin motion Rilis 2 |

⚠️ Aset brand (logo resmi, warna, kalimat CTA, rekening) **disediakan tim Nurul Hayat**. Template tidak memuat
data rekening sebelum disetujui.

## 6. Desktop & Ekspor

| Fitur | Status | Rincian |
|---|---|---|
| Fitur inti tanpa internet (transkripsi, klip, caption, animasi otomatis, render) | ✅ di mode lokal | ⚠️ kini model diunduh saat pertama dipakai & `start.bat` memasang library dari internet |
| Pratinjau cepat tanpa render ulang | ✅ | resep editing (P1); proxy 480p |
| Render massal (klip dicentang) | ✅ | satu rasio per kali |
| **Offline penuh sejak instalasi** | 🎯 | model, library, FFmpeg, font dibundel; uji dengan jaringan dimatikan; impor URL disembunyikan saat offline |
| **Installer Windows** untuk banyak komputer | 🎯 | Inno Setup; per-mesin atau per-pengguna; mode senyap untuk pemasangan massal (USB/jaringan/Intune); uninstaller; pembaruan = pasang versi baru di atasnya (migrasi database otomatis sudah ada) |
| **Mode satu pengguna** | 🎯 | tanpa login di desktop (akun lokal otomatis); peran/akun hanya di versi server |
| **Batch export 9:16, 1:1, 16:9 sekaligus** | 🎯 | pilih klip × rasio dalam satu aksi; posisi crop per rasio; folder & pola nama keluaran; antrean dengan progres & batal |
| Encoder GPU (NVIDIA/Intel/AMD) bila ada | ✅ | otomatis kembali ke CPU |
| Kebijakan media | 🎯 | desktop tidak menghapus otomatis 2/5 jam (itu aturan server); pembersihan cache proxy/sementara manual & otomatis untuk proyek lama |
| Tanda tangan kode installer | 🔜 | ⚠️ sertifikat code signing **berbayar** (butuh persetujuan). Tanpa itu Windows SmartScreen menampilkan peringatan "Run anyway", sehingga perlu panduan pemasangan |
| Pembaruan otomatis, versi macOS | 🔜 | — |

### Spesifikasi PC (target, diukur di Rilis 1)

| | Minimum | Disarankan |
|---|---|---|
| OS | Windows 10 64-bit (22H2) | Windows 11 |
| CPU | 4 core (Intel i5 gen 8 / Ryzen 5 2xxx) | 6–8 core |
| RAM | 8 GB | 16 GB |
| Disk | 5 GB untuk aplikasi + ruang proyek | SSD |
| GPU | tidak wajib | NVIDIA/Intel untuk render lebih cepat |

Perkiraan ukuran installer: ±0,8–1,2 GB (runtime ±400 MB, FFmpeg ±120 MB, Whisper base ±145 MB; small +±480 MB
sebagai komponen opsional).

---

## 7. Lisensi (semua bebas biaya; tidak ada API berbayar)

| Komponen | Lisensi | Status / kewajiban |
|---|---|---|
| Kode aplikasi | milik Nurul Hayat/tim | — |
| Python & library produksi | permisif + LGPL/MPL (psycopg tidak dipakai di desktop) | CI lisensi sudah berjalan (`scripts/check_licenses.py`) |
| PyInstaller, pywebview, Inno Setup | GPL+exception / BSD-3 / Inno Setup License | aman untuk aplikasi internal |
| FFmpeg + libx264 | GPL | distribusi ke komputer organisasi: sertakan teks GPL & tautan/sumber build; ⚠️ paten H.264/AAC untuk penggunaan non-komersial internal perlu dikonfirmasi legal (sudah tercatat di `docs/LICENSES.md`) |
| Whisper base/small (Systran faster-whisper) | diharapkan MIT | **MANUAL**: cek kartu model per ukuran (sudah tercatat) |
| YuNet | MIT | terverifikasi |
| libass / JavascriptSubtitlesOctopus | ISC / MIT | baru; periksa saat ditambahkan |
| Font Poppins, Anton, Bebas Neue | OFL | sudah dibundel dengan teks lisensi |
| Sastrawi (stemming, Rilis 2) | MIT | baru |
| Remotion (opsional, Rilis 2+) | source-available; gratis untuk nirlaba | **hanya** setelah konfirmasi tertulis status nirlaba |

Installer menyertakan `THIRD_PARTY_NOTICES.txt` (dibuat otomatis dari daftar di atas) dan berhenti dibangun bila CI
lisensi menemukan GPL baru yang belum disetujui.

---

## 8. Rencana rilis

| Tahap | Isi | Hasil |
|---|---|---|
| **M1 — Installer MVP** (didahulukan) | pywebview + PyInstaller + Inno Setup; bundel FFmpeg, model base, font, YuNet; mode satu pengguna; offline sejak instalasi; batch export 3 rasio; preset platform | **Beta internal** yang sudah bisa dipakai: impor → klip AI → caption (gaya yang ada) → edit → ekspor massal |
| **M2 — Caption animasi** | outline/bayangan/kotak; pop/bounce/slide/fade/zoom; highlight otomatis; koreksi teks per kata; pratinjau libass-wasm = MP4; ±10 preset | caption setara short-form modern |
| **M3 — Identitas & motion dasar** | brand kit; 5 template Nurul Hayat; hook pembuka; lower-third; logo; outro CTA; overlay gambar; pan/zoom manual; deteksi & potong jeda tepi; hook lebih baik | **Rilis 1** = M1 + M2 + M3 |
| Rilis 2+ | klip multi-potongan (jump cut, transisi, B-roll video), pustaka & rekomendasi B-roll, kinetic typography, animasi angka, pelacakan subjek, kata pengisi, Lottie/Remotion opsional, code signing, pembaruan otomatis | penyempurnaan |

M1 dirilis sebagai beta internal **tanpa menunggu** M2/M3, sehingga installer tidak tertunda oleh efek motion.

## 9. Kriteria terima Rilis 1 (terukur)

| # | Kriteria | Target |
|---|---|---|
| D1 | Pasang di PC Windows bersih **tanpa internet** | selesai ≤ 5 menit; aplikasi terbuka; tidak ada unduhan saat jalan (diuji dengan jaringan diputus) |
| D2 | Pemasangan senyap | `setup.exe /VERYSILENT` berhasil di ≥ 3 PC berbeda |
| D3 | Alur MVP | video 10 menit → ≥ 3 kandidat klip dengan skor & alasan → caption animasi → ekspor |
| D4 | Kecepatan analisis (model base, PC minimum) | ≤ 1,5× durasi video (diukur, bukan janji) |
| D5 | Batch export | 3 klip × 3 rasio = 9 MP4 benar (resolusi, durasi ±0,2 dtk, audio, caption) |
| D6 | Pratinjau = MP4 | selisih piksel rata-rata frame pratinjau vs MP4 ≤ 3/255 untuk setiap gaya animasi caption (mesin libass yang sama) |
| D7 | Template NH | 5 template menghasilkan video dengan logo, lower-third, hook, dan outro CTA dari brand kit; brand kit bisa diekspor & dipakai di PC lain |
| D8 | Lisensi | `THIRD_PARTY_NOTICES` lengkap; tidak ada API berbayar; tidak ada dependensi komersial tanpa persetujuan |
| D9 | Uji pengguna | ≥ 3 editor Nurul Hayat menyelesaikan alur MVP tanpa bantuan; ≥ 2 dari 3 kandidat klip teratas dinilai layak |

## 10. Keputusan yang dibutuhkan

1. Setuju bentuk desktop: kode yang sama + pywebview + PyInstaller + Inno Setup (§0)?
2. Remotion: tidak diwajibkan; opsional Rilis 2+ setelah konfirmasi status nirlaba?
3. FFmpeg GPL (libx264) didistribusikan internal dengan teks lisensi; perlu konfirmasi legal soal paten H.264/AAC?
4. Code signing berbayar, atau terima peringatan SmartScreen + panduan untuk beta?
5. Penyedia aset brand (logo resmi, warna, teks CTA, rekening/QR) untuk template.
6. Urutan kerja terhadap staging VPS (audit menunggu dijalankan): desktop M1 lebih dulu, paralel, atau setelah staging?
