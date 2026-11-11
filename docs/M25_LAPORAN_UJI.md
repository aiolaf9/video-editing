# M2.5 Motion Library: laporan uji (60 preset)

Tanggal uji: 2026-11-11. Mesin uji lokal: Linux, 4 CPU, tanpa GPU, FFmpeg 6.1 (CI Windows: FFmpeg 7.1.1
essentials). Semua angka di bawah diukur dari jalur render aplikasi (`pipeline.assemble` -> `render.build_command`),
yang juga dipakai pratinjau dan ekspor MP4.

## 1. Video nyata yang dipakai (tidak di-commit)

| Berkas | Sumber & lisensi | Isi |
|---|---|---|
| kajian (60 dtk, 640x480) | "Timeless Medicine", khutbah Imam Lyndon A. Bilal, NAICCDC, archive.org `TimelessMedicine_090211`, **CC0 1.0** | ceramah di mimbar, satu wajah, rak buku di belakang, ucapan bahasa Inggris |
| peternakan (126 dtk, 1920x1080) | "Animals with their off-spring - Lamb and baby goats", NatureClip, archive.org `youtube-TBAyVlWpE4M`, **CC BY 3.0** | domba & anak kambing, kamera genggam, tanpa ucapan |

Catatan jujur: Wikimedia Commons (video kajian 1080p dan sapi) menolak unduhan dari lingkungan uji ini (HTTP 429
terus-menerus berjam-jam), jadi dipakai arsip CC0/CC BY di atas. Kajian sumbernya 640x480 sehingga hasil 9:16
terlihat lunak (keterbatasan sumber, bukan encoder). Video peternakan tanpa ucapan: caption pada contoh itu memakai
transkrip sintetis (kata contoh), hanya untuk memperlihatkan animasinya.

Transkrip kajian: Whisper **Small** (jalur standar aplikasi), 131 kata, 29,6 dtk untuk audio 60 dtk (CPU).

## 2. Hasil per kategori (MP4 1080x1920, klip 40 dtk)

Contoh kategori memadatkan semua preset satu kategori dalam satu klip pada tingkat Dinamis; `--strict` gagal bila
ada preset yang tidak tampil. **Kedua video lolos: 60/60 preset tampil.**

| Video | Kategori | Waktu render | x real-time | CPU (dtk) | RAM puncak FFmpeg |
|---|---|---|---|---|---|
| kajian | captions (15) | 34,5 dtk | 1,16 | 124 | 724 MB |
| kajian | titles (10) | 34,8 dtk | 1,15 | 125 | 737 MB |
| kajian | camera (10) | 45,1 dtk | 0,89 | 160 | 868 MB |
| kajian | transitions (10) | 42,0 dtk | 0,95 | 143 | 1068 MB |
| kajian | graphics (10) | 35,5 dtk | 1,13 | 126 | 734 MB |
| kajian | story (5) | 34,6 dtk | 1,16 | 125 | 728 MB |
| peternakan | titles | 65,1 dtk | 0,61 | 245 | 906 MB |
| peternakan | camera | 68,9 dtk | 0,58 | 259 | 895 MB |
| peternakan | transitions | 65,4 dtk | 0,61 | 242 | 1115 MB |
| peternakan | graphics | 62,1 dtk | 0,64 | 233 | 905 MB |
| peternakan | story | 61,6 dtk | 0,65 | 230 | 899 MB |

Variasi pada video yang sama (kombinasi Auto Style "Kajian": judul sinematik + Ken Burns + bab + bilah ikuti,
subtitle karaoke): Ringan 35,2 dtk, Seimbang 35,6 dtk, Dinamis 36,4 dtk, RAM 738-744 MB. Intensitas tidak
menambah biaya berarti.

Kesimpulan sumber daya: tidak ada preset yang butuh GPU; RAM puncak FFmpeg ≤ 1,12 GB (transisi piksel/glitch
paling berat karena konversi RGB), aman untuk PC 8 GB. Sumber 1080p dirender ±0,6x real-time di 4 inti CPU
(klip 60 dtk ±1,6 menit); pratinjau (360x640) jauh lebih cepat. Galeri: 60 klip dibuat 149 dtk sekali, lalu dari cache.

## 3. Safe-zone pada video nyata

| Video | Penempatan grafis | Bebas (tetap/dipindah/diperkecil) | Tumpang tindih |
|---|---|---|---|
| kajian | 25 | 25 (10 tetap, 15 dipindah) | **0**: tidak ada yang menutup wajah atau subtitle |
| peternakan | 27 | 21 | 6 grafis informasi (lihat catatan) |

Temuan & perbaikan dari uji nyata:

1. **Rak buku terbaca sebagai papan materi** (Smart Framing M2): klip kajian berpindah ke tata letak
   Materi + Wajah sehingga wajah tampil dua kali dan grafis diperkecil 50 %. Penyebab: area "detail" (tepi padat)
   dan aktivitasnya ikut menghitung gerak pemateri sendiri. Diperbaiki: gerak di area badan pemateri tidak dihitung,
   dan area "detail" (bukan papan tulis) butuh perubahan jelas (≥ 2,5x ambang) untuk memicu Materi + Wajah.
   Hasil: seluruh 60 dtk menjadi Fokus Wajah. Tes regresi ditambahkan.
2. **Hewan belum dikenali sebagai objek penting** (terbuka). Safe-zone melindungi wajah manusia, area materi,
   subtitle, dan area tombol aplikasi; pada video peternakan tanpa wajah, beberapa grafis (cincin, pin, grafik)
   jatuh di atas domba/kambing, dan 6 grafis informasi tumpang tindih karena tidak ada ruang kosong. Usulan tahap
   berikut: deteksi objek bergerak/menonjol (tanpa model berbayar) sebagai area "objek" + area lindung yang bisa
   digambar pengguna. Sampai itu ada, kontrol "Posisi" per item dipakai untuk memindahkan grafis.
3. Iris (transisi) awalnya tampil sebagai kotak hitam kecil (lubang gambar ASS tidak terbentuk); diganti teknik
   lingkaran bergaris tepi tebal yang mengecil. Rotasi kamera dicek piksel: tidak ada sudut hitam sampai intensitas
   maksimum (Dinamis x 2 = ±8,7°).

## 4. Konsistensi pratinjau = MP4

Tes API: pratinjau 360x640 dan render final 1080x1920 dari resep yang sama memakai elemen Motion Library yang sama
(daftar preset di ASS identik, ekspresi kamera/kecerahan sama). Galeri dirender dengan fungsi yang sama.

## 5. Yang sudah dan belum

Sudah: 60 preset (15/10/10/10/10/5) tersedia, bisa dipilih dari galeri, lolos tes otomatis dan tampil di kedua
video nyata. Kompatibilitas: resep M1/M2 lama tetap terbuka (`motion.level` bawaan), Whisper Small standar &
Turbo opsional tidak berubah, encoder MP4 tetap libx264 medium crf 20.

Belum: perlindungan objek non-wajah (hewan/benda) di safe-zone; video kajian berbahasa Indonesia dengan papan tulis
sungguhan berlisensi bebas belum ditemukan (uji papan tulis tetap dari video sintetis M2 di CI Windows).
