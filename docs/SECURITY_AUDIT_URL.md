# Audit akses URL eksternal (pra-P5.4)

Status: **audit saja, belum ada perbaikan kode.** Implementasi menunggu persetujuan P5.4.

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

## 4. Rencana P5.4 (usulan, belum dikerjakan)

1. Modul `app/netpolicy.py`: klasifikasi IP, validasi URL pengguna & admin (unit test tabel lengkap, termasuk
   IPv6 & bentuk IPv4 tidak lazim seperti `http://2130706433/`, `http://0x7f.1/`).
2. `ai._http`: opener tanpa redirect + validasi skema/host (menutup B310, U3, U5); hapus entri B310 dari
   `.bandit-baseline.json` setelah lolos.
3. `put_settings`: aturan base_url admin + pemutusan key saat base_url berganti (U4); ringkas error untuk editor (U6).
4. `pipeline.download`: request handler yt-dlp yang memvalidasi IP setiap koneksi (U1, U2); `from-url` menolak
   lebih awal dengan pesan jelas. `tests/test_ytdlp_local.py` diberi pengaturan uji eksplisit untuk host lokal.
5. Tes: SSRF langsung, redirect ke privat, DNS rebinding (resolver tiruan), Ollama localhost tetap jalan,
   key tidak terkirim ke host redirect.
6. Dokumentasi deployment: isolasi jaringan worker (egress) — sebagai rekomendasi, tanpa mengubah produksi.
