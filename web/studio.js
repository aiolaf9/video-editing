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

  function setup() {
    $("frameModeSeg").addEventListener("click", (e) => { if (e.target.dataset.v) setFrameMode(e.target.dataset.v); });
    $("frameZoom").addEventListener("input", () => { $("frameZoomVal").textContent = `${(+$("frameZoom").value).toFixed(2)}×`; });
    $("frameSections").addEventListener("change", (e) => {
      if (e.target.classList.contains("fs-pick")) pickSection(+e.target.closest(".fs-row").dataset.i, e.target.value);
    });
    $("previewClipBtn").addEventListener("click", previewClip);
    $("previewClipClose").addEventListener("click", () => { const v = $("previewClip"); v.pause(); $("previewClipBox").hidden = true; });
    setFrameMode($("frameMode").value || "auto", false);
  }

  window.Studio = { setFrameMode, onSelection: refreshSections, refreshSections };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup); else setup();
})();
