# Audit & perlindungan akses URL eksternal (P5.4)

Status: audit pra-P5.4 (bagian 1–2) dan **implementasi P5.4** (bagian 5). Rencana awal di bagian 3–4 dipertahankan
sebagai catatan keputusan.

## 1. Titik akses keluar

| # | Titik | Siapa yang menentukan URL | Klien HTTP | Redirect | Header rahasia |
|---|---|---|---|---|---|
| A | `POST /api/projects/from-url` → worker `pipeline.download` (yt-dlp) | **pengguna biasa** (`ALLOW_URL_IMPORT=all` bawaan) | yt-dlp (requests/urllib3) | diikuti | cookie situs (tidak dipakai) |
| B | `ai._http` → `{base_url}/models`, `{base_url}/chat/completions` (Gemini, OpenAI, OpenRouter, Groq, DeepSeek, Ollama, custom) | admin global (`PUT /api/settings`, peran `admin`) | `urllib.request.urlopen` (bandit B310) | **diikuti otomatis** | `Authorization: Bearer <API key>` |
| C | Claude (`anthropic` SDK) | tetap (`api.anthropic.com` / `ANTHROPIC_BASE_URL` env) | httpx | SDK | `x-api-key` |
| D | Whisper (`huggingface_hub`) | tetap (Hugging Face) | – | – | – |

## 2. Temuan

| ID | Risiko | Bukti | Tingkat |
|---|---|---|---|
| U1 | **SSRF dari pengguna biasa**: `from-url` hanya memeriksa awalan `http(s)://`. URL `http://127.0.0.1:...`, `http://169.254.169.254/...` (metadata cloud), `http://postgres:5432`, `http://10.x.x.x` diterima dan diambil oleh worker. Respons berupa media disimpan sebagai video proyek (bisa diunduh/ditonton pengguna) dan pesan error yt-dlp ditampilkan ke pengguna. | `app/main.py:462`; `tests/test_ytdlp_local.py` sendiri mengunduh dari `127.0.0.1` lewat `pipeline.download` | **Tinggi** |
| U2 | **Redirect ke alamat privat** (U1 melalui pengalihan): URL publik yang mengalihkan ke alamat internal. Pemeriksaan hanya pada URL awal tidak cukup; juga DNS rebinding (nama yang di-resolve ke IP privat saat koneksi) dan segmen DASH/HLS yang menunjuk host lain. | perilaku yt-dlp/requests (redirect diikuti) | **Tinggi** |
| U3 | **API key bocor lewat redirect**: `urllib` meneruskan header `Authorization` ke tujuan redirect walau host berbeda. Penyedia/proxy yang mengalihkan (atau base_url `http://` yang dimanipulasi di jaringan) menerima key. | diuji: 302 dari `127.0.0.1` ke `127.0.0.2` → `Authorization` diterima host kedua | **Tinggi** (key penyedia berbayar) |
| U4 | **Key bawaan server dikirim ke base_url apa pun**: bila base_url penyedia diubah, key dari env (`OPENAI_API_KEY`, dll.) atau key tersimpan ikut dikirim ke host baru tanpa konfirmasi. Saat ini hanya admin global yang dapat mengubah → risiko penyalahgunaan/kesalahan konfigurasi, bukan eskalasi. | `app/main.py:_provider_cfg`, `put_settings` | Sedang |
| U5 | **Skema & transport**: pola `^(https?://.*)?$` membolehkan `http://` publik untuk penyedia ber-key → key terkirim tanpa TLS. Skema lain (`file:`, `ftp:`) sudah ditolak skema Pydantic, tetapi `urlopen` sendiri menerima `file:` (B310) — tidak ada pertahanan berlapis di `ai._http`. | `app/schemas.py:91`, `app/ai.py:55` | Sedang |
| U6 | **Pantulan respons internal ke editor**: error penyedia (≤300 karakter isi respons) dan "Tidak bisa terhubung ke {url}" diteruskan ke semua editor lewat `plan/ai` → membocorkan host internal/isi respons bila base_url salah arah. | `app/ai.py:_friendly`, `app/main.py:plan_ai` | Rendah–sedang |
| U7 | **Kebocoran key antar-workspace**: pengaturan AI bersifat global (bukan per unit). Nilai key tidak pernah dikirim ke klien (hanya 4 karakter terakhir, hanya admin global). Tidak ditemukan jalur unit_admin/editor membaca atau mengganti key/base_url. Risiko yang tersisa: **biaya** — semua unit memakai key yang sama (dibatasi kuota `ai` per unit). | `get_settings` (`require_admin`), `auth.require_admin` hanya peran `admin` | Rendah (catat untuk SaaS multi-tenant) |
| U8 | Batas ukuran/durasi unduhan yt-dlp tergantung kuota & `ensure_disk`, bukan batas byte per unduhan. | `pipeline.download` opsi | Rendah |

## 3. Kebijakan URL yang diusulkan

**URL dari pengguna biasa (`from-url`)** — tidak dipercaya:
- skema hanya `http`/`https`; tanpa userinfo (`user:pass@`); port 80/443 (bawaan, dapat diatur admin);
- setiap koneksi (awal, setiap redirect, setiap segmen) di-resolve dan **IP tujuan** harus publik: tolak loopback,
  privat RFC1918, link-local (169.254/16, fe80::/10), CGNAT 100.64/10, ULA fc00::/7, multicast, unspecified,
  IPv4-mapped IPv6 dari rentang tersebut;
- validasi dilakukan pada IP yang benar-benar dipakai koneksi (mencegah DNS rebinding), lewat request handler
  yt-dlp khusus / validasi pada socket, bukan hanya pada string URL;
- pesan error ke pengguna generik (tanpa isi respons internal);
- pertahanan berlapis di deployment: worker unduhan di jaringan Docker yang tidak dapat menjangkau `postgres`,
  `web`, dan metadata cloud (egress firewall / proxy keluar).

**URL yang dikonfigurasi admin (base_url penyedia AI)** — dipercaya terbatas:
- skema `http`/`https` saja (ditegakkan juga di `ai._http`, bukan hanya di skema API);
- alamat privat/localhost **diizinkan** (Ollama, server inferensi internal) tetapi hanya bila admin memilihnya
  secara eksplisit; `http://` hanya untuk alamat privat/localhost; host publik wajib `https://`;
- **redirect tidak diikuti** (atau hanya ke origin yang sama, tanpa header `Authorization` ke origin lain);
- mengganti base_url penyedia yang punya key tersimpan/env → key tidak ikut terkirim sampai admin memasukkan ulang
  atau mengonfirmasi (mencegah U4);
- error ke editor diringkas; detail lengkap hanya di log server & halaman admin.

## 4. Rencana P5.4 (sudah dikerjakan, lihat bagian 5)

## 5. Implementasi P5.4

| Temuan | Perbaikan | Bukti uji |
|---|---|---|
| U3 key bocor lewat redirect | `ai._http` memakai opener sendiri: **redirect selalu ditolak** (301/302/303/307/308), hanya handler http/https (tanpa `file:`/`ftp:`/`data:`) | `tests/test_p54_ai_security.py`: dua server berbeda (127.0.0.1 → 127.0.0.2), host kedua menerima **0 permintaan** untuk 5 kode redirect, termasuk POST chat |
| U5 skema/transport, B310 | `netpolicy.check_admin_url` di `PUT /api/settings` **dan** di setiap permintaan: host publik wajib `https`; `http` hanya untuk localhost/IP privat (Ollama, server internal); tanpa userinfo. Entri B310 dihapus dari `.bandit-baseline.json` (bandit: 0 temuan) | tes kebijakan URL admin (izinkan/tolak), Ollama lokal tetap jalan tanpa header `Authorization` |
| U4 key berpindah alamat | key tersimpan dilepas bila Base URL berganti tanpa key baru; key dari environment hanya untuk alamat resmi penyedia | tes API pengaturan & `_provider_cfg` |
| U6 pantulan respons ke editor | `AIError.public`: editor hanya melihat ringkasan; detail ke admin & log. API key yang disalin penyedia ke pesan error diganti `***` | tes 401 yang memantulkan key |
| (baru) konfigurasi global dari pengguna biasa | model yang dipilih otomatis hanya disimpan ke pengaturan global bila peminta admin global | – |
| U1 SSRF impor link | `check_import_url` di API (pesan cepat) + **`import_guard` di worker**: `socket.getaddrinfo` pada thread unduhan hanya mengembalikan alamat yang diizinkan, sehingga koneksi hanya mungkin ke IP yang lolos pemeriksaan (validasi = alamat yang dihubungi) | `tests/test_p54_ssrf.py`: loopback, privat, link-local/metadata, CGNAT, multicast, IPv6 (::1, ULA, link-local, IPv4-mapped, NAT64, 6to4, Teredo), bentuk IPv4 desimal/heksa/oktal |
| U2 redirect, URL turunan, DNS rebinding | tertangkap oleh guard yang sama (setiap koneksi baru di-resolve ulang & diperiksa); batas redirect `URL_IMPORT_MAX_REDIRECTS` (bawaan 5) pada handler requests & urllib yt-dlp | unduhan yt-dlp nyata: redirect ke server internal, manifest DASH publik dengan segmen di server internal, DNS rebinding (publik saat dicek, internal saat koneksi), nama publik → IP privat; server internal menerima **0 permintaan**; alamat internal tidak muncul di pesan error |
| Pengunduh eksternal | FFmpeg/aria2c/curl/wget/rtmpdump membuka koneksi di luar Python: ditahan selama unduhan (termasuk delegasi diam-diam HLS→FFmpeg) | tes RTMP & `FFmpegFD.real_download` |
| U8 ukuran | batas total byte `URL_IMPORT_MAX_MB` lewat progress hook (berlaku juga untuk fragmen DASH/HLS tanpa ukuran di header); timeout socket 30 dtk & `DOWNLOAD_TIMEOUT` job tetap | tes batas ukuran file tunggal & DASH |
| Kebijakan terpisah | `URL_IMPORT_TRUSTED_NETWORKS` (CIDR) hanya untuk impor oleh admin global; editor/admin unit selalu IP publik | tes API editor (400 walau jaringan tepercaya diset) & admin (berhasil) |

### Batasan yang tersisa (perlu lapisan jaringan)

1. **Proxy keluar** (`DOWNLOAD_PROXY`): aplikasi hanya melihat alamat proxy; proxy itulah yang wajib menyaring
   alamat internal. Variabel `HTTP(S)_PROXY` lingkungan sengaja tidak dipakai untuk unduhan.
2. **Proses di luar Python** yang mungkin dijalankan yt-dlp selain pengunduh (runtime JavaScript Deno/Node untuk
   tantangan YouTube) tidak melewati guard; skrip yang dijalankan berasal dari paket `yt-dlp-ejs`, bukan dari URL.
3. **Pengecualian psycopg**: resolusi host database dari thread unduhan (pencatatan progres job) tidak diperiksa,
   karena pemanggilnya hanya psycopg (tidak pernah membawa URL pengguna).
4. **Egress filtering tetap direkomendasikan** (pertahanan berlapis): worker di jaringan yang tidak dapat menjangkau
   database, layanan internal, dan 169.254.169.254 — lihat DEPLOY.md. Pengaturan produksi tidak diubah oleh P5.4.
5. U7: pengaturan AI masih global (satu key untuk semua unit). Untuk SaaS multi-tenant, key per workspace perlu
   dirancang di tahap berikutnya.
