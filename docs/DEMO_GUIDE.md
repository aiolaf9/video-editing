# Panduan Demo Prototipe (uji pengguna terbatas)

Demo ini berjalan di **satu komputer** (lokal, terisolasi). Tidak dibuka ke internet.

## 1. Menjalankan demo

Syarat: Linux/Mac, Python 3.12, FFmpeg, koneksi internet untuk mengunduh model Whisper "small" sekali (±500 MB).

```bash
read -r -s -p "Password akun uji (min. 12 karakter): " ADMIN_PASSWORD; echo; export ADMIN_PASSWORD
ADMIN_EMAIL=uji@contoh.local ./scripts/demo_local.sh
```
(Password diketik tanpa tampil di layar dan tidak tersimpan di riwayat shell.)

Buka **http://127.0.0.1:8000** di browser komputer yang sama. Data demo tersimpan di `./data-demo` (hapus folder ini
untuk mulai bersih). Hentikan dengan `Ctrl+C`.

Pengaturan demo (hanya di skrip ini, bukan bawaan aplikasi): transkripsi per potongan ucapan (`WHISPER_CHUNKED=1`)
dengan saringan halusinasi terbaru, dan batas video **15 menit**.

## 2. Login

Masuk dengan email & password yang dipakai saat menjalankan skrip. Akun ini Admin; untuk penguji lain buat akun
"editor" lewat menu **Admin**.

## 3. Alur pemakaian

1. **Upload**: pilih atau seret satu video (≤ 15 menit). Video lebih panjang ditolak dengan pesan yang jelas, di
   browser maupun di server. Upload bisa dijeda dan dilanjutkan.
2. **Tunggu analisis**: status tampil bertahap (preview → wajah → audio → transkrip). Video 10–15 menit bisa memakan
   beberapa menit.
3. **Pilih klip**: tab *Klip pilihan AI* menampilkan kandidat. Ketuk satu kandidat; atur awal/akhir dengan kolom
   *Awal/Akhir* atau tombol *Set awal / Set akhir* saat video diputar.
4. **Koreksi subtitle**: tab *Transkrip* → tombol **Koreksi** pada segmen → ubah teks → **Simpan**. Teks yang
   dikoreksi dipakai untuk subtitle pada render berikutnya.
5. **Render**: pilih format **9:16**, lalu tombol render. Pantau di tab *Hasil render*.
6. **Download**: tombol **⬇ Download MP4** di tab *Hasil render*.

## 4. Wajib sebelum ekspor

**Periksa transkrip/subtitle pada rentang klip sebelum render.** Transkripsi otomatis dapat salah eja nama, angka,
atau istilah, dan sesekali menambahkan kalimat yang tidak diucapkan (mis. "Terima kasih telah menonton").
Koreksi lewat tombol *Koreksi*.

## 5. Batasan prototipe

- Video maksimal 15 menit; podcast panjang (60 menit) belum didukung.
- Bila video tidak berisi ucapan, tidak ada subtitle; kandidat klip hanya dipilih dari energi audio.
- Mutu transkrip lebih rendah dari transkripsi per kalimat (WER beberapa poin lebih tinggi) — selalu periksa.
- Hanya satu komputer; beberapa pengguna bersamaan dan pemakaian memori untuk banyak job belum divalidasi.
- Editor sudah diuji pada viewport HP (emulasi). Pengujian di HP sungguhan belum dilakukan.
- Media & hasil render dihapus otomatis setelah masa simpan (lihat catatan di halaman utama).

## 6. Mencoba dari HP (perlu persetujuan sebelum dipakai)

Demo hanya mendengar `127.0.0.1`. Cara paling aman tanpa membuka server ke jaringan: **USB port forwarding**
dari HP Android (Chrome DevTools → *Port forwarding* `8000 → localhost:8000`), lalu buka `http://localhost:8000`
di Chrome HP. Alternatif lain (mengikat server ke alamat LAN atau tunnel) membuka akses jaringan dan memerlukan
persetujuan terpisah.
