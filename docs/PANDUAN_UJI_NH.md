# Panduan uji akurasi transkripsi dengan video NH (NH AI Clip Studio M1.1)

Tujuan: mengetahui seberapa banyak subtitle yang masih perlu dikoreksi untuk video Nurul Hayat yang SEBENARNYA,
di komputer tim sendiri, sebelum M1.1 dinyatakan siap dipakai tim. Angka uji sebelumnya berasal dari rekaman
bahasa Indonesia umum (FLEURS), bukan ceramah NH, jadi belum boleh dianggap berlaku untuk video NH.

Semua proses berjalan di komputer ini tanpa internet. Video tidak dikirim ke mana pun. Yang dikirim untuk ditinjau
hanya berkas laporan `laporan-uji.md`.

## 1. Pilih 3–5 video

Pilih video yang mewakili pekerjaan sehari-hari, misalnya:

| No | Jenis | Contoh |
|---|---|---|
| 1 | Ceramah/kajian di ruangan, suara jernih | kajian rutin |
| 2 | Banyak istilah Arab, ayat, atau doa | tausiyah dengan kutipan ayat |
| 3 | Suara bising/luar ruangan | liputan kegiatan, penyaluran bantuan |
| 4 | Dialog/wawancara (lebih dari satu pembicara) | testimoni penerima manfaat |
| 5 | Video panjang (lebih dari 10 menit) | rekaman pengajian utuh |

Saran: potong video menjadi 2–10 menit agar transkrip rujukan tidak terlalu berat diketik. Untuk jenis no. 5,
sertakan satu video panjang, karena mode Kualitas Tinggi dapat menghilangkan sebagian ucapan pada rekaman panjang.

## 2. Buat transkrip rujukan (oleh manusia)

Untuk setiap video, buat berkas teks dengan **nama yang sama**:

```
D:\Uji NH\
    kajian-rutin.mp4
    kajian-rutin.txt
    liputan-bantuan.mp4
    liputan-bantuan.txt
```

Aturan menulis transkrip rujukan:

1. Ketik **semua ucapan apa adanya**, termasuk kata ulang dan "eh" yang jelas terdengar. Jangan diringkas.
2. Tulis istilah Arab/dakwah dengan **ejaan pedoman NH** yang ingin dipakai di subtitle
   (misalnya "insyaallah", "zakat", "sedekah", "Nurul Hayat"). Ejaan inilah yang dinilai.
3. Ayat atau doa berbahasa Arab: tulis dengan cara yang sama seperti di subtitle NH (latin atau terjemahan),
   konsisten di semua berkas.
4. Tanda baca dan huruf besar/kecil boleh bebas: penilaian mengabaikannya.
5. Bagian yang benar-benar tidak terdengar: lewati, jangan ditebak.
6. **Periksa ulang oleh orang kedua** sambil mendengarkan videonya. Transkrip rujukan yang salah membuat angka uji salah.
7. Simpan sebagai teks biasa UTF-8 (Notepad: Simpan sebagai → Pengodean UTF-8).

Cara cepat: jalankan video di NH AI Clip Studio, koreksi semua subtitle di editor sampai benar, lalu salin teksnya
ke berkas .txt. Pastikan tetap didengar ulang secara utuh, jangan hanya menerima hasil mesin.

## 3. Jalankan uji di aplikasi

1. Buka NH AI Clip Studio, klik **🧪 Uji akurasi** (kanan atas).
2. Klik **📂 Pilih folder…** (atau ketik alamat foldernya), lalu **Periksa**. Aplikasi menampilkan video yang siap diuji
   dan video yang dilewati karena belum punya .txt.
3. Pilih mode: **Standar** (model bawaan) dan, bila paket tambahan terpasang, **Kualitas Tinggi**.
4. Run per mode: biarkan **2** (untuk melihat apakah hasil dua kali uji sama).
5. Klik **Mulai uji**. Lama uji kira-kira 2 menit (Standar) dan 4–5 menit (Kualitas Tinggi) per 10 menit video
   untuk setiap run, pada CPU 4 inti. Komputer yang lebih lambat butuh lebih lama. Selama uji, sebaiknya jangan
   memproses video lain. Uji bisa dibatalkan kapan saja.
6. Setelah selesai, tabel hasil muncul. Klik **📂 Buka folder laporan**.

Pilihan lain (tanpa antarmuka), misalnya untuk admin IT:

```
"%LOCALAPPDATA%\Programs\NH AI Clip Studio\NH-AI-Clip-Studio.exe" --eval "D:\Uji NH" --runs 2
```

## 4. Uji di PC 8 GB dan 16 GB

Bila tersedia, jalankan uji yang sama (folder yang sama) di:

- satu PC dengan RAM **8 GB**, dan
- satu PC dengan RAM **16 GB**.

Tutup aplikasi lain yang berat (browser dengan banyak tab, Premiere, dll.) sebelum uji. Laporan otomatis mencatat RAM,
jumlah thread CPU, dan puncak RAM yang dipakai. Pada CI (16 GB, 4 thread) puncak RAM terukur ±0,7 GB untuk Standar
dan ±1,9 GB untuk Kualitas Tinggi. PC 8 GB belum pernah diuji, jadi hasil dari PC 8 GB sangat dibutuhkan.

## 5. Apa yang dikirim untuk ditinjau

Kirim dari setiap PC:

- `laporan-uji.md` (dan bila diminta `laporan-uji.json`), dari folder uji;
- keterangan singkat: jenis tiap video (lihat tabel bagian 1) dan apakah transkrip rujukannya sudah diperiksa orang kedua.

**Jangan kirim video atau transkrip lengkap** bila berisi data pribadi penerima manfaat. Folder `hasil\` berisi
transkrip mesin per video untuk diperiksa sendiri di komputer ini.

## 6. Cara membaca hasil

| Kolom | Arti |
|---|---|
| Salah kata (WER) | Bagian kata yang salah, hilang, atau berlebih. 20% ≈ 1 dari 5 kata perlu dikoreksi. |
| Salah huruf (CER) | Seperti WER tetapi per huruf. CER kecil dengan WER besar = kebanyakan salah ejaan kecil. |
| Waktu | Lama transkripsi per 10 menit video di komputer ini. |
| Puncak RAM | Memori terbesar yang dipakai satu transkripsi. |
| 2 run sama | "ya" = dua kali uji menghasilkan teks yang persis sama di komputer ini. |
| Istilah tepat | Dari istilah kosakata aplikasi yang muncul di rujukan, berapa yang tertulis persis sama. |

Penting:

- Angka hanya berlaku untuk video dan komputer yang diuji, bukan jaminan untuk video lain.
- Aplikasi **tidak** dinyatakan akurat untuk bahasa Arab, ayat, doa, atau istilah dakwah. Bagian itu tetap wajib
  diperiksa manusia, apa pun hasil ujinya.
- Penanda "kurang yakin" di editor membantu menemukan kesalahan, tetapi kata yang tidak ditandai belum tentu benar.
- Pemeriksaan subtitle sebelum ekspor tetap wajib.

Keputusan berikutnya (konfigurasi final dan apakah M2 dimulai) diambil setelah laporan uji NH ini ditinjau.
