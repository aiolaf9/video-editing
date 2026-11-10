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

1. **`condition_on_previous_text=False` + temperature 0** menjadi bawaan: small panjang 28,3 % → 16,0 %, base 38,9 % →
   30,7 %, 2x lebih cepat, RAM lebih kecil, deterministik. Conditioning teks sebelumnya adalah penyebab terbesar salah
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
