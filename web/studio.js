// M2 Motion Studio (desktop/m2): Smart Framing per bagian + pratinjau MP4 dari pipeline render yang sama.
// Memakai state, rs, $, api, postJSON, markDirty, flushRecipe, esc, toast dari app.js (skrip klasik, cakupan global).
(function () {
  const FRAME_HINTS = {
    auto: "Otomatis: Fokus Wajah, lalu papan + wajah saat pemateri ke papan atau menulis, papan saja bila wajah tidak terlihat.",
    face: "Fokus Wajah: bingkai mengikuti wajah pemateri (bergeser halus, tidak bergetar).",
    material: "Fokus Materi: papan/layar di atas, wajah di bawah. Tanpa wajah: papan utuh di atas latar blur.",
    manual: "Manual: bingkai tetap sesuai posisi & zoom yang Anda atur.",
  };
  const LAYOUTS = [["", "Ikuti mode"], ["face", "Fokus Wajah"], ["split", "Materi + Wajah"], ["material", "Materi saja"], ["center", "Bingkai tetap"]];
  let sectionTimer = null;
  let lastPlan = [];

  const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

  // Pembesaran piksel sumber -> output (sama dengan app/framing.zoom_limit): di atas 2x gambar mulai kurang tajam.
  function zoomHint() {
    const z = +$("frameZoom").value;
    const p = state.project || {};
    const [ow, oh] = OUTPUT_SIZES[state.aspect] || [p.width || 1, p.height || 1];
    const sw = p.width || ow, sh = p.height || oh;
    const ch = sw / sh > ow / oh ? sh : sw / (ow / oh);
    const up = Math.min((oh / ch) * z, 2.5);
    $("frameZoomVal").textContent = `${z.toFixed(2)}× · gambar diperbesar ${up.toFixed(1)}× dari sumber${up >= 2 ? " (kurang tajam; maks 2,5×)" : ""}`;
  }

  function setFrameMode(mode, dirty = true) {
    if (!["auto", "face", "material", "manual"].includes(mode)) mode = "auto";
    $("frameMode").value = mode;
    document.querySelectorAll("#frameModeSeg button").forEach((b) => b.classList.toggle("active", b.dataset.v === mode));
    document.querySelectorAll(".frame-opt").forEach((el) => { el.hidden = !el.dataset.modes.split(" ").includes(mode); });
    $("frameModeHint").textContent = FRAME_HINTS[mode];
    zoomHint();
    if (dirty) { markDirty(); if (typeof layoutCropFrame === "function") layoutCropFrame(); }
    refreshSections();
  }

  function overrideAt(seg) {
    const o = (state.frameSections || []).find((x) => Math.abs(x.start - seg.start) < 0.05 && Math.abs(x.end - seg.end) < 0.05);
    return o ? o.layout : "";
  }

  function renderSections(data) {
    const box = $("frameSections");
    lastPlan = data.segments || [];
    if (!state.project || !lastPlan.length) { box.innerHTML = ""; return; }
    const warn = data.analyzed ? "" : '<p class="hint warn">Proyek ini belum punya analisis area materi (proyek lama): klik "Analisis ulang" untuk Fokus Materi.</p>';
    box.innerHTML = `<div class="fs-head">Bagian bingkai klip ini</div>${warn}` + lastPlan.map((seg, i) => `
      <div class="fs-row${seg.manual ? " manual" : ""}" data-i="${i}">
        <span class="fs-time">${fmt(seg.start)}–${fmt(seg.end)}</span>
        <span class="fs-label lay-${esc(seg.layout)}">${esc(seg.label)}</span>
        <select class="fs-pick" aria-label="Tata letak bagian ${i + 1}">
          ${LAYOUTS.map(([v, l]) => `<option value="${v}"${overrideAt(seg) === v ? " selected" : ""}>${l}</option>`).join("")}
        </select>
        <span class="fs-why">${esc(seg.reason)}</span>
      </div>`).join("");
  }

  async function refreshSections() {
    clearTimeout(sectionTimer);
    sectionTimer = setTimeout(async () => {
      if (!state.project || !(state.sel.end > state.sel.start)) { $("frameSections").innerHTML = ""; return; }
      try {
        const q = new URLSearchParams({ start: state.sel.start.toFixed(3), end: state.sel.end.toFixed(3), mode: $("frameMode").value });
        renderSections(await api(`/api/projects/${state.project.id}/framing?${q}`));
      } catch { $("frameSections").innerHTML = ""; }
    }, 250);
  }

  function pickSection(i, layout) {
    const seg = lastPlan[i];
    if (!seg) return;
    const keep = (state.frameSections || []).filter((x) => x.end <= seg.start + 0.05 || x.start >= seg.end - 0.05);
    if (layout) keep.push({ start: Math.round(seg.start * 1000) / 1000, end: Math.round(seg.end * 1000) / 1000, layout });
    state.frameSections = keep.sort((a, b) => a.start - b.start);
    markDirty();
    refreshSections();
  }

  async function previewClip() {
    if (!state.project) return;
    const btn = $("previewClipBtn");
    const t = $("player").currentTime;
    const start = t >= state.sel.start && t < state.sel.end - 1 ? t : state.sel.start;
    const end = Math.min(state.sel.end, start + 10);
    btn.disabled = true;
    $("previewClipState").textContent = "Merender pratinjau…";
    try {
      await flushRecipe();
      const res = await postJSON(`/api/projects/${state.project.id}/preview-clip`, { version: rs.version, start, end });
      const v = $("previewClip");
      $("previewClipBox").hidden = false;
      v.src = res.url;
      v.play().catch(() => {});
      $("previewClipState").textContent = `Pratinjau ${fmt(res.start)}–${fmt(res.end)} (resolusi kecil, tampilan sama dengan hasil render).`;
    } catch (err) {
      $("previewClipState").textContent = "";
      toast(err.message, true);
    } finally { btn.disabled = false; }
  }

  // ---------------------------------------------------------------- M2.2 galeri gaya subtitle
  let presets = [];

  function markPreset(style) {
    document.querySelectorAll(".preset-card").forEach((c) => {
      c.classList.toggle("active", c.dataset.id === style);
      c.setAttribute("aria-selected", c.dataset.id === style ? "true" : "false");
    });
  }

  // ---------------------------------------------------------------- M2.4 gaya per bagian
  let scope = "all";
  const STYLE_LABEL = (id) => (presets.find((p) => p.id === id) || {}).label || id;

  function segmentAt(t) {
    const segs = (state.transcript && state.transcript.segments) || [];
    return segs.find((g) => t >= g.start - 0.05 && t <= g.end + 0.05) ||
      segs.reduce((best, g) => (!best || Math.abs(g.start - t) < Math.abs(best.start - t) ? g : best), null);
  }

  function renderSubSections() {
    const box = $("subSections");
    if (!box) return;
    const list = (state.subSections || []).filter((x) => x.end > state.sel.start && x.start < state.sel.end);
    box.innerHTML = list.length ? `<div class="fs-head">Gaya khusus per kalimat</div>` + list.map((x) => `
      <div class="ss-row"><span class="fs-time">${fmt(x.start)}–${fmt(x.end)}</span><b>${esc(STYLE_LABEL(x.style))}</b>
        <button type="button" class="ghost small" data-start="${x.start}" title="Kembali ke gaya utama">✕</button></div>`).join("") : "";
  }

  function applyPart(id) {
    const seg = segmentAt($("player").currentTime);
    if (!seg) { toast("Tidak ada kalimat di posisi video ini.", true); return; }
    const keep = (state.subSections || []).filter((x) => x.end <= seg.start + 0.01 || x.start >= seg.end - 0.01);
    keep.push({ start: Math.round(seg.start * 1000) / 1000, end: Math.round(seg.end * 1000) / 1000, style: id });
    state.subSections = keep.sort((a, b) => a.start - b.start).slice(0, 60);
    renderSubSections();
    markDirty();
    toast(`Kalimat ${fmt(seg.start)}–${fmt(seg.end)} memakai gaya "${STYLE_LABEL(id)}". Font & warna tetap mengikuti gaya utama.`);
  }

  function applyPreset(id) {
    if (scope === "part") { applyPart(id); return; }
    const p = presets.find((x) => x.id === id);
    if (!p) return;
    const d = p.defaults;
    const hex = (v) => String(v).toLowerCase();
    setControl("subStyle", id);
    if (d.font) setControl("subFont", d.font);
    if (d.uppercase !== undefined) setControl("subUpper", d.uppercase);
    if (d.words_per_line) setControl("subWords", Math.min(6, d.words_per_line));
    if (d.highlight) setControl("subHighlight", hex(d.highlight));
    setControl("subColor", hex(d.color || "#FFFFFF"));
    setControl("subSize", d.size || 1);
    setControl("subOutline", d.outline ?? 1);
    setControl("subShadow", d.shadow ?? 1);
    setControl("subBox", !!d.box);
    setControl("subBoxOpacity", d.box_opacity ?? 0.6);
    setControl("subPosition", d.pos_y != null ? "custom" : "bottom");
    setControl("subPosY", d.pos_y != null ? d.pos_y : 0.76);
    $("subPosYRow").hidden = d.pos_y == null;
    setControl("subEmphasis", true);
    markPreset(id);
    markDirty();
    applyLivePreview();
    if (typeof updateCaptionPreview === "function") updateCaptionPreview();
    toast(`Gaya "${p.label}" dipakai. Klik ▶ Pratinjau hasil untuk melihat animasinya di video Anda.`);
  }

  async function loadPresets() {
    try { presets = await api("/api/subtitle-presets"); } catch { return; }
    $("presetGallery").innerHTML = presets.map((p) => `
      <button type="button" class="preset-card" role="option" data-id="${esc(p.id)}" title="${esc(p.desc)}">
        <span class="pc-media"><img loading="lazy" alt="" src="/api/subtitle-presets/${esc(p.id)}/thumb.png">
          <video muted loop playsinline preload="none" data-src="/api/subtitle-presets/${esc(p.id)}/preview.mp4"></video></span>
        <span class="pc-name">${esc(p.label)}</span>
      </button>`).join("");
    markPreset($("subStyle").value);
  }

  function playCard(card, on) {
    const v = card.querySelector("video");
    if (!v) return;
    if (on) {
      if (!v.src) v.src = v.dataset.src;
      card.classList.add("playing");
      v.play().catch(() => {});
    } else { v.pause(); card.classList.remove("playing"); }
  }

  // ---------------------------------------------------------------- M2.5 gaya otomatis
  let autoRes = null;
  let autoTimer = null;

  function renderAuto() {
    if (!autoRes) return;
    const r = autoRes;
    const sel = $("autoStyleCat");
    if (!sel.options.length) sel.innerHTML = r.options.map((o) => `<option value="${o.id}">${esc(o.label)}</option>`).join("");
    sel.value = r.recommendation.category;
    $("autoStyleText").innerHTML = `Rekomendasi: <b>${esc(r.label)}</b> (keyakinan ${Math.round(r.confidence * 100)}%) — ${esc(r.reasons.join(", "))}.`;
    const rec = r.recommendation;
    $("autoStyleWhy").textContent = `${rec.why} Subtitle: ${STYLE_LABEL(rec.subtitle)}; bingkai: ${({ auto: "Otomatis", face: "Fokus Wajah" })[rec.frame] || rec.frame}; ${rec.items.length} elemen motion.`;
  }

  function refreshAuto(category) {
    clearTimeout(autoTimer);
    autoTimer = setTimeout(async () => {
      if (!state.project || !(state.sel.end > state.sel.start)) return;
      const q = new URLSearchParams({ start: state.sel.start.toFixed(3), end: state.sel.end.toFixed(3) });
      if (category) q.set("category", category);
      try { autoRes = await api(`/api/projects/${state.project.id}/auto-style?${q}`); renderAuto(); } catch { /* abaikan */ }
    }, 400);
  }

  function applyAuto() {
    if (!autoRes) return;
    const rec = autoRes.recommendation;
    const prev = scope;
    scope = "all";
    applyPreset(rec.subtitle);
    scope = prev;
    setFrameMode(rec.frame);
    setControl("colorSelect", rec.look);
    applyLivePreview();
    let added = 0;
    if ($("autoStyleMotion").checked) {
      for (const it of rec.items) {
        const clash = state.plan.some((p) => p.type === it.type && (it.type !== "fx" || p.preset === it.preset)
          && p.end > it.start && p.start < it.end);
        if (clash) continue;
        state.plan.push({ ...it, id: Math.random().toString(16).slice(2, 14) });
        added++;
      }
      state.plan.sort((a, b) => a.start - b.start);
      renderPlan();
      savePlanSoon();
    }
    markDirty();
    toast(`Gaya "${rec.label}" diterapkan${added ? ` + ${added} elemen motion (edit di tab Animasi)` : ""}. Klik ↶ untuk membatalkan.`);
  }

  // ---------------------------------------------------------------- safe-zone grafis
  let placeTimer = null;
  function refreshPlacement(delay = 900) {
    clearTimeout(placeTimer);
    placeTimer = setTimeout(async () => {
      if (!state.project || !(state.sel.end - state.sel.start >= 1)) return;
      const q = new URLSearchParams({ start: state.sel.start.toFixed(3), end: state.sel.end.toFixed(3) });
      try {
        const res = await api(`/api/projects/${state.project.id}/motion-layout?${q}`);
        state.placement = res.items || {};
        renderPlan();
        $("ovPreview").dataset.key = "";
        if (typeof updateOverlayPreview === "function") updateOverlayPreview();
      } catch { /* abaikan */ }
    }, delay);
  }

  // ---------------------------------------------------------------- M2.4 undo / redo
  // Riwayat pengaturan gaya, bingkai, subtitle, dan animasi di perangkat ini (bukan teks transkrip: koreksi
  // transkrip M1.1 punya alurnya sendiri dan tidak ikut dibatalkan).
  const SERVER_ONLY = ["text_fixes", "seg_edits", "reviewed", "vocab_ignored", "overlays"];
  const hist = { stack: [], i: -1, restoring: false, timer: null };

  function snapshot() {
    const r = recipeFromControls();
    for (const k of SERVER_ONLY) delete r[k];
    r.__plan = JSON.parse(JSON.stringify(state.plan || []));
    return JSON.stringify(r);
  }

  function updateUndoButtons() {
    $("undoBtn").disabled = hist.i <= 0;
    $("redoBtn").disabled = hist.i >= hist.stack.length - 1;
  }

  function record() {
    if (hist.restoring || !state.project) return;
    const snap = snapshot();
    if (hist.stack[hist.i] === snap) return;
    hist.stack = hist.stack.slice(0, hist.i + 1);
    hist.stack.push(snap);
    if (hist.stack.length > 100) hist.stack.shift();
    hist.i = hist.stack.length - 1;
    updateUndoButtons();
  }

  function onDirty() {
    refreshPlacement(1500);                     // setelah tersimpan: posisi grafis dihitung ulang
    if (hist.restoring) return;
    clearTimeout(hist.timer);
    hist.timer = setTimeout(record, 350);       // satu langkah per rangkaian perubahan (mis. menggeser slider)
  }

  function resetHistory() {
    hist.stack = [];
    hist.i = -1;
    clearTimeout(hist.timer);
    setTimeout(record, 600);                    // keadaan awal setelah resep dimuat
    updateUndoButtons();
  }

  function restore(delta) {
    clearTimeout(hist.timer);
    record();
    const j = hist.i + delta;
    if (j < 0 || j >= hist.stack.length) return;
    hist.i = j;
    const r = JSON.parse(hist.stack[j]);
    const plan = r.__plan;
    delete r.__plan;
    hist.restoring = true;
    try {
      const full = { ...(rs.recipe || {}), ...r };
      applyRecipeToControls(full);
      if (full.selection) setSelection(full.selection.start, full.selection.end);
      state.plan = plan;
      renderPlan();
      savePlanSoon();
      $("ovPreview").dataset.key = "";
      refreshSections();
      renderSubSections();
      markDirty();
    } finally { hist.restoring = false; }
    updateUndoButtons();
  }

  function setup() {
    $("autoStyleCat").addEventListener("change", (e) => refreshAuto(e.target.value));
    $("autoStyleApply").addEventListener("click", applyAuto);
    $("undoBtn").addEventListener("click", () => restore(-1));
    $("redoBtn").addEventListener("click", () => restore(1));
    document.addEventListener("keydown", (e) => {
      if (!(e.ctrlKey || e.metaKey) || !state.project) return;
      const tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" && e.target.type === "text" || tag === "textarea" || e.target.isContentEditable) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) { e.preventDefault(); restore(-1); }
      else if ((k === "z" && e.shiftKey) || k === "y") { e.preventDefault(); restore(1); }
    });
    $("presetScope").addEventListener("click", (e) => {
      if (!e.target.dataset.v) return;
      scope = e.target.dataset.v;
      document.querySelectorAll("#presetScope button").forEach((b) => b.classList.toggle("active", b.dataset.v === scope));
    });
    $("subSections").addEventListener("click", (e) => {
      const st = e.target.dataset.start;
      if (st === undefined) return;
      state.subSections = (state.subSections || []).filter((x) => Math.abs(x.start - +st) > 0.001);
      renderSubSections();
      markDirty();
    });
    const gal = $("presetGallery");
    gal.addEventListener("click", (e) => { const c = e.target.closest(".preset-card"); if (c) applyPreset(c.dataset.id); });
    gal.addEventListener("mouseover", (e) => { const c = e.target.closest(".preset-card"); if (c) playCard(c, true); });
    gal.addEventListener("mouseout", (e) => { const c = e.target.closest(".preset-card"); if (c && !c.contains(e.relatedTarget)) playCard(c, false); });
    gal.addEventListener("focusin", (e) => { const c = e.target.closest(".preset-card"); if (c) playCard(c, true); });
    gal.addEventListener("focusout", (e) => { const c = e.target.closest(".preset-card"); if (c) playCard(c, false); });
    $("subStyle").addEventListener("change", () => markPreset($("subStyle").value));
    $("subPosition").addEventListener("change", () => { $("subPosYRow").hidden = $("subPosition").value !== "custom"; });
    loadPresets();
    $("frameModeSeg").addEventListener("click", (e) => { if (e.target.dataset.v) setFrameMode(e.target.dataset.v); });
    $("frameZoom").addEventListener("input", zoomHint);
    $("frameSections").addEventListener("change", (e) => {
      if (e.target.classList.contains("fs-pick")) pickSection(+e.target.closest(".fs-row").dataset.i, e.target.value);
    });
    $("previewClipBtn").addEventListener("click", previewClip);
    $("previewClipClose").addEventListener("click", () => { const v = $("previewClip"); v.pause(); $("previewClipBox").hidden = true; });
    setFrameMode($("frameMode").value || "auto", false);
  }

  window.Studio = { setFrameMode, onSelection: () => { refreshSections(); renderSubSections(); refreshAuto(); refreshPlacement(); }, refreshSections,
    markPreset, applyPreset, renderSubSections, onDirty, resetHistory, restore, refreshPlacement };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup); else setup();
})();
