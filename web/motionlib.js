// M2.5 Motion Library: galeri preset (thumbnail + klip gerak dari engine render yang sama), terapkan sekali klik ke
// seluruh klip / posisi video / beberapa rentang, kontrol per item di daftar animasi, intensitas ringan-seimbang-dinamis.
// Item tersimpan di resep (state.plan -> PUT /plan) sehingga ikut undo/redo, pratinjau, dan MP4 final.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let lib = null;
  let cat = "captions";
  let scope = "here";
  const NUM = ["intensity", "speed", "value", "pos_y"];
  const CONTROL_UI = {
    text: (p, v) => `<input data-p="text" value="${esc(v ?? "")}" placeholder="Teks">`,
    label: (p, v) => `<input class="label" data-p="label" value="${esc(v ?? "")}" placeholder="Keterangan">`,
    items: (p, v) => `<input data-p="items" value="${esc((v || []).join(", "))}" placeholder="Label:angka, pisahkan koma (isi data Anda)">`,
    value: (p, v) => `<label class="mini">Nilai % <input type="number" min="0" max="100" data-p="value" value="${v ?? 0}"></label>`,
    color: (p, v) => `<label class="mini">Warna <input type="color" data-p="${p.kind === "caption" ? "highlight" : "accent"}" value="${v || "#FFD43B"}"></label>`,
    font: (p, v) => `<select data-p="font" title="Font">${["Anton", "Poppins", "Bebas Neue", "Arial"].map((f) => `<option${f === v ? " selected" : ""}>${f}</option>`).join("")}</select>`,
    position: (p, v) => `<label class="mini" title="Posisi vertikal yang diinginkan; safe-zone tetap menggeser bila menutupi wajah/papan/subtitle">Posisi <input type="range" data-p="pos_y" min="0.05" max="0.95" step="0.01" value="${v ?? 0.5}"></label>`,
    intensity: (p, v) => `<label class="mini">Intensitas <input type="range" data-p="intensity" min="0.3" max="2" step="0.05" value="${v ?? 1}"></label>`,
    speed: (p, v) => `<label class="mini">Kecepatan <input type="range" data-p="speed" min="0.4" max="2.5" step="0.05" value="${v ?? 1}"></label>`,
    easing: (p, v) => `<select data-p="easing" title="Easing">${[["out", "Halus di akhir"], ["in", "Halus di awal"], ["inout", "Halus keduanya"], ["linear", "Linear"]].map(([k, l]) => `<option value="${k}"${k === v ? " selected" : ""}>${l}</option>`).join("")}</select>`,
    direction: (p, v) => `<select data-p="direction" title="Arah">${["kanan", "kiri", "atas", "bawah"].map((k) => `<option${k === v ? " selected" : ""}>${k}</option>`).join("")}</select>`,
    duration: () => "",
  };

  function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]); }
  function preset(id) { return lib && lib.presets.find((p) => p.id === id); }
  function newId() { return Math.random().toString(16).slice(2, 14); }

  async function load() {
    try { lib = await api("/api/motion-presets"); } catch { return; }
    $("mlCats").innerHTML = lib.categories.map((c) => `<button type="button" data-cat="${c.id}" class="${c.id === cat ? "active" : ""}">${esc(c.label)} <small>${c.count}</small></button>`).join("");
    renderGallery();
    markLevel();
  }

  // Kartu tampil seketika (nama + placeholder); gambar baru dimuat setelah server melaporkan "siap". Render galeri
  // pertama kali berjalan di satu thread server, jadi koneksi browser tetap bebas untuk menyimpan resep.
  let pollTimer = null;
  function renderGallery() {
    if (!lib) return;
    $("mlGallery").innerHTML = lib.presets.filter((p) => p.category === cat).map((p) => `
      <button type="button" class="preset-card ml-card pending" data-id="${esc(p.id)}" title="${esc(p.desc)}">
        <span class="pc-media ml-media"><img alt="" data-src="/api/motion-presets/${esc(p.id)}/thumb.png">
          <video muted loop playsinline preload="none" data-src="/api/motion-presets/${esc(p.id)}/preview.mp4"></video></span>
        <span class="pc-name">${esc(p.label)}</span>
      </button>`).join("");
    pollGallery();
  }

  async function pollGallery() {
    clearTimeout(pollTimer);
    const cards = [...document.querySelectorAll("#mlGallery .ml-card.pending")];
    if (!cards.length) return;
    let st = {};
    try { st = await api(`/api/motion-presets/status?ids=${encodeURIComponent(cards.map((c) => c.dataset.id).join(","))}`); }
    catch { /* coba lagi */ }
    for (const c of cards) {
      if (st[c.dataset.id] === "siap") {
        const img = c.querySelector("img");
        img.src = img.dataset.src;
        c.classList.remove("pending");
      } else if (st[c.dataset.id] === "gagal") {
        c.classList.remove("pending");
        c.classList.add("failed");
      }
    }
    if (document.querySelector("#mlGallery .ml-card.pending")) pollTimer = setTimeout(pollGallery, 1500);
  }

  function parseRanges(txt, s0, s1) {
    // "0-3, 10-12.5" (detik dari awal klip) -> rentang detik sumber
    const out = [];
    for (const part of String(txt || "").split(/[,;]/)) {
      const m = part.trim().match(/^(\d+(?:[.,]\d+)?)\s*-\s*(\d+(?:[.,]\d+)?)$/);
      if (!m) continue;
      const a = s0 + parseFloat(m[1].replace(",", ".")), b = s0 + parseFloat(m[2].replace(",", "."));
      if (b - a >= 0.2 && a < s1) out.push([Math.max(s0, a), Math.min(s1, b)]);
    }
    return out;
  }

  function apply(pid) {
    const p = preset(pid);
    const st = state;
    if (!p || !st.project) return;
    const s0 = st.sel.start, s1 = st.sel.end;
    const dur = +(p.defaults.duration || (p.category === "camera" ? 4 : 3));
    let ranges;
    if (scope === "clip") ranges = [[s0, s1]];
    else if (scope === "ranges") {
      ranges = parseRanges($("mlRanges").value, s0, s1);
      if (!ranges.length) { toast("Tulis rentang dari awal klip, mis. 0-3, 10-12.5", true); return; }
    } else {
      const t = Math.max(s0, Math.min($("player").currentTime, s1 - 0.3));
      ranges = p.category === "transitions" ? [[t - dur / 2, t + dur / 2]] : [[t, Math.min(s1, t + dur)]];
    }
    for (const [a, b] of ranges) {
      let s = a, e = b;
      if (p.category === "transitions" && scope !== "here") { const m = (a + b) / 2; s = m - dur / 2; e = m + dur / 2; }
      st.plan.push({ id: newId(), type: "fx", preset: p.id, params: {}, start: +Math.max(s0, s).toFixed(2),
        end: +Math.min(s1, e).toFixed(2), text: "", label: "", items: [] });
    }
    st.plan.sort((x, y) => x.start - y.start);
    renderPlan();
    savePlanSoon();
    toast(`"${p.label}" ditambahkan (${ranges.length} rentang). Atur di daftar Animasi; ▶ Pratinjau hasil = MP4 final.`);
  }

  // ---- item fx di daftar animasi (dipanggil renderPlan di app.js)
  function itemFields(it) {
    const p = preset(it.preset);
    if (!p) return `<span class="muted">Preset tidak dikenal (${esc(it.preset)})</span>`;
    const v = { ...p.defaults, ...(it.params || {}) };
    return `<b class="ml-name" title="${esc(p.desc)}">${esc(p.label)}</b>` +
      p.controls.map((c) => (CONTROL_UI[c] ? CONTROL_UI[c](p, c === "color" ? (v.accent || v.highlight) : c === "position" ? v.pos_y : v[c]) : "")).join("");
  }

  function onParam(e) {
    const key = e.target.dataset.p;
    if (!key) return false;
    const li = e.target.closest("li[data-id]");
    const item = li && state.plan.find((i) => i.id === li.dataset.id);
    if (!item) return true;
    item.params = item.params || {};
    let val = e.target.value;
    if (NUM.includes(key)) val = +val;
    else if (key === "items") val = val.split(",").map((x) => x.trim()).filter(Boolean);
    else if (/^#/.test(val)) val = val.toUpperCase();
    item.params[key] = val;
    $("ovPreview").dataset.key = "";
    savePlanSoon();
    return true;
  }

  function markLevel() {
    const lv = (typeof state !== "undefined" && state.motionLevel) || "seimbang";
    document.querySelectorAll("#mlLevel button").forEach((b) => b.classList.toggle("active", b.dataset.v === lv));
  }

  function playCard(card, on) {
    const v = card.querySelector("video");
    if (!v) return;
    if (on && card.classList.contains("pending")) return;          // klip gerak dimuat setelah galeri siap
    if (on) { if (!v.src) v.src = v.dataset.src; card.classList.add("playing"); v.play().catch(() => {}); }
    else { v.pause(); card.classList.remove("playing"); }
  }

  function setup() {
    $("mlCats").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-cat]");
      if (!b) return;
      cat = b.dataset.cat;
      document.querySelectorAll("#mlCats button").forEach((x) => x.classList.toggle("active", x === b));
      renderGallery();
    });
    $("mlScope").addEventListener("click", (e) => {
      if (!e.target.dataset.v) return;
      scope = e.target.dataset.v;
      document.querySelectorAll("#mlScope button").forEach((b) => b.classList.toggle("active", b.dataset.v === scope));
      $("mlRangesRow").hidden = scope !== "ranges";
    });
    $("mlLevel").addEventListener("click", (e) => {
      if (!e.target.dataset.v || !state.project) return;
      state.motionLevel = e.target.dataset.v;
      markLevel();
      markDirty();
    });
    const gal = $("mlGallery");
    gal.addEventListener("click", (e) => { const c = e.target.closest(".ml-card"); if (c) apply(c.dataset.id); });
    gal.addEventListener("mouseover", (e) => { const c = e.target.closest(".ml-card"); if (c) playCard(c, true); });
    gal.addEventListener("mouseout", (e) => { const c = e.target.closest(".ml-card"); if (c && !c.contains(e.relatedTarget)) playCard(c, false); });
    gal.addEventListener("focusin", (e) => { const c = e.target.closest(".ml-card"); if (c) playCard(c, true); });
    gal.addEventListener("focusout", (e) => { const c = e.target.closest(".ml-card"); if (c) playCard(c, false); });
    load();
  }

  window.MotionLib = { itemFields, onParam, markLevel, preset, apply, parseRanges, get lib() { return lib; } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup); else setup();
})();
