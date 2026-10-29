"use strict";

const $ = (id) => document.getElementById(id);
const OUTPUT_SIZES = { "9:16": [1080, 1920], "1:1": [1080, 1080], "4:5": [1080, 1350], "16:9": [1920, 1080] };

const state = {
  projects: [],
  project: null,
  transcript: { segments: [] },
  words: [],
  clips: [],
  checked: new Set(),
  activeClip: -1,
  sel: { start: 0, end: 0 },
  aspect: "9:16",
  playingClip: false,
  pollTimer: null,
  plan: [],
  faces: [],
};

// ------------------------------------------------------------ util
const fmt = (s) => {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1).padStart(4, "0");
  return `${m}:${sec}`;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtSize = (b) => (b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.round(b / 1e3) + " KB");

function toast(msg, isError = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (isError ? " error" : "");
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (el.hidden = true), isError ? 7000 : 3500);
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* respons kosong */ }
  if (!res.ok) {
    let msg = data && data.detail ? data.detail : `Error ${res.status}`;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    throw new Error(msg);
  }
  return data;
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ------------------------------------------------------------ pengaturan tersimpan
const SETTING_IDS = ["cropX", "colorSelect", "brightness", "contrast", "saturation", "speedSelect", "zoomCheck", "fadeCheck",
  "subEnabled", "subStyle", "subFont", "subColor", "subHighlight", "subSize", "subWords", "subPosition", "subUpper",
  "musicVolume", "duckCheck", "normCheck", "encoderSelect", "langSelect", "targetSelect", "reframeCheck", "animEnabled"];

function saveSettings() {
  const data = { aspect: state.aspect };
  for (const id of SETTING_IDS) {
    const el = $(id);
    data[id] = el.type === "checkbox" ? el.checked : el.value;
  }
  try { localStorage.setItem("ve-settings", JSON.stringify(data)); } catch { /* abaikan */ }
}

function loadSettings() {
  let data = null;
  try { data = JSON.parse(localStorage.getItem("ve-settings") || "null"); } catch { /* abaikan */ }
  if (!data) return;
  for (const id of SETTING_IDS) {
    if (!(id in data)) continue;
    const el = $(id);
    if (el.type === "checkbox") el.checked = data[id]; else el.value = data[id];
  }
  if (data.aspect) setAspect(data.aspect, false);
}

function renderSettings(start, end, title) {
  const music = $("musicSelect").value;
  return {
    start, end, title,
    aspect: state.aspect,
    crop_x: +$("cropX").value,
    reframe: $("reframeCheck").checked ? "auto" : "manual",
    animations: $("animEnabled").checked,
    crop_y: 0.5,
    color: $("colorSelect").value,
    brightness: +$("brightness").value,
    contrast: +$("contrast").value,
    saturation: +$("saturation").value,
    speed: +$("speedSelect").value,
    zoom: $("zoomCheck").checked ? "slow" : "none",
    fade: $("fadeCheck").checked,
    normalize_audio: $("normCheck").checked,
    encoder: $("encoderSelect").value,
    subtitles: {
      enabled: $("subEnabled").checked,
      style: $("subStyle").value,
      font: $("subFont").value,
      size: +$("subSize").value,
      color: $("subColor").value,
      highlight: $("subHighlight").value,
      position: $("subPosition").value,
      uppercase: $("subUpper").checked,
      words_per_line: +$("subWords").value,
    },
    music: { file: music || null, volume: +$("musicVolume").value, ducking: $("duckCheck").checked },
  };
}

// ------------------------------------------------------------ proyek
async function refreshProjects() {
  try {
    state.projects = await api("/api/projects");
  } catch (e) { return; }
  const list = $("projectList");
  list.innerHTML = state.projects.map((p) => `
    <li data-id="${p.id}" class="${state.project && state.project.id === p.id ? "active" : ""}">
      <span class="name">${esc(p.name)}</span>
      <span class="meta">${esc(p.status)}${p.duration ? " · " + fmt(p.duration) : ""}${p.renders && p.renders.length ? " · " + p.renders.length + " klip" : ""}</span>
    </li>`).join("") || '<li class="muted">Belum ada proyek.</li>';
}

async function openProject(id) {
  state.checked.clear();
  state.activeClip = -1;
  state.project = await api(`/api/projects/${id}`);
  $("emptyState").hidden = true;
  $("projectView").hidden = false;
  const player = $("player");
  player.removeAttribute("src");
  player.load();
  state.transcript = { segments: [] };
  state.plan = [];
  state.faces = [];
  state.words = [];
  state.clips = [];
  await refreshProjectData();
  refreshProjects();
  startPolling();
}

async function refreshProjectData() {
  const p = state.project;
  if (!p) return;
  $("projectTitle").textContent = p.name;
  renderStatus();
  renderMusicOptions();
  renderRenders();
  if (p.status === "siap") {
    if (!$("player").src) {
      $("player").src = `/api/projects/${p.id}/preview`;
    }
    if (!state.words.length && !state.clips.length) {
      const [tr, clips] = await Promise.all([api(`/api/projects/${p.id}/transcript`), api(`/api/projects/${p.id}/clips`)]);
      state.transcript = tr;
      state.words = tr.segments.flatMap((s) => s.words && s.words.length ? s.words : []);
      state.clips = clips;
      const [planItems, faces] = await Promise.all([api(`/api/projects/${p.id}/plan`), api(`/api/projects/${p.id}/faces`)]);
      state.plan = planItems;
      state.faces = faces;
      renderTranscript();
      renderClips();
      if (clips.length) selectClip(0, false);
      else setSelection(0, Math.min(30, p.duration || 30));
    }
  }
}

function renderStatus() {
  const p = state.project;
  const box = $("analyzeStatus");
  const job = [...(p.jobs || [])].reverse().find((j) => j.kind === "analisis");
  if (job && (job.status === "antre" || job.status === "jalan")) {
    box.hidden = false;
    box.classList.remove("error");
    box.querySelector(".status-text").textContent = `${job.message} (${Math.round(job.progress)}%)`;
    box.querySelector(".bar > div").style.width = job.progress + "%";
  } else if (p.status === "gagal" && job && job.error) {
    box.hidden = false;
    box.classList.add("error");
    box.querySelector(".status-text").textContent = "Analisis gagal: " + job.error;
    box.querySelector(".bar > div").style.width = "0";
  } else if (p.status === "gagal") {
    box.hidden = false;
    box.classList.add("error");
    box.querySelector(".status-text").textContent = "Analisis gagal. Coba \"Analisis ulang\".";
  } else {
    box.hidden = true;
  }
  const busy = (p.jobs || []).some((j) => j.kind === "analisis" && (j.status === "antre" || j.status === "jalan"));
  $("reanalyzeBtn").disabled = busy;
  $("deleteBtn").disabled = (p.jobs || []).some((j) => j.status === "antre" || j.status === "jalan");
}

function startPolling() {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    const p = state.project;
    if (!p) return;
    const active = (p.jobs || []).some((j) => j.status === "antre" || j.status === "jalan");
    if (!active && !["antre", "mengunduh", "dianalisis"].includes(p.status)) return;
    try {
      const wasReady = p.status === "siap";
      state.project = await api(`/api/projects/${p.id}`);
      if (!wasReady && state.project.status === "siap") toast("Analisis selesai! Pilih klip favorit Anda.");
      await refreshProjectData();
      if (!state.project.jobs.some((j) => j.status === "antre" || j.status === "jalan")) refreshProjects();
    } catch { /* coba lagi di putaran berikutnya */ }
  }, 1500);
}

// ------------------------------------------------------------ klip
function renderClips() {
  const list = $("clipList");
  if (!state.clips.length) {
    list.innerHTML = '<li class="muted">Belum ada kandidat. Coba ubah durasi target lalu "Cari ulang", atau pilih manual dari transkrip.</li>';
    renderTimeline();
    return;
  }
  list.innerHTML = state.clips.map((c, i) => `
    <li class="clip ${i === state.activeClip ? "active" : ""}" data-i="${i}">
      <input type="checkbox" ${state.checked.has(i) ? "checked" : ""} title="Centang untuk render massal">
      <div>
        <div class="time">#${i + 1} · ${fmt(c.start)} – ${fmt(c.end)} <span class="muted">(${(c.end - c.start).toFixed(0)} dtk)</span></div>
        <div class="text">${esc(c.text) || '<span class="muted">(tanpa ucapan)</span>'}</div>
        <div class="reasons">${(c.reasons || []).map((r) => `<span class="chip">${esc(r)}</span>`).join("")}</div>
      </div>
      <div class="score">${Math.round(c.score)}<small>skor</small></div>
    </li>`).join("");
  renderTimeline();
}

function selectClip(i, play = true) {
  const c = state.clips[i];
  if (!c) return;
  state.activeClip = i;
  setSelection(c.start, c.end);
  renderClips();
  if (play) playSelection();
}

function setSelection(start, end) {
  const dur = state.project && state.project.duration ? state.project.duration : Infinity;
  start = Math.max(0, Math.min(start, dur));
  end = Math.max(start, Math.min(end, dur));
  state.sel = { start, end };
  $("startInput").value = start.toFixed(1);
  $("endInput").value = end.toFixed(1);
  $("durLabel").textContent = `Durasi: ${(end - start).toFixed(1)} dtk`;
  renderTimeline();
  highlightTranscriptRange();
  renderPlan();
}

function playSelection() {
  const player = $("player");
  if (!player.src) return;
  player.currentTime = state.sel.start;
  state.playingClip = true;
  player.play().catch(() => {});
}

// ------------------------------------------------------------ timeline
function renderTimeline() {
  const dur = state.project && state.project.duration;
  if (!dur) return;
  $("tlClips").innerHTML = state.clips.map((c, i) =>
    `<div class="tl-clip ${state.checked.has(i) ? "checked" : ""}" style="left:${(c.start / dur) * 100}%;width:${((c.end - c.start) / dur) * 100}%"></div>`).join("");
  const sel = $("tlSel");
  sel.style.left = (state.sel.start / dur) * 100 + "%";
  sel.style.width = ((state.sel.end - state.sel.start) / dur) * 100 + "%";
}

function updatePlayhead() {
  const dur = state.project && state.project.duration;
  if (!dur) return;
  $("tlHead").style.left = ($("player").currentTime / dur) * 100 + "%";
}

// ------------------------------------------------------------ transkrip
function renderTranscript() {
  const box = $("transcriptBox");
  const segs = state.transcript.segments || [];
  if (!segs.length) {
    box.innerHTML = '<p class="muted">Tidak ada ucapan yang terdeteksi.</p>';
    return;
  }
  let idx = 0;
  box.innerHTML = segs.map((s) => {
    const words = s.words && s.words.length
      ? s.words.map((w) => `<span class="w" data-i="${idx++}" data-t="${w.start}">${esc(w.word)}</span>`).join(" ")
      : `<span class="w" data-t="${s.start}">${esc(s.text)}</span>`;
    return `<div class="seg"><span class="ts">${fmt(s.start)}</span>${words}</div>`;
  }).join("");
  highlightTranscriptRange();
}

function highlightTranscriptRange() {
  const { start, end } = state.sel;
  document.querySelectorAll("#transcriptBox .w[data-i]").forEach((el) => {
    const w = state.words[+el.dataset.i];
    el.classList.toggle("in", w && w.start >= start && w.end <= end);
  });
}

let lastNowWord = null;
function highlightNowWord(t) {
  const i = state.words.findIndex((w) => t >= w.start && t < w.end);
  if (lastNowWord) lastNowWord.classList.remove("now");
  lastNowWord = i >= 0 ? document.querySelector(`#transcriptBox .w[data-i="${i}"]`) : null;
  if (lastNowWord) lastNowWord.classList.add("now");
}

// ------------------------------------------------------------ bingkai crop + pratinjau subtitle
function setAspect(v, save = true) {
  state.aspect = v;
  document.querySelectorAll("#aspectSeg button").forEach((b) => b.classList.toggle("active", b.dataset.v === v));
  layoutCropFrame();
  if (save) saveSettings();
}

function displayedVideoRect() {
  const player = $("player");
  const wrap = $("playerWrap").getBoundingClientRect();
  const vw = player.videoWidth, vh = player.videoHeight;
  if (!vw || !vh) return null;
  const scale = Math.min(wrap.width / vw, wrap.height / vh);
  const w = vw * scale, h = vh * scale;
  return { x: (wrap.width - w) / 2, y: (wrap.height - h) / 2, w, h, vw, vh };
}

function layoutCropFrame() {
  const frame = $("cropFrame");
  const r = displayedVideoRect();
  if (!r) { frame.hidden = true; return; }
  let fw = r.w, fh = r.h;
  if (OUTPUT_SIZES[state.aspect]) {
    const [ow, oh] = OUTPUT_SIZES[state.aspect];
    const target = ow / oh;
    if (r.w / r.h > target) { fh = r.h; fw = r.h * target; } else { fw = r.w; fh = r.w / target; }
  }
  let cx = +$("cropX").value;
  const fx = $("reframeCheck").checked ? faceCenterAt($("player").currentTime) : null;
  if (fx !== null && r.w > fw + 1) cx = Math.max(0, Math.min(1, (fx * r.w - fw / 2) / (r.w - fw)));
  // Di mode otomatis (dan wajah terdeteksi) geser manual tidak berlaku.
  $("cropHandle").hidden = fx !== null;
  $("cropX").disabled = fx !== null;
  frame.hidden = false;
  frame.style.width = fw + "px";
  frame.style.height = fh + "px";
  frame.style.left = r.x + (r.w - fw) * cx + "px";
  frame.style.top = r.y + (r.h - fh) * 0.5 + "px";
  frame.dataset.range = String(r.w - fw);
  updateCaptionPreview();
  updateOverlayPreview();
}

// Posisi wajah (0..1) di sekitar waktu t, dirata-rata agar pratinjau bingkai tidak bergetar.
function faceCenterAt(t) {
  const f = state.faces;
  if (!f || !f.length) return null;
  const i = Math.round(t * 2);  // 2 sampel per detik
  const xs = f.slice(Math.max(0, i - 2), i + 3).filter(Boolean).map((p) => p.x);
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
}

function chunkWords(words, maxWords, maxGap = 0.6) {
  const chunks = [];
  let cur = [];
  for (const w of words) {
    if (cur.length && (cur.length >= maxWords || w.start - cur[cur.length - 1].end > maxGap)) { chunks.push(cur); cur = []; }
    cur.push(w);
    if (/[.!?,]$/.test(w.word)) { chunks.push(cur); cur = []; }
  }
  if (cur.length) chunks.push(cur);
  return chunks;
}

function updateCaptionPreview() {
  const cap = $("captionPreview");
  const t = $("player").currentTime;
  const frame = $("cropFrame");
  if (!$("subEnabled").checked || frame.hidden || t < state.sel.start || t > state.sel.end) { cap.innerHTML = ""; return; }
  const style = $("subStyle").value;
  const inRange = state.words.filter((w) => w.end > state.sel.start && w.start < state.sel.end);
  const chunks = chunkWords(inRange, style === "word" ? 1 : +$("subWords").value);
  const ci = chunks.findIndex((c, i) => t >= c[0].start && t < Math.min(c[c.length - 1].end + 0.25, chunks[i + 1] ? chunks[i + 1][0].start : Infinity));
  if (ci < 0) { cap.innerHTML = ""; return; }
  const chunk = chunks[ci];
  const upper = $("subUpper").checked;
  const hl = $("subHighlight").value;
  const fh = frame.getBoundingClientRect().height;
  const fw = frame.getBoundingClientRect().width;
  const [ow, oh] = OUTPUT_SIZES[state.aspect] || [fw, fh];
  let px = Math.min(ow, oh) * 0.075 * +$("subSize").value * (fh / oh);
  if (style === "word") px *= 1.4;
  cap.style.fontSize = px + "px";
  cap.style.fontFamily = `"${$("subFont").value}", Impact, sans-serif`;
  cap.style.color = $("subColor").value;
  const pos = $("subPosition").value;
  cap.style.top = cap.style.bottom = "";
  cap.style.transform = "";
  if (pos === "top") cap.style.top = fh * 0.12 + "px";
  else if (pos === "middle") { cap.style.top = "50%"; cap.style.transform = "translateY(-50%)"; }
  else cap.style.bottom = fh * 0.18 + "px";
  cap.innerHTML = chunk.map((w) => {
    const word = esc(upper ? w.word.toUpperCase() : w.word);
    const active = t >= w.start && t < w.end + 0.05;
    if (style === "simple" || !active) return word;
    const scale = style === "pop" ? "display:inline-block;transform:scale(1.18);" : "";
    return `<span style="color:${hl};${scale}">${word}</span>`;
  }).join(" ");
}

// ------------------------------------------------------------ musik & render
function renderMusicOptions() {
  const sel = $("musicSelect");
  const current = sel.value;
  const files = (state.project && state.project.music) || [];
  sel.innerHTML = '<option value="">Tanpa musik</option>' + files.map((f) => `<option>${esc(f)}</option>`).join("");
  if (files.includes(current)) sel.value = current;
}

function renderRenders() {
  const p = state.project;
  const done = [...(p.renders || [])].reverse();
  const pending = (p.jobs || []).filter((j) => j.kind === "render" && j.status !== "selesai").reverse();
  $("renderCount").textContent = done.length ? `(${done.length})` : "";
  const items = [
    ...pending.map((j) => `
      <li class="render-item">
        <div></div>
        <div>
          <div class="title">${esc(j.label)}</div>
          ${j.status === "gagal"
            ? `<div class="err">Gagal: ${esc(j.error)}</div>`
            : `<div class="muted">${esc(j.message)} ${Math.round(j.progress)}%</div><div class="bar"><div style="width:${j.progress}%"></div></div>`}
        </div>
      </li>`),
    ...done.map((r) => {
      const url = `/api/projects/${p.id}/renders/${encodeURIComponent(r.file)}`;
      return `
      <li class="render-item">
        <video src="${url}" controls preload="metadata"></video>
        <div>
          <div class="title">${esc(r.title)}</div>
          <div class="muted">${esc(r.aspect)} · ${fmt(r.start)}–${fmt(r.end)} · ${fmtSize(r.size)}</div>
          <p><a href="${url}?download=true">⬇ Unduh MP4</a></p>
        </div>
      </li>`;
    }),
  ];
  const html = items.join("") || '<li class="muted">Belum ada hasil render.</li>';
  const list = $("renderList");
  if (list.dataset.html !== html) { list.innerHTML = html; list.dataset.html = html; }
}

async function submitRender(start, end, title) {
  saveSettings();
  return postJSON(`/api/projects/${state.project.id}/render`, renderSettings(start, end, title));
}

async function afterRenderSubmit(count) {
  toast(`${count} klip masuk antrean render.`);
  state.project = await api(`/api/projects/${state.project.id}`);
  renderRenders();
  switchTab("rendersTab");
  startPolling();
}

function switchTab(id) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === id));
  document.querySelectorAll(".tab-panel").forEach((p) => (p.hidden = p.id !== id));
}

// ------------------------------------------------------------ impor
async function uploadFile(file) {
  const form = new FormData();
  form.append("file", file);
  const lang = $("langSelect").value;
  if (lang) form.append("language", lang);
  toast(`Mengunggah ${file.name}...`);
  try {
    const res = await api("/api/projects", { method: "POST", body: form });
    await refreshProjects();
    await openProject(res.project.id);
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------ event
function bind() {
  const dz = $("dropzone");
  $("fileInput").addEventListener("change", (e) => { if (e.target.files[0]) uploadFile(e.target.files[0]); e.target.value = ""; });
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) uploadFile(f); });

  $("urlForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await postJSON("/api/projects/from-url", { url: $("urlInput").value.trim(), language: $("langSelect").value || null });
      $("urlInput").value = "";
      await refreshProjects();
      await openProject(res.project.id);
    } catch (err) { toast(err.message, true); }
  });

  $("projectList").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    if (li) openProject(li.dataset.id).catch((err) => toast(err.message, true));
  });

  $("deleteBtn").addEventListener("click", async () => {
    if (!state.project || !confirm(`Hapus proyek "${state.project.name}" beserta semua hasil rendernya?`)) return;
    try {
      await api(`/api/projects/${state.project.id}`, { method: "DELETE" });
      state.project = null;
      $("projectView").hidden = true;
      $("emptyState").hidden = false;
      refreshProjects();
    } catch (err) { toast(err.message, true); }
  });

  $("reanalyzeBtn").addEventListener("click", async () => {
    try {
      const lang = $("langSelect").value;
      await api(`/api/projects/${state.project.id}/reanalyze${lang ? "?language=" + lang : ""}`, { method: "POST" });
      state.project = await api(`/api/projects/${state.project.id}`);
      state.words = []; state.clips = [];
      renderStatus();
      startPolling();
    } catch (err) { toast(err.message, true); }
  });

  const player = $("player");
  player.addEventListener("loadedmetadata", layoutCropFrame);
  player.addEventListener("timeupdate", () => {
    const t = player.currentTime;
    if (state.playingClip && t >= state.sel.end) { player.pause(); state.playingClip = false; }
    updatePlayhead();
    highlightNowWord(t);
    if ($("reframeCheck").checked && state.faces.length) layoutCropFrame();
    else { updateCaptionPreview(); updateOverlayPreview(); }
  });
  player.addEventListener("pause", () => (state.playingClip = false));
  window.addEventListener("resize", layoutCropFrame);

  // Geser bingkai crop dengan mouse/sentuh.
  const frame = $("cropFrame");
  const handle = $("cropHandle");
  let drag = null;
  handle.addEventListener("pointerdown", (e) => {
    drag = { x: e.clientX, start: +$("cropX").value, range: +frame.dataset.range || 1 };
    handle.setPointerCapture(e.pointerId);
  });
  handle.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const v = Math.max(0, Math.min(1, drag.start + (e.clientX - drag.x) / drag.range));
    $("cropX").value = v.toFixed(3);
    layoutCropFrame();
  });
  handle.addEventListener("pointerup", () => { if (drag) { drag = null; saveSettings(); } });

  $("timeline").addEventListener("click", (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const dur = state.project && state.project.duration;
    if (!dur) return;
    player.currentTime = ((e.clientX - r.left) / r.width) * dur;
  });

  $("startInput").addEventListener("change", () => setSelection(+$("startInput").value, state.sel.end));
  $("endInput").addEventListener("change", () => setSelection(state.sel.start, +$("endInput").value));
  $("setStartBtn").addEventListener("click", () => setSelection(player.currentTime, Math.max(state.sel.end, player.currentTime + 1)));
  $("setEndBtn").addEventListener("click", () => setSelection(Math.min(state.sel.start, player.currentTime - 1), player.currentTime));
  $("playClipBtn").addEventListener("click", playSelection);

  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

  $("clipList").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-i]");
    if (!li) return;
    const i = +li.dataset.i;
    if (e.target.matches("input[type=checkbox]")) {
      if (e.target.checked) state.checked.add(i); else state.checked.delete(i);
      renderTimeline();
      return;
    }
    selectClip(i);
  });

  $("transcriptBox").addEventListener("click", (e) => {
    const w = e.target.closest(".w");
    if (w) { player.currentTime = +w.dataset.t; }
  });

  $("suggestBtn").addEventListener("click", async () => {
    try {
      state.clips = await postJSON(`/api/projects/${state.project.id}/clips/suggest`, { target: +$("targetSelect").value, top_n: 10 });
      state.checked.clear();
      state.activeClip = -1;
      renderClips();
      saveSettings();
      toast(`${state.clips.length} kandidat klip ditemukan.`);
    } catch (err) { toast(err.message, true); }
  });

  $("renderBtn").addEventListener("click", async () => {
    try {
      const clip = state.clips[state.activeClip];
      const same = clip && Math.abs(clip.start - state.sel.start) < 0.05 && Math.abs(clip.end - state.sel.end) < 0.05;
      await submitRender(state.sel.start, state.sel.end, same ? `Klip #${state.activeClip + 1}` : "");
      await afterRenderSubmit(1);
    } catch (err) { toast(err.message, true); }
  });

  $("renderSelectedBtn").addEventListener("click", async () => {
    if (!state.checked.size) { toast("Centang dulu klip yang ingin dirender.", true); return; }
    try {
      const idx = [...state.checked].sort((a, b) => a - b);
      for (const i of idx) await submitRender(state.clips[i].start, state.clips[i].end, `Klip #${i + 1}`);
      await afterRenderSubmit(idx.length);
    } catch (err) { toast(err.message, true); }
  });

  $("aspectSeg").addEventListener("click", (e) => { if (e.target.dataset.v) setAspect(e.target.dataset.v); });

  $("musicInput").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f || !state.project) return;
    const form = new FormData();
    form.append("file", f);
    try {
      const res = await api(`/api/projects/${state.project.id}/music`, { method: "POST", body: form });
      state.project = await api(`/api/projects/${state.project.id}`);
      renderMusicOptions();
      $("musicSelect").value = res.file;
      toast("Musik diunggah.");
    } catch (err) { toast(err.message, true); }
  });

  for (const id of SETTING_IDS) {
    $(id).addEventListener("change", () => { saveSettings(); layoutCropFrame(); });
    $(id).addEventListener("input", () => { if (id === "cropX") layoutCropFrame(); updateCaptionPreview(); });
  }
}

// ------------------------------------------------------------ rencana animasi
const PLAN_LABELS = { zoom: "🔍 Zoom", keyword: "🔤 Kata", card: "🪪 Kartu", chips: "🏷 Chips", checklist: "✅ Checklist" };

function planInRange() {
  return state.plan.filter((i) => i.end > state.sel.start && i.start < state.sel.end);
}

function renderPlan() {
  const list = $("planList");
  if (!list || !state.project) return;
  const items = planInRange();
  $("animCount").textContent = items.length ? `(${items.length})` : "";
  if (!items.length) {
    list.innerHTML = '<li class="muted">Belum ada animasi di klip ini. Klik "Otomatis", atau buat dengan Claude.</li>';
    return;
  }
  list.innerHTML = items.map((it) => {
    let fields = "";
    if (it.type === "card") {
      fields = `<input class="label" data-f="label" value="${esc(it.label)}" placeholder="Label">
                <input data-f="text" value="${esc(it.text)}" placeholder="Judul kartu">`;
    } else if (it.type === "keyword") {
      fields = `<input data-f="text" value="${esc(it.text)}" placeholder="Kata kunci">`;
    } else if (it.type === "chips" || it.type === "checklist") {
      fields = `<input data-f="items" value="${esc(it.items.join(", "))}" placeholder="Pisahkan dengan koma">`;
    } else {
      fields = '<span class="muted">Punch-in zoom</span>';
    }
    return `<li class="plan-item" data-id="${esc(it.id)}">
      <span class="type">${PLAN_LABELS[it.type] || it.type}</span>
      <span class="times"><input type="number" step="0.1" data-f="start" value="${it.start}"> – <input type="number" step="0.1" data-f="end" value="${it.end}"></span>
      <span class="fields">${fields}</span>
      <span><button class="ghost" data-act="seek" title="Putar dari sini">▶</button> <button class="ghost danger" data-act="del" title="Hapus">✕</button></span>
    </li>`;
  }).join("");
}

let planSaveTimer = null;
function savePlanSoon() {
  clearTimeout(planSaveTimer);
  planSaveTimer = setTimeout(async () => {
    try {
      state.plan = await api(`/api/projects/${state.project.id}/plan`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state.plan),
      });
    } catch (e) { toast(e.message, true); }
  }, 500);
}

function updateOverlayPreview() {
  const box = $("ovPreview");
  if (!box) return;
  const t = $("player").currentTime;
  const frame = $("cropFrame");
  if (!$("animEnabled").checked || frame.hidden) { box.innerHTML = ""; return; }
  const active = state.plan.filter((i) => i.type !== "zoom" && t >= i.start && t < i.end);
  const key = active.map((i) => i.id).join(",");
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  const fh = frame.getBoundingClientRect().height;
  const [ow, oh] = OUTPUT_SIZES[state.aspect] || [1, 1];
  const unit = Math.min(ow, oh) * (fh / oh);
  box.innerHTML = active.map((i) => {
    if (i.type === "keyword") return `<div class="ovp-keyword" style="font-size:${unit * 0.13}px">${esc(i.text)}</div>`;
    if (i.type === "card") return `<div class="ovp-card" style="font-size:${unit * 0.058}px">${i.label ? `<small style="font-size:${unit * 0.034}px">${esc(i.label)}</small>` : ""}${esc(i.text)}</div>`;
    const cls = i.type === "chips" ? "ovp-chips" : "ovp-check";
    return `<div class="${cls}" style="font-size:${unit * 0.04}px">${i.items.map((x) => `<span>${esc(x)}</span>`).join("")}</div>`;
  }).join("");
}

async function runPlan(kind) {
  const body = { start: state.sel.start, end: state.sel.end, guidebook: $("guidebook").value };
  const btn = { auto: "planAutoBtn", ai: "planAiBtn" }[kind];
  const old = $(btn).textContent;
  $(btn).disabled = true;
  $(btn).textContent = kind === "ai" ? "⏳ Claude sedang membaca transkrip..." : "⏳ Memproses...";
  try {
    state.plan = await postJSON(`/api/projects/${state.project.id}/plan/${kind}`, body);
    renderPlan();
    updateOverlayPreview();
    toast(`${planInRange().length} animasi siap. Putar klip untuk melihat pratinjaunya.`);
  } catch (e) { toast(e.message, true); }
  finally { $(btn).disabled = false; $(btn).textContent = old; }
}

async function loadAiSettings() {
  try {
    const s = await api("/api/settings");
    $("guidebook").value = s.guidebook || "";
    $("apiModel").value = s.model || "claude-opus-5-5";
    $("keyStatus").textContent = s.has_api_key ? `Kunci tersimpan (${s.api_key_hint})` : "Belum ada API key";
  } catch { /* abaikan */ }
}

async function saveAiSettings(showToast = true) {
  try {
    const s = await api("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ anthropic_api_key: $("apiKey").value.trim() || null, model: $("apiModel").value, guidebook: $("guidebook").value }),
    });
    $("apiKey").value = "";
    $("keyStatus").textContent = s.has_api_key ? `Kunci tersimpan (${s.api_key_hint})` : "Belum ada API key";
    if (showToast) toast("Pengaturan disimpan.");
  } catch (e) { toast(e.message, true); }
}

function bindPlan() {
  $("planAutoBtn").addEventListener("click", () => runPlan("auto"));
  $("planAiBtn").addEventListener("click", () => runPlan("ai"));
  $("planPromptBtn").addEventListener("click", async () => {
    try {
      const res = await postJSON(`/api/projects/${state.project.id}/plan/prompt`,
        { start: state.sel.start, end: state.sel.end, guidebook: $("guidebook").value });
      try { await navigator.clipboard.writeText(res.prompt); }
      catch { $("pasteInput").value = res.prompt; toast("Salin otomatis gagal: prompt ada di kotak di bawah, salin manual.", true); }
      $("pasteBox").hidden = false;
      $("pasteInput").focus();
    } catch (e) { toast(e.message, true); }
  });
  $("pasteCancelBtn").addEventListener("click", () => { $("pasteBox").hidden = true; $("pasteInput").value = ""; });
  $("pasteApplyBtn").addEventListener("click", async () => {
    try {
      state.plan = await postJSON(`/api/projects/${state.project.id}/plan/import`,
        { start: state.sel.start, end: state.sel.end, text: $("pasteInput").value });
      $("pasteBox").hidden = true;
      $("pasteInput").value = "";
      renderPlan();
      toast(`${planInRange().length} animasi dari Claude diterapkan.`);
    } catch (e) { toast(e.message, true); }
  });
  $("planList").addEventListener("input", (e) => {
    const li = e.target.closest("li[data-id]");
    const item = li && state.plan.find((i) => i.id === li.dataset.id);
    if (!item) return;
    const f = e.target.dataset.f;
    if (f === "start" || f === "end") item[f] = +e.target.value;
    else if (f === "items") item.items = e.target.value.split(",").map((x) => x.trim()).filter(Boolean);
    else item[f] = e.target.value;
    $("ovPreview").dataset.key = "";
    savePlanSoon();
  });
  $("planList").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    const act = e.target.dataset.act;
    if (!li || !act) return;
    const item = state.plan.find((i) => i.id === li.dataset.id);
    if (act === "del") {
      state.plan = state.plan.filter((i) => i.id !== li.dataset.id);
      renderPlan();
      savePlanSoon();
    } else if (act === "seek" && item) {
      $("player").currentTime = item.start;
      $("player").play().catch(() => {});
    }
  });
  $("planAddBtn").addEventListener("click", () => {
    const type = $("planAddType").value;
    const t = Math.max(state.sel.start, Math.min($("player").currentTime, state.sel.end - 1));
    const item = { id: Math.random().toString(16).slice(2, 14), type, start: +t.toFixed(1),
      end: +Math.min(state.sel.end, t + (type === "zoom" ? 1.5 : 3)).toFixed(1),
      text: type === "card" ? "Judul kartu" : type === "keyword" ? "KATA" : "", label: type === "card" ? "Tips" : "",
      items: type === "chips" || type === "checklist" ? ["Poin 1", "Poin 2"] : [] };
    state.plan.push(item);
    state.plan.sort((a, b) => a.start - b.start);
    renderPlan();
    savePlanSoon();
  });
  $("settingsSaveBtn").addEventListener("click", () => saveAiSettings());
  $("guidebook").addEventListener("change", () => saveAiSettings(false));
}

async function checkHealth() {
  try {
    const h = await api("/api/health");
    if (!h.ffmpeg || !h.ffprobe) $("health").textContent = "⚠ FFmpeg tidak ditemukan. Install dulu (lihat README).";
  } catch { $("health").textContent = "⚠ Server tidak merespons."; }
}

bind();
bindPlan();
loadAiSettings();
loadSettings();
checkHealth();
refreshProjects();
