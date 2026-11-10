# M1.1 — Audit & perbaikan transkrip/subtitle (NH AI Clip Studio)

Latar belakang: uji M1 oleh pengguna dengan video nyata menemukan banyak salah transkripsi (bahasa Indonesia
bercampur istilah Arab), subtitle salah langsung masuk MP4, dan editor subtitle berupa daftar koreksi teks.
Dokumen ini mencatat audit kode, temuan, rancangan, dan **hasil evaluasi terukur**. Klaim akurasi hanya dari angka di
bagian "Hasil evaluasi".

## 1. Audit pipeline M1

| Bagian | Perilaku M1 | Temuan |
|---|---|---|
| Model | desktop membundel **base** | Data P5.5.2 (FLEURS id, CI): WER base **28,7 %** (ucapan bersih) / **38,0 %** (±10 mnt) vs small **14,1 % / 30,0 %**. Base kira-kira 2x lebih banyak salah. |
| Bahasa | bawaan UI "Deteksi otomatis" → `detect_language` pada **30 dtk pertama** saja | Ceramah dibuka salam/doa berbahasa Arab: risiko seluruh video ditranskripsi sebagai bahasa lain. |
| Prompt | hanya kalimat contoh bertanda baca | Tidak ada kosakata (istilah dakwah, Arab, nama lembaga/program). |
| Decoding | beam 5, `condition_on_previous_text=True`, fallback temperature bawaan | P5.5.2: hasil **tidak deterministik** pada audio panjang (WER small id panjang 21–34 % antar-run); conditioning teks sebelumnya dikenal memicu pengulangan/halusinasi berantai pada audio panjang. Perlu diukur, bukan diasumsikan. |
| VAD | Silero, parameter bawaan (jeda min 2 dtk, pad 400 ms) | Diukur sebagai varian. |
| Timestamp kata | `word_timestamps=True` | Probabilitas kata **dibuang** → editor tidak bisa menandai bagian yang tidak yakin. |
| Audio | FFmpeg → 16 kHz mono float32, tanpa normalisasi | Tidak diubah di M1.1 tanpa bukti. |
| Koreksi teks | `text_fixes` per segmen; bila jumlah kata berubah, **semua kata segmen dibagi rata** | Sorotan karaoke bergeser dari ucapan setelah koreksi sekecil apa pun (menambah/menghapus satu kata). |
| Editor | daftar segmen + tombol "Koreksi" (textarea) | Tidak ada ubah waktu, cari & ganti, penanda kata tidak yakin, atau koreksi massal. |
| Render | memakai koreksi tersimpan, tanpa peringatan | Subtitle yang belum ditinjau langsung dibakar ke MP4. |

## 2. Perbaikan M1.1 (sudah diimplementasikan)

1. **Mode kualitas** "Cepat" / "Akurat" per video (upload, impor link, analisis ulang). Model per mode diatur
   `WHISPER_MODEL_FAST` / `WHISPER_MODEL_ACCURATE` (desktop: keduanya dibundel). Satu model saja di RAM; estimasi RAM
   admission mengikuti model mode. Versi web tidak berubah (tanpa variabel itu, `WHISPER_MODEL` seperti sebelumnya).
2. **Bahasa**: desktop memakai bahasa Indonesia sebagai bawaan. Bila "Deteksi otomatis" dipilih, bahasa dideteksi dari
   3 jendela (awal, tengah, akhir), bukan hanya pembukaan.
3. **Kosakata** (`app/vocab.py`, menu Kosakata): bawaan (istilah dakwah/Arab umum, ejaan KBBI, + "Nurul Hayat"; nama
   program NH **tidak** diisi otomatis), global/desktop, dan per unit. Dipakai sebagai `hotwords` Whisper: hanya
   membiaskan decoder bila audionya mendukung.
4. **Tidak ada koreksi otomatis istilah Arab.** Saran istilah ("ejaan" = varian yang didaftarkan pengguna; "mirip" =
   ejaan mirip *dan* keyakinan Whisper rendah) tampil di editor dan hanya berlaku bila diterima. Penolakan diingat per
   proyek. Istilah < 5 huruf tidak ditebak dari kemiripan (mis. "dua" ≠ "duha").
5. **Probabilitas kata & metrik segmen** disimpan (`p`, `avg_logprob`, `no_speech_prob`, `compression_ratio`). Kata
   p < 0,5 ditandai "perlu ditinjau" (ambang dievaluasi: presisi/recall penanda di tabel hasil).
6. **Model edit baru** (`app/transcript_edit.py`, resep `seg_edits`): koreksi teks diselaraskan dengan kata lama
   (difflib) — kata yang sama tetap memakai waktu Whisper, kata pengganti mengisi rentang kata yang diganti, sisipan
   mengisi celah. Ubah rentang segmen (geser/regang) dan waktu per kata. Cari & ganti seluruh transkrip dengan
   pratinjau (utuh kata / huruf besar-kecil). Tanda "sudah ditinjau" per segmen. Transkrip mentah tidak diubah.
7. **Subtitle akhir = koreksi pengguna**: render memakai snapshot `seg_edits` dari resep yang sama dengan pratinjau.
   Diuji: kata di berkas ASS yang dibakar = kata transkrip efektif untuk semua gaya (karaoke, pop, word, simple), dan
   render nyata memakai teks koreksi.
8. **Gerbang tinjau sebelum render**: render menampilkan jumlah & daftar kata yang belum ditinjau di rentang klip;
   pengguna memilih meninjau dulu atau tetap render.

### Panduan singkat editor subtitle (tab 📝 Transkrip)

| Tugas | Cara |
|---|---|
| Koreksi kata | Klik teks baris → ketik → **Enter** (simpan) / **Esc** (batal) / **Tab** (simpan & baris berikutnya). Video melompat ke kata yang diklik. |
| Kata yang perlu didengar ulang | Garis bergelombang jingga = keyakinan Whisper < 50 %. Filter **⚠ Perlu ditinjau** menampilkan baris itu saja; "Tandai semua yang tampil sudah ditinjau" setelah diperiksa. |
| Koreksi massal | **Cari** (pratinjau jumlah baris) → isi "Ganti dengan" → **Ganti semua** (konfirmasi). Kata utuh & huruf besar/kecil dapat diatur. **Ctrl+F** membuka pencarian. |
| Istilah Arab / lembaga / program | **📚 Kosakata**: daftarkan ejaan benar + varian yang sering salah. Filter **💡 Saran istilah**: ✓ terima / ✕ tolak per saran, atau "Terima semua" per istilah. |
| Waktu subtitle | − / + menggeser awal/akhir 0,1 dtk; ⇤ / ⇥ memakai posisi video; ⏱ mengatur waktu per kata. |
| Sebelum render | Bila klip masih memuat kata belum ditinjau, muncul dialog: **Tinjau dulu** atau **Tetap render**. |

## 3. Rencana evaluasi

`bench/m11/asr_eval.py` (lewat `app.transcribe.transcribe`, subprocess per kombinasi) di workflow
`.github/workflows/asr-eval.yml`:

- Korpus FLEURS id_id (CC-BY 4.0): `id_nodup` (20 kalimat unik, ±3,5 mnt) dan `id_long` (±10 mnt berurutan).
  **Keterbatasan:** ucapan baca berita, bukan ceramah, tanpa istilah Arab. Tidak membuktikan perbaikan untuk istilah
  dakwah.
- Model: base, small, large-v3-turbo. Varian: `m1` (perilaku M1), `auto3` (deteksi 3 jendela), `lang_id`,
  `noprev` (tanpa conditioning), `noprev_t0` (temperature 0), `noprev_hst` (hallucination_silence_threshold 2 dtk),
  `noprev_vad` (VAD jeda 0,5 dtk), `*_vocab` (kosakata bawaan sebagai hotwords).
- Metrik: WER, CER (NFKC, huruf kecil, tanpa tanda baca), RTF, puncak RAM, bahasa, presisi & recall penanda p < 0,5.
- **Sampel NH (wajib sebelum distribusi):** 3–5 potongan video asli (1–3 menit, berisi istilah Arab) + transkrip
  rujukan manual → `asr_eval.py - out.jsonl --samples <folder>`. Tanpa ini tidak ada klaim perbaikan untuk ceramah.

Pengaturan bawaan (`DEFAULT_DECODE`) dan pilihan model per mode diubah **hanya** sesuai hasil di bawah.

## 4. Hasil evaluasi

### Putaran 1 — varian pengaturan (run [42778046306](https://github.com/aiolaf9/video-editing/actions/runs/42778046306), commit `f05452c`)

FLEURS id_id; `id_long` = ±10 mnt ucapan berurutan (597 dtk), `id_nodup` = 20 kalimat unik (243 dtk). Runner
ubuntu-24.04, 4 vCPU, CPU int8. **Satu run per varian**: selisih kecil bisa noise; varian dengan
`condition_on_previous_text=True` atau fallback temperature tidak deterministik.

| Model | Varian | WER panjang | CER panjang | WER kalimat | RTF panjang | RAM puncak |
|---|---|---|---|---|---|---|
| base | m1 (rilis M1) | 38,9 % | 18,1 % | 31,9 % | 0,20 | 965 MB |
| base | lang_id | 37,1 % | 17,8 % | 31,9 % | 0,23 | 956 MB |
| base | noprev | 34,7 % | 15,1 % | 31,9 % | 0,13 | 959 MB |
| base | **noprev_t0** | **30,7 %** | **10,2 %** | 31,9 % | **0,09** | 961 MB |
| base | noprev_hst | 32,8 % | 11,3 % | 31,9 % | 0,16 | 928 MB |
| base | noprev_vad | 38,9 % | 19,9 % | 32,7 % | 0,12 | 939 MB |
| base | noprev_vocab | 40,3 % | 20,9 % | 35,7 % | 0,19 | 1108 MB |
| small | m1 (rilis M1) | 28,3 % | 17,4 % | 17,0 % | 0,56 | 1785 MB |
| small | auto3 | 34,1 % | 26,1 % | 17,0 % | 0,47 | 1510 MB |
| small | lang_id | 38,1 % | 29,9 % | 17,0 % | 0,47 | 1507 MB |
| small | noprev | **15,6 %** | 4,9 % | 17,0 % | 0,46 | 1468 MB |
| small | **noprev_t0** | **16,0 %** | **4,8 %** | 17,0 % | **0,20** | 1263 MB |
| small | noprev_hst | 16,7 % | 4,4 % | 17,0 % | 0,28 | 1469 MB |
| small | noprev_vad | 19,1 % | 10,4 % | 17,0 % | 0,19 | 1229 MB |
| small | noprev_vocab | 24,8 % | 14,8 % | 16,5 % | 0,51 | 1519 MB |
| small | lang_id_vocab | **91,3 %** (gagal) | 89,4 % | 16,5 % | 0,09 | 1387 MB |
| large-v3-turbo | lang_id | 22,7 % | 18,7 % | 6,0 % | 0,54 | 1956 MB |
| large-v3-turbo | noprev | 22,0 % | 17,5 % | 6,0 % | 0,47 | 1691 MB |
| large-v3-turbo | noprev_vocab | 8,6 % | 2,4 % | 6,0 % | 0,56 | 1817 MB |

Kesimpulan putaran 1 (dipakai sebagai bawaan):

1. **`condition_on_previous_text=False` + temperature 0** menjadi bawaan sementara (disetujui pengguna): small panjang 28,3 % → 16,0 %, base 38,9 % →
   30,7 %, 2x lebih cepat, RAM lebih kecil (determinisme belum terbukti: diuji di putaran 2). Conditioning teks sebelumnya adalah penyebab terbesar salah
   transkripsi pada video panjang (dan hasilnya acak: small 28–38 % antar-varian yang hanya beda bahasa).
2. **Kosakata bawaan TIDAK dipakai sebagai hotwords**: memperburuk WER (base +5,6 poin; small +9 poin; small+conditioning
   gagal total 91 %) dan di uji Windows membuat base melewatkan ±separuh ucapan. Kosakata dipakai untuk saran yang
   dikonfirmasi; hotwords hanya opsi eksperimental untuk istilah buatan pengguna (perlu uji dengan video NH).
3. **large-v3-turbo** jauh lebih akurat pada kalimat (6,0 % vs small 17,0 %), tetapi hasil panjangnya tidak stabil
   (8,6–22,7 %) karena fallback acak; putaran 2 mengujinya dengan bawaan baru (temperature 0) dua kali.
4. **Penanda "perlu ditinjau" (p < 0,5)**: presisi ±0,6–0,8 (sebagian besar kata bertanda memang salah) tetapi recall
   hanya ±0,13–0,35 (kebanyakan kata salah TIDAK bertanda). Penanda membantu memprioritaskan, bukan jaminan; semua
   subtitle tetap perlu dibaca sebelum publikasi.

### Uji Windows (installer `f05452c`, sampel FLEURS id 76,6 dtk, tanpa koreksi manual)

| | Model | WER | CER | Catatan |
|---|---|---|---|---|
| Sebelum (pengaturan M1) | base | 41,7 % | 22,6 % | 105 kata |
| Sesudah (M1.1 Akurat, pengaturan putaran-1 lama) | small | 39,4 % | 30,1 % | 93 kata |
| Aplikasi terpasang, Cepat + hotwords kosakata | base | 66,9 % | 53,4 % | 57 kata: ±separuh ucapan hilang → hotwords dimatikan |

Sampel ini hanya 5 kalimat panjang (bug durasi diperbaiki di `034cba6`); diuji ulang dengan bawaan baru.

### Putaran 2 — bawaan M1.1, dua run per model (run [42784852435](https://github.com/aiolaf9/video-editing/actions/runs/42784852435), commit `30ae9e0`)

Bawaan M1.1 (`condition_on_previous_text=False`, temperature 0, bahasa id, tanpa hotwords). Dua run terpisah per
model; **kedua run menghasilkan transkrip identik (hash sama) untuk semua model & set** — determinisme terbukti di
runner ini (CPU int8, ubuntu-24.04, 4 vCPU). Belum diuji di Windows/CPU lain; `--eval --runs 2` di komputer NH
memeriksanya lagi. Bising = `id_dialog` + noise pink SNR 10 dB (24 kalimat, 269 dtk).

| Model | WER panjang | CER panjang | WER kalimat | CER kalimat | WER bising | CER bising | RTF panjang / kalimat / bising | RAM puncak (2 run) |
|---|---|---|---|---|---|---|---|---|
| base | 29,7 % | 9,7 % | 31,9 % | 11,4 % | 69,4 % | 47,0 % | 0,04 / 0,06 / 0,03 | 904–933 MB |
| small | **14,0 %** | **4,4 %** | 17,2 % | 4,9 % | 50,9 % | 42,1 % | 0,19 / 0,30 / 0,15 | 1160–1342 MB |
| large-v3-turbo | 20,1 % | 15,5 % | **6,2 %** | **2,5 %** | **30,3 %** | **21,0 %** | 0,24 / 0,59 / 0,25 | 1747–1877 MB |

Variasi antar-run: 0 (WER, CER & teks identik); RTF ±1 %, RAM ±8–15 % (run pertama memuat model dari disk).

Temuan putaran 2:

1. **small** terbaik untuk audio panjang bersih (14,0 %); base 2x lebih banyak salah.
2. **turbo** jauh terbaik untuk kalimat (6,2 %) dan audio bising (30,3 % vs small 50,9 %), tetapi pada audio panjang
   CER 15,5 % (kalimat 2,5 %) = **bagian ucapan hilang**, bukan salah eja. Belum layak untuk video panjang sampai
   penyebabnya diperbaiki; putaran 3 menguji mode potongan ucapan (≤ 20 dtk).
3. **Bising vs bersih: berbeda sangat signifikan** untuk semua model (base 2,3x, small 3,6x, turbo 4,9x WER kalimat
   bersih). RTF bising yang sangat rendah (base 0,03) menandakan ucapan ikut terbuang oleh VAD/decoder. Ceramah dengan
   musik latar, gema masjid, atau mikrofon jauh perlu dicek manual lebih teliti.
4. Penanda "perlu ditinjau" tetap hanya bantuan: recall turbo pada audio panjang 4 % (hampir semua kesalahan tidak
   bertanda).

### Putaran 3 — mode potongan ucapan & pasangan bersih/bising (run [42790026222](https://github.com/aiolaf9/video-editing/actions/runs/42790026222), commit `96497b8`)

`id_dialog` = 24 kalimat berurutan (269 dtk, bersih); `id_noisy_snr10` = audio yang sama + noise pink SNR 10 dB.
Mode potongan = `WHISPER_CHUNKED=1` (VAD, potongan ≤ 20 dtk), dua run.

| Model / mode | Dialog bersih | Dialog bising | Panjang 10 mnt | Kalimat | RTF panjang | RAM puncak |
|---|---|---|---|---|---|---|
| small, bawaan | 22,8 % (CER 10,6) | 48,9 % (CER 37,0) | 14,0 % (CER 4,4) | 17,2 % | 0,20 | 1341 MB |
| small, potongan (2 run identik) | 19,6 % | **96,0 %** | 18,4 % | 17,2 % | 0,46 | 769–869 MB |
| turbo, bawaan | **7,8 %** (CER 4,9) | **31,3 %** (CER 26,3) | 20,1 % (CER 15,5) | **6,2 %** | 0,24 | 1973 MB |
| turbo, potongan (2 run identik) | 11,8 % | 47,6 % | 13,7 % (CER 7,8) | 8,0 % | ~1,0 | 1681 MB |

- Mode potongan **tidak** dijadikan bawaan: small runtuh pada audio bising (96 %), turbo membaik hanya pada audio panjang
  tetapi memburuk di set lain dan 4x lebih lambat.
- Antar-runner (CPU berbeda), audio panjang identik di semua run; audio bising bergeser ±1–2 poin (small 48,9/50,9 %,
  turbo 30,3/31,3 %). Determinisme hanya terjamin di mesin yang sama.

### Uji Windows — installer & alat `--eval` (sampel FLEURS id 156 dtk, 12 kalimat, 2 run per mode)

| Mode | Model | WER (CPU AMD, run `96497b8`) | WER (CPU Intel, run `64aa9b3`) | CER (Intel) | RTF (Intel) | Puncak RAM proses (Intel) |
|---|---|---|---|---|---|---|
| Cepat | base | 46,7 % | 32,8 % | 10,4 % | 0,09 | 358 MB |
| Akurat | small | 16,2 % | 16,6 % | 5,2 % | 0,22 | 726 MB |
| Kualitas tinggi | large-v3-turbo | 8,5 % | 7,7 % | 3,7 % | 0,45 | 1911 MB |

Dua run per mode identik di masing-masing mesin. **base bergeser 14 poin antar-CPU**; small & turbo ±1 poin.
Ukuran: `NH-AI-Clip-Studio-Setup.exe` 689,1 MB (base + small); `NH-AI-Clip-Studio-Model-KualitasTinggi.exe` (opsional)
1350,9 MB; folder aplikasi terpasang 1160 MB. RAM di atas = proses transkripsi saja (tanpa jendela WebView2 & Windows).

## 5. Rekomendasi konfigurasi (menunggu persetujuan & uji video NH)

| Pertanyaan | Rekomendasi dari data | Alasan |
|---|---|---|
| Model terbaik untuk **Cepat** | base hanya sebagai mode darurat PC lemah; **disarankan** Cepat diganti/dihapus (lihat opsi) | base 29,7–69,4 % WER di FLEURS dan 32,8–46,7 % di sampel Windows yang sama (tidak stabil antar-CPU); small sudah cukup cepat (RTF 0,15–0,22 ≈ 1,5–2,5 mnt per 10 mnt video, CPU 4 thread) |
| Model terbaik untuk **Akurat** (bawaan) | **small**, pengaturan M1.1 | Terbaik/konsisten untuk audio panjang (14,0 %), 16,2–16,6 % sampel Windows, RAM ±0,7–1,3 GB, deterministik di mesin yang sama |
| **Turbo** sebagai "Kualitas tinggi" | **Layak sebagai paket opsional untuk klip pendek & audio bising**; **belum** untuk video panjang | Kalimat 6,2 % / dialog 7,8 % / bising 31,3 % (terbaik), sampel Windows 7,7–8,5 %; tetapi audio panjang kehilangan ucapan (CER 15,5 % vs small 4,4 %), RTF 0,45 Windows, RAM ±1,9–2,0 GB, paket 1351 MB |
| Bersih vs bising | Perbedaan **sangat signifikan** untuk semua model | Dialog yang sama: small 22,8 → 48,9 %, turbo 7,8 → 31,3 %, base (kalimat→bising) 31,9 → 69,4 % |
| RAM 8 / 16 GB | Semua mode muat di 16 GB; 8 GB kemungkinan muat (proses ≤ 2 GB + Windows + WebView2 ±3–4 GB) | **Belum diukur di PC 8 GB sungguhan** → jalankan `--eval` di PC tim |

Opsi untuk keputusan pengguna (belum diterapkan):

1. **Disarankan:** satu mode standar *small* + paket opsional *Kualitas tinggi* (turbo); hapus base dari installer
   (±140 MB lebih kecil) atau pertahankan sebagai "Hemat RAM (kurang akurat)" untuk PC < 8 GB.
2. Pertahankan Cepat = base / Akurat = small seperti sekarang, dengan peringatan "Cepat: lebih banyak salah kata".

### Risiko yang masih ada (bahasa Arab & istilah dakwah)

- **Belum ada data**: FLEURS adalah berita yang dibacakan, tanpa istilah Arab. Tidak ada angka di dokumen ini yang
  membuktikan akurasi ceramah, ayat, doa, atau istilah dakwah.
- Hotwords kosakata dimatikan karena memperburuk WER; istilah Arab bergantung pada model + saran kosakata yang harus
  dikonfirmasi + gerbang tinjau sebelum ekspor.
- Penanda "perlu ditinjau" recall 4–40 %: sebagian besar kata salah tidak bertanda. Semua subtitle wajib dibaca.
- Audio bising (musik latar, gema masjid, mikrofon jauh) menaikkan kesalahan 2–4x.
- Hasil dapat bergeser antar-CPU (base 14 poin; small/turbo ±1–2 poin).
- Turbo dapat melewatkan ucapan pada audio panjang.

**Belum siap produksi** sampai `--eval` dengan 3–5 video NH asli + transkrip rujukan manual dijalankan di PC 8 GB &
16 GB tim dan hasilnya disetujui.

### Paket "Kualitas tinggi" (opsional) & uji dengan video NH asli

- **Paket model tambahan** `NH-AI-Clip-Studio-Model-KualitasTinggi.exe` (Whisper large-v3-turbo, MIT) **tidak**
  termasuk installer utama. Dipasang per pengguna ke `%LOCALAPPDATA%\NH AI Clip Studio\models\whisper-large-v3-turbo`;
  mode "Kualitas tinggi" baru muncul di aplikasi bila paket ini ada. Butuh RAM lebih besar (lihat tabel putaran 2).
- **Uji video NH di komputer NH sendiri** (offline, video tidak dikirim ke mana pun):

  1. Buat folder, mis. `D:\Uji NH`, berisi 3–5 potongan video asli (1–3 menit, ada istilah Arab) dan untuk tiap
     video berkas `.txt` bernama sama berisi transkrip yang diketik manual (ejaan pedoman NH).
  2. Jalankan (Command Prompt): `"%LOCALAPPDATA%\Programs\NH AI Clip Studio\NH-AI-Clip-Studio.exe" --eval "D:\Uji NH" --runs 2`
  3. Hasil: `laporan-uji.md` (WER, CER, RTF, puncak RAM, apakah 2 run identik, akurasi istilah kosakata per mode,
     spesifikasi komputer) dan folder `hasil\` (transkrip mentah). Kirim `laporan-uji.md` saja untuk ditinjau.

  Uji di komputer 8 GB dan 16 GB yang dipakai tim. Hasil berlaku untuk video & komputer yang diuji.
