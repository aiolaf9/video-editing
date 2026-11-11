/* Service worker AI Video Studio (P2).
 *
 * Kebijakan cache (sengaja ketat):
 * - HANYA berkas statis tanpa data pribadi dari daftar SHELL di bawah yang pernah disimpan di Cache Storage
 *   (halaman HTML statis, CSS, JS, ikon, font). Tidak ada penyimpanan saat berjalan untuk URL lain.
 * - /api/* (akun, proyek, resep, transkrip), video pratinjau & hasil render (permintaan Range), permintaan selain
 *   GET, dan asal (origin) lain TIDAK disentuh sama sekali: langsung ke jaringan, tidak pernah dicache.
 *   Server juga mengirim "Cache-Control: no-store" untuk semua /api/*.
 * - Navigasi: jaringan dulu; bila offline, tampilkan offline.html.
 * - Berkas statis: jaringan dulu (versi aplikasi selalu cocok dengan server), cache hanya cadangan saat offline.
 */
const CACHE = "avs-static-v7";
const SHELL = [
  "/", "/index.html", "/login.html", "/admin.html", "/offline.html",
  "/style.css", "/app.js", "/subeditor.js", "/studio.js", "/motionlib.js", "/protect.js",
  "/evaltool.js", "/admin.js", "/pwa.js", "/manifest.webmanifest",
  "/icons/icon-192.png", "/icons/icon-512.png", "/icons/maskable-512.png", "/icons/apple-touch-icon.png",
  "/icons/favicon-32.png",
  "/fonts/Anton-Regular.ttf", "/fonts/BebasNeue-Regular.ttf", "/fonts/Poppins-Bold.ttf",
  "/fonts/Poppins-ExtraBold.ttf", "/fonts/Poppins-SemiBold.ttf",
];
const STATIC = new Set(SHELL);

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Dipanggil saat keluar akun: hapus semua cache (hanya berkas statis, tetapi tetap dibersihkan).
self.addEventListener("message", (event) => {
  if (event.data === "clear-caches") {
    event.waitUntil(caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k)))));
  }
});

function bypass(request, url) {
  return request.method !== "GET" || url.origin !== self.location.origin || request.headers.has("range")
    || url.pathname.startsWith("/api/") || url.pathname === "/metrics" || url.pathname === "/sw.js";
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (bypass(request, url)) return;                       // jaringan langsung, tidak pernah dicache
  if (request.mode === "navigate") {
    // Selalu ke server (tanpa cache HTTP); gagal = server tidak terjangkau -> halaman offline.
    event.respondWith(fetch(request, { cache: "no-cache" }).catch(() => caches.match("/offline.html")));
    return;
  }
  if (!STATIC.has(url.pathname) || url.search) return;     // bukan berkas shell: jangan sentuh
  event.respondWith(fetch(request).then((res) => {
    if (res.ok && res.type === "basic") {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(url.pathname, copy));
    }
    return res;
  }).catch(() => caches.match(url.pathname)));
});
