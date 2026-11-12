# M2.5.1 Protect Subject: Area Dilindungi & kualitas 60 preset

Tanggal uji: 2026-11-11. Mesin lokal: Linux, 4 CPU, tanpa GPU, FFmpeg 6.1. CI Windows memakai FFmpeg 7.1.1.

## 1. Fitur

### Area Dilindungi (manual, utama)
* Tab **Animasi → 🛡 Area Dilindungi**: **▭ + Kotak** (seret di video) atau **⬠ + Poligon** (klik titik, klik dua kali
  untuk menutup). Area baru berlaku 4 dtk dari posisi video; waktu bisa diketik, **↔ Seluruh klip** untuk sepanjang klip.
* Koordinat disimpan relatif terhadap **video sumber** (0..1) di resep proyek (`recipe.protect`), jadi tetap benar
  apa pun format keluaran (9:16, 1:1, …) dan tata letak bingkai. Ikut autosave, undo/redo, render massal, buka ulang.
* **Subjek bergerak**: area tetap tidak mengikuti gerakan. Putar ke titik subjek pindah → **✂ Pisah di sini** →
  **✎ Gambar ulang** untuk bagian berikutnya. Setiap rentang punya kotaknya sendiri.
* Poligon dipakai safe-zone sebagai **kotak pembatasnya** (lebih aman, sedikit lebih luas dari bentuknya).

### Integrasi safe-zone (satu sistem penempatan)
* `framing.occupied_rects(..., protect=...)` memetakan area sumber ke output dengan geometri bingkai yang sama dengan
  wajah/papan (Fokus Wajah, Materi + Wajah: panel atas & bawah, Fokus Materi, bingkai tetap). Jenis area baru
  `lindung` berbobot sama dengan wajah, dan hanya berlaku di rentang waktunya sendiri.
* Aturan tetap sama: informasi (nama, angka, judul, progres) dipindah/diperkecil, atau tetap tampil di posisi yang
  paling sedikit menutupi beserta alasannya; **dekoratif** (chips, CTA kecil, kinetic, judul hentak/glitch/flip)
  boleh disembunyikan. **Subtitle tidak pernah disembunyikan** oleh area dilindungi.
* **Chips & checklist** kini ikut safe-zone (ukuran dihitung dari renderer PNG). Bila safe-zone memperkecil
  grafis PNG (kata kunci, kartu, chips, checklist), PNG **benar-benar** diperkecil saat render; sebelumnya hanya posisi.
* **Keterbacaan**: bila diperkecil sampai teks terkecilnya < 32 px pada lebar 1080, alasan penempatan diberi
  peringatan "⚠ … sulit dibaca di HP" agar editor memberi ruang atau memperpendek teks.

### Saran area otomatis (EKSPERIMENTAL, opsional)
* Tombol **✨ Saran area (eksperimental)** + pilihan jenis (hewan / orang / kendaraan / produk). Deteksi lokal
  **YOLOX-nano** (Megvii, Apache-2.0, 3,7 MB ONNX, sha256 dikunci) lewat onnxruntime CPU yang sudah dibundel:
  tanpa GPU, internet, atau API berbayar.
* Objek utama per sampel (2 sampel/dtk) digabung per jendela 2 dtk menjadi area berlabel "Saran: …". Saran **tidak
  disimpan otomatis**: masuk sebagai area biasa yang wajib diperiksa editor; saran lama di rentang yang sama diganti.
* Ini **bukan** deteksi hewan yang andal. Lihat batasnya di §3.

## 2. Temuan & perbaikan di luar rencana

**Zoom/geser kamera salah sejak M2** (ditemukan uji variasi preset). Render memakai `scale(eval=frame)` lalu `crop`;
filter crop memakai lebar masukan frame pertama, sehingga:
* semua zoom (termasuk punch-in M2, Ken Burns, punch-in tegas, whip) **selalu menuju pojok kiri atas**, bukan pusat;
* geser kamera (Geser horizontal/vertikal, Kamera genggam, bagian geser Ken Burns/Whip) **tidak berpengaruh sama
  sekali**. Geser horizontal & vertikal menghasilkan MP4 identik byte-per-byte.

Diperbaiki dengan filter `perspective` (ukuran tetap, posisi sub-piksel per frame). Uji titik penanda: jangkar
zoom kini tetap (dulu bergeser 162 px ke kanan-bawah saat punch-in 1,3x). `zoompan` juga diuji tetapi bergetar
±1 px (Ken Burns: 56 kali balik arah dalam 100 frame vs 2 dengan `perspective`). Biaya: klip 10 dtk 1080x1920
7,4 dtk tanpa zoom → 9,4 dtk dengan zoom (linear; cubic 10,8 dtk), hanya bila ada efek kamera/punch-in.
Tes regresi (`test_zoom_and_pan_really_render`) gagal pada kode lama dan lulus pada kode baru.

## 3. Hasil uji

### 3.1 MP4 sebelum/sesudah: video peternakan (subjek bergerak)
Video: domba & anak kambing, kamera genggam, 1080p, CC BY 3.0 (NatureClip, archive.org `youtube-TBAyVlWpE4M`),
40 dtk mulai detik 20; 8 elemen (judul, nama, chips, progres, checklist, bab, cincin, CTA). Area = 7 saran otomatis
objek utama (60/80 sampel berisi subjek) yang saya periksa visual pada lembar kontak, **bukan** penandaan tangan
editor. Pengukuran dari piksel MP4 (selisih dengan MP4 tanpa grafis), subjek pembanding = semua hewan terdeteksi
per sampel 0,5 dtk (`bench/m251/protect_compare.py`).

| | Sebelum | Sesudah |
|---|---|---|
| Area subjek tertutup grafis (rata-rata) | 2,5 % | **0,9 %** |
| Area subjek tertutup (maksimum satu sampel) | 12,8 % | **3,4 %** |
| Sampel dengan subjek tertutup > 5 % | 4 | **0** |
| Pita subtitle tertutup | 0 % | 0 % |
| Grafis disembunyikan | CTA | CTA, chips (dekoratif) |

Catatan jujur: rekaman ini close-up genggam; subjek mengisi ±55 % bingkai, sehingga beberapa grafis informasi
(progres, checklist, bab, cincin) **tetap tumpang tindih** karena memang tidak ada ruang kosong. Safe-zone
menaruhnya di posisi yang paling sedikit menutupi dan menjelaskan alasannya. Hewan yang tidak terdeteksi
(mis. kambing dewasa di tepi atas) tidak ikut terukur maupun terlindungi.

### 3.2 Evaluasi deteksi otomatis lokal (YOLOX-nano)
| Video | Sampel | Berisi subjek terdeteksi | Kecepatan |
|---|---|---|---|
| Peternakan (domba/kambing) | 80 | 59 (74 %) | 9 ms/frame (2 thread) |
| Kajian (imam) | 80 | 80 orang | 11,5 ms/frame |

Kotak cukup tepat bila hewan jelas; **terlewat** saat kabur/bergerak cepat, terpotong tepi, atau sangat dekat.
Label kelas tidak andal (kambing tidak ada di COCO → terbaca anjing/beruang/burung/sapi). Kesimpulan: layak sebagai
**saran yang diperiksa editor**, tidak untuk perlindungan otomatis tanpa tinjauan.

### 3.3 Kualitas 60 preset (`bench/m251/preset_quality.py`)
* **Keterbacaan HP**: teks terkecil tiap preset (pada skala 1, lebar 1080) ≥ **42 px** untuk semua preset teks.
  Tiga preset dinaikkan: Bilah Ikuti 36→45, Grafik Garis 37→45, subjudul Judul Sinematik 38→45. Dikunci tes.
* **Variasi nyata** (jarak sinyal efek antarpreset satu kategori pada klip galeri):
  * Kamera: setelah perbaikan zoom/geser, tidak ada pasangan yang mirip (sebelumnya Geser horizontal = vertikal).
  * Pasangan paling mirip yang tersisa: Karaoke Klasik ↔ Isi Warna (keduanya mewarnai kata yang diucapkan;
    teknik berbeda: per kata vs isian kiri-ke-kanan), Garis Waktu ↔ Daftar Bernomor, Kartu Bab ↔ Kutipan,
    Glitch ↔ Piksel. Pengecekan visual pada video nyata menunjukkan transisi berbeda jelas; metrik galeri kurang peka
    untuk efek < 0,5 dtk di adegan datar. Karaoke Klasik ↔ Isi Warna memang mirip sekilas (dicatat, tidak diubah agar
    proyek lama tidak berubah tampilan).
  * Glitch & Piksel diperkuat (geser warna 18→34 px, blok 28→54 px pada 1080) karena hampir tak terlihat.
* **PC spesifikasi rendah** (kajian nyata 640x480 → 1080x1920, 30 dtk, kombinasi Auto Style Kajian + caption):

| CPU dipakai FFmpeg | Render final | Pratinjau 10 dtk (360x640) | RAM puncak FFmpeg |
|---|---|---|---|
| 1 inti | 87 dtk (0,34x real-time) | 1,3 dtk | 577 MB |
| 2 inti | 50 dtk (0,60x) | 1,3 dtk | 664 MB |
| 4 inti | 28 dtk (1,07x) | 1,3 dtk | 747 MB |

  Sumber 1080p lebih berat (±0,6x real-time di 4 inti, M2.5); pada PC 2 inti perkirakan ±3x durasi klip.

## 3.4 Perbaikan setelah review c3f2b04

**Race galeri (CI run 43120628624).** Bug nyata, bukan asumsi tes: pekerja galeri mengeluarkan preset dari antrean
lalu merendernya (±2,5 dtk); selama itu berkas belum ada, sehingga polling status berikutnya melaporkan "antre" dan
`warm()` mengantrekan preset yang sama lagi -> render ganda dan antrean berisi preset yang sudah "siap". Dibuktikan
deterministik (render tiruan ditahan Event, tanpa sleep): status "antre" saat diproses, render dipanggil 2x.
Perbaikan: status pekerjaan dijaga satu lock, `belum -> antre -> proses -> siap | gagal`; `warm()` idempoten
(tidak mengantrekan yang antre/diproses/siap/gagal); preset dilepas dari "proses" setelah berkasnya tertulis;
`wait_idle()` berbasis Condition. Tes regresi `tests/test_m251_gallery_jobs.py` (selesai, gagal, 8 klien
bersamaan, endpoint) gagal di kode lama dan lulus 20/20 putaran di kode baru.

**Gerakan kamera pratinjau = final.** `app/camera_check.py` mengukur zoom & geser dari MP4 terhadap MP4 tanpa efek
pada detik yang sama (zoom & geser ditaksir bersama). Hasil (detik 0,8; pecahan bingkai):

| Efek | Final 1080x1920 | Pratinjau 360x640 |
|---|---|---|
| Geser horizontal | zoom 1,12; dx −0,044; dy 0 | sama |
| Geser vertikal | zoom 1,13; dx 0; dy −0,034 | sama |
| Punch-in | zoom 1,17; dx 0; dy 0 | sama |

Dikunci tes (`test_camera_motion_matches_in_preview_and_final`, gagal di kode c86a89b) dan selftest aplikasi
terpasang (klip derau bertekstur dari FFmpeg bundel, koneksi keluar diblokir di CI Windows).

**Proyek lama.** CI Windows kini memasang M1.1, lalu beta M2.5 (c86a89b) di atasnya; keduanya membuat proyek
(termasuk item Motion Library), lalu versi baru dipasang dan memeriksa + merender ulang semua proyek lama.
Tes Linux memakai resep asli yang dihasilkan kode M2 (ba5f0bf) dan beta M2.5 (c86a89b).

## 4. Kekurangan yang belum teratasi
1. Area dilindungi **tidak mengikuti subjek otomatis**; subjek bergerak perlu dipisah per rentang (atau saran
   eksperimental yang tetap harus diperiksa).
2. Poligon dihitung sebagai kotak pembatasnya, jadi area di luar bentuk ikut terlindungi.
3. Pada close-up yang subjeknya memenuhi bingkai, grafis informasi tetap tumpang tindih (tidak ada ruang).
4. Deteksi lokal melewatkan subjek kabur/terpotong/sangat dekat; label kelas tidak andal.
5. Tata letak Materi + Wajah horizontal (16:9) memetakan area ke panel secara perkiraan.
6. Keterbacaan diukur dari ukuran font, bukan uji mata manusia di beberapa ukuran HP.
