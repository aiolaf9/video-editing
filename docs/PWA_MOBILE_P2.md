# P2 — PWA dan editor di HP

## Pasang ke layar utama

AI Video Studio adalah Progressive Web App: `web/manifest.webmanifest`, ikon `web/icons/` (dibuat ulang dengan
`python scripts/make_icons.py`), dan service worker `web/sw.js`. Tombol **📲 Pasang aplikasi** di bilah atas tampil
sesuai dukungan browser:

| Platform | Cara memasang | Di aplikasi |
|---|---|---|
| Android (Chrome, Edge, Samsung Internet) | Prompt pemasangan asli | Tombol memunculkan prompt |
| Windows, Mac, ChromeOS (Chrome/Edge) | Prompt asli atau ikon pasang di kolom alamat | Tombol memunculkan prompt |
| iPhone/iPad (Safari; Chrome/Edge di iOS 16.4+) | Bagikan → **Tambahkan ke Layar Utama** | Tombol menampilkan petunjuk |
| Mac Safari (macOS Sonoma ke atas) | File → **Tambahkan ke Dock** | Tombol menampilkan petunjuk |
| Firefox desktop | Tidak mendukung pemasangan PWA | Tombol tidak tampil; aplikasi tetap jalan di browser |

PWA hanya bisa dipasang lewat **HTTPS** (atau `localhost`). Itu sudah terpenuhi oleh Caddy atau web server di depan
aplikasi.

## Apa yang disimpan di perangkat

| Disimpan | Tidak pernah disimpan |
|---|---|
| Cache Storage: hanya berkas statis dari daftar `SHELL` di `sw.js` (halaman HTML tanpa data, CSS, JS, ikon, font) | Video sumber, pratinjau, hasil render (permintaan `Range`), gambar mini |
| `localStorage`: pengaturan editor terakhir (bawaan proyek baru) dan alamat sesi upload yang bisa dilanjutkan | Data akun, daftar proyek, resep, transkrip (semua lewat `/api/*`) |
| Cookie sesi (HttpOnly) | IndexedDB (tidak dipakai) |

- Service worker tidak menangani `/api/*`, permintaan selain GET, permintaan `Range`, maupun asal (origin) lain.
- Server mengirim `Cache-Control: no-store` untuk semua `/api/*`, jadi cache HTTP browser pun tidak menyimpannya.
- HTML/JS/CSS dikirim dengan `no-cache` (selalu divalidasi ulang dengan ETag), sehingga setelah server diperbarui
  browser tidak memakai kode lama.
- Saat **Keluar**, Cache Storage dibersihkan.
- Saat server tidak terjangkau, navigasi menampilkan `offline.html`. Pengaturan editing yang sudah tersimpan tetap
  aman di server. Mengedit secara offline sengaja tidak didukung, karena pratinjau dan render membutuhkan server.

## Editor di HP (lebar layar ≤ 760 px)

- **Daftar proyek:** disembunyikan saat proyek terbuka; **← Proyek** kembali ke daftar.
- **Pratinjau:** selebar layar dan **menempel di atas** saat menggulir ke pengaturan, sehingga perubahan subtitle,
  crop, dan warna langsung terlihat. Tidak ada FFmpeg; aturannya sama dengan P1.
- **Bilah aksi bawah:** ▶ Putar, ⏮ Awal, Akhir ⏭, 🎬 Render. Tombol setinggi 48 px dan memperhitungkan *safe area*
  iPhone.
- **Pintasan bagian:** Klip, Format, Warna, Subtitle, Musik, Render.
- **Timeline:** pegangan awal/akhir bisa diseret dengan jari maupun mouse. Video ikut ke titik yang diseret, dibatasi
  sekitar 8 kali per detik.
- **Crop:** bingkai digeser dengan jari.
- **Ukuran sentuh:** semua tombol minimal 44 px pada layar sentuh. Kolom isian 16 px agar iPhone tidak memperbesar
  halaman.
- **Tanpa gulir samping:** sudah diuji pada lebar 320, 390, dan 412 px.
- **Simpan otomatis seperti P1:** ditambah penyimpanan langsung saat aplikasi berpindah ke latar belakang
  (`visibilitychange`/`pagehide`, `fetch keepalive`), karena `beforeunload` tidak andal di HP.

## Pengujian dan batasnya

- `tests/test_p2_pwa.py`: manifest dan ikon, header, kebijakan cache service worker, `no-store` untuk API (termasuk
  video dan gambar mini), halaman offline, dan tidak ada pendaftaran mandiri (akun tetap dibuat Superadmin).
- `tests/test_p2_mobile_browser.py`:
  - Chromium: installability (`Page.getInstallabilityErrors`), isi Cache Storage setelah dipakai, offline, keluar
    akun, tombol pasang per platform;
  - alur editor dengan sentuhan pada profil Pixel 7, iPhone 14, dan Galaxy S9+;
  - WebKit iPhone 14 di CI.
- **Semua uji HP adalah EMULASI** (profil perangkat Playwright dan sentuhan lewat DevTools Protocol). Uji di HP
  sungguhan belum dilakukan: Safari iOS, Chrome Android, pemasangan ke layar utama, perilaku latar belakang dan
  pemutaran video di perangkat fisik, serta keyboard virtual.
