// PWA AI Video Studio (P2): pendaftaran service worker & tombol "Pasang aplikasi" sesuai dukungan browser.
// - Chrome/Edge (Android, Windows, Mac, ChromeOS): prompt pemasangan asli (beforeinstallprompt).
// - iPhone/iPad (Safari, juga Chrome/Edge di iOS 16.4+): petunjuk "Bagikan -> Tambahkan ke Layar Utama".
// - Safari di Mac (Sonoma ke atas): petunjuk "File -> Tambahkan ke Dock".
// - Browser tanpa dukungan (mis. Firefox desktop): tombol tidak ditampilkan; aplikasi tetap jalan di browser.
"use strict";
(() => {
  const standalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const ua = navigator.userAgent;
  const isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const isMacSafari = !isIOS && /Macintosh/.test(ua) && /Safari\//.test(ua) && !/Chrome|Chromium|Edg|Firefox/.test(ua);
  let deferred = null;

  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost"
      || location.hostname === "127.0.0.1")) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => { /* aplikasi tetap jalan tanpa PWA */ });
  }

  function button() { return document.getElementById("installBtn"); }

  function help(html) {
    const dlg = document.getElementById("installHelp");
    if (!dlg) return;
    document.getElementById("installHelpText").innerHTML = html;
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  }

  function refresh() {
    const btn = button();
    if (!btn) return;
    btn.hidden = standalone() || !(deferred || isIOS || isMacSafari);
  }

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();             // tampilkan lewat tombol kita, bukan banner otomatis
    deferred = e;
    refresh();
  });
  window.addEventListener("appinstalled", () => { deferred = null; refresh(); });

  document.addEventListener("DOMContentLoaded", () => {
    const btn = button();
    if (btn) {
      btn.addEventListener("click", async () => {
        if (deferred) {
          deferred.prompt();
          try { await deferred.userChoice; } catch { /* abaikan */ }
          deferred = null;
          refresh();
        } else if (isIOS) {
          help("Di Safari, ketuk tombol <b>Bagikan</b> (kotak dengan panah ke atas), lalu pilih <b>Tambahkan ke Layar " +
               "Utama</b>. Ikon AI Video Studio akan muncul seperti aplikasi biasa.");
        } else if (isMacSafari) {
          help("Di Safari, buka menu <b>File → Tambahkan ke Dock</b> (macOS Sonoma ke atas). Di Chrome atau Edge, " +
               "pakai ikon pasang di kolom alamat.");
        }
      });
    }
    const close = document.getElementById("installHelpClose");
    if (close) close.addEventListener("click", () => document.getElementById("installHelp").close());
    refresh();
  });

  // Keluar akun: bersihkan Cache Storage (isinya hanya berkas statis, tetapi tidak perlu tertinggal).
  window.pwaClearCaches = async () => {
    try {
      if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage("clear-caches");
      if (window.caches) await Promise.all((await caches.keys()).map((k) => caches.delete(k)));
    } catch { /* abaikan */ }
  };
})();
