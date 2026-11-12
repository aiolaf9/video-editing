# NH StoryMaker AI

Unggah **3–10 video liputan lapangan** (durasi berbeda-beda, horizontal atau vertikal). AI memahami
seluruh rekaman, memilih hook terbaik yang tetap sesuai fakta, menyusun cerita
**Hook → Masalah → Proses/Solusi → Dampak → Penutup**, memilih potongan dan B-roll terbaik, lalu
merender **satu video vertikal 1080×1920 sekitar 60 detik** dengan subtitle, transisi, dan audio yang
sudah dinormalisasi.

> Proyek ini menggantikan NH AI Clip Studio (folder `app/`, `web/` lama), yang tidak dikembangkan lagi.
> StoryMaker berdiri sendiri di folder `storymaker/` dan hanya memakai ulang `fonts/` dan `models/`.

## Menjalankan

```bash
pip install -r storymaker/requirements.txt      # plus FFmpeg di PATH
export ANTHROPIC_API_KEY=...                    # opsional: tanpa ini memakai penyusun lokal
python -m storymaker                            # buka http://localhost:8100
```

## Alur

| Tahap | Yang dikerjakan | Modul |
|---|---|---|
| 1. Masukkan video | 3–10 file, dicek dengan ffprobe (rotasi HP ditangani) | `pipeline.add_clip` |
| 2. Transkripsi | Whisper lokal, timestamp & keyakinan per kata | `transcribe.py` |
| 3. Analisis | Kalimat utuh (dipotong di tanda baca/jeda), skor hook, kejelasan, energi suara, kecocokan peran cerita. Adegan visual dari deteksi pergantian kamera + jendela 3 detik, dinilai ketajaman/kecerahan/goyangan. Posisi wajah per klip. Dengan Claude: setiap adegan dideskripsikan dari thumbnail (vision). | `analyze.py`, `media.py`, `faces.py`, `ai.describe_shots` |
| 4–5. Hook & struktur | Claude membaca katalog teks lengkap dan memilih **ID** kalimat/adegan. Tanpa API key: penyusun heuristik lokal. Alternatif gratis: salin prompt ke Claude.ai lalu tempel jawabannya. | `ai.plan_story`, `story.heuristic_plan` |
| 6. Pilih potongan | Validasi: ID tak dikenal dibuang, tanpa duplikasi, durasi dipangkas ke target ±10%, B-roll dilengkapi otomatis untuk kalimat panjang (bukan di hook) | `story.validate_plan`, `story.auto_broll` |
| 7. Gabungkan | Tiap potongan dinormalisasi ke 1080×1920/30fps (crop mengikuti wajah, atau latar blur bila tidak ada wajah), B-roll menutup gambar sementara suara narasumber tetap, lalu xfade + acrossfade | `timeline.py`, `render.py` |
| 8. Subtitle & audio | Subtitle ASS dari kata asli transkrip, kata aktif disorot, judul di 3 detik pertama; musik latar dengan ducking; loudness −14 LUFS | `subtitles.py`, `render.py` |

## Prinsip fakta

- AI **tidak menulis kalimat** untuk diucapkan atau dijadikan subtitle; ia hanya memilih ID ucapan asli.
- Setiap rencana (dari AI, heuristik, atau editan pengguna) melewati `validate_plan`.
- Salah dengar transkrip (nama orang/tempat) bisa dikoreksi per kalimat di UI sebelum render;
  koreksi disimpan dan bertahan saat analisis diulang.
- Satu-satunya teks karangan AI adalah **judul** (bisa diedit/dimatikan). Tanpa AI, judul = nama proyek.

## Konfigurasi (environment variable)

| Variabel | Default | Keterangan |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Mengaktifkan Claude untuk deskripsi adegan & penyusunan cerita |
| `STORY_PLANNER` | `auto` | `auto` / `claude` / `heuristic` |
| `STORY_CLAUDE_MODEL` | `claude-opus-5-5` | Model Claude |
| `WHISPER_MODEL` | `small` | `medium`/`large-v3` lebih akurat untuk subtitle |
| `STORY_DATA` | `./data-storymaker` | Lokasi proyek & hasil render |
| `STORY_MIN_CLIPS` / `STORY_MAX_CLIPS` | 3 / 10 | Batas jumlah video |

## API ringkas

`POST /api/projects` · `POST /api/projects/{id}/clips` (multipart `files`) · `POST …/analyze` ·
`GET …/{id}` (status, rencana, kandidat hook) · `PUT …/plan` (edit) · `POST …/plan` (susun ulang / ganti hook) ·
`GET …/prompt` + `POST …/plan/paste` (mode salin-tempel) · `PUT …/utterances/{uid}` (koreksi subtitle) ·
`POST …/music` · `POST …/render` · `GET …/files/outputs/<file>.mp4`

## Tes

```bash
python -m pytest storymaker/tests
```
Tes end-to-end membuat 3 video sintetis (2 horizontal, 1 vertikal), menjalankan analisis → rencana →
edit → render, lalu memeriksa hasil 1080×1920 dengan audio dan durasi sesuai timeline.
