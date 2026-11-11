// M2.5.1 Area Dilindungi: editor menandai bagian gambar (kotak atau poligon) yang tidak boleh tertutup grafis motion
// selama rentang waktu tertentu. Koordinat disimpan relatif terhadap video SUMBER (0..1), sehingga tetap benar
// apa pun format/bingkai keluarannya; safe-zone server memetakannya lewat geometri bingkai yang sama dengan wajah.
// Area tetap tidak mengikuti gerakan subjek: untuk subjek bergerak, pisah area di beberapa titik waktu lalu
// gambar ulang tiap rentang ("Pisah di sini" + "Gambar ulang"). Saran otomatis (eksperimental) hanya mengusulkan
// area per 2 dtk dari deteksi objek lokal; editor tetap memeriksa.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let mode = null;          // null | {kind: "rect"|"poly", target: id|null}
  let draft = null;         // titik sementara (koordinat sumber)
  const DEFAULT_LEN = 4;    // dtk: area baru berlaku 4 dtk dari posisi video (subjek biasanya bergerak)

  function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]); }
  function newId() { return "p" + Math.random().toString(16).slice(2, 12); }
  function areas() { return state.protect || (state.protect = []); }
  function fmt(t) { return (+t).toFixed(1); }

  function toSource(ev) {
    const r = displayedVideoRect();
    if (!r) return null;
    const wrap = $("playerWrap").getBoundingClientRect();
    const x = (ev.clientX - wrap.left - r.x) / r.w, y = (ev.clientY - wrap.top - r.y) / r.h;
    return [Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))];
  }

  function boxOf(pts) {
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x = Math.min(...xs), y = Math.min(...ys);
    return [x, y, Math.max(...xs) - x, Math.max(...ys) - y].map((v) => +v.toFixed(4));
  }

  function commit(pts, kind) {
    const box = boxOf(pts);
    if (box[2] < 0.01 || box[3] < 0.01) { toast("Area terlalu kecil. Seret lebih lebar.", true); return; }
    const t = $("player").currentTime;
    const s0 = state.sel.start, s1 = state.sel.end;
    if (mode && mode.target) {
      const a = areas().find((x) => x.id === mode.target);
      if (a) { a.box = box; a.points = kind === "poly" ? pts.map((p) => p.map((v) => +v.toFixed(4))) : []; }
    } else {
      const start = Math.max(s0, Math.min(t, s1 - 0.5));
      areas().push({ id: newId(), start: +start.toFixed(2), end: +Math.min(s1, start + DEFAULT_LEN).toFixed(2), box,
        points: kind === "poly" ? pts.map((p) => p.map((v) => +v.toFixed(4))) : [], label: "" });
      areas().sort((a, b) => a.start - b.start);
    }
    stop();
    changed();
  }

  function changed() {
    render();
    markDirty();                       // resep tersimpan -> riwayat undo, safe-zone dihitung ulang, pratinjau = MP4
  }

  function start(kind, target = null) {
    if (!state.project) return;
    $("player").pause();
    mode = { kind, target };
    draft = [];
    $("protectLayer").classList.add("drawing");
    $("protectHint").textContent = kind === "rect"
      ? "Seret di video untuk menggambar kotak area dilindungi. Esc = batal."
      : "Klik titik-titik di sekeliling subjek, klik dua kali (atau tombol Selesai) untuk menutup. Esc = batal.";
    $("protectDone").hidden = kind !== "poly";
    drawOverlay();
  }

  function stop() {
    mode = null;
    draft = null;
    $("protectLayer").classList.remove("drawing");
    $("protectHint").textContent = "";
    $("protectDone").hidden = true;
    drawOverlay();
  }

  // ---- gambar di atas video
  function drawOverlay() {
    const svg = $("protectSvg");
    const r = displayedVideoRect();
    if (!svg) return;
    if (!r || !$("protectShow").checked && !mode) { svg.innerHTML = ""; return; }
    const t = $("player").currentTime;
    const P = (p) => `${(r.x + p[0] * r.w).toFixed(1)},${(r.y + p[1] * r.h).toFixed(1)}`;
    const shape = (a, cls) => {
      const pts = a.points && a.points.length >= 3 ? a.points
        : [[a.box[0], a.box[1]], [a.box[0] + a.box[2], a.box[1]], [a.box[0] + a.box[2], a.box[1] + a.box[3]], [a.box[0], a.box[1] + a.box[3]]];
      return `<polygon class="${cls}" points="${pts.map(P).join(" ")}"/>` +
        `<text x="${(r.x + a.box[0] * r.w + 4).toFixed(0)}" y="${(r.y + a.box[1] * r.h + 14).toFixed(0)}">🛡 ${esc(a.label || "dilindungi")}</text>`;
    };
    let html = areas().filter((a) => t >= a.start && t < a.end && (!mode || a.id !== mode.target))
      .map((a) => shape(a, "pa")).join("");
    if (draft && draft.length) {
      if (mode.kind === "rect" && draft.length === 2) html += shape({ box: boxOf(draft), points: [] }, "pa draft");
      else html += `<polyline class="pa draft" points="${draft.map(P).join(" ")}"/>`;
    }
    svg.setAttribute("width", $("playerWrap").clientWidth);
    svg.setAttribute("height", $("playerWrap").clientHeight);
    svg.innerHTML = html;
  }

  // ---- daftar area
  function render() {
    const list = $("protectList");
    if (!list) return;
    const s0 = state.sel ? state.sel.start : 0, s1 = state.sel ? state.sel.end : 0;
    const mine = areas().filter((a) => a.end > s0 && a.start < s1);
    const outside = areas().length - mine.length;
    $("protectCount").textContent = areas().length ? `(${areas().length})` : "";
    list.innerHTML = mine.map((a) => `
      <li class="plan-item protect-item" data-id="${esc(a.id)}">
        <span class="type">🛡 ${a.points && a.points.length ? "Poligon" : "Kotak"}</span>
        <span class="times"><input type="number" step="0.1" data-k="start" value="${fmt(a.start)}"> – <input type="number" step="0.1" data-k="end" value="${fmt(a.end)}"></span>
        <span class="fields"><input data-k="label" value="${esc(a.label)}" placeholder="Nama subjek (mis. sapi, mobil bantuan)"></span>
        <span class="pa-actions">
          <button class="ghost" data-act="seek" title="Putar dari awal area">▶</button>
          <button class="ghost" data-act="split" title="Subjek bergerak: pisah area di posisi video, lalu gambar ulang bagian berikutnya">✂ Pisah di sini</button>
          <button class="ghost" data-act="redraw" title="Gambar ulang kotak area ini (waktu tetap)">✎ Gambar ulang</button>
          <button class="ghost" data-act="clip" title="Berlaku sepanjang klip">↔ Seluruh klip</button>
          <button class="ghost danger" data-act="del" title="Hapus area">✕</button>
        </span>
      </li>`).join("") || '<li class="muted">Belum ada area dilindungi di klip ini.</li>';
    if (outside) list.insertAdjacentHTML("beforeend", `<li class="muted small">${outside} area lain di luar klip terpilih.</li>`);
    drawOverlay();
  }

  async function suggest() {
    if (!state.project) return;
    const btn = $("protectSuggestBtn");
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = "⏳ Mendeteksi di komputer ini...";
    const s0 = state.sel.start, s1 = state.sel.end;
    try {
      const res = await postJSON(`/api/projects/${state.project.id}/protect/suggest`,
        { start: s0, end: s1, groups: [$("protectGroup").value] });
      // saran lama di rentang ini diganti (tidak menumpuk); area buatan editor tidak disentuh
      state.protect = areas().filter((a) => !(String(a.label).startsWith("Saran:") && a.end > s0 && a.start < s1));
      for (const a of res.areas) areas().push({ ...a, id: newId() });
      areas().sort((x, y) => x.start - y.start);
      changed();
      toast(res.areas.length
        ? `${res.areas.length} saran area dari ${res.seen}/${res.samples} sampel yang berisi subjek. Periksa tiap saran (putar ▶) lalu ubah/hapus bila keliru.`
        : `Tidak ada subjek terdeteksi (${res.samples} sampel). Tandai manual dengan + Kotak.`);
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; btn.textContent = old; }
  }

  function setup() {
    const layer = $("protectLayer");
    if (!layer) return;
    $("protectRectBtn").addEventListener("click", () => start("rect"));
    $("protectPolyBtn").addEventListener("click", () => start("poly"));
    $("protectDone").addEventListener("click", () => { if (draft && draft.length >= 3) commit(draft, "poly"); });
    $("protectShow").addEventListener("change", drawOverlay);
    $("protectSuggestBtn").addEventListener("click", suggest);
    layer.addEventListener("pointerdown", (e) => {
      if (!mode) return;
      const p = toSource(e);
      if (!p) return;
      e.preventDefault();
      if (mode.kind === "rect") { draft = [p, p]; layer.setPointerCapture(e.pointerId); }
      else { draft.push(p); }
      drawOverlay();
    });
    layer.addEventListener("pointermove", (e) => {
      if (!mode || mode.kind !== "rect" || !draft || draft.length !== 2 || !(e.buttons & 1)) return;
      const p = toSource(e);
      if (p) { draft[1] = p; drawOverlay(); }
    });
    layer.addEventListener("pointerup", () => {
      if (mode && mode.kind === "rect" && draft && draft.length === 2) commit(draft, "rect");
    });
    layer.addEventListener("dblclick", (e) => {
      if (mode && mode.kind === "poly" && draft) {
        e.preventDefault();
        const pts = draft.filter((p, i) => i === 0 || Math.hypot(p[0] - draft[i - 1][0], p[1] - draft[i - 1][1]) > 0.002);
        if (pts.length >= 3) commit(pts, "poly"); else toast("Poligon butuh minimal 3 titik.", true);
      }
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && mode) stop(); });
    $("protectList").addEventListener("change", (e) => {
      const li = e.target.closest("li[data-id]");
      const a = li && areas().find((x) => x.id === li.dataset.id);
      const k = e.target.dataset.k;
      if (!a || !k) return;
      if (k === "label") a.label = e.target.value.slice(0, 40);
      else {
        const v = +e.target.value;
        if (k === "start" && v < a.end - 0.1) a.start = +v.toFixed(2);
        if (k === "end" && v > a.start + 0.1) a.end = +v.toFixed(2);
      }
      changed();
    });
    $("protectList").addEventListener("click", (e) => {
      const act = e.target.dataset.act;
      const li = e.target.closest("li[data-id]");
      const a = li && areas().find((x) => x.id === li.dataset.id);
      if (!a || !act) return;
      const t = $("player").currentTime;
      if (act === "seek") { $("player").currentTime = a.start; $("player").play().catch(() => {}); }
      else if (act === "del") { state.protect = areas().filter((x) => x !== a); changed(); }
      else if (act === "clip") { a.start = +state.sel.start.toFixed(2); a.end = +state.sel.end.toFixed(2); changed(); }
      else if (act === "redraw") { $("player").currentTime = Math.max(a.start, Math.min(t, a.end - 0.05)); start(a.points && a.points.length ? "poly" : "rect", a.id); }
      else if (act === "split") {
        if (t <= a.start + 0.2 || t >= a.end - 0.2) { toast("Putar video ke titik di DALAM rentang area ini lalu klik Pisah.", true); return; }
        const next = { ...a, id: newId(), start: +t.toFixed(2), box: [...a.box], points: (a.points || []).map((p) => [...p]) };
        a.end = +t.toFixed(2);
        areas().push(next);
        areas().sort((x, y) => x.start - y.start);
        changed();
        toast("Area dipisah. Sekarang gambar ulang kotak untuk posisi subjek di bagian berikutnya.");
        start(next.points.length ? "poly" : "rect", next.id);
      }
    });
    $("player").addEventListener("timeupdate", drawOverlay);
    $("player").addEventListener("seeked", drawOverlay);
    window.addEventListener("resize", drawOverlay);
  }

  window.Protect = { render, drawOverlay, start, stop, areas };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup); else setup();
})();
