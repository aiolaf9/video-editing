"use strict";
// Editor subtitle M1.1: edit teks langsung, ubah waktu, cari & ganti, kata yang perlu ditinjau, saran istilah
// (selalu menunggu persetujuan), tanda "sudah ditinjau", dan konfirmasi sebelum render. Memakai state/api/$ dari
// app.js. Kata tetap memakai kelas .w[data-i] (indeks global state.words) untuk sorotan saat video diputar.

const UNSURE_P = 0.5;
const sub = { filter: "all", suggestions: [], found: null, editing: null, times: null, suggTimer: null };

function subPid() { return state.project && state.project.id; }

function isUnsure(seg, w) { return !seg.reviewed && w.p !== undefined && w.p < UNSURE_P; }

function fmtT(t) {
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}

function rowVisible(seg, si, suggBySeg) {
  if (sub.found && !sub.found.has(si)) return false;
  if (sub.filter === "unsure") return (seg.words || []).some((w) => isUnsure(seg, w));
  if (sub.filter === "clip") return seg.end > state.sel.start && seg.start < state.sel.end;
  if (sub.filter === "sugg") return (suggBySeg[si] || []).length > 0;
  return true;
}

function renderTranscript() {
  const box = $("transcriptBox");
  const segs = state.transcript.segments || [];
  if (!segs.length) {
    box.innerHTML = '<p class="muted">Tidak ada ucapan yang terdeteksi.</p>';
    updateSubCounts();
    return;
  }
  const suggBySeg = {};
  for (const s of sub.suggestions) (suggBySeg[s.seg] = suggBySeg[s.seg] || []).push(s);
  let idx = 0;
  const rows = [];
  segs.forEach((seg, si) => {
    const words = segWords(seg);
    const first = idx;
    idx += words.length;
    if (!rowVisible(seg, si, suggBySeg)) return;
    const html = words.map((w, k) => {
      const unsure = isUnsure(seg, w);
      const tip = unsure ? ` title="Whisper kurang yakin (${Math.round(w.p * 100)}%). Dengarkan lalu koreksi bila perlu."` : "";
      return `<span class="w${unsure ? " unsure" : ""}" data-i="${first + k}" data-t="${w.start}"${tip}>${esc(w.word)}</span>`;
    }).join(" ");
    const sugg = (suggBySeg[si] || []).map((s) =>
      `<span class="sugg" data-id="${esc(s.id)}" title="${s.reason === "ejaan" ? "Varian ejaan di kosakata" : "Mirip istilah kosakata & Whisper kurang yakin"}">` +
      `<s>${esc(s.found)}</s> → <b>${esc(s.replacement)}</b>` +
      `<button type="button" class="sugg-ok" data-act="accept" data-id="${esc(s.id)}" aria-label="Terima">✓</button>` +
      `<button type="button" class="sugg-no" data-act="ignore" data-id="${esc(s.id)}" aria-label="Tolak">✕</button></span>`).join("");
    const times = sub.times === si ? wordTimesPanel(seg, si) : "";
    rows.push(`<div class="sub-row${seg.reviewed ? " reviewed" : ""}${seg.edited ? " edited" : ""}" data-s="${si}">
      <div class="sub-time">
        <button type="button" class="ghost icon" data-act="play" title="Putar baris ini" aria-label="Putar">▶</button>
        <span class="t-edit"><button type="button" class="nudge" data-act="start-" title="Awal −0,1 dtk">−</button><span class="t-val" title="Awal">${fmtT(seg.start)}</span><button type="button" class="nudge" data-act="start+" title="Awal +0,1 dtk">+</button></span>
        <span class="t-sep">→</span>
        <span class="t-edit"><button type="button" class="nudge" data-act="end-" title="Akhir −0,1 dtk">−</button><span class="t-val" title="Akhir">${fmtT(seg.end)}</span><button type="button" class="nudge" data-act="end+" title="Akhir +0,1 dtk">+</button></span>
        <button type="button" class="ghost icon" data-act="setstart" title="Awal = posisi video sekarang">⇤</button>
        <button type="button" class="ghost icon" data-act="setend" title="Akhir = posisi video sekarang">⇥</button>
        <button type="button" class="ghost icon" data-act="times" title="Atur waktu per kata">⏱</button>
      </div>
      <div class="sub-text" data-act="edit" tabindex="0" role="textbox" aria-label="Teks subtitle, klik untuk mengedit">${html}</div>
      ${sugg ? `<div class="sub-sugg">${sugg}</div>` : ""}
      <div class="sub-side"><button type="button" class="rev${seg.reviewed ? " on" : ""}" data-act="review" aria-pressed="${!!seg.reviewed}">${seg.reviewed ? "✓ Ditinjau" : "Tandai ditinjau"}</button></div>
      ${times}
    </div>`);
  });
  let head = "";
  if (sub.filter === "sugg" && sub.suggestions.length) head = suggestionSummary();
  if (sub.filter === "unsure" && rows.length) {
    head = `<div class="bulk-bar"><span>${rows.length} baris perlu ditinjau.</span>
      <button type="button" class="ghost small" id="reviewAllBtn">Tandai semua yang tampil sudah ditinjau</button></div>`;
  }
  box.innerHTML = head + (rows.join("") || '<p class="muted">Tidak ada baris untuk filter ini.</p>');
  updateSubCounts();
  highlightTranscriptRange();
}

function suggestionSummary() {
  const byTerm = {};
  for (const s of sub.suggestions) (byTerm[s.term] = byTerm[s.term] || []).push(s);
  return `<div class="bulk-bar">${Object.entries(byTerm).map(([term, items]) =>
    `<span class="bulk-term"><b>${esc(term)}</b> ×${items.length}
      <button type="button" class="ghost small" data-bulk="accept" data-term="${esc(term)}">Terima semua</button>
      <button type="button" class="ghost small" data-bulk="ignore" data-term="${esc(term)}">Tolak semua</button></span>`).join("")}</div>`;
}

function wordTimesPanel(seg, si) {
  const words = seg.words || [];
  if (!words.length) return "";
  return `<div class="word-times" data-s="${si}">${words.map((w, i) =>
    `<label class="wt"><span>${esc(w.word)}</span>
      <input type="number" step="0.05" min="0" data-wi="${i}" data-k="start" value="${w.start.toFixed(2)}" aria-label="Awal ${esc(w.word)}">
      <input type="number" step="0.05" min="0" data-wi="${i}" data-k="end" value="${w.end.toFixed(2)}" aria-label="Akhir ${esc(w.word)}"></label>`).join("")}
    <div class="row"><button type="button" class="primary small" data-act="times-save">Simpan waktu kata</button>
    <button type="button" class="ghost small" data-act="times-close">Tutup</button></div></div>`;
}

function updateSubCounts() {
  const segs = state.transcript.segments || [];
  const n = segs.reduce((a, seg) => a + (seg.words || []).filter((w) => isUnsure(seg, w)).length, 0);
  $("unsureCount").textContent = n ? `(${n})` : "";
  $("suggCount").textContent = sub.suggestions.length ? `(${sub.suggestions.length})` : "";
}

async function applyTranscriptResponse(tr) {
  state.transcript = tr;
  // Saran lama bisa tidak berlaku lagi untuk teks baru: sembunyikan sampai daftar terbaru dimuat.
  sub.suggestions = sub.suggestions.filter((s) => {
    const seg = tr.segments[s.seg];
    return seg && segWords(seg).map((w) => w.word).join(" ").includes(s.found);
  });
  state.words = tr.segments.flatMap(segWords);
  renderTranscript();
  updateCaptionPreview();
  scheduleSuggestions();
}

async function putTranscript(body) {
  const tr = await api(`/api/projects/${subPid()}/transcript`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  await applyTranscriptResponse(tr);
  return tr;
}

function scheduleSuggestions() {
  clearTimeout(sub.suggTimer);
  sub.suggTimer = setTimeout(loadSuggestions, 300);
}

async function loadSuggestions() {
  if (!subPid()) return;
  try {
    sub.suggestions = await api(`/api/projects/${subPid()}/transcript/suggestions`);
  } catch { sub.suggestions = []; }
  renderTranscript();
}

// ------------------------------------------------------------ edit teks langsung
function startEdit(si, caretWord) {
  const seg = state.transcript.segments[si];
  const row = document.querySelector(`#transcriptBox .sub-row[data-s="${si}"]`);
  if (!seg || !row) return;
  if (sub.editing && sub.editing.si !== si) finishEdit(true);
  const el = row.querySelector(".sub-text");
  const text = segWords(seg).map((w) => w.word).join(" ");
  sub.editing = { si, original: text };
  el.textContent = text;
  el.contentEditable = "plaintext-only";
  if (el.contentEditable !== "plaintext-only") el.contentEditable = "true";
  el.classList.add("editing");
  el.focus();
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  if (caretWord !== undefined && el.firstChild) {   // kursor di kata yang diklik
    const words = text.split(" ");
    const pos = words.slice(0, caretWord + 1).join(" ").length;
    range.setStart(el.firstChild, Math.min(pos, el.firstChild.length));
    range.collapse(true);
  }
  const selObj = window.getSelection();
  selObj.removeAllRanges();
  selObj.addRange(range);
}

async function finishEdit(save, next = 0) {
  const ed = sub.editing;
  if (!ed) return;
  sub.editing = null;
  const row = document.querySelector(`#transcriptBox .sub-row[data-s="${ed.si}"]`);
  const el = row && row.querySelector(".sub-text");
  const text = el ? el.textContent.replace(/\s+/g, " ").trim() : ed.original;
  let target = null;
  if (next) {
    const rows = [...document.querySelectorAll("#transcriptBox .sub-row")].map((r) => +r.dataset.s);
    target = rows[rows.indexOf(ed.si) + next];
  }
  if (save && text !== ed.original) {
    try {
      await putTranscript({ segments: [{ index: ed.si, text }] });
    } catch (err) { toast(err.message, true); renderTranscript(); }
  } else {
    renderTranscript();
  }
  if (target !== undefined && target !== null) startEdit(target);
}

// ------------------------------------------------------------ waktu
async function nudge(si, which, delta) {
  const seg = state.transcript.segments[si];
  let start = seg.start, end = seg.end;
  if (which === "start") start = Math.max(0, +(start + delta).toFixed(3));
  else end = +(end + delta).toFixed(3);
  if (end - start < 0.1) { toast("Durasi subtitle terlalu pendek.", true); return; }
  try { await putTranscript({ segments: [{ index: si, start, end }] }); } catch (err) { toast(err.message, true); }
}

async function saveWordTimes(si) {
  const panel = document.querySelector(`.word-times[data-s="${si}"]`);
  const times = {};
  panel.querySelectorAll("input[data-wi]").forEach((inp) => {
    const i = +inp.dataset.wi;
    times[i] = times[i] || { i };
    times[i][inp.dataset.k] = +inp.value;
  });
  try {
    sub.times = null;
    await putTranscript({ segments: [{ index: si, word_times: Object.values(times) }] });
    toast("Waktu kata disimpan.");
  } catch (err) { sub.times = si; toast(err.message, true); }
}

// ------------------------------------------------------------ cari & ganti
function findBody(dry) {
  return { find: $("findInput").value, replace: $("replaceInput").value, whole_word: $("findWhole").checked,
    case_sensitive: $("findCase").checked, dry_run: dry };
}

async function runFind() {
  const q = $("findInput").value.trim();
  if (!q) { sub.found = null; $("findResult").hidden = true; $("replaceAllBtn").disabled = true; renderTranscript(); return; }
  try {
    const r = await postJSON(`/api/projects/${subPid()}/transcript/replace`, findBody(true));
    sub.found = new Set(r.preview.map((p) => p.index));
    $("findResult").hidden = false;
    $("findResult").textContent = r.count ? `${r.count} baris cocok${r.count > r.preview.length ? ` (menampilkan ${r.preview.length})` : ""}. Periksa, lalu "Ganti semua" bila sudah benar.`
      : "Tidak ditemukan.";
    $("replaceAllBtn").disabled = !r.count;
    renderTranscript();
  } catch (err) { toast(err.message, true); }
}

async function replaceAll() {
  const body = findBody(false);
  const n = sub.found ? sub.found.size : 0;
  if (!confirm(`Ganti "${body.find}" menjadi "${body.replace}" di ${n} baris? Timing kata lain tidak berubah.`)) return;
  try {
    const r = await postJSON(`/api/projects/${subPid()}/transcript/replace`, body);
    sub.found = null;
    $("findResult").textContent = `${r.count} baris diganti.`;
    $("replaceAllBtn").disabled = true;
    await applyTranscriptResponse(r.transcript);
  } catch (err) { toast(err.message, true); }
}

// ------------------------------------------------------------ saran istilah
async function decide(accept, ignore) {
  try {
    const tr = await postJSON(`/api/projects/${subPid()}/transcript/suggestions`, { accept, ignore });
    await applyTranscriptResponse(tr);
  } catch (err) {
    toast(err.message, true);
    loadSuggestions();
  }
}

// ------------------------------------------------------------ tinjau sebelum render
function confirmReviewed(ranges) {
  return (async () => {
    if (!subPid()) return true;
    let words = [];
    for (const r of ranges) {
      try {
        const res = await api(`/api/projects/${subPid()}/transcript/review?start=${r.start}&end=${r.end}`);
        words = words.concat(res.words);
      } catch { return true; }   // pemeriksaan hanya bantuan: jangan menghalangi render
    }
    if (!words.length || !$("subEnabled").checked) return true;
    const dlg = $("reviewDialog");
    $("reviewText").textContent = `${words.length} kata di klip ini belum ditinjau (Whisper kurang yakin). Subtitle MP4 memakai teks apa adanya.`;
    $("reviewList").innerHTML = words.slice(0, 12).map((w) =>
      `<li><span class="ts">${fmtT(w.start)}</span> <b>${esc(w.word)}</b> <span class="muted">(${Math.round((w.p || 0) * 100)}%)</span></li>`).join("")
      + (words.length > 12 ? `<li class="muted">… dan ${words.length - 12} lainnya</li>` : "");
    return await new Promise((resolve) => {
      const done = (ok) => { dlg.close(); $("reviewGoBtn").onclick = $("reviewRenderBtn").onclick = null; resolve(ok); };
      $("reviewGoBtn").onclick = () => {
        done(false);
        switchTab("transcriptTab");
        setSubFilter("unsure");
      };
      $("reviewRenderBtn").onclick = () => done(true);
      dlg.onclose = () => resolve(false);
      dlg.showModal();
    });
  })();
}

function setSubFilter(f) {
  sub.filter = f;
  document.querySelectorAll(".filter-row .chip").forEach((c) => c.classList.toggle("active", c.dataset.filter === f));
  renderTranscript();
}

// ------------------------------------------------------------ kosakata
async function openVocab() {
  let v;
  try { v = await api("/api/vocab"); } catch (err) { toast(err.message, true); return; }
  $("vocabDefaults").checked = v.use_defaults;
  $("vocabDefaultCount").textContent = v.defaults.length;
  $("vocabRows").innerHTML = "";
  v.entries.forEach(addVocabRow);
  if (!v.entries.length) addVocabRow();
  const editable = v.can_edit;
  $("vocabDialog").querySelectorAll("input, select, #vocabAddBtn, #vocabSaveBtn, .vocab-del").forEach((el) => { el.disabled = !editable; });
  $("vocabDialog").showModal();
}

function addVocabRow(e = { term: "", variants: [], kind: "istilah" }) {
  const kinds = [["istilah", "Istilah"], ["arab", "Arab"], ["lembaga", "Lembaga"], ["program", "Program"], ["nama", "Nama orang"], ["lain", "Lain"]];
  const tr = document.createElement("tr");
  tr.innerHTML = `<td><input type="text" class="v-term" maxlength="60" value="${esc(e.term)}" placeholder="mis. Sedekah Subuh"></td>
    <td><input type="text" class="v-var" maxlength="400" value="${esc((e.variants || []).join(", "))}" placeholder="mis. sedekah subu"></td>
    <td><select class="v-kind">${kinds.map(([k, l]) => `<option value="${k}"${k === e.kind ? " selected" : ""}>${l}</option>`).join("")}</select></td>
    <td><button type="button" class="ghost small vocab-del" aria-label="Hapus">✕</button></td>`;
  $("vocabRows").appendChild(tr);
}

async function saveVocab() {
  const entries = [...$("vocabRows").querySelectorAll("tr")].map((tr) => ({
    term: tr.querySelector(".v-term").value.trim(),
    variants: tr.querySelector(".v-var").value.split(",").map((x) => x.trim()).filter(Boolean),
    kind: tr.querySelector(".v-kind").value,
  })).filter((e) => e.term);
  try {
    await api("/api/vocab", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries, use_defaults: $("vocabDefaults").checked }) });
    $("vocabDialog").close();
    toast("Kosakata disimpan. Saran istilah diperbarui; petunjuk ejaan Whisper berlaku untuk analisis berikutnya.");
    loadSuggestions();
  } catch (err) { toast(err.message, true); }
}

// ------------------------------------------------------------ pasang event
function initSubEditor(player) {
  const box = $("transcriptBox");
  box.addEventListener("click", async (e) => {
    const bulk = e.target.closest("[data-bulk]");
    if (bulk) {
      const ids = sub.suggestions.filter((s) => s.term === bulk.dataset.term).map((s) => s.id);
      if (bulk.dataset.bulk === "accept") decide(ids, []); else decide([], ids);
      return;
    }
    if (e.target.id === "reviewAllBtn") {
      const ids = [...box.querySelectorAll(".sub-row")].map((r) => +r.dataset.s);
      try { await putTranscript({ reviewed: ids }); toast(`${ids.length} baris ditandai sudah ditinjau.`); } catch (err) { toast(err.message, true); }
      return;
    }
    const row = e.target.closest(".sub-row");
    if (!row) return;
    const si = +row.dataset.s;
    const seg = state.transcript.segments[si];
    const actEl = e.target.closest("[data-act]");
    const act = actEl && actEl.dataset.act;
    if (act === "accept" || act === "ignore") { decide(act === "accept" ? [actEl.dataset.id] : [], act === "ignore" ? [actEl.dataset.id] : []); return; }
    if (act === "play") { player.currentTime = seg.start; player.play(); return; }
    if (act === "start-" || act === "start+") { nudge(si, "start", act === "start-" ? -0.1 : 0.1); return; }
    if (act === "end-" || act === "end+") { nudge(si, "end", act === "end-" ? -0.1 : 0.1); return; }
    if (act === "setstart" || act === "setend") {
      const t = +player.currentTime.toFixed(3);
      const body = act === "setstart" ? { index: si, start: t, end: Math.max(seg.end, t + 0.3) } : { index: si, start: Math.min(seg.start, t - 0.3), end: t };
      try { await putTranscript({ segments: [body] }); } catch (err) { toast(err.message, true); }
      return;
    }
    if (act === "times") { sub.times = sub.times === si ? null : si; renderTranscript(); return; }
    if (act === "times-close") { sub.times = null; renderTranscript(); return; }
    if (act === "times-save") { saveWordTimes(si); return; }
    if (act === "review") {
      try { await putTranscript(seg.reviewed ? { unreviewed: [si] } : { reviewed: [si] }); } catch (err) { toast(err.message, true); }
      return;
    }
    if (e.target.closest(".word-times")) return;
    const textEl = e.target.closest(".sub-text");
    if (textEl && !textEl.classList.contains("editing")) {
      const w = e.target.closest(".w");
      if (w) player.currentTime = +w.dataset.t;          // dengarkan bagian yang dikoreksi
      const words = [...textEl.querySelectorAll(".w")];
      startEdit(si, w ? words.indexOf(w) : undefined);
    }
  });
  box.addEventListener("keydown", (e) => {
    const el = e.target.closest(".sub-text");
    if (!el) return;
    if (!el.classList.contains("editing")) {
      if (e.key === "Enter") { e.preventDefault(); startEdit(+el.closest(".sub-row").dataset.s); }
      return;
    }
    if (e.key === "Enter") { e.preventDefault(); finishEdit(true); }
    else if (e.key === "Escape") { e.preventDefault(); finishEdit(false); }
    else if (e.key === "Tab") { e.preventDefault(); finishEdit(true, e.shiftKey ? -1 : 1); }
  });
  box.addEventListener("focusout", (e) => {
    const el = e.target.closest && e.target.closest(".sub-text.editing");
    if (el && sub.editing) setTimeout(() => { if (sub.editing && document.activeElement !== el) finishEdit(true); }, 0);
  });
  box.addEventListener("paste", (e) => {      // tempel sebagai teks biasa
    if (!e.target.closest(".sub-text.editing")) return;
    e.preventDefault();
    document.execCommand("insertText", false, (e.clipboardData || window.clipboardData).getData("text/plain"));
  });
  $("findForm").addEventListener("submit", (e) => { e.preventDefault(); runFind(); });
  $("findInput").addEventListener("input", () => { if (!$("findInput").value) runFind(); else $("replaceAllBtn").disabled = true; });
  $("replaceAllBtn").addEventListener("click", replaceAll);
  document.querySelectorAll(".filter-row .chip").forEach((c) => c.addEventListener("click", () => setSubFilter(c.dataset.filter)));
  $("vocabBtn").addEventListener("click", openVocab);
  $("vocabAddBtn").addEventListener("click", () => addVocabRow());
  $("vocabRows").addEventListener("click", (e) => { if (e.target.closest(".vocab-del")) e.target.closest("tr").remove(); });
  $("vocabSaveBtn").addEventListener("click", saveVocab);
  $("vocabCloseBtn").addEventListener("click", () => $("vocabDialog").close());
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f" && !$("transcriptTab").hidden) {
      e.preventDefault();
      $("findInput").focus();
    }
  });
}
