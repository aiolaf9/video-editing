# M2.5 Motion Library Pro

Perpustakaan efek motion berbasis data untuk NH AI Clip Studio (desktop). 60 preset dalam 6 kategori, semuanya
dirender oleh engine yang sama untuk galeri, pratinjau, dan MP4 final (FFmpeg + libass, CPU, offline).

## 1. Arsitektur

| Bagian | Berkas | Isi |
|---|---|---|
| Data preset | `app/data/motion_presets.json` | id, kategori, `kind`, nilai bawaan, kontrol yang boleh diubah, bobot visual (1-3) |
| Engine | `app/motionlib.py` | loader, validasi params (`clean_item`), intensitas, guard kombinasi, ekspresi kamera/transisi |
| Renderer ASS | `app/motion_kinds.py` | satu fungsi `_k_<kind>` per jenis judul/grafis/cerita/transisi layar penuh |
| Caption | `app/subtitles.py` | animasi per kata (15 gaya, termasuk baru: `wordbounce`, `letterpop`), tampilan per rentang |
| Kamera/transisi | `app/render.py` (`camera_fx`) | zoom, geser, rotasi (dengan zoom kompensasi), blur, kecerahan, `pixelize`, `rgbashift` |
| Penyusunan | `app/pipeline.py` (`assemble`, `fx_plan`) | item fx -> subtitle / kamera / ASS + safe-zone; jalur sama untuk pratinjau & render |
| Galeri | `app/motion_gallery.py`, `GET /api/motion-presets[/status|/<id>/preview.mp4|thumb.png]` | klip 4 dtk per preset dari jalur render yang sama, adegan contoh digambar PIL sendiri; installer membawa galeri yang sudah dirender saat build (`scripts/build_motion_gallery.py`), di luar itu dirender satu per satu di thread latar belakang |
| UI | `web/motionlib.js`, panel "Motion Library" | kategori, kartu bergerak, sekali klik, cakupan, intensitas; kontrol per item di tab Animasi |

Menambah preset = menambah data; menambah *teknik* baru = satu renderer `kind`.

### Item fx di resep

```json
{"type": "fx", "preset": "title.cinematic", "params": {"text": "Judul", "speed": 1.2}, "start": 3.0, "end": 6.0}
```

`params` hanya menerima kunci yang dikenal dengan batas nilai (teks dibersihkan dari tag ASS, warna `#RRGGBB`,
font dari daftar bundel). Item tersimpan di resep proyek (`overlays`), ikut autosave, undo/redo, render massal,
dan dibuka kembali utuh. Resep M1/M2 lama tetap valid (`motion.level` bawaan `seimbang`).

## 2. Intensitas & kombinasi (anti berlebihan)

Tingkat klip: **Ringan** (×0,6), **Seimbang** (×1,0, bawaan), **Dinamis** (×1,45) -> pengali amplitudo gerak +
aturan kepadatan (`motionlib.guard`):

* satu efek kamera pada satu waktu; satu judul besar pada satu waktu; satu preset caption per rentang;
* jarak minimum antartransisi: 6 / 4 / 2,5 dtk;
* total bobot visual dalam jendela ±3 dtk: 3 / 5 / 8 (caption tidak dihitung).

Efek yang melanggar **dilewati dengan alasan** yang tampil di daftar Animasi (ikon ⏭), tidak diam-diam.
Kamera & whip juga dilewati pada tata letak Materi + Wajah / Materi (memperbesar kanvas akan memotong papan).

## 3. Auto Style

Kategori: Kajian, Wawancara, Dokumentasi Sosial, Kisah Penerima Manfaat, Fundraising, **Edukasi** (baru).
Tiap kategori punya kombinasi preset (judul pembuka, satu gerak kamera halus, satu transisi, bab/grafis, CTA) yang
diberi waktu agar tidak bertumpuk dan lolos guard tingkat Seimbang (diuji untuk keenam kategori).
Angka grafis **tidak pernah dikarang**: bar progres hanya ditambahkan bila transkrip menyebut persentase
(nilainya diambil dari ucapan); grafik batang/garis/donat/perbandingan tidak pernah ditambahkan otomatis.

## 4. Safe-zone

Grafis ASS Motion Library memakai safe-zone M2: posisi pilihan pengguna (kontrol "Posisi") dicoba lebih dulu,
lalu dipindah/diperkecil bila menutup wajah, area materi, subtitle, atau area tombol aplikasi media sosial;
grafis dekoratif disembunyikan bila tidak ada ruang; grafis informasi ditempatkan di posisi yang paling sedikit
menutupi. Transisi layar penuh (cahaya, sapuan, iris, tirai) sengaja tidak memakai safe-zone (< 1 dtk).

## 5. Daftar 60 preset

### Animated Captions (15)

| ID | Nama | Teknik | Bobot | Deskripsi |
|---|---|---|---|---|
| `cap.karaoke_pop` | Karaoke Pop | subtitle libass per kata (`pop`) | 1 | Kata yang diucapkan berganti warna dan membesar, 3 kata per baris. |
| `cap.highlight_box` | Kotak Sorot | subtitle libass per kata (`highlight`) | 1 | Kata aktif diberi kotak warna di belakangnya, gaya kreator modern. |
| `cap.word_bounce` | Kata Memantul | subtitle libass per kata (`wordbounce`) | 2 | Satu kata besar meluncur dari bawah, memantul, dan sedikit miring bergantian. |
| `cap.karaoke_classic` | Karaoke Klasik | subtitle libass per kata (`karaoke`) | 1 | Baris tetap, kata yang diucapkan berganti warna seperti karaoke. |
| `cap.word_punch` | Satu Kata Hentak | subtitle libass per kata (`word`) | 1 | Satu kata besar di tengah, muncul dengan hentakan skala. |
| `cap.line_bounce` | Baris Memantul | subtitle libass per kata (`bounce`) | 1 | Seluruh baris memantul masuk (overshoot), kata aktif berwarna. |
| `cap.slide_up` | Meluncur Naik | subtitle libass per kata (`slide`) | 1 | Baris meluncur naik sambil memudar masuk. |
| `cap.zoom_blur` | Zoom Fokus | subtitle libass per kata (`zoom`) | 1 | Baris membesar dari kabur menjadi tajam. |
| `cap.soft_fade` | Pudar Lembut | subtitle libass per kata (`fade`) | 1 | Kata muncul perlahan satu per satu, tenang untuk kajian. |
| `cap.cinema_spaced` | Sinema Renggang | subtitle libass per kata (`cinematic`) | 1 | Huruf renggang yang menyempit dan fade lembut, tanpa sorotan. |
| `cap.news_box` | Kotak Liputan | subtitle libass per kata (`documentary`) | 1 | Kalimat utuh di kotak gelap seperti liputan berita. |
| `cap.story_dim` | Redup Bercerita | subtitle libass per kata (`storytelling`) | 1 | Kalimat tampil redup, kata yang diucapkan menyala. |
| `cap.neon_glow` | Neon Berpendar | subtitle libass per kata (`neon`) | 1 | Huruf berpendar (glow blur), kata aktif paling terang. |
| `cap.fill_sweep` | Isi Warna | subtitle libass per kata (`typewriter`) | 1 | Warna mengisi kata dari kiri ke kanan mengikuti ucapan (\kf). |
| `cap.letter_pop` | Huruf Meletup | subtitle libass per kata (`letterpop`) | 1 | Huruf muncul satu per satu mengikuti ucapan, tiap huruf meletup. |

### Text & Titles (10)

| ID | Nama | Teknik | Bobot | Deskripsi |
|---|---|---|---|---|
| `title.cinematic` | Judul Sinematik | ASS `title_cinematic` | 3 | Bilah letterbox masuk, judul dengan huruf renggang yang menyempit, subjudul memudar. |
| `title.slam_stack` | Tumpuk Hentak | ASS `title_slam` | 3 | Kata demi kata menghentak masuk bertumpuk ke bawah dengan getaran singkat. |
| `title.mask_wipe` | Sapuan Reveal | ASS `title_wipe` | 2 | Bilah warna menyapu dari kiri dan membuka teks di belakangnya. |
| `title.typewriter` | Mesin Ketik | ASS `title_typewriter` | 2 | Huruf muncul satu per satu dengan kursor berkedip. |
| `title.split` | Belah Bertemu | ASS `title_split` | 3 | Dua baris meluncur dari sisi berlawanan dan bertemu di tengah, garis aksen tumbuh. |
| `title.boxed` | Kotak Melebar | ASS `title_boxed` | 2 | Kotak warna melebar dari tengah lalu teks gelap muncul di dalamnya. |
| `title.glitch` | Glitch RGB | ASS `title_glitch` | 3 | Salinan merah & biru bergeser acak lalu menyatu menjadi teks utuh. |
| `title.outline_fill` | Garis Lalu Isi | ASS `title_outline_fill` | 2 | Teks bergaris luar transparan, warna mengisi dari bawah ke atas. |
| `title.marker` | Stabilo | ASS `title_marker` | 2 | Teks muncul lalu stabilo miring tergores di belakang kata terakhir. |
| `title.flip` | Putar 3D | ASS `title_flip` | 3 | Teks berputar masuk pada sumbu horizontal (flip 3D) dan sedikit memantul. |

### Camera & Scene Motion (10)

| ID | Nama | Teknik | Bobot | Deskripsi |
|---|---|---|---|---|
| `cam.ken_burns` | Ken Burns Halus | ekspresi FFmpeg (zoom/geser/rotasi/blur) (`kenburns`) | 1 | Zoom masuk perlahan sambil bergeser sedikit, memberi kesan hidup pada bagian yang diam. |
| `cam.punch_in` | Punch-in Tegas | ekspresi FFmpeg (zoom/geser/rotasi/blur) (`punch`) | 2 | Zoom cepat ke dalam pada kalimat penting, tahan, lalu kembali. |
| `cam.handheld` | Kamera Genggam | ekspresi FFmpeg (zoom/geser/rotasi/blur) (`shake`) | 2 | Goyangan halus seperti kamera dipegang tangan untuk suasana liputan. |
| `cam.zoom_out` | Mundur Membuka | ekspresi FFmpeg (zoom/geser/rotasi/blur) (`zoomout`) | 1 | Mulai dekat lalu mundur perlahan memperlihatkan seluruh adegan. |
| `cam.pan` | Geser Horizontal | ekspresi FFmpeg (zoom/geser/rotasi/blur) (`pan`) | 1 | Kamera bergeser pelan dari kiri ke kanan (atau sebaliknya). |
| `cam.tilt` | Geser Vertikal | ekspresi FFmpeg (zoom/geser/rotasi/blur) (`tilt`) | 1 | Kamera bergerak pelan naik atau turun (tilt). |
| `cam.breathe` | Napas Pelan | ekspresi FFmpeg (zoom/geser/rotasi/blur) (`breathe`) | 1 | Zoom masuk-keluar sangat pelan seperti bernapas, untuk bagian yang statis. |
| `cam.rotate` | Miring Sinematik | ekspresi FFmpeg (zoom/geser/rotasi/blur) (`rotate`) | 2 | Bingkai miring pelan beberapa derajat dengan zoom kompensasi (tanpa sudut hitam). |
| `cam.focus` | Tarik Fokus | ekspresi FFmpeg (zoom/geser/rotasi/blur) (`focus`) | 1 | Gambar mulai kabur lalu menajam bertahap, cocok untuk pembuka. |
| `cam.steps` | Zoom Bertingkat | ekspresi FFmpeg (zoom/geser/rotasi/blur) (`steps`) | 2 | Tiga hentakan zoom kecil berurutan untuk membangun penekanan. |

### Transitions (10)

| ID | Nama | Teknik | Bobot | Deskripsi |
|---|---|---|---|---|
| `tr.flash` | Kilat Putih | filter FFmpeg berjendela waktu (`flash`) | 2 | Kilatan putih singkat untuk menandai pergantian bagian. |
| `tr.whip` | Whip Pan | filter FFmpeg berjendela waktu (`whip`) | 3 | Gambar disentak ke samping dengan motion blur, lalu kembali. |
| `tr.zoom_blur` | Zoom Blur | filter FFmpeg berjendela waktu (`zoomblur`) | 3 | Gambar melesat masuk dengan blur dua arah lalu kembali. |
| `tr.spin` | Putar Cepat | filter FFmpeg berjendela waktu (`spin`) | 3 | Bingkai berputar singkat dengan zoom, lalu kembali tegak. |
| `tr.glitch` | Glitch | filter FFmpeg berjendela waktu (`glitch`) | 2 | Pergeseran kanal warna acak singkat seperti sinyal terganggu. |
| `tr.pixelate` | Piksel | filter FFmpeg berjendela waktu (`pixelate`) | 2 | Gambar memecah menjadi piksel besar lalu tajam kembali. |
| `tr.light_leak` | Kebocoran Cahaya | ASS `tr_light_leak` | 2 | Cahaya hangat kabur menyapu diagonal di atas gambar. |
| `tr.swipe` | Sapuan Warna | ASS `tr_swipe` | 2 | Bidang warna menyapu menutup lalu membuka layar. |
| `tr.iris` | Iris | ASS `tr_iris` | 2 | Lingkaran gelap menutup ke tengah lalu membuka kembali. |
| `tr.blinds` | Tirai | ASS `tr_blinds` | 2 | Enam bilah horizontal menutup bergantian lalu membuka. |

### Graphics & Infographics (10)

| ID | Nama | Teknik | Bobot | Deskripsi |
|---|---|---|---|---|
| `gfx.progress` | Bar Progres | ASS `progress` | 2 | Bar terisi beranimasi dengan persentase yang berhitung naik. |
| `gfx.bar_chart` | Grafik Batang | ASS `bars` | 2 | Tiga batang tumbuh bergantian dengan label dan nilai. |
| `gfx.ring` | Cincin Progres | ASS `ring` | 2 | Cincin menyala segmen demi segmen sampai nilai, angka di tengah ikut naik. |
| `gfx.donut` | Diagram Donat | ASS `donut` | 2 | Bagian donat tumbuh bergantian dengan legenda persentase. Isi data Anda sendiri. |
| `gfx.location` | Pin Lokasi | ASS `location` | 2 | Pin jatuh & memantul dengan gelombang, nama tempat meluncur. |
| `gfx.timeline` | Garis Waktu | ASS `timeline` | 2 | Garis tumbuh dan titik tahap menyala satu per satu dengan label. |
| `gfx.compare` | Sebelum / Sesudah | ASS `compare` | 2 | Dua panel meluncur dari kiri & kanan dengan nilai dan panah. Isi data Anda sendiri. |
| `gfx.numlist` | Daftar Bernomor | ASS `numlist` | 2 | Lingkaran nomor memantul masuk lalu teks poin meluncur berurutan. |
| `gfx.line_chart` | Grafik Garis | ASS `linechart` | 2 | Garis tergambar kiri ke kanan, titik data dan nilainya muncul saat dilewati. Isi data Anda sendiri. |
| `gfx.stat_card` | Kartu Statistik | ASS `statcard` | 2 | Kartu terbuka dari bawah, angka besar berhitung naik, label dan garis aksen. |

### Storytelling & CTA (5)

| ID | Nama | Teknik | Bobot | Deskripsi |
|---|---|---|---|---|
| `story.chapter` | Kartu Bab | ASS `chapter` | 2 | Nomor bab besar bergaris, judul meluncur dari kanan, garis tumbuh di bawahnya. |
| `cta.follow_bar` | Bilah Ikuti | ASS `follow_bar` | 2 | Bilah ajakan naik dari bawah dengan ikon lingkaran yang berdenyut. |
| `story.quote` | Kutipan | ASS `quote` | 2 | Tanda kutip besar, kalimat muncul kata demi kata, nama sumber meluncur. |
| `cta.button` | Tombol Ajakan | ASS `cta_button` | 2 | Tombol membesar, kursor bergerak dan mengklik, ikon lonceng bergoyang. |
| `story.end_card` | Kartu Penutup | ASS `end_card` | 2 | Bingkai garis tergambar mengelilingi layar, ucapan terima kasih dan ajakan. |

## 6. Uji

* `tests/test_m25_motionlib.py`: data & target 15/10/10/10/10/5, **tiap preset memakai teknik yang berbeda**
  (tanda tangan teknik libass/FFmpeg unik untuk ke-60 preset), semua renderer ASS menghasilkan event valid,
  filter FFmpeg baru dijalankan sungguhan (rotasi/piksel/glitch/fokus), injeksi tag ASS ditolak, guard &
  intensitas, kamera dilewati di tata letak papan, caption per rentang, dedup judul vs subtitle, Auto Style 6
  kategori tanpa angka karangan, resep (simpan/buka ulang/resep lama), MP4 nyata lewat API dengan pratinjau dan
  render final memakai elemen yang sama, galeri dari engine render, posisi pilihan + safe-zone, regresi rak buku.
* `tests/test_m25_browser_motionlib.py` (Chromium): galeri bergerak, sekali klik di posisi video / beberapa
  rentang / seluruh klip, kontrol item tersimpan di resep, intensitas, hapus + urungkan, buka ulang proyek.
* Selftest exe Windows (koneksi keluar diblokir): galeri satu preset per kategori + item fx di render final.
* CI Windows: `bench/m25/library_demo.py --strict` -> MP4 contoh per kategori + 3 tingkat intensitas, gagal bila
  ada preset yang tidak tampil; artefak `m25-motion-library`.

Hasil uji video nyata dan ukuran CPU/RAM: lihat `docs/M25_LAPORAN_UJI.md`.
