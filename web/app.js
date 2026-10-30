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
const ACTIVE_JOB = ["antre", "jalan", "berhenti"];
const isActive = (j) => ACTIVE_JOB.includes(j.status);
// Sisa waktu yang mudah dibaca: "5 jam 20 menit", "40 menit".
function fmtRemaining(sec) {
  if (sec <= 0) return "kurang dari 1 menit";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return h ? `${h} jam${m ? " " + m + " menit" : ""}` : `${Math.max(1, m)} menit`;
}
const fmtDateTime = (t) => new Date(t * 1000).toLocaleString("id-ID", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function mediaRemaining(media) {
  if (!media || media.state !== "tersedia" || !media.expires_at) return null;
  return media.expires_at - Date.now() / 1000;
}

function mediaGone(media) {
  return !!media && ["dihapus", "menghapus", "kedaluwarsa"].includes(media.state);
}

function mediaListLabel(media) {
  if (!media || media.state === "lama") return "";
  if (mediaGone(media)) return ' · <span class="exp-gone">media dihapus</span>';
  const rem = mediaRemaining(media);
  if (rem === null) return "";
  const cls = media.warning ? "exp-warn" : "";
  return ` · <span class="${cls}">sisa ${fmtRemaining(rem)}</span>`;
}

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
  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    location.replace("/login.html?next=" + encodeURIComponent(location.pathname + location.hash));
    throw new Error("Silakan login.");
  }
  if (res.status === 410 && state.project) {
    // Media terhapus saat halaman terbuka: tampilkan keadaan terbaru.
    setTimeout(() => api(`/api/projects/${state.project.id}`).then((p) => { state.project = p; refreshProjectData(); }).catch(() => {}), 0);
  }
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
    <li data-id="${p.id}" class="${state.project && state.project.id === p.id ? "active" : ""}${p.thumb ? " has-thumb" : ""}">
      ${p.thumb ? `<img class="thumb" src="/api/projects/${p.id}/thumb" alt="" loading="lazy">` : ""}
      <span class="name">${esc(p.name)}</span>
      <span class="meta">${esc(p.status)}${p.duration ? " · " + fmt(p.duration) : ""}${p.render_count ? " · " + p.render_count + " klip" : ""}${p.owner_name ? " · " + esc(p.owner_name) : ""}${state.me && state.me.role === "admin" && p.unit_name ? " · " + esc(p.unit_name) : ""}${mediaListLabel(p.media)}</span>
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
  renderMedia();
  renderStatus();
  renderMusicOptions();
  renderRenders();
  if (mediaGone(p.media)) return;  // media sudah dihapus: jangan memuat preview/transkrip
  if (p.preview_ready && p.status !== "siap" && !$("player").src) {
    $("player").src = `/api/projects/${p.id}/preview`;  // preview bisa diputar sambil transkrip dibuat
    toast("Preview siap diputar. Transkrip & klip menyusul.");
  }
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
      layoutCropFrame();  // data wajah baru tersedia: posisikan ulang bingkai
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
  const cancelBtn = $("cancelAnalyzeBtn");
  cancelBtn.hidden = true;
  if (job && isActive(job)) {
    box.hidden = false;
    box.classList.remove("error");
    box.querySelector(".status-text").textContent = `${job.message} (${Math.round(job.progress)}%)`;
    box.querySelector(".bar > div").style.width = job.progress + "%";
    cancelBtn.hidden = false;
    cancelBtn.dataset.job = job.id;
    cancelBtn.disabled = job.status === "berhenti";
    cancelBtn.textContent = job.status === "berhenti" ? "Membatalkan..." : "Batalkan";
  } else if (p.status === "batal") {
    box.hidden = false;
    box.classList.remove("error");
    box.querySelector(".status-text").textContent = "Analisis dibatalkan. Klik \"Analisis ulang\" untuk memulai lagi.";
    box.querySelector(".bar > div").style.width = "0";
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
  const busy = (p.jobs || []).some((j) => j.kind === "analisis" && isActive(j));
  $("reanalyzeBtn").disabled = busy;
  $("deleteBtn").hidden = p.can_delete === false;
  $("deleteBtn").disabled = (p.jobs || []).some(isActive);
}

function startPolling() {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    const p = state.project;
    if (!p) return;
    const active = (p.jobs || []).some(isActive);
    if (!active && !["antre", "mengunduh", "dianalisis"].includes(p.status)) return;
    try {
      const wasReady = p.status === "siap";
      state.project = await api(`/api/projects/${p.id}`);
      if (!wasReady && state.project.status === "siap") toast("Analisis selesai! Pilih klip favorit Anda.");
      await refreshProjectData();
      if (!state.project.jobs.some(isActive)) refreshProjects();
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

// Masa simpan media: badge di judul, peringatan menjelang dihapus, dan tampilan saat sudah dihapus.
function renderMedia() {
  const p = state.project;
  const media = p.media || {};
  const badge = $("mediaBadge");
  const gone = mediaGone(media);
  $("mediaGone").hidden = !gone;
  $("editorArea").hidden = gone;
  $("reanalyzeBtn").hidden = gone;
  badge.hidden = false;
  badge.className = "media-badge";
  $("mediaWarning").hidden = true;
  const history = $("goneHistory");
  const renders = [...(p.renders || [])].reverse();
  history.hidden = !gone || !renders.length;
  if (gone) history.querySelector("ul").innerHTML = renders.map((r) => historyItem(r)).join("");
  if (gone) {
    badge.textContent = "Media dihapus";
    badge.classList.add("gone");
    const player = $("player");
    if (player.src) { player.removeAttribute("src"); player.load(); }
    return;
  }
  if (media.state === "lama" || !media.state) {
    badge.textContent = "Proyek lama";
    badge.title = "Dibuat sebelum penyimpanan sementara berlaku; belum dijadwalkan untuk dihapus.";
    return;
  }
  const rem = mediaRemaining(media);
  if (rem === null) { badge.hidden = true; return; }
  badge.textContent = `Media tersimpan sampai ${fmtDateTime(media.expires_at)}`;
  badge.title = "Diperpanjang setiap kali Anda bekerja di proyek ini, paling lama sampai " + fmtDateTime(media.hard_expires_at);
  if (media.warning) {
    badge.classList.add("warn");
    const w = $("mediaWarning");
    w.hidden = false;
    w.innerHTML = `⚠️ <b>Media akan dihapus dalam ${fmtRemaining(rem)}</b> (${fmtDateTime(media.expires_at)}).
      Unduh hasil render yang Anda perlukan sekarang. Setelah dihapus, proyek tidak bisa dibuka kembali.`;
  }
}

// Hasil render yang filenya sudah dihapus: tetap tercatat sebagai riwayat, tanpa pemutar/tombol unduh.
function historyItem(r, expired = true) {
  return `
      <li class="render-item unavailable">
        <div></div>
        <div>
          <div class="title">${esc(r.title)}</div>
          <div class="muted">${esc(r.aspect)} · ${fmt(r.start)}–${fmt(r.end)}</div>
          <div class="muted cancelled">File sudah tidak tersedia${expired ? " (masa simpan berakhir)" : ""}.</div>
        </div>
      </li>`;
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
            : j.status === "batal"
              ? '<div class="muted cancelled">Dibatalkan</div>'
              : `<div class="muted">${esc(j.message)} ${Math.round(j.progress)}%</div><div class="bar"><div style="width:${j.progress}%"></div></div>
                 <button class="ghost danger cancel" data-cancel="${esc(j.id)}" ${j.status === "berhenti" ? "disabled" : ""}>${j.status === "berhenti" ? "Membatalkan..." : "Batalkan"}</button>`}
        </div>
      </li>`),
    ...done.map((r) => {
      const url = `/api/projects/${p.id}/renders/${encodeURIComponent(r.file)}`;
      if (r.available === false || mediaGone(p.media)) return historyItem(r, mediaGone(p.media));
      return `
      <li class="render-item">
        <video src="${url}" controls preload="metadata"></video>
        <div>
          <div class="title">${esc(r.title)}</div>
          <div class="muted">${esc(r.aspect)} · ${fmt(r.start)}–${fmt(r.end)} · ${fmtSize(r.size)}</div>
          <p><a class="download-btn" href="${url}?download=true" download>⬇ Download MP4</a></p>
        </div>
      </li>`;
    }),
  ];
  const html = items.join("") || '<li class="muted">Belum ada hasil render.</li>';
  const list = $("renderList");
  if (list.dataset.html !== html) { list.innerHTML = html; list.dataset.html = html; }
}

async function cancelJob(jobId) {
  try {
    await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    state.project = await api(`/api/projects/${state.project.id}`);
    renderStatus();
    renderRenders();
    startPolling();
  } catch (e) { toast(e.message, true); }
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

// ------------------------------------------------------------ upload bertahap (protokol tus 1.0.0)
// File dikirim per potongan. Bila koneksi putus, browser di-refresh, atau upload dijeda, upload melanjutkan
// dari byte terakhir yang sudah DIKONFIRMASI server (bukan dari awal). Progres & kecepatan dihitung dari
// byte yang sudah diterima server; "selesai" baru tampil setelah server memverifikasi file.
const MB = 1024 * 1024;
const uploadCfg = { chunkMb: 8, maxMb: 4096, resumeHours: 12 };
const uploads = [];          // Upload yang sedang/baru saja berjalan di tab ini
let serverUploads = [];      // upload belum selesai dari sesi sebelumnya (setelah refresh / tab lain)
const UPLOAD_PARALLEL = 2;

const b64 = (s) => btoa(unescape(encodeURIComponent(s)));
const fmtSpeed = (bps) => (bps >= MB ? (bps / MB).toFixed(1) + " MB/dtk" : Math.round(bps / 1024) + " KB/dtk");
const fmtEta = (sec) => (sec < 60 ? `${Math.max(1, Math.round(sec))} dtk` : sec < 3600 ? `${Math.round(sec / 60)} mnt` : `${(sec / 3600).toFixed(1)} jam`);
const uploadKey = (file) => `ve-upload:${state.me ? state.me.id : ""}:${file.name}:${file.size}:${file.lastModified}`;

async function sha256b64(buf) {
  if (!(window.crypto && crypto.subtle)) return null;  // http non-localhost: tanpa checksum (server tetap memeriksa ukuran & isi)
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", buf));
  let bin = "";
  for (let i = 0; i < d.length; i++) bin += String.fromCharCode(d[i]);
  return btoa(bin);
}

class UploadFailure extends Error {
  constructor(status, message, offset) { super(message); this.status = status; this.offset = offset; }
}

class Upload {
  constructor(file, url = null) {
    this.file = file; this.size = file.size; this.url = url; this.offset = 0; this.inflight = 0;
    this.state = "menunggu"; this.note = ""; this.retries = 0; this.paused = false; this.running = false;
    // Mulai kecil (progres cepat terlihat), lalu menyesuaikan kecepatan koneksi (lihat run()).
    this.chunk = Math.min(uploadCfg.chunkMb, 2) * MB; this.samples = []; this.xhr = null; this.result = null;
    this.uid = Math.random().toString(36).slice(2);
  }

  get speed() {  // byte/dtk dari byte yang sudah dikonfirmasi server, jendela ±8 dtk
    const s = this.samples;
    if (s.length < 2) return 0;
    const [t0, o0] = s[0], [t1, o1] = s[s.length - 1];
    return t1 > t0 ? (o1 - o0) / ((t1 - t0) / 1000) : 0;
  }

  set(stateName, note = "") { this.state = stateName; this.note = note; renderUploads(); }

  async request(method, extra = {}) {
    let res;
    try {
      res = await fetch(this.url || "/api/uploads", { method, headers: { "Tus-Resumable": "1.0.0", ...extra } });
    } catch {
      throw new UploadFailure(0, "Koneksi terputus.");  // jaringan belum pulih: dicoba lagi otomatis
    }
    let detail = "";
    try { detail = (await res.clone().json()).detail || ""; } catch { /* tanpa isi */ }
    return { res, detail };
  }

  async open() {
    const key = uploadKey(this.file);
    if (!this.url) this.url = localStorage.getItem(key);
    if (this.url) {
      const { res } = await this.request("HEAD");
      if (res.ok) { this.offset = +res.headers.get("Upload-Offset"); return; }
      localStorage.removeItem(key);  // sesi lama sudah tidak ada: mulai baru
      this.url = null;
    }
    const lang = $("langSelect").value;
    const meta = `filename ${b64(this.file.name)}` + (lang ? `,language ${b64(lang)}` : "");
    const { res, detail } = await this.request("POST", { "Upload-Length": String(this.size), "Upload-Metadata": meta });
    if (res.status !== 201) throw new UploadFailure(res.status, detail || `Upload ditolak (${res.status}).`);
    this.url = res.headers.get("Location");
    this.offset = 0;
    localStorage.setItem(key, this.url);
  }

  patch(buf, checksum) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      this.xhr = xhr;
      xhr.open("PATCH", this.url);
      xhr.timeout = 120000;
      xhr.setRequestHeader("Tus-Resumable", "1.0.0");
      xhr.setRequestHeader("Upload-Offset", String(this.offset));
      xhr.setRequestHeader("Content-Type", "application/offset+octet-stream");
      if (checksum) xhr.setRequestHeader("Upload-Checksum", `sha256 ${checksum}`);
      xhr.upload.onprogress = (e) => {
        this.inflight = e.loaded;
        if (this.offset + e.loaded >= this.size) this.set("verifikasi", "Memverifikasi file di server...");
        else renderUploads();
      };
      xhr.onload = () => {
        this.inflight = 0;
        if (xhr.status === 204) return resolve(+xhr.getResponseHeader("Upload-Offset"));
        let detail = "";
        try { detail = JSON.parse(xhr.responseText).detail; } catch { /* kosong */ }
        reject(new UploadFailure(xhr.status, detail || `Error ${xhr.status}`, +(xhr.getResponseHeader("Upload-Offset") || -1)));
      };
      xhr.onerror = xhr.ontimeout = () => { this.inflight = 0; reject(new UploadFailure(0, "Koneksi terputus.")); };
      xhr.onabort = () => { this.inflight = 0; reject(new UploadFailure(-1, "Dijeda.")); };
      xhr.send(buf);
    });
  }

  async run() {
    if (this.running) return;
    this.running = true;
    try {
      if (!this.url || this.state === "menunggu" || this.state === "gagal") {
        this.set("menyiapkan", "Menyiapkan upload...");
        await this.open();
      }
      while (this.offset < this.size) {
        if (this.paused) return this.set("dijeda", "Dijeda. Klik Lanjutkan untuk meneruskan.");
        if (!navigator.onLine) return this.set("terputus", "Koneksi internet terputus. Upload dilanjutkan otomatis saat online.");
        this.set("mengunggah");
        const end = Math.min(this.size, this.offset + this.chunk);
        const buf = await this.file.slice(this.offset, end).arrayBuffer();
        const checksum = await sha256b64(buf);
        const t0 = Date.now();
        try {
          this.offset = await this.patch(buf, checksum);
          this.retries = 0;
          const dt = (Date.now() - t0) / 1000;  // ukuran potongan menyesuaikan kecepatan (target ±5 dtk)
          if (dt < 2 && this.chunk < 32 * MB) this.chunk *= 2;
          else if (dt > 10 && this.chunk > MB) this.chunk = Math.max(MB, this.chunk / 2);
          this.samples.push([Date.now(), this.offset]);
          while (this.samples.length > 2 && Date.now() - this.samples[0][0] > 8000) this.samples.shift();
        } catch (e) {
          if (e.status === -1) continue;                         // dijeda/dibatalkan: dicek di awal loop
          if (e.status === 409 || e.status === 423) {             // posisi beda (mis. tab lain): ikuti server
            if (e.offset >= 0) this.offset = e.offset;
            else { const { res } = await this.request("HEAD"); this.offset = +res.headers.get("Upload-Offset"); }
            if (e.status === 423) await new Promise((r) => setTimeout(r, 2000));
            continue;
          }
          if (e.status === 460) continue;                        // potongan rusak di jalan: kirim ulang
          if (e.status === 0 || e.status >= 500 && e.status !== 507) {
            this.retries += 1;
            if (this.retries > 8) return this.set("terputus", "Koneksi bermasalah. Klik Coba lagi untuk melanjutkan dari bagian terakhir.");
            const wait = Math.min(30, 2 ** this.retries);
            this.set("terputus", `Koneksi terputus. Mencoba lagi dalam ${wait} dtk (melanjutkan dari ${fmtSize(this.offset)})...`);
            await new Promise((r) => setTimeout(r, wait * 1000));
            if (this.offset >= 0) { const { res } = await this.request("HEAD"); if (res.ok) this.offset = +res.headers.get("Upload-Offset"); }
            continue;
          }
          throw e;
        }
      }
      let info = null;
      for (let i = 0; i < 30; i++) {  // finalisasi bisa sedang dilanjutkan server (mis. setelah restart)
        const { res } = await this.request("GET");
        info = await res.json();
        if (info.state !== "final" && !(info.state === "aktif" && info.offset === info.size)) break;
        this.set("verifikasi", "Memverifikasi file di server...");
        await new Promise((r) => setTimeout(r, 2000));
      }
      if (info.state !== "selesai") throw new UploadFailure(422, info.error || "Upload belum terverifikasi.");
      localStorage.removeItem(uploadKey(this.file));
      this.result = info;
      this.set("selesai", info.job ? "Selesai & terverifikasi. Membuat preview..." : "Selesai & terverifikasi.");
      await refreshProjects();
      if (!state.project) await openProject(info.project_id);
    } catch (e) {
      if (e.status === 0 && !this.paused) {  // gangguan jaringan saat membuka/menutup sesi: coba lagi otomatis
        this.retries += 1;
        if (this.retries <= 8) {
          const wait = Math.min(30, 2 ** this.retries);
          this.set("terputus", `Koneksi terputus. Mencoba lagi dalam ${wait} dtk...`);
          setTimeout(() => { if (this.state === "terputus" && !this.paused) this.resume(); }, wait * 1000);
        } else this.set("terputus", "Koneksi bermasalah. Klik Coba lagi untuk melanjutkan dari bagian terakhir.");
        return;
      }
      const full = e.status === 507;
      if (e.status === 410 || e.status === 404) localStorage.removeItem(uploadKey(this.file));
      this.set("gagal", full ? `Penyimpanan server tidak cukup: ${e.message}` : e.message);
    } finally {
      this.running = false;
      pumpUploads();
    }
  }

  pause() { this.paused = true; if (this.xhr) this.xhr.abort(); this.set("dijeda", "Dijeda."); }
  resume() { this.paused = false; if (this.state !== "terputus") this.retries = 0; this.set("menunggu"); pumpUploads(); }

  async cancel() {
    this.paused = true;
    if (this.xhr) this.xhr.abort();
    if (this.url) { try { await this.request("DELETE"); } catch { /* sesi akan kedaluwarsa sendiri */ } }
    localStorage.removeItem(uploadKey(this.file));
    uploads.splice(uploads.indexOf(this), 1);
    renderUploads();
    refreshProjects();
  }
}

function pumpUploads() {
  const running = uploads.filter((u) => u.running).length;
  uploads.filter((u) => u.state === "menunggu" && !u.running && !u.paused).slice(0, Math.max(0, UPLOAD_PARALLEL - running))
    .forEach((u) => u.run());
}

function uploadFiles(files) {
  for (const file of files) {
    if (uploadCfg.maxMb && file.size > uploadCfg.maxMb * MB) { toast(`${file.name}: terlalu besar (maks ${uploadCfg.maxMb} MB).`, true); continue; }
    const pending = serverUploads.find((s) => s.filename === file.name && s.size === file.size);
    uploads.push(new Upload(file, pending ? `/api/uploads/${pending.id}` : null));
    if (pending) serverUploads = serverUploads.filter((s) => s !== pending);
  }
  renderUploads();
  pumpUploads();
}

function renderUploads() {
  const label = { menunggu: "Menunggu giliran", menyiapkan: "Menyiapkan", mengunggah: "Mengunggah", verifikasi: "Memverifikasi",
    dijeda: "Dijeda", terputus: "Terputus", selesai: "Selesai", gagal: "Gagal" };
  const rows = uploads.map((u) => {
    const done = u.offset / u.size * 100, fly = Math.min(100 - done, u.inflight / u.size * 100);
    const sp = u.speed;
    const sending = u.inflight > 0 && u.state === "mengunggah" ? ` (+${fmtSize(u.inflight)} sedang dikirim)` : "";
    const detail = u.state === "mengunggah" && sp > 0
      ? `${fmtSize(u.offset)} / ${fmtSize(u.size)} diterima server${sending} · ${fmtSpeed(sp)} · sisa ±${fmtEta((u.size - u.offset) / sp)}`
      : `${fmtSize(u.offset)} / ${fmtSize(u.size)} diterima server${sending}`;
    const btn = (act, text, cls = "ghost") => `<button class="${cls}" data-up="${u.uid}" data-act="${act}">${text}</button>`;
    const actions = u.state === "selesai" ? (u.result ? btn("open", "Buka proyek") : "")
      : u.state === "gagal" ? btn("retry", "Coba lagi") + btn("remove", "Tutup")
      : u.state === "terputus" ? btn("retry", "Coba lagi") + btn("cancel", "Batalkan", "ghost danger")
      : u.paused ? btn("resume", "Lanjutkan") + btn("cancel", "Batalkan", "ghost danger")
      : u.state === "verifikasi" ? "" : btn("pause", "Jeda") + btn("cancel", "Batalkan", "ghost danger");
    return `<li class="upload-item ${u.state}">
      <div class="upload-name" title="${esc(u.file.name)}">${esc(u.file.name)}</div>
      <div class="bar"><div style="width:${done.toFixed(2)}%"></div><div class="inflight" style="left:${done.toFixed(2)}%;width:${fly.toFixed(2)}%"></div></div>
      <div class="upload-meta"><b>${label[u.state] || u.state}</b> · ${Math.floor(done)}% · ${detail}</div>
      ${u.note ? `<div class="upload-note">${esc(u.note)}</div>` : ""}
      <div class="row">${actions}</div></li>`;
  });
  const stale = serverUploads.map((s) => `<li class="upload-item terputus">
      <div class="upload-name">${esc(s.filename)}</div>
      <div class="bar"><div style="width:${(s.offset / s.size * 100).toFixed(2)}%"></div></div>
      <div class="upload-meta"><b>Belum selesai</b> · ${Math.floor(s.offset / s.size * 100)}% · ${fmtSize(s.offset)} / ${fmtSize(s.size)}</div>
      <div class="upload-note">Pilih file yang sama untuk melanjutkan dari bagian terakhir (berlaku sampai ${fmtDateTime(s.expires_at)}).</div>
      <div class="row"><button class="ghost" data-stale="${s.id}" data-act="pick">Pilih file</button>
        <button class="ghost danger" data-stale="${s.id}" data-act="drop">Batalkan</button></div></li>`);
  $("uploadList").innerHTML = rows.concat(stale).join("");
}

async function loadServerUploads() {
  try {
    const data = await api("/api/uploads");
    Object.assign(uploadCfg, { chunkMb: data.chunk_mb, maxMb: data.max_mb, resumeHours: data.resume_hours });
    serverUploads = data.uploads.filter((s) => !uploads.some((u) => u.url && u.url.endsWith(s.id)));
    renderUploads();
  } catch { /* abaikan */ }
}

function bindUploads() {
  let pickFor = null;
  $("uploadList").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.stale) {
      const s = serverUploads.find((x) => x.id === b.dataset.stale);
      if (b.dataset.act === "pick") { pickFor = s; $("resumeInput").click(); }
      if (b.dataset.act === "drop") {
        await fetch(`/api/uploads/${s.id}`, { method: "DELETE", headers: { "Tus-Resumable": "1.0.0" } });
        serverUploads = serverUploads.filter((x) => x !== s);
        renderUploads();
        refreshProjects();
      }
      return;
    }
    const u = uploads.find((x) => x.uid === b.dataset.up);
    if (!u) return;
    ({ pause: () => u.pause(), resume: () => u.resume(), retry: () => u.resume(), cancel: () => u.cancel(),
       remove: () => { uploads.splice(uploads.indexOf(u), 1); renderUploads(); },
       open: () => openProject(u.result.project_id) }[b.dataset.act] || (() => {}))();
  });
  $("resumeInput").addEventListener("change", (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file || !pickFor) return;
    if (file.name !== pickFor.filename || file.size !== pickFor.size) {
      toast(`Itu bukan file yang sama (butuh ${pickFor.filename}, ${fmtSize(pickFor.size)}).`, true);
      return;
    }
    uploadFiles([file]);
  });
  window.addEventListener("online", () => uploads.filter((u) => u.state === "terputus").forEach((u) => u.resume()));
  window.addEventListener("offline", () => uploads.filter((u) => u.running).forEach((u) => u.note = "Koneksi internet terputus..."));
  window.addEventListener("beforeunload", (e) => {
    if (uploads.some((u) => u.running)) { e.preventDefault(); e.returnValue = ""; }  // upload tetap bisa dilanjutkan nanti
  });
}

// ------------------------------------------------------------ event
function bind() {
  const dz = $("dropzone");
  $("fileInput").addEventListener("change", (e) => { uploadFiles([...e.target.files]); e.target.value = ""; });
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => uploadFiles([...e.dataTransfer.files]));

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

  $("cancelAnalyzeBtn").addEventListener("click", (e) => {
    if (confirm("Batalkan analisis video ini?")) cancelJob(e.target.dataset.job);
  });
  $("renderList").addEventListener("click", (e) => {
    const id = e.target.dataset.cancel;
    if (id) cancelJob(id);
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
    list.innerHTML = '<li class="muted">Belum ada animasi di klip ini. Klik "Otomatis", atau buat dengan AI (salin prompt / API).</li>';
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
  const old = $(btn).innerHTML;
  $(btn).disabled = true;
  $(btn).textContent = kind === "ai" ? `⏳ ${$("aiName").textContent} sedang membaca transkrip...` : "⏳ Memproses...";
  try {
    state.plan = await postJSON(`/api/projects/${state.project.id}/plan/${kind}`, body);
    renderPlan();
    updateOverlayPreview();
    toast(`${planInRange().length} animasi siap. Putar klip untuk melihat pratinjaunya.`);
  } catch (e) { toast(e.message, true); }
  finally { $(btn).disabled = false; $(btn).innerHTML = old; }
}

async function loadMe() {
  state.me = await api("/api/auth/me");
  const me = state.me;
  $("whoami").innerHTML = `<b>${esc(me.name)}</b><br>${esc(me.role_label)}${me.unit_name ? " · " + esc(me.unit_name) : ""}`;
  $("adminLink").hidden = me.role === "editor";
  $("urlForm").hidden = $("urlOr").hidden = !me.url_import;
  const pol = me.media_policy || {};
  document.querySelector(".storage-note").hidden = pol.lifecycle === false;
  if (pol.retention_hours) {
    $("retentionText").textContent = `${pol.retention_hours} jam setelah aktivitas terakhir, paling lama ${pol.max_hours} jam sejak upload`;
  }
  $("aiName").textContent = me.ai.name;
  $("planAiBtn").disabled = !me.ai.ready;
  $("planAiBtn").title = me.ai.ready ? "Otomatis lewat API AI yang diatur admin"
    : "API AI belum diatur admin. Pakai 'Salin prompt' dulu.";
}

async function loadUnitGuidebook() {
  try {
    const unit = await api("/api/unit");
    $("guidebook").value = unit.guidebook || "";
    $("guidebook").readOnly = !unit.can_edit;
    $("guidebookSaveBtn").hidden = !unit.can_edit;
    $("guidebookHint").textContent = unit.can_edit ? "" : "Hanya Admin Unit yang bisa mengubah guidebook.";
  } catch { /* abaikan */ }
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
      toast(`${planInRange().length} animasi dari AI diterapkan.`);
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
  $("guidebookSaveBtn").addEventListener("click", async () => {
    try {
      await api("/api/unit/guidebook", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guidebook: $("guidebook").value }) });
      toast("Guidebook unit disimpan.");
    } catch (e) { toast(e.message, true); }
  });
}

async function checkHealth() {
  try {
    const h = await api("/api/health");
    if (!h.ffmpeg || !h.ffprobe) $("health").textContent = "⚠ FFmpeg tidak ditemukan. Install dulu (lihat README).";
  } catch { $("health").textContent = "⚠ Server tidak merespons."; }
}

bind();
bindPlan();
bindUploads();
setInterval(() => { if (state.project) renderMedia(); }, 60_000);
loadSettings();
$("logoutBtn").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch { /* abaikan */ }
  location.replace("/login.html");
});
loadMe().then(() => {
  loadUnitGuidebook();
  checkHealth();
  refreshProjects();
  loadServerUploads();
}).catch(() => { /* diarahkan ke halaman login */ });
