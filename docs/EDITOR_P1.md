# P1 — Editor hibrida: resep editing, pratinjau langsung, template

Tujuan: pengalaman edit yang responsif tanpa beban CPU tinggi, baik di perangkat pengguna maupun di server. Edit kecil
hanya mengubah sebuah dokumen JSON. Pratinjau digambar browser di atas proxy video berkualitas rendah. FFmpeg hanya
berjalan saat pengguna menekan **Render**.

## Resep editing per proyek

- Disimpan di tabel `project_recipes` (migrasi `0007_recipes`), satu baris per proyek, divalidasi ketat oleh
  `app/recipe.py`: field tak dikenal ditolak, nilai berbatas, dan nama font tanpa karakter yang bisa merusak berkas
  ASS. Ukuran maksimal 256 KB.
- Isi resep:
  - rentang klip dan klip yang dicentang (untuk render massal), durasi target;
  - rasio, crop (posisi, bingkai otomatis);
  - filter dan penyesuaian warna;
  - kecepatan, zoom, fade;
  - gaya subtitle, **koreksi teks** per segmen, **overlay animasi**;
  - audio, template yang dipakai, encoder, bahasa.
- Resep tetap ada setelah media sementara dihapus (2 jam setelah aktivitas, maksimal 5 jam). Isinya hanya
  metadata; video dan hasil render tidak pernah disimpan permanen. Menyimpan resep dihitung sebagai aktivitas
  editing, sehingga masa simpan media diperpanjang (maksimal sekali per menit).
- **API**
  - `GET /api/projects/{id}/recipe` mengembalikan `{version, recipe, updated, updated_by_name}`;
    `version` 0 berarti belum pernah disimpan.
  - `PUT /api/projects/{id}/recipe` menerima `{version, recipe}`. Bila versi di server sudah berubah, server
    menjawab **409** beserta isi terbaru. Endpoint ini hanya menulis ke database dan tidak pernah membuat job.
  - `POST /api/projects/{id}/render-recipe` menerima `{version, start, end, title}` dan merender dari resep
    **tersimpan**. Bila versi berbeda dari yang dilihat pengguna, render ditolak 409 agar hasilnya tidak berbeda dari
    pratinjau. Job menyimpan snapshot resep (termasuk overlay dan koreksi teks), jadi edit setelah menekan Render
    tidak mengubah render yang sedang antre.
  - `POST /api/projects/{id}/render` (pengaturan lengkap dari klien) tetap ada untuk kompatibilitas.
- **Perubahan dari sisi server**
  - Yang termasuk: koreksi teks (`PUT /transcript`) dan rencana animasi (`/plan`, `/plan/auto`, `/plan/ai`,
    `/plan/import`).
  - Perubahan ini memakai baca-ubah-tulis bersyarat versi yang diulang bila bertabrakan, sehingga tidak menimpa
    perubahan lain. Versi baru dikirim di header `X-Recipe-Version`.
  - Transkrip mentah di folder media tidak lagi diubah; koreksi diterapkan saat dibaca.
  - Analisis ulang menghapus koreksi teks, karena nomor segmennya berubah.

### Kompatibilitas

- **Proyek lama tanpa resep** memakai nilai bawaan. Di browser, kontrol memakai pengaturan terakhir di perangkat itu
  (format `localStorage` lama tetap dibaca), lalu tersimpan sebagai resep proyek pada perubahan pertama atau sebelum
  render.
- **Overlay `null`** berarti rencana animasi masih dibaca dari `plan.json` proyek lama. Berkas itu tidak diubah;
  perubahan pertama memindahkannya ke resep.
- Transkrip yang dikoreksi sebelum P1 (berkas sudah berisi teks koreksi) tetap terpakai apa adanya.

## Autosave dan dua perangkat

- Setiap perubahan kontrol, rentang, centang klip, crop, rasio, dan template disimpan otomatis setelah jeda 0,8 detik.
- Indikator di samping judul: **● Belum tersimpan**, **⏳ Menyimpan…**, **✓ Tersimpan hh:mm**, atau **⚠ Gagal
  menyimpan**. Saat gagal, penyimpanan dicoba ulang otomatis dengan jeda 3, 5, 10, 20, lalu 30 detik, atau saat
  indikator diklik.
- Saat halaman ditutup, simpanan terakhir dikirim dengan `keepalive` dan browser meminta konfirmasi.
- Bila perangkat atau tab lain menyimpan lebih dulu, muncul banner **"Proyek ini baru saja diubah di perangkat atau
  tab lain"** dengan dua pilihan:
  - **Muat versi terbaru**: memakai versi server;
  - **Simpan perubahan saya**: menimpa secara sadar; koreksi teks dan animasi dari server tetap dipakai.
- Tabrakan yang hanya menyangkut koreksi teks atau animasi milik tab yang sama digabung otomatis tanpa banner.
- Perangkat yang sedang diam memeriksa versi terbaru setiap 15 detik (hanya saat tab terlihat) dan ikut diperbarui.
- Sebelum render, simpanan yang tertunda dikirim lebih dulu.

## Pratinjau langsung (tanpa membuat ulang MP4)

| Bagian | Pratinjau di browser | Render FFmpeg |
|---|---|---|
| Video | Proxy 480p (atau salinan langsung bila sumber ≤ 720p dan ringan) | Video sumber resolusi penuh |
| Crop / rasio | Bingkai dengan rumus `crop_box` yang sama | `crop` + `scale` |
| Filter warna | CSS `filter` (GPU perangkat). Preset dari tabel bersama `app/looks.py` (`/api/editor/config`) | `COLOR_PRESETS` dari tabel yang sama |
| Kecerahan / kontras / saturasi | Padanan eq: `contrast(c/k) brightness(k)` (k = 1 + 2b′; urutan dibalik untuk b′ < 0), `saturate(s/c)`, b′ = b·255/219 (limited range) | `eq=brightness:contrast:saturation` |
| Kecepatan | `playbackRate` | `setpts` / `atempo` |
| Subtitle | Aturan kata (urut, geser tumpang tindih, minimal 0,12 detik), pemenggalan baris, durasi baris, kata aktif, font berkas yang sama (`/fonts`), tebal, outline 8% | Berkas ASS (`app/subtitles.py`) |
| Animasi | Lapisan HTML | Lapisan PNG (`app/overlays.py`) |

**Selisih yang terukur** (`tests/test_p1_browser_editor.py`, Chromium, galat rata-rata per kanal 0–255):

| Kasus | Galat |
|---|---|
| Frame render vs pratinjau pada detik yang sama, di luar pita subtitle | ±5 (crop yang salah: ±100) |
| Pita subtitle | posisinya sama (dibandingkan lewat citra selisih) |
| Kecerahan / kontras / saturasi | 1,2–6,8 |
| Vivid | 5,3 |
| Hangat | 3,9 |
| Sejuk | 2,5 |
| Hitam putih | 7,4 (bobot luma CSS Rec.709 vs FFmpeg BT.601) |
| Sinematik | 8,5 |
| Vintage | 9,3 |

Preset berbasis curves/colorbalance adalah perkiraan yang disetel dengan pengukuran. Zoom perlahan, punch-in, dan
fade tidak dipratinjau.

## Template

- **Bawaan** (hanya-baca): Karaoke kuning, Pop hijau, Satu kata besar, Minimal putih, Berita (atas). Semua gaya
  subtitle yang sudah ada tetap tersedia.
- **Pribadi**: setiap pengguna, maksimal 50.
- **Unit**: dibuat dan dikelola Admin Unit untuk unitnya atau oleh Admin, terlihat oleh anggota unit, maksimal 100 per
  unit.
- **Global**: dikelola Admin, terlihat semua pengguna, maksimal 100.
- Isi template: gaya subtitle, ditambah warna (dan rasio, opsional). Memakai template mengisi kontrol, mencatat
  `template_id` di resep, dan langsung terlihat di pratinjau.
- **API**: `GET/POST /api/templates`, `PUT/DELETE /api/templates/{id}`. Template milik unit lain dan template pribadi
  orang lain tidak terlihat (404).

## Beban server

Satu edit berarti satu `UPDATE` JSON kecil, tanpa FFmpeg dan tanpa membaca media. Pemeriksaan versi dari perangkat
lain hanya `SELECT` satu baris setiap 15 detik per tab yang terlihat. Fitur inti tidak memakai API AI berbayar.
