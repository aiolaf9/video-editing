# M2 — Smart Video Editing & Motion Studio (NH AI Clip Studio, branch `desktop/m2`)

Semua pemrosesan berjalan lokal: OpenCV (YuNet, deteksi wajah), FFmpeg + libass (render, subtitle, motion),
Pillow (PNG overlay), dan Whisper small untuk transkrip (M1.1, tidak berubah). Tidak ada API AI berbayar.
Versi web produksi (`v2/phase-1`) tidak disentuh.

## Milestone

| Milestone | Commit | Isi |
|---|---|---|
| M2.1 Smart Framing | `d4354dd` | wajah + area materi, mode Otomatis / Fokus Wajah / Fokus Materi / Manual, per bagian, pratinjau MP4 |
| M2.2 Subtitle animasi | `feb25af` | 14 preset libass, sorot kata penting, pengaturan gaya, galeri (thumbnail + klip gerak) |
| M2.3 Motion graphics | `2e35c46` | hook, kinetic text, lower-third, animasi angka, CTA, gambar/logo, punch-in yang bisa diatur |
| M2.4 / M2.5 | `93b60ce` | gaya per kalimat, undo/redo, Auto Style 5 jenis konten |
| M2.6 CI & installer | `e2a9656` dst. | selftest M2 di aplikasi terpasang, uji upgrade M1.1 → M2, bukti MP4 |

## 1. Smart Framing (`app/framing.py`)

**Cara kerja (jujur):** analisis sekali per proyek dari preview 480p (640 px, 2 sampel/detik):

- **Wajah**: detektor YuNet (OpenCV), wajah terbesar = pemateri.
- **Area materi**: HEURISTIK, bukan pemahaman isi. Papan/layar = area terang, warna netral, berbentuk persegi,
  dan berisi garis/tulisan (kepadatan tepi Canny); kemeja putih pemateri dan dinding polos ditolak. Bila tidak
  ada papan terang: kumpulan sel gambar dengan tepi terpadat di luar pemateri ("area detail").
  Aplikasi tidak membaca tulisan dan tidak tahu apakah isinya penting.
- **Aktivitas**: % piksel di area materi yang berubah antarsampel (menulis, menunjuk, ganti slide).

**Tata letak per bagian** (minimal 3 dtk, tiap bagian punya alasan yang ditampilkan di editor):

| Tata letak | Kapan (mode Otomatis) | Hasil 9:16 |
|---|---|---|
| Fokus Wajah | wajah terlihat, papan tidak berubah / tidak ada papan | bingkai mengikuti wajah (x & y), gerak halus smoothstep, zoom otomatis maks 1,6× untuk pemateri jauh |
| Materi + Wajah | pemateri di dekat papan, atau ada aktivitas di papan | atas: papan (56 %), bawah: wajah; subtitle pindah ke garis sambung |
| Fokus Materi | papan ada, wajah tidak terlihat (menghadap papan) | papan utuh di atas latar blur |
| Bingkai tetap | manual / tidak ada wajah & papan | posisi X/Y + zoom dari pengguna |

Mode: **Otomatis**, **Fokus Wajah**, **Fokus Materi**, **Manual**; jarak wajah Otomatis/Dekat/Sedang/Lebar; pilihan
tata letak per bagian (menimpa Otomatis; bila tidak bisa dipenuhi, editor menjelaskan alasannya).
Proyek M1/M1.1: resep lama tanpa `mode` tetap dirender seperti dulu (`reframe` auto → Fokus Wajah).

Biaya analisis: ±0,9 dtk untuk video 24 dtk di CPU 4 thread lokal (±2–3 menit untuk kajian 60 menit).

## 2. Subtitle animasi (`app/subtitles.py`, `app/gallery.py`)

14 preset, masing-masing teknik libass berbeda: Karaoke, Pop, Satu Kata, Sederhana, Bounce (skala memantul),
Slide (`\move` + fade), Zoom (skala + blur), Fade (kata muncul satu per satu), Cinematic (huruf renggang menyempit),
Dokumenter (kotak gelap, kalimat utuh), Bercerita (kalimat redup, kata yang diucapkan menyala), Sorot Kotak (kotak
warna di belakang kata aktif), Neon (dua lapis, glow), Isi Warna (`\kf` menyapu warna mengikuti ucapan).

- **Kata penting**: istilah kosakata (M1.1) > angka > kata kunci ≥ 7 huruf, maks 1 per baris (aturan, bukan AI).
- **Pengaturan**: font, ukuran, warna, warna sorot, outline (warna & tebal), bayangan, kotak latar (warna & pekat),
  posisi atas/tengah/bawah/bebas, huruf besar, kata per baris; gaya khusus per kalimat.
- **Koreksi transkrip M1.1 dipakai semua gaya** (diuji untuk ke-14 preset, di pratinjau dan render final).
- **Konsistensi**: galeri (thumbnail + klip gerak) dan "▶ Pratinjau hasil" dirender dengan `build_ass` + FFmpeg/libass
  yang sama dengan render final. Teks di atas player hanya perkiraan cepat (diberi label).
- Bawaan opsi baru = tampilan M1.1: subtitle proyek lama tidak berubah.

## 3. Motion graphics (`app/motion.py`, `app/overlays.py`)

| Elemen | Implementasi | Dapat diatur |
|---|---|---|
| Hook pembuka | pita gelap + teks besar "dibanting" masuk, kata terakhir berwarna | teks, mulai/akhir; disarankan otomatis (pertanyaan > angka > kata pemancing > kalimat pertama) |
| Kinetic typography | kata demi kata, miring bergantian, zoom masuk | teks, mulai/akhir |
| Lower-third | bilah aksen + nama & jabatan meluncur dari kiri | nama, jabatan, mulai/akhir |
| Kartu informasi, kata kunci, chips, checklist | PNG (dari P1) | teks, mulai/akhir |
| Animasi angka | angka berhitung naik (format Indonesia: 2,5 / 1.500.000) | teks angka, keterangan |
| CTA | tombol aksen membal lalu berdenyut | teks, mulai/akhir |
| Gambar/logo | PNG/JPG/WebP proyek (divalidasi, maks 8 MB) | posisi, ukuran, kepekatan, mulai/akhir |
| Zoom/punch-in | skala per elemen | kekuatan 5–40 %; otomatis dilewati pada bagian Materi+Wajah/Materi |

Data rekening/QR donasi **tidak pernah** dibuat otomatis.

## 4. Galeri, per bagian, undo/redo (`web/studio.js`)

Galeri dengan thumbnail dan klip gerak (arahkan kursor), satu klik memakai preset; "Kalimat ini saja" untuk gaya
per kalimat; ↶ Urungkan / ↷ Ulangi (Ctrl+Z, Ctrl+Shift+Z, Ctrl+Y) untuk gaya, bingkai, subtitle, dan animasi.
Koreksi teks transkrip sengaja tidak ikut undo (alurnya sendiri di editor M1.1).

## 5. Auto Style (`app/autostyle.py`)

Aturan lokal yang dapat dijelaskan: kata penanda di transkrip + sinyal bingkai (papan terdeteksi, lebih dari satu
wajah, wajah jarang terlihat, kata ganti orang pertama, kalimat tanya, kalimat ajakan). Editor menampilkan kategori,
keyakinan, dan alasannya; pengguna dapat memilih kategori lain lalu "Terapkan" (bisa dibatalkan).

| Kategori | Subtitle | Bingkai | Warna | Motion disarankan |
|---|---|---|---|---|
| Kajian | Sorot Kotak | Otomatis | asli | hook, lower-third pemateri |
| Wawancara | Dokumenter | Fokus Wajah | asli | lower-third |
| Dokumentasi Sosial | Cinematic | Otomatis | sinematik | hook, angka |
| Kisah Penerima Manfaat | Bercerita | Fokus Wajah | hangat | hook |
| Fundraising | Bounce | Otomatis | cerah | hook, angka, CTA (tanpa rekening/QR) |

## 6. Remotion: tidak dipakai

Dievaluasi dan **tidak ditambahkan**: (1) perlu Node.js + Chromium headless per render. Itu ratusan MB tambahan
dan berat untuk PC 8 GB, padahal FFmpeg + libass sudah dibundel. (2) Lisensi Remotion (setahu saya) gratis hanya
untuk perorangan dan organisasi kecil; organisasi yang lebih besar memerlukan lisensi berbayar. Perlu dicek ulang
oleh NH bila suatu saat dipertimbangkan. Semua animasi M2 dibuat dengan libass/FFmpeg (LGPL/GPL, sudah dibundel M1).

## 7. Pengujian

- **Otomatis lokal**: 556 tes non-browser + tes browser Chromium (framing, galeri, motion, Auto Style, undo/redo,
  gaya per kalimat, pratinjau MP4).
- **Windows CI**:
  - Selftest M2 di aplikasi beku dan terpasang, memakai FFmpeg/libass/font bundel. Isinya: preset, galeri, rencana
    bingkai, 5 elemen motion, pratinjau, dan render 1080×1920.
  - Uji upgrade dari installer M1.1 asli (run 42818439989): proyek M1.1 dibuat, M2 dipasang di atasnya, berkas,
    tabel, dan resep dibandingkan, lalu proyek lama dirender ulang dengan M2.
- **Bukti MP4** (artefak `m2-bukti-mp4`). Video uji kajian sintetis: pemateri = foto astronaut NASA (domain publik),
  papan tulis digambar, dan audio ucapan Indonesia asli FLEURS ditranskripsi Whisper small.
  - `sebelum.mp4` vs `sesudah-<preset>.mp4`.
  - `sesudah-lengkap.mp4`: motion otomatis tanpa edit manual.
  - `bingkai-pemateri-jauh/`: semua mode framing untuk pemateri yang jauh.

## 8. Keterbatasan (belum terselesaikan)

- **Belum diuji dengan video kajian NH asli.** Tidak ada video NH yang sah di lingkungan kerja. Video uji sintetis
  tidak mewakili pencahayaan masjid, papan yang terpantul, atau kamera yang bergerak.
- Deteksi papan mengandalkan papan terang. Papan hitam/hijau, layar proyektor gelap, atau kamera yang bergerak
  akan jatuh ke "area detail" (kurang andal) atau tidak terdeteksi. Selalu cek dengan Pratinjau.
- Satu wajah utama (terbesar). Wawancara dua orang tidak dibingkai bergantian per pembicara (tidak ada deteksi
  siapa yang bicara).
- Kecepatan animasi dan ukuran grafis disetel untuk 9:16. Pada 16:9/1:1 grafis tetap jalan, tetapi belum dipoles.
- Gaya per kalimat hanya mengganti animasi; font & warna mengikuti gaya utama.
- Pratinjau di atas player adalah perkiraan; tampilan persis hanya lewat "▶ Pratinjau hasil" (render 10 dtk).
- Belum diukur di PC 8 GB sungguhan (runner CI 16 GB).

## 9. Perbaikan ekspor (laporan pengguna: teks ganda di awal, gambar kurang tajam)

**Teks ganda "APA JAWAB MEREKA?"**: keduanya dibuat aplikasi. Hook pembuka otomatis memakai kalimat pertanyaan
pertama di klip, dan kalimat itu juga sedang diucapkan sehingga subtitle (gaya Pop, sorot hijau) menampilkannya
bersamaan. Aturan awal (≥ 60 % kata sama) sudah DIGANTI dengan aturan ekuivalensi ketat (bagian 10). Bila animasi dimatikan, subtitle pembuka kembali tampil. Cara memastikan teks tidak menyatu di video
sumber: matikan subtitle dan animasi, lalu klik "▶ Pratinjau hasil"; teks yang masih terlihat berasal dari sumber.

**Kurang tajam**: penyebab terbukti = pembesaran crop, bukan preview atau encode berulang.

| Pemeriksaan | Hasil |
|---|---|
| Sumber render | video sumber asli (`source.*`), bukan `preview.mp4` 480p (diuji) |
| Jumlah encode | satu perintah FFmpeg dari sumber ke MP4 final (diuji); salinan ke folder ekspor = salin berkas |
| Tombol Download | `renders/<id>.mp4` = berkas final (byte identik, diuji); pratinjau ada di `previews/` |
| Crop Fokus Wajah (jarak Otomatis), sumber 1080p | **378×674 → 1080×1920 = 2,85×** (zoom otomatis 1,6× di atas crop 9:16 yang sudah 1,78×) |
| Sesudah perbaikan | **540×960 → 2,0×** (2× lebih banyak piksel sumber); sumber 4K boleh zoom s.d. 2,25×; 720p tanpa zoom tambahan |
| Materi + Wajah (bagian tengah) | papan 1000×994 → 1,08×, wajah 688×540 → 1,57×: sudah di bawah batas, tidak berubah |
| Encoder | veryfast → medium (CRF 20 tetap): SSIM terhadap render lossless 0,9963 → 0,9979; render CPU ±1,5× lebih lama |

Batas pembesaran: otomatis 2,0×, zoom manual 2,5× (`app/framing.py` `MAX_UPSCALE`); slider zoom menampilkan
pembesaran dari sumber. Video sumber 16:9 1080p yang dijadikan 9:16 selalu diperbesar minimal 1,78× (keterbatasan
fisik crop vertikal); hasil paling tajam bila sumber 4K atau direkam vertikal.

## 10. Safe-zone dinamis motion graphics & deduplikasi ketat

**Area terpakai** per bagian tata letak (`app/framing.occupied_rects`, geometri crop yang sama dengan render):
kepala & bahu pemateri (Fokus Wajah, panel bawah Materi + Wajah, bingkai tetap), papan/materi (panel atas,
papan di atas latar blur), pita subtitle (bawah / garis sambung panel / posisi bebas), area tombol aplikasi media
sosial (atas 6 %, bawah 14 % pada format vertikal), dan grafis lain yang tampil bersamaan.

**Penempatan** (`app/safezone.py`), urutan prioritas lower-third → angka → hook → kartu → CTA → kata kunci → kinetic:
posisi bawaan bila bebas; bila tidak, dipindah ke posisi bebas pertama (pindai 1 % tinggi bingkai); lalu diperkecil
80 % / 65 % (informasi juga 50 %). Grafis yang melintasi pergantian tata letak dan tidak muat di keduanya dipecah:
posisinya berganti tepat di titik potong tata letak tanpa animasi masuk ulang. Bila tetap tidak ada ruang:

| Jenis | Tanpa ruang aman |
|---|---|
| Dekoratif: kinetic text, CTA, kata kunci, chips | disembunyikan, dengan alasan |
| Informasi: hook, lower-third, angka, kartu, checklist | tetap tampil di posisi yang paling sedikit menutupi (bobot wajah > subtitle > UI > papan), dengan alasan |
| Subtitle (isi percakapan) | tidak pernah disembunyikan oleh safe-zone |

Editor (tab Animasi) menampilkan hasilnya per elemen: ↕ dipindah, ↘ diperkecil, ⚠ tumpang tindih, 🚫 disembunyikan,
beserta alasannya; pratinjau di player memakai posisi yang sama (`GET /api/projects/{pid}/motion-layout`).

**Deduplikasi subtitle–hook** (pengganti aturan 60 %): sebuah baris subtitle disembunyikan HANYA bila
1. semua katanya sama persis dan berurutan dengan kata-kata hook/kinetic text,
2. kata-kata itu diucapkan di rentang waktu kalimat hook tersebut (deret kata transkrip ≥ 3 kata, atau seluruh hook
   bila lebih pendek, yang identik dengan hook di sekitar waktu tampilnya), dan
3. baris itu muncul saat teks besar benar-benar tampil (grafis yang disembunyikan safe-zone tidak berlaku).
Hook yang diedit menjadi kalimat lain, kalimat yang sama diucapkan di luar waktu hook, kalimat yang mirip, dan baris
yang hanya sebagian sama: semuanya tetap tampil (diuji). Bug yang ditemukan lewat pemeriksaan MP4 sebelum rilis:
kinetic text yang disembunyikan safe-zone sempat ikut menyembunyikan subtitle kalimat yang sama; sudah diperbaiki.

**Bukti MP4** (`bench/m2/studio_demo.py --safezone` + `bench/m2/safezone_check.py`): elemen yang sama pada momen
berisiko (hook di awal, lower-third melintasi Fokus Wajah → Materi + Wajah, angka saat Materi + Wajah, kinetic + kata
kunci saat Fokus Materi, CTA di akhir) dirender tanpa dan dengan safe-zone. Piksel grafis = selisih MP4 terhadap
render "hanya subtitle"; diukur tiap 0,25 dtk (96 frame) di area wajah, papan, subtitle:

| Video uji | Varian | Maks wajah tertutup | Maks papan | Maks subtitle | Frame > 2 % (wajah/papan/subtitle) |
|---|---|---|---|---|---|
| Pemateri dekat | tanpa safe-zone | 28,2 % | 18,2 % | 13,3 % | 38 / 20 / 13 |
| Pemateri dekat | dengan safe-zone | 0,0 % | 1,1 % | 0,0 % | 0 / 0 / 0 |
| Pemateri jauh | tanpa safe-zone | 59,1 % | 19,5 % | 13,3 % | 27 / 20 / 13 |
| Pemateri jauh | dengan safe-zone | 0,0 % | 1,1 % | 0,0 % | 0 / 0 / 0 |

CI Windows menjalankan pemeriksaan yang sama dengan audio ucapan asli + transkrip Whisper dan GAGAL bila safe-zone
menutup wajah/subtitle > 2 % atau papan > 5 %.

Keterbatasan: posisi wajah per bagian memakai median (pemateri yang banyak bergerak dalam satu bagian bisa
tersentuh tepi grafis); PNG multi-bagian (chips, checklist) belum dipindahkan; ukuran teks panjang diperkirakan.
