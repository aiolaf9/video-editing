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

  function setFrameMode(mode, dirty = true) {
    if (!["auto", "face", "material", "manual"].includes(mode)) mode = "auto";
    $("frameMode").value = mode;
    document.querySelectorAll("#frameModeSeg button").forEach((b) => b.classList.toggle("active", b.dataset.v === mode));
    document.querySelectorAll(".frame-opt").forEach((el) => { el.hidden = !el.dataset.modes.split(" ").includes(mode); });
    $("frameModeHint").textContent = FRAME_HINTS[mode];
    $("frameZoomVal").textContent = `${(+$("frameZoom").value).toFixed(2)}×`;
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

  function applyPreset(id) {
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

  function setup() {
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
    $("frameZoom").addEventListener("input", () => { $("frameZoomVal").textContent = `${(+$("frameZoom").value).toFixed(2)}×`; });
    $("frameSections").addEventListener("change", (e) => {
      if (e.target.classList.contains("fs-pick")) pickSection(+e.target.closest(".fs-row").dataset.i, e.target.value);
    });
    $("previewClipBtn").addEventListener("click", previewClip);
    $("previewClipClose").addEventListener("click", () => { const v = $("previewClip"); v.pause(); $("previewClipBox").hidden = true; });
    setFrameMode($("frameMode").value || "auto", false);
  }

  window.Studio = { setFrameMode, onSelection: refreshSections, refreshSections, markPreset, applyPreset };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup); else setup();
})();
