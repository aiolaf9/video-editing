# Rancangan egress filtering (P5.4, belum diterapkan)

Status: **rancangan**. Tidak ada perubahan pada server/firewall produksi. Diuji di staging pada P5.5.

## 1. Mengapa perlu lapisan jaringan

Perlindungan SSRF di aplikasi (`app/netpolicy.py`) memeriksa alamat pada `socket.getaddrinfo` di thread unduhan.
Itu kuat untuk kode Python yang dipakai yt-dlp, tetapi **bukan satu-satunya perlindungan**:

| Jalur keluar dari `worker-io` | Melewati guard aplikasi? | Pengendali saat ini |
|---|---|---|
| yt-dlp handler Requests/urllib3 & Urllib (http.client, ftplib) | ya | guard + batas redirect |
| yt-dlp handler Websockets | ya (koneksi dibuat lewat helper yt-dlp) | guard |
| handler lain, mis. CurlCFFI (resolver libcurl) | **tidak** | dibuang dari director (gagal tertutup); `tests/test_p54_ssrf.py` |
| FFmpeg/aria2c/curl/wget/rtmpdump sebagai pengunduh | **tidak** (proses lain) | ditahan selama unduhan, termasuk delegasi HLS→FFmpeg |
| Runtime JavaScript yt-dlp | **tidak** (proses lain) | hanya Deno (sandbox tanpa izin jaringan); Node/Bun tidak diaktifkan |
| FFmpeg pada file hasil unduhan/upload (probe, preview, render) | tidak | FFmpeg membatasi segmen playlist lokal ke `file,crypto,data` (terverifikasi di FFmpeg 6.1; **verifikasi versi image produksi di P5.5**) |
| Koneksi database (psycopg) | dikecualikan (pemanggil psycopg saja) | perlu dibatasi di jaringan (bagian 2) |
| `DOWNLOAD_PROXY` | alamat proxy dikecualikan | proxy wajib menyaring (bagian 2) |
| Library lain yang kelak memakai resolver/soket native | tidak | hanya lapisan jaringan yang menjamin |

## 2. Rancangan: worker unduhan tanpa rute langsung ke internet

```
             internet
                │
        ┌───────┴────────┐
        │  egress-proxy  │  (smokescreen/squid: tolak IP privat, link-local, metadata, CGNAT, port selain 80/443)
        └───────┬────────┘
   net: dl-egress (internal)        net: backend (internal)          net: edge
        │                               │        │                      │
   ┌────┴──────┐   net: dl-db      ┌────┴──┐ ┌───┴────────┐        ┌────┴──┐
   │ worker-io │────(internal)─────│  db   │ │ web/worker │────────│ caddy │
   └───────────┘                   └───────┘ └────────────┘        └───────┘
```

- `worker-io` hanya terhubung ke dua jaringan Docker `internal: true`:
  - `dl-egress`: berisi `worker-io` + `egress-proxy`;
  - `dl-db`: berisi `worker-io` + `db`.
  Tanpa rute default ke internet, FFmpeg, runtime JS, atau library native **tidak bisa** keluar kecuali lewat proxy,
  dan tidak bisa menjangkau `web`, worker lain, Caddy, atau metadata cloud.
- `worker-io` memakai `DOWNLOAD_PROXY=http://egress-proxy:4750`. Proxy me-resolve dan memeriksa IP tujuan sendiri
  (aman terhadap DNS rebinding di sisi proxy) serta mencatat setiap koneksi (audit).
- Kebijakan proxy (contoh): izinkan TCP 80/443 ke IP publik; tolak 0.0.0.0/8, 10/8, 100.64/10, 127/8, 169.254/16,
  172.16/12, 192.168/16, 198.18/15, 224/4, 240/4, ::1, fc00::/7, fe80::/10, ff00::/8, 64:ff9b::/96 (embedded
  privat); batas waktu koneksi & durasi; batas koneksi bersamaan.
- Database: role PostgreSQL terpisah untuk `worker-io` dengan hak minimum (tabel jobs/projects/uploads yang
  diperlukan) — dirancang di P5.5, bukan sekarang.
- Proses lain:
  - `web` perlu HTTPS ke penyedia AI → lewat proxy yang sama dengan **allowlist domain penyedia**
    (api.anthropic.com, api.openai.com, generativelanguage.googleapis.com, openrouter.ai, api.groq.com,
    api.deepseek.com) + Ollama internal yang ditentukan admin.
  - `worker-analisis`/`worker-render`: tidak perlu internet bila model Whisper sudah tersedia di volume
    (`HF_HUB_OFFLINE=1`); unduh model sekali saat provisioning.
- Mode satu komputer (SQLite, worker di dalam proses web) tidak mendapat isolasi ini: impor dari link sebaiknya
  `ALLOW_URL_IMPORT=admins` atau `none` pada mode tersebut.

Alternatif tanpa proxy: aturan `DOCKER-USER` iptables/nftables di host untuk subnet `worker-io` (izinkan DNS ke
resolver, `db:5432`, TCP 80/443 ke non-privat; tolak sisanya). Lebih sederhana, tetapi tanpa audit per tujuan dan
DNS rebinding tidak dicegah di tingkat jaringan untuk alamat publik→privat (aturan berbasis IP tetap memblokirnya).

## 3. Uji yang wajib lolos di staging (masuk P5.5)

Dijalankan dari dalam container `worker-io`:

| Uji | Hasil yang diharapkan |
|---|---|
| `curl -m5 https://example.com` tanpa proxy | gagal (tidak ada rute) |
| `curl -m5 -x $DOWNLOAD_PROXY https://example.com` | 200 |
| lewat proxy ke `http://169.254.169.254/`, `http://10.0.0.1/`, `http://127.0.0.1:5432/` | ditolak proxy (dicatat) |
| `ffmpeg -i http://example.com/x.mp4` tanpa proxy | gagal (tidak ada rute) |
| koneksi ke `web:8000`, `caddy:443` | gagal (jaringan berbeda) |
| koneksi ke `db:5432` | berhasil (dibutuhkan antrean) |
| impor link publik lewat UI | berhasil, tercatat di log proxy |
