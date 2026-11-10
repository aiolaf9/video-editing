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
  templates: [],
  templateId: null,
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
// Job yang antre tidak diberi "0%": server mengirim alasan menunggu & lamanya (job.wait).
const jobStalled = (j) => !!(j.wait && ["worker_tidak_aktif", "worker_hilang", "kapasitas_tidak_cukup"].includes(j.wait.reason));
const jobStatusText = (j) => (j.status === "antre" || jobStalled(j)) ? j.message : `${j.message} (${Math.round(j.progress)}%)`;
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
    const err = new Error(msg);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  const rv = res.headers.get("X-Recipe-Version");   // server mengubah resep (koreksi teks / animasi)
  if (rv && state.project) syncServerRecipe(+rv);
  return data;
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ------------------------------------------------------------ resep editing (P1)
// Pengaturan editing disimpan per proyek di database (autosave), bukan hanya di browser. localStorage hanya
// menyimpan "pengaturan terakhir" sebagai bawaan proyek yang belum pernah diedit (kompatibel dengan versi lama).
const SETTING_IDS = ["cropX", "colorSelect", "brightness", "contrast", "saturation", "speedSelect", "zoomCheck", "fadeCheck",
  "subEnabled", "subStyle", "subFont", "subColor", "subHighlight", "subSize", "subWords", "subPosition", "subUpper",
  "subPosY", "subEmphasis", "subEmphasisColor", "subOutlineColor", "subOutline", "subShadow", "subBox", "subBoxColor",
  "subBoxOpacity",
  "musicVolume", "duckCheck", "normCheck", "encoderSelect", "langSelect", "targetSelect", "frameMode", "frameDistance",
  "cropY", "frameZoom", "animEnabled"];
const SAVE_DELAY = 800;          // debounce autosave (ms)
const REMOTE_CHECK = 15000;      // cek perubahan dari perangkat lain (ms), hanya saat tab terlihat & tidak ada edit
const editorCfg = { looks: {} };
const rs = { version: 0, recipe: null, dirty: false, saving: false, timer: null, retry: 0, conflict: null,
  loading: false, savedAt: null, pid: null };
const round3 = (x) => Math.round(x * 1000) / 1000;

function saveSettings() {   // bawaan untuk proyek baru (format lama tetap dibaca)
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

function recipeFromControls() {
  const base = rs.recipe || {};
  const checked = [...state.checked].sort((a, b) => a - b).map((i) => state.clips[i]).filter(Boolean).slice(0, 30);
  return {
    v: 1,
    selection: state.sel.end - state.sel.start > 0.05 ? { start: round3(state.sel.start), end: round3(state.sel.end) } : null,
    target: +$("targetSelect").value || 30,
    checked: checked.map((c) => ({ start: round3(c.start), end: round3(c.end) })),
    aspect: state.aspect,
    crop: { x: +$("cropX").value, y: +$("cropY").value, reframe: $("frameMode").value === "manual" ? "manual" : "auto",
      mode: $("frameMode").value || "auto", zoom: +$("frameZoom").value > 1.001 ? +$("frameZoom").value : 0,
      distance: $("frameDistance").value || "auto", sections: (state.frameSections || []).slice(0, 60) },
    look: { color: $("colorSelect").value, brightness: +$("brightness").value, contrast: +$("contrast").value,
      saturation: +$("saturation").value },
    motion: { speed: +$("speedSelect").value, zoom: $("zoomCheck").checked ? "slow" : "none", fade: $("fadeCheck").checked },
    subtitles: {
      enabled: $("subEnabled").checked, style: $("subStyle").value, font: $("subFont").value, size: +$("subSize").value,
      color: $("subColor").value.toUpperCase(), highlight: $("subHighlight").value.toUpperCase(),
      position: $("subPosition").value === "custom" ? "bottom" : $("subPosition").value, uppercase: $("subUpper").checked,
      words_per_line: +$("subWords").value,
      pos_y: $("subPosition").value === "custom" ? +$("subPosY").value : null,
      emphasis: $("subEmphasis").checked, emphasis_color: $("subEmphasisColor").value.toUpperCase(),
      outline_color: $("subOutlineColor").value.toUpperCase(), outline: +$("subOutline").value,
      shadow: +$("subShadow").value, box: $("subBox").checked, box_color: $("subBoxColor").value.toUpperCase(),
      box_opacity: +$("subBoxOpacity").value, sections: (state.subSections || []).slice(0, 60),
    },
    audio: { music: $("musicSelect").value || null, volume: +$("musicVolume").value, ducking: $("duckCheck").checked,
      normalize: $("normCheck").checked },
    animations: $("animEnabled").checked,
    overlays: base.overlays === undefined ? null : base.overlays,
    text_fixes: base.text_fixes || {},
    seg_edits: base.seg_edits || {},
    reviewed: base.reviewed || [],
    vocab_ignored: base.vocab_ignored || [],
    template_id: state.templateId || null,
    encoder: $("encoderSelect").value,
    language: $("langSelect").value || null,
  };
}

// M2.2: opsi subtitle tambahan (bawaan = tampilan M1.1).
function applySubExtras(s) {
  setControl("subPosition", s.pos_y != null ? "custom" : s.position);
  setControl("subPosY", s.pos_y != null ? s.pos_y : 0.76);
  $("subPosYRow").hidden = s.pos_y == null;
  setControl("subEmphasis", !!s.emphasis);
  setControl("subEmphasisColor", (s.emphasis_color || "#FFD43B").toLowerCase());
  setControl("subOutlineColor", (s.outline_color || "#000000").toLowerCase());
  setControl("subOutline", s.outline ?? 1); setControl("subShadow", s.shadow ?? 1);
  setControl("subBox", !!s.box); setControl("subBoxColor", (s.box_color || "#000000").toLowerCase());
  setControl("subBoxOpacity", s.box_opacity ?? 0.6);
  if (s.sections) state.subSections = s.sections;
  if (window.Studio) { window.Studio.markPreset(s.style); window.Studio.renderSubSections(); }
}

function setControl(id, value) {
  const el = $(id);
  if (!el || value === undefined || value === null) return;
  if (el.type === "checkbox") el.checked = !!value; else el.value = String(value);
}

function applyRecipeToControls(r) {
  setControl("cropX", r.crop.x);
  setControl("cropY", r.crop.y);
  setControl("frameDistance", r.crop.distance || "auto");
  setControl("frameZoom", r.crop.zoom > 1 ? r.crop.zoom : 1);
  state.frameSections = r.crop.sections || [];
  if (window.Studio) window.Studio.setFrameMode(r.crop.mode || (r.crop.reframe === "auto" ? "face" : "manual"), false);
  setControl("colorSelect", r.look.color);
  setControl("brightness", r.look.brightness);
  setControl("contrast", r.look.contrast);
  setControl("saturation", r.look.saturation);
  setControl("speedSelect", r.motion.speed);
  setControl("zoomCheck", r.motion.zoom === "slow");
  setControl("fadeCheck", r.motion.fade);
  const s = r.subtitles;
  setControl("subEnabled", s.enabled); setControl("subStyle", s.style); setControl("subFont", s.font);
  setControl("subSize", s.size); setControl("subColor", s.color.toLowerCase());
  setControl("subHighlight", s.highlight.toLowerCase()); setControl("subPosition", s.position);
  setControl("subUpper", s.uppercase); setControl("subWords", s.words_per_line);
  applySubExtras(s);
  setControl("musicVolume", r.audio.volume); setControl("duckCheck", r.audio.ducking); setControl("normCheck", r.audio.normalize);
  setControl("encoderSelect", r.encoder); setControl("langSelect", r.language || "");
  setControl("targetSelect", r.target); setControl("animEnabled", r.animations);
  state.templateId = r.template_id || null;
  setControl("templateSelect", state.templateId || "");
  renderMusicOptions();
  if (r.audio.music) $("musicSelect").value = r.audio.music;
  setAspect(r.aspect, false);
  applyLivePreview();
}

// Rentang & klip dicentang dari resep, dipasang setelah kandidat klip dimuat.
function applyRecipeSelection(r) {
  if (!r) return false;
  state.checked.clear();
  for (const c of r.checked || []) {
    const i = state.clips.findIndex((k) => Math.abs(k.start - c.start) < 0.05 && Math.abs(k.end - c.end) < 0.05);
    if (i >= 0) state.checked.add(i);
  }
  if (!r.selection) return false;
  state.activeClip = state.clips.findIndex((k) => Math.abs(k.start - r.selection.start) < 0.05 && Math.abs(k.end - r.selection.end) < 0.05);
  setSelection(r.selection.start, r.selection.end);
  renderClips();
  return true;
}

function setSaveState(kind, detail = "") {
  const el = $("saveState");
  if (!el) return;
  el.hidden = !state.project;
  el.className = `save-state ${kind}`;
  const time = rs.savedAt ? new Date(rs.savedAt).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" }) : "";
  el.textContent = { dirty: "● Belum tersimpan", saving: "⏳ Menyimpan…", saved: `✓ Tersimpan${time ? " " + time : ""}`,
    error: "⚠ Gagal menyimpan — klik untuk coba lagi", conflict: "⚠ Diubah di perangkat lain", idle: "Belum ada perubahan" }[kind] || "";
  el.title = detail || "Pengaturan editing tersimpan otomatis di server untuk proyek ini.";
}

function markDirty() {
  if (!state.project || rs.loading) return;
  if (window.Studio) window.Studio.onDirty();       // M2.4 riwayat undo/redo
  saveSettings();
  rs.dirty = true;
  if (!rs.conflict) setSaveState("dirty");
  clearTimeout(rs.timer);
  rs.timer = setTimeout(saveRecipe, SAVE_DELAY);
}

async function saveRecipe(keepalive = false) {
  clearTimeout(rs.timer);
  if (!state.project || rs.conflict) return;
  if (rs.saving) { rs.timer = setTimeout(saveRecipe, 300); return; }
  const pid = state.project.id;
  const body = { version: rs.version, recipe: recipeFromControls() };
  rs.saving = true;
  rs.dirty = false;
  setSaveState("saving");
  try {
    const res = await fetch(`/api/projects/${pid}/recipe`, { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), keepalive });
    const data = await res.json().catch(() => null);
    if (res.status === 401) { location.replace("/login.html"); return; }
    if (!state.project || state.project.id !== pid) return;
    if (res.status === 409 && data && data.conflict) {
      if (sameSettings(data.current.recipe, rs.recipe)) {
        // Versi naik karena koreksi teks/animasi dari tab ini (atau hanya kolom itu yang berubah): gabungkan & ulang.
        rs.version = data.current.version;
        rs.recipe = data.current.recipe;
        rs.dirty = true;
        rs.timer = setTimeout(saveRecipe, 50);
        return;
      }
      showConflict(data.current, body.recipe);
      return;
    }
    if (!res.ok) {
      const d = data && data.detail;
      throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || `Error ${res.status}`);
    }
    rs.version = data.version;
    rs.recipe = data.recipe;
    rs.retry = 0;
    rs.savedAt = Date.now();
    setSaveState(rs.dirty ? "dirty" : "saved");
  } catch (e) {
    if (!state.project || state.project.id !== pid) return;
    rs.dirty = true;
    rs.retry = Math.min(rs.retry + 1, 5);
    setSaveState("error", e.message);
    if (e.message && !/fetch|network|Failed/i.test(e.message)) toast(`Gagal menyimpan: ${e.message}`, true);
    rs.timer = setTimeout(saveRecipe, [3, 5, 10, 20, 30][rs.retry - 1] * 1000);
  } finally {
    rs.saving = false;
  }
}

// Pastikan resep di server sama dengan yang dilihat pengguna (sebelum render / pindah proyek).
async function flushRecipe() {
  if (!state.project) return;
  if (rs.version === 0 && !rs.dirty) rs.dirty = true;     // proyek belum pernah disimpan: simpan dulu
  const deadline = Date.now() + 15000;
  while ((rs.dirty || rs.saving) && Date.now() < deadline) {
    if (rs.conflict) break;
    if (!rs.saving) await saveRecipe(); else await new Promise((r) => setTimeout(r, 100));
  }
  if (rs.conflict) throw new Error("Proyek diubah di perangkat lain. Pilih versi yang dipakai dulu.");
  if (rs.dirty || rs.saving) throw new Error("Pengaturan belum tersimpan. Periksa koneksi, lalu coba lagi.");
}

function showConflict(current, mine) {
  rs.conflict = { current, mine };
  clearTimeout(rs.timer);
  const who = current.updated_by_name ? ` oleh ${esc(current.updated_by_name)}` : "";
  const when = current.updated ? ` pukul ${new Date(current.updated * 1000).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}` : "";
  $("conflictText").innerHTML = `<b>Proyek ini baru saja diubah di perangkat atau tab lain${who}${when}.</b> Perubahan Anda di sini belum
    disimpan agar tidak menimpa perubahan tersebut. Pilih versi yang dipakai.`;
  $("conflictBanner").hidden = false;
  setSaveState("conflict");
}

function applyServerRecipe(cur) {
  rs.loading = true;
  try {
    const fixesChanged = TRANSCRIPT_FIELDS.some((k) => JSON.stringify((rs.recipe || {})[k] || null) !== JSON.stringify(cur.recipe[k] || null));
    rs.version = cur.version;
    rs.recipe = cur.recipe;
    rs.dirty = false;
    applyRecipeToControls(cur.recipe);
    if (cur.recipe.overlays) { state.plan = cur.recipe.overlays; renderPlan(); updateOverlayPreview(); }
    if (state.clips.length) applyRecipeSelection(cur.recipe);
    if (fixesChanged && state.project) reloadTranscript();
    rs.savedAt = cur.updated ? cur.updated * 1000 : Date.now();
    setSaveState("saved");
  } finally { rs.loading = false; }
}

// Kata per segmen; segmen tanpa waktu per kata dibagi rata (sama dengan app/subtitles.collect_words).
function segWords(s) {
  if (s.words && s.words.length) return s.words;
  const tokens = String(s.text || "").split(/\s+/).filter(Boolean);
  const step = (s.end - s.start) / Math.max(tokens.length, 1);
  return tokens.map((t, i) => ({ start: s.start + i * step, end: s.start + (i + 1) * step, word: t }));
}

async function reloadTranscript() {
  const tr = await api(`/api/projects/${state.project.id}/transcript`);
  state.transcript = tr;
  state.words = tr.segments.flatMap(segWords);
  renderTranscript();
  updateCaptionPreview();
  scheduleSuggestions();
}

// Perubahan resep oleh server atas permintaan pengguna ini (koreksi teks, rencana animasi): ambil kolom yang diubah
// server tanpa membuang edit lokal yang belum tersimpan. Bila versi melompat lebih jauh, ada perangkat lain: konflik.
async function syncServerRecipe(headerVersion) {
  if (!state.project || rs.conflict) return;
  const pid = state.project.id;
  const cur = await api(`/api/projects/${pid}/recipe`).catch(() => null);
  if (!cur || !state.project || state.project.id !== pid) return;
  if (cur.version > headerVersion && !sameSettings(cur.recipe, rs.recipe)) {   // perangkat lain ikut mengubah
    if (rs.dirty || rs.saving) showConflict(cur, recipeFromControls()); else applyServerRecipe(cur);
    return;
  }
  // Perubahan kita sendiri: layar sudah diperbarui dari respons; cukup ambil versi & kolom milik server.
  rs.version = Math.max(rs.version, cur.version);
  rs.recipe = { ...(rs.recipe || cur.recipe), ...serverFields(cur.recipe) };
  if (!rs.dirty && !rs.saving) { rs.savedAt = Date.now(); setSaveState("saved"); }
}

// Kolom resep yang diubah server atas permintaan editor (koreksi transkrip, tinjau, saran istilah, animasi):
// autosave pengaturan selalu membawa nilai terbaru dari server agar koreksi tidak tertimpa.
const TRANSCRIPT_FIELDS = ["text_fixes", "seg_edits", "reviewed", "vocab_ignored"];
const SERVER_FIELDS = [...TRANSCRIPT_FIELDS, "overlays"];
function serverFields(r) {
  return Object.fromEntries(SERVER_FIELDS.map((k) => [k, r[k]]));
}

// Sama kecuali kolom milik server (lihat SERVER_FIELDS).
function sameSettings(a, b) {
  if (!a || !b) return false;
  const strip = (r) => JSON.stringify({ ...r, ...Object.fromEntries(SERVER_FIELDS.map((k) => [k, null])) });
  return strip(a) === strip(b);
}

async function checkRemoteRecipe() {
  if (!state.project || rs.dirty || rs.saving || rs.conflict || document.visibilityState !== "visible") return;
  const pid = state.project.id;
  const cur = await api(`/api/projects/${pid}/recipe`).catch(() => null);
  if (!cur || !state.project || state.project.id !== pid || rs.dirty || rs.saving) return;
  if (cur.version > rs.version) {
    applyServerRecipe(cur);
    toast(`Pengaturan diperbarui dari perangkat lain${cur.updated_by_name ? ` (${cur.updated_by_name})` : ""}.`);
  }
}

async function loadRecipe(pid) {
  const cur = await api(`/api/projects/${pid}/recipe`);
  rs.pid = pid;
  rs.conflict = null;
  $("conflictBanner").hidden = true;
  rs.retry = 0;
  if (window.Studio) window.Studio.resetHistory();
  if (cur.version > 0) {
    applyServerRecipe(cur);
  } else {
    // Proyek lama / baru: belum ada resep. Kontrol tetap memakai pengaturan terakhir di perangkat ini, lalu
    // tersimpan sebagai resep proyek pada perubahan pertama atau sebelum render.
    rs.version = 0;
    rs.recipe = cur.recipe;
    rs.dirty = false;
    state.templateId = null;
    setControl("templateSelect", "");
    rs.savedAt = null;
    setSaveState("idle");
    applyLivePreview();
  }
}

// Pratinjau langsung di browser: filter warna lewat CSS (GPU perangkat), kecepatan lewat playbackRate.
// Penyesuaian = eq FFmpeg: luma y = kontras * (x - 0.5) + 0.5 + kecerahan, warna (kroma) dikali saturasi.
// CSS brightness() mengalikan, jadi garis yang sama (y = c*x + 0.5*(1 - c) + b) dibentuk dari pasangan
// brightness & contrast. Urutannya dipilih agar nilai tidak terpotong di tengah rantai:
//   b >= 0: contrast(c / k) lalu brightness(k), k = 1 + 2b
//   b <  0: brightness(c / c2) lalu contrast(c2), c2 = c - 2b   (gelapkan dulu, baru naikkan kontras)
// Pasangan itu juga mengalikan kroma dengan c (eq tidak), sehingga saturasi CSS dibagi c: saturate(s / c).
// eq bekerja pada video YUV "limited range" (16-235): offset b setara b * 255/219 di RGB layar.
// Selisih yang tersisa diukur di tests/test_p1_browser_editor.py (paritas warna).
const EQ_RANGE = 255 / 219;
function lookCss() {
  const preset = (editorCfg.looks[$("colorSelect").value] || {}).css || "";
  const b = +$("brightness").value * EQ_RANGE, c = +$("contrast").value, s = +$("saturation").value;
  const parts = [preset];
  if (b >= 0 && (Math.abs(c - 1) > 1e-3 || b > 1e-3)) {
    const k = 1 + 2 * b;
    parts.push(`contrast(${(c / k).toFixed(4)})`, `brightness(${k.toFixed(4)})`);
  } else if (b < 0) {
    const c2 = c - 2 * b;
    parts.push(`brightness(${(c / c2).toFixed(4)})`, `contrast(${c2.toFixed(4)})`);
  }
  const sat = s / c;
  if (Math.abs(sat - 1) > 1e-3) parts.push(`saturate(${sat.toFixed(4)})`);
  return parts.filter(Boolean).join(" ");
}

function applyLivePreview() {
  const player = $("player");
  player.style.filter = lookCss();
  const speed = +$("speedSelect").value || 1;
  if (player.playbackRate !== speed) player.playbackRate = speed;
}

async function loadEditorConfig() {
  try { Object.assign(editorCfg, await api("/api/editor/config")); } catch { /* pratinjau tanpa preset warna */ }
  applyLivePreview();
}

async function submitRender(start, end, title) {
  await flushRecipe();
  try {
    return await postJSON(`/api/projects/${state.project.id}/render-recipe`, { version: rs.version, start, end, title });
  } catch (e) {
    if (e.status === 409 && e.data && e.data.current) showConflict(e.data.current, recipeFromControls());
    throw e;
  }
}

// ------------------------------------------------------------ template
async function loadTemplates() {
  try { state.templates = await api("/api/templates"); } catch { state.templates = []; return; }
  const groups = [["bawaan", "Bawaan"], ["global", "Semua unit"], ["unit", "Unit kerja"], ["personal", "Preset saya"]];
  $("templateSelect").innerHTML = '<option value="">— Pilih template —</option>' + groups.map(([scope, label]) => {
    const items = state.templates.filter((t) => t.scope === scope);
    if (!items.length) return "";
    return `<optgroup label="${label}">${items.map((t) =>
      `<option value="${esc(t.id)}">${esc(t.name)}${scope === "unit" && t.unit_name && state.me && state.me.role === "admin" ? ` (${esc(t.unit_name)})` : ""}</option>`).join("")}</optgroup>`;
  }).join("");
  setControl("templateSelect", state.templateId || "");
  updateTemplateButtons();
}

function updateTemplateButtons() {
  const t = (state.templates || []).find((x) => x.id === $("templateSelect").value);
  $("tplDeleteBtn").hidden = $("tplUpdateBtn").hidden = !(t && t.can_edit);
  const role = state.me ? state.me.role : "editor";
  $("tplScope").querySelector('[value="unit"]').hidden = !(role === "admin" || role === "unit_admin");
  $("tplScope").querySelector('[value="global"]').hidden = role !== "admin";
}

function applyTemplate(id) {
  const t = (state.templates || []).find((x) => x.id === id);
  state.templateId = t ? t.id : null;
  updateTemplateButtons();
  if (!t) { markDirty(); return; }
  const d = t.data, s = d.subtitles;
  setControl("subStyle", s.style); setControl("subFont", s.font); setControl("subSize", s.size);
  setControl("subColor", s.color.toLowerCase()); setControl("subHighlight", s.highlight.toLowerCase());
  setControl("subPosition", s.position); setControl("subUpper", s.uppercase); setControl("subWords", s.words_per_line);
  setControl("subEnabled", s.enabled);
  applySubExtras(s);
  if (d.look) {
    setControl("colorSelect", d.look.color); setControl("brightness", d.look.brightness);
    setControl("contrast", d.look.contrast); setControl("saturation", d.look.saturation);
  }
  if (d.aspect) setAspect(d.aspect, false);
  applyLivePreview();
  layoutCropFrame();
  markDirty();
  toast(`Template "${t.name}" dipakai. Pratinjau langsung berubah; render memakai gaya ini.`);
}

async function saveTemplate() {
  const name = (prompt("Nama preset (gaya subtitle & warna saat ini):", "") || "").trim();
  if (!name) return;
  const r = recipeFromControls();
  try {
    const t = await postJSON("/api/templates", { name, scope: $("tplScope").value, data: { subtitles: r.subtitles, look: r.look } });
    state.templateId = t.id;
    await loadTemplates();
    markDirty();
    toast(`Preset "${t.name}" disimpan.`);
  } catch (e) { toast(e.message, true); }
}

async function updateTemplate() {
  const t = (state.templates || []).find((x) => x.id === $("templateSelect").value);
  if (!t || !confirm(`Timpa template "${t.name}" dengan gaya subtitle & warna saat ini?`)) return;
  const r = recipeFromControls();
  try {
    await api(`/api/templates/${encodeURIComponent(t.id)}`, { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data: { subtitles: r.subtitles, look: r.look } }) });
    await loadTemplates();
    toast(`Template "${t.name}" diperbarui.`);
  } catch (e) { toast(e.message, true); }
}

async function deleteTemplate() {
  const t = (state.templates || []).find((x) => x.id === $("templateSelect").value);
  if (!t || !confirm(`Hapus template "${t.name}"? Proyek yang memakainya tidak berubah.`)) return;
  try {
    await api(`/api/templates/${encodeURIComponent(t.id)}`, { method: "DELETE" });
    state.templateId = null;
    await loadTemplates();
    markDirty();
  } catch (e) { toast(e.message, true); }
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
      <span class="meta">${esc(p.status)}${p.duration ? " · " + fmt(p.duration) : ""}${p.render_count ? " · " + p.render_count + " hasil render" : ""}${p.owner_name ? " · " + esc(p.owner_name) : ""}${state.me && state.me.role === "admin" && p.unit_name ? " · " + esc(p.unit_name) : ""}${mediaListLabel(p.media)}</span>
    </li>`).join("") || '<li class="muted">Belum ada proyek.</li>';
}

async function openProject(id) {
  if (state.project && (rs.dirty || rs.saving)) await flushRecipe().catch(() => {});   // jangan hilangkan edit
  const project = await api(`/api/projects/${id}`);
  state.checked.clear();
  state.activeClip = -1;
  state.selTouched = false;
  state.project = project;
  document.body.classList.add("project-open");
  $("mobileBar").hidden = false;
  await loadRecipe(id);
  $("emptyState").hidden = true;
  $("projectView").hidden = false;
  const player = $("player");
  player.removeAttribute("src");
  player.load();
  state.transcript = { segments: [] };
  state.plan = [];
  state.faces = [];
  state.assets = [];
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
      state.words = tr.segments.flatMap(segWords);
      state.clips = clips;
      const [planItems, faces] = await Promise.all([api(`/api/projects/${p.id}/plan`), api(`/api/projects/${p.id}/faces`)]);
      state.plan = planItems;
      state.faces = faces;
      api(`/api/projects/${p.id}/assets`).then((a) => { state.assets = a; }).catch(() => {});   // tidak menahan pembukaan
      layoutCropFrame();  // data wajah baru tersedia: posisikan ulang bingkai
      renderTranscript();
      scheduleSuggestions();
      renderClips();
      rs.loading = true;   // memasang rentang dari resep bukan perubahan pengguna
      try {
        // Pengguna sudah mengubah rentang saat data klip masih dimuat: jangan ditimpa rentang awal.
        if (state.selTouched) { renderClips(); return; }
        if (!applyRecipeSelection(rs.version > 0 ? rs.recipe : null)) {
          if (clips.length) selectClip(0, false);
          else setSelection(0, Math.min(30, p.duration || 30));
        }
      } finally { rs.loading = false; }
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
    box.classList.toggle("error", jobStalled(job));
    box.querySelector(".status-text").textContent = jobStatusText(job);
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
      // Nama/status proyek berubah (mis. unduhan dari link selesai sementara analisis masih menunggu):
      // perbarui daftar Proyek juga, jangan menunggu semua job selesai.
      if (state.project.name !== p.name || state.project.status !== p.status) refreshProjects();
      await refreshProjectData();
      if (!wasReady && state.project.status === "siap") {
        toast((state.transcript.segments || []).length ? "Analisis selesai! Pilih klip favorit Anda."
          : "Analisis selesai, tetapi tidak ada ucapan yang terdeteksi.", !(state.transcript.segments || []).length);
      }
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
  const noSpeech = !(state.transcript.segments || []).length;
  list.innerHTML = (noSpeech ? `<li class="notice">Tidak ada ucapan yang terdeteksi di video ini, jadi tidak ada subtitle.
    Kandidat di bawah hanya dipilih dari energi audio. Pastikan video bersuara, atau pilih rentang secara manual.</li>` : "") +
    state.clips.map((c, i) => `
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
  if (!rs.loading && state.project) state.selTouched = true;
  $("startInput").value = start.toFixed(1);
  $("endInput").value = end.toFixed(1);
  $("durLabel").textContent = `Durasi: ${(end - start).toFixed(1)} dtk`;
  renderTimeline();
  highlightTranscriptRange();
  renderPlan();
  markDirty();
  if (window.Studio) window.Studio.onSelection();
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

// Pegangan awal/akhir di timeline (P2): seret dengan jari atau mouse. Video ikut menuju titik yang diseret
// (dibatasi ~8x per detik agar ringan); rentang tersimpan otomatis lewat resep.
const tlDrag = { which: null, lastSeek: 0, justEnded: false };
const MIN_CLIP = 1.0;

function bindTimelineHandles() {
  const tl = $("timeline");
  tl.querySelectorAll(".tl-h").forEach((h) => {
    h.addEventListener("pointerdown", (e) => {
      if (!state.project || !state.project.duration) return;
      e.preventDefault();
      e.stopPropagation();
      tlDrag.which = h.dataset.h;
      try { h.setPointerCapture(e.pointerId); } catch { /* pointer sudah dilepas */ }
    });
    h.addEventListener("pointermove", (e) => {
      if (tlDrag.which !== h.dataset.h) return;
      const r = tl.getBoundingClientRect();
      const dur = state.project.duration;
      const t = Math.max(0, Math.min(dur, ((e.clientX - r.left) / r.width) * dur));
      if (tlDrag.which === "start") setSelection(Math.min(t, state.sel.end - MIN_CLIP), state.sel.end);
      else setSelection(state.sel.start, Math.max(t, state.sel.start + MIN_CLIP));
      const now = performance.now();
      if (now - tlDrag.lastSeek > 120) { tlDrag.lastSeek = now; $("player").currentTime = tlDrag.which === "start" ? state.sel.start : state.sel.end; }
    });
    const end = () => {
      if (!tlDrag.which) return;
      tlDrag.which = null;
      tlDrag.justEnded = true;
      setTimeout(() => { tlDrag.justEnded = false; }, 250);
    };
    h.addEventListener("pointerup", end);
    h.addEventListener("pointercancel", end);
  });
}

// HP (P2): tombol kembali ke daftar, bilah aksi bawah, pintasan bagian, simpan saat aplikasi ke latar belakang.
function scrollToSection(id) {
  const el = $(id);
  if (!el) return;
  const wrap = $("playerWrap");
  const sticky = getComputedStyle(wrap).position === "sticky" ? wrap.getBoundingClientRect().height : 0;
  window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - sticky - 8, behavior: "smooth" });
}

function bindMobile() {
  $("backBtn").addEventListener("click", () => {
    document.body.classList.remove("project-open");
    $("mobileBar").hidden = true;
    $("player").pause();
    window.scrollTo({ top: 0 });
  });
  $("mbPlay").addEventListener("click", () => {
    const player = $("player");
    if (!player.paused && state.playingClip) player.pause(); else playSelection();
  });
  $("mbStart").addEventListener("click", () => $("setStartBtn").click());
  $("mbEnd").addEventListener("click", () => $("setEndBtn").click());
  $("mbRender").addEventListener("click", () => $("renderBtn").click());
  $("mobileNav").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-target]");
    if (b) scrollToSection(b.dataset.target);
  });
  const flush = () => { if (state.project && rs.dirty && !rs.saving && !rs.conflict) saveRecipe(true); };
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });
  window.addEventListener("pagehide", flush);
}

// ------------------------------------------------------------ transkrip (editor: web/subeditor.js)
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
  if (save) markDirty();
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
  const fx = $("frameMode").value !== "manual" ? faceCenterAt($("player").currentTime) : null;
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

// Kata di rentang klip dengan aturan yang sama seperti render (app/subtitles.collect_words): dipotong ke rentang,
// diurutkan, dan kata yang bertumpuk digeser agar tidak mendahului kata sebelumnya (minimal 0,12 detik).
function clipWords(start, end) {
  const MIN_WORD = 0.12;
  const out = [];
  for (const w of state.words) {
    if (w.end <= start || w.start >= end) continue;
    const s = Math.max(w.start, start), e = Math.min(w.end, end);
    if (e > s) out.push({ start: s, end: e, word: w.word });
  }
  out.sort((a, b) => a.start - b.start || a.end - b.end);
  let last = start;
  return out.map((w) => {
    let s = Math.max(w.start, last), e = w.end;
    if (e - s < MIN_WORD) { e = Math.min(Math.max(e, s + MIN_WORD), end); s = Math.min(s, Math.max(start, e - MIN_WORD)); }
    last = Math.max(last, e);
    return { ...w, start: s, end: e };
  });
}

function updateCaptionPreview() {
  const cap = $("captionPreview");
  const t = $("player").currentTime;
  const frame = $("cropFrame");
  if (!$("subEnabled").checked || frame.hidden || t < state.sel.start || t > state.sel.end) { cap.innerHTML = ""; return; }
  const style = $("subStyle").value;
  const chunks = chunkWords(clipWords(state.sel.start, state.sel.end), style === "word" ? 1 : +$("subWords").value);
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
  cap.style.webkitTextStroke = `${Math.max(1, px * 0.16).toFixed(1)}px #000`;   // ASS: outline 8% di luar huruf
  cap.style.fontFamily = `"${$("subFont").value}", Impact, sans-serif`;
  cap.style.color = $("subColor").value;
  const pos = $("subPosition").value;
  cap.style.top = cap.style.bottom = "";
  cap.style.transform = "";
  if (pos === "custom") { cap.style.top = fh * +$("subPosY").value + "px"; cap.style.transform = "translateY(-50%)"; }
  else if (pos === "top") cap.style.top = fh * 0.12 + "px";
  else if (pos === "middle") { cap.style.top = "50%"; cap.style.transform = "translateY(-50%)"; }
  else cap.style.bottom = fh * 0.18 + "px";
  // M2.2 (perkiraan): outline, latar kotak. Animasi persis hanya di "Pratinjau hasil" (libass).
  cap.style.webkitTextStroke = `${Math.max(0, px * 0.16 * +$("subOutline").value).toFixed(1)}px ${$("subOutlineColor").value}`;
  const box = $("subBox").checked;
  const bc = $("subBoxColor").value, op = +$("subBoxOpacity").value;
  cap.style.background = box ? `rgba(${parseInt(bc.slice(1, 3), 16)},${parseInt(bc.slice(3, 5), 16)},${parseInt(bc.slice(5, 7), 16)},${op})` : "";
  cap.style.padding = box ? `${px * 0.15}px ${px * 0.25}px` : "";
  // Sama dengan event ASS: kata ke-k tersorot dari awalnya (kata pertama: dari awal baris) sampai kata berikutnya
  // mulai; gaya "satu kata" & "sederhana" tanpa warna sorotan.
  let k = 0;
  chunk.forEach((w, i) => { if (i > 0 && t >= w.start) k = i; });
  cap.innerHTML = chunk.map((w, i) => {
    const word = esc(upper ? w.word.toUpperCase() : w.word);
    if (["simple", "word", "cinematic", "documentary"].includes(style) || i !== k) return word;
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
              : `<div class="${jobStalled(j) ? "err" : "muted"}">${esc(jobStatusText(j))}</div><div class="bar"><div style="width:${j.progress}%"></div></div>
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
    const quality = selectedQuality();
    const meta = `filename ${b64(this.file.name)}` + (lang ? `,language ${b64(lang)}` : "") + (quality ? `,quality ${b64(quality)}` : "");
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

// Durasi video menurut browser (null bila browser tidak bisa membaca formatnya; server tetap memeriksa).
function videoDuration(file) {
  return new Promise((resolve) => {
    const v = document.createElement("video");
    const url = URL.createObjectURL(file);
    const done = (d) => { URL.revokeObjectURL(url); resolve(d); };
    v.preload = "metadata";
    v.onloadedmetadata = () => done(Number.isFinite(v.duration) ? v.duration : null);
    v.onerror = () => done(null);
    setTimeout(() => done(null), 10000);
    v.src = url;
  });
}

async function uploadFiles(files) {
  const maxSec = state.me && state.me.max_video_seconds;
  for (const file of files) {
    if (uploadCfg.maxMb && file.size > uploadCfg.maxMb * MB) { toast(`${file.name}: terlalu besar (maks ${uploadCfg.maxMb} MB).`, true); continue; }
    if (maxSec) {
      const d = await videoDuration(file);
      if (d && d > maxSec) {
        toast(`${file.name}: durasi ${(d / 60).toFixed(1)} menit melebihi batas ${Math.round(maxSec / 60)} menit. Potong videonya dulu.`, true);
        continue;
      }
    }
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
      const res = await postJSON("/api/projects/from-url", { url: $("urlInput").value.trim(), language: $("langSelect").value || null, quality: selectedQuality() });
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
      const q = new URLSearchParams();
      if (lang) q.set("language", lang);
      if (selectedQuality()) q.set("quality", selectedQuality());
      if (rs.recipe && Object.keys(rs.recipe.seg_edits || {}).length
          && !confirm("Analisis ulang membuat transkrip baru: koreksi subtitle di proyek ini akan dihapus. Lanjutkan?")) return;
      await api(`/api/projects/${state.project.id}/reanalyze${q.toString() ? "?" + q : ""}`, { method: "POST" });
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
    if ($("frameMode").value !== "manual" && state.faces.length) layoutCropFrame();
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
    try { handle.setPointerCapture(e.pointerId); } catch { /* pointer sudah dilepas */ }
  });
  handle.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const v = Math.max(0, Math.min(1, drag.start + (e.clientX - drag.x) / drag.range));
    $("cropX").value = v.toFixed(3);
    layoutCropFrame();
  });
  handle.addEventListener("pointerup", () => { if (drag) { drag = null; markDirty(); } });
  handle.addEventListener("pointercancel", () => { if (drag) { drag = null; markDirty(); } });

  bindTimelineHandles();
  $("timeline").addEventListener("click", (e) => {
    if (tlDrag.justEnded) return;
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
  bindMobile();

  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

  $("clipList").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-i]");
    if (!li) return;
    const i = +li.dataset.i;
    if (e.target.matches("input[type=checkbox]")) {
      if (e.target.checked) state.checked.add(i); else state.checked.delete(i);
      renderTimeline();
      markDirty();
      return;
    }
    selectClip(i);
  });

  initSubEditor(player);

  $("suggestBtn").addEventListener("click", async () => {
    try {
      state.clips = await postJSON(`/api/projects/${state.project.id}/clips/suggest`, { target: +$("targetSelect").value, top_n: 10 });
      state.checked.clear();
      state.activeClip = -1;
      renderClips();
      markDirty();
      toast(`${state.clips.length} kandidat klip ditemukan.`);
    } catch (err) { toast(err.message, true); }
  });

  $("renderBtn").addEventListener("click", async () => {
    try {
      const clip = state.clips[state.activeClip];
      const same = clip && Math.abs(clip.start - state.sel.start) < 0.05 && Math.abs(clip.end - state.sel.end) < 0.05;
      if (!(await confirmReviewed([state.sel]))) return;
      await submitRender(state.sel.start, state.sel.end, same ? `Klip #${state.activeClip + 1}` : "");
      await afterRenderSubmit(1);
    } catch (err) { toast(err.message, true); }
  });

  $("renderSelectedBtn").addEventListener("click", async () => {
    if (!state.checked.size) { toast("Centang dulu klip yang ingin dirender.", true); return; }
    try {
      const idx = [...state.checked].sort((a, b) => a - b);
      if (!(await confirmReviewed(idx.map((i) => state.clips[i])))) return;
      for (const i of idx) await submitRender(state.clips[i].start, state.clips[i].end, `Klip #${i + 1}`);
      await afterRenderSubmit(idx.length);
    } catch (err) { toast(err.message, true); }
  });

  $("batchExportBtn").addEventListener("click", async () => {
    if (!state.checked.size) { toast("Centang dulu klip yang ingin diekspor.", true); return; }
    const aspects = [...document.querySelectorAll(".batch-aspect:checked")].map((el) => el.value);
    if (!aspects.length) { toast("Pilih minimal satu rasio.", true); return; }
    try {
      await flushRecipe();
      const idx = [...state.checked].sort((a, b) => a - b);
      if (!(await confirmReviewed(idx.map((i) => state.clips[i])))) return;
      const clips = idx.map((i) => ({ start: state.clips[i].start, end: state.clips[i].end, title: `Klip #${i + 1}` }));
      let res;
      try {
        res = await postJSON(`/api/projects/${state.project.id}/render-batch`, { version: rs.version, clips, aspects });
      } catch (e) {
        if (e.status === 409 && e.data && e.data.current) showConflict(e.data.current, recipeFromControls());
        throw e;
      }
      await afterRenderSubmit(res.count);
    } catch (err) { toast(err.message, true); }
  });

  $("openExportBtn").addEventListener("click", async () => {
    try { await postJSON("/api/desktop/open-folder", {}); } catch (err) { toast(err.message, true); }
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
    $(id).addEventListener("change", () => { markDirty(); applyLivePreview(); layoutCropFrame(); });
    $(id).addEventListener("input", () => { if (id === "cropX") layoutCropFrame(); applyLivePreview(); updateCaptionPreview(); });
  }
  $("musicSelect").addEventListener("change", markDirty);
  $("templateSelect").addEventListener("change", (e) => applyTemplate(e.target.value));
  $("tplSaveBtn").addEventListener("click", saveTemplate);
  $("tplDeleteBtn").addEventListener("click", deleteTemplate);
  $("tplUpdateBtn").addEventListener("click", updateTemplate);
  $("saveState").addEventListener("click", () => { if (!rs.conflict) { rs.retry = 0; saveRecipe(); } });
  $("conflictLoadBtn").addEventListener("click", () => {
    const c = rs.conflict;
    if (!c) return;
    rs.conflict = null;
    $("conflictBanner").hidden = true;
    applyServerRecipe(c.current);
    toast("Versi terbaru dimuat.");
  });
  $("conflictKeepBtn").addEventListener("click", () => {
    const c = rs.conflict;
    if (!c) return;
    rs.conflict = null;
    $("conflictBanner").hidden = true;
    // Simpan pengaturan di layar ini di atas versi terbaru; koreksi teks & animasi dari server tetap dipakai.
    rs.version = c.current.version;
    rs.recipe = { ...c.mine, ...serverFields(c.current.recipe) };
    rs.dirty = true;
    saveRecipe();
  });
  window.addEventListener("beforeunload", (e) => {
    if (!state.project || !(rs.dirty || rs.saving)) return;
    try {   // simpan terakhir tanpa menunggu (keepalive tetap terkirim saat halaman ditutup)
      fetch(`/api/projects/${state.project.id}/recipe`, { method: "PUT", keepalive: true,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: rs.version, recipe: recipeFromControls() }) });
    } catch { /* abaikan */ }
    e.preventDefault();
    e.returnValue = "";
  });
  document.addEventListener("visibilitychange", checkRemoteRecipe);
  setInterval(checkRemoteRecipe, REMOTE_CHECK);
}

// ------------------------------------------------------------ rencana animasi
const PLAN_LABELS = { zoom: "🔍 Zoom", keyword: "🔤 Kata", card: "🪪 Kartu", chips: "🏷 Chips", checklist: "✅ Checklist",
  hook: "🎣 Hook", kinetic: "💥 Kinetic", lower_third: "👤 Narasumber", counter: "🔢 Angka", cta: "👉 CTA", image: "🖼 Gambar/logo" };
const POS_LABELS = { tr: "Kanan atas", tl: "Kiri atas", br: "Kanan bawah", bl: "Kiri bawah", top: "Atas tengah", center: "Tengah", bottom: "Bawah tengah" };

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
    } else if (it.type === "hook" || it.type === "kinetic" || it.type === "cta") {
      const ph = { hook: "Kalimat pembuka (mis. pertanyaan)", kinetic: "Kata-kata yang muncul bergantian", cta: "Ajakan (mis. Ikuti untuk kajian berikutnya)" }[it.type];
      fields = `<input data-f="text" value="${esc(it.text)}" placeholder="${ph}">`;
    } else if (it.type === "lower_third") {
      fields = `<input data-f="text" value="${esc(it.text)}" placeholder="Nama narasumber">
                <input class="label" data-f="label" value="${esc(it.label)}" placeholder="Jabatan / keterangan">`;
    } else if (it.type === "counter") {
      fields = `<input data-f="text" value="${esc(it.text)}" placeholder="Angka, mis. 2,5 kg atau Rp 50.000">
                <input class="label" data-f="label" value="${esc(it.label)}" placeholder="Keterangan (opsional)">`;
    } else if (it.type === "image") {
      const opts = (state.assets || []).map((a) => `<option${a === it.asset ? " selected" : ""}>${esc(a)}</option>`).join("");
      fields = `<select data-f="asset">${opts || '<option value="">(unggah gambar dulu)</option>'}</select>
        <select data-f="pos">${Object.entries(POS_LABELS).map(([k, l]) => `<option value="${k}"${k === it.pos ? " selected" : ""}>${l}</option>`).join("")}</select>
        <label class="mini">Ukuran <input type="range" data-f="scale" min="0.05" max="1" step="0.01" value="${it.scale ?? 0.22}"></label>
        <label class="mini">Pekat <input type="range" data-f="opacity" min="0.1" max="1" step="0.05" value="${it.opacity ?? 1}"></label>`;
    } else {
      fields = `<label class="mini">Kekuatan punch-in <input type="range" data-f="strength" min="0.05" max="0.4" step="0.01" value="${it.strength ?? 0.14}"></label>`;
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
  if (window.Studio) window.Studio.onDirty();
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
    if (i.type === "hook") return `<div class="ovp-hook" style="font-size:${unit * 0.105}px">${esc(i.text.toUpperCase())}</div>`;
    if (i.type === "kinetic") return `<div class="ovp-kinetic" style="font-size:${unit * 0.2}px">${esc((i.text.split(/\s+/)[0] || "").toUpperCase())}…</div>`;
    if (i.type === "counter") return `<div class="ovp-counter" style="font-size:${unit * 0.17}px">${esc(i.text)}${i.label ? `<small style="font-size:${unit * 0.045}px">${esc(i.label)}</small>` : ""}</div>`;
    if (i.type === "cta") return `<div class="ovp-cta" style="font-size:${unit * 0.06}px"><span>${esc(i.text)}</span></div>`;
    if (i.type === "lower_third") return `<div class="ovp-lower" style="font-size:${unit * 0.064}px"><b>${esc(i.text)}</b>${i.label ? `<small style="font-size:${unit * 0.044}px">${esc(i.label)}</small>` : ""}</div>`;
    if (i.type === "image") return `<img class="ovp-image ovp-${esc(i.pos || "tr")}" style="width:${(i.scale || 0.22) * 100}%;opacity:${i.opacity ?? 1}" src="/api/projects/${state.project.id}/assets/${encodeURIComponent(i.asset)}" alt="">`;
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
  if (me.desktop) { applyDesktopMode(me); window.EvalTool && window.EvalTool.setup(me); }
  setupQuality(me.transcribe || {});
  $("aiName").textContent = me.ai.name;
  $("planAiBtn").hidden = !me.ai.enabled;   // bawaan: tanpa AI eksternal, hanya ⚡ Otomatis (lokal)
  $("planAiBtn").disabled = !me.ai.ready;
  $("planAiBtn").title = me.ai.ready ? "Otomatis lewat API AI yang diatur admin"
    : "API AI belum diatur admin. Pakai 'Salin prompt' dulu.";
}

// Kualitas transkripsi (M1.1): Cepat / Akurat, dengan perkiraan RAM & waktu. Pilihan diingat di perangkat ini.
function setupQuality(info) {
  const qs = info.qualities || [];
  $("qualityField").hidden = !qs.length;
  if (!qs.length) return;
  let saved = null;
  try { saved = localStorage.getItem("avs-quality"); } catch { /* penyimpanan tidak tersedia */ }
  $("qualitySelect").innerHTML = qs.map((q) => `<option value="${q.id}">${esc(q.label)}</option>`).join("");
  $("qualitySelect").value = qs.some((q) => q.id === saved) ? saved : (info.default || qs[qs.length - 1].id);
  const hint = () => {
    const q = qs.find((x) => x.id === $("qualitySelect").value);
    const gb = (q.ram_mb / 1024).toFixed(1);
    $("qualityHint").textContent = `Butuh RAM ±${gb} GB` + (q.minutes_per_10min ? `, ±${q.minutes_per_10min} menit per 10 menit video (CPU 4 inti)` : "")
      + (q.note ? `. ${q.note}` : ".");
  };
  hint();
  $("qualitySelect").onchange = () => { hint(); try { localStorage.setItem("avs-quality", $("qualitySelect").value); } catch { /* abaikan */ } };
}

function selectedQuality() {
  return $("qualityField").hidden ? null : $("qualitySelect").value || null;
}

// Aplikasi desktop: satu pengguna lokal tanpa login; proyek tersimpan di komputer ini (tidak dihapus otomatis).
function applyDesktopMode(me) {
  document.body.classList.add("desktop");
  document.title = "NH AI Clip Studio";
  $("brand").textContent = "🎬 NH AI Clip Studio";
  $("logoutBtn").hidden = $("adminLink").hidden = $("accountLink").hidden = true;
  $("installBtn").remove();
  const note = document.querySelector(".storage-note");
  note.hidden = false;
  note.innerHTML = "💾 Proyek tersimpan di komputer ini dan <b>tidak dihapus otomatis</b>. "
    + "Hanya cache & file sementara yang dibersihkan.";
  $("batchExport").hidden = false;
  if (!$("langSelect").value) $("langSelect").value = "id";      // ceramah NH: bahasa Indonesia (bukan deteksi 30 dtk)
  $("emptyStart").textContent = "Pilih video dari komputer ini untuk mulai. Semua proses berjalan di komputer ini, tanpa internet.";
  document.querySelector(".upload-hint").textContent = "Video disalin ke folder aplikasi; file asli Anda tidak diubah.";
  if (me.export_dir) {
    $("exportFolderRow").hidden = false;
    $("exportFolderText").textContent = `Hasil render juga disalin ke: ${me.export_dir}`;
  }
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
    if (["start", "end", "strength", "scale", "opacity"].includes(f)) item[f] = +e.target.value;
    else if (f === "items") item.items = e.target.value.split(",").map((x) => x.trim()).filter(Boolean);
    else item[f] = e.target.value;
    $("ovPreview").dataset.key = "";
    savePlanSoon();
  });
  $("planList").addEventListener("change", (e) => {
    if (e.target.tagName !== "SELECT") return;
    const li = e.target.closest("li[data-id]");
    const item = li && state.plan.find((i) => i.id === li.dataset.id);
    if (!item) return;
    item[e.target.dataset.f] = e.target.value;
    $("ovPreview").dataset.key = "";
    savePlanSoon();
  });
  $("assetInput").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f || !state.project) return;
    const form = new FormData();
    form.append("file", f);
    try {
      const res = await api(`/api/projects/${state.project.id}/assets`, { method: "POST", body: form });
      state.assets = await api(`/api/projects/${state.project.id}/assets`);
      toast(`Gambar "${res.name}" diunggah. Tambahkan lewat "+ Gambar/logo".`);
      $("planAddType").value = "image";
      renderPlan();
    } catch (err) { toast(err.message, true); }
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
      text: { card: "Judul kartu", keyword: "KATA", hook: "Tahukah Anda?", kinetic: "Kata yang ingin ditekankan",
        lower_third: "Nama narasumber", counter: "100", cta: "Ikuti untuk kajian berikutnya" }[type] || "",
      label: { card: "Tips", lower_third: "Jabatan" }[type] || "",
      items: type === "chips" || type === "checklist" ? ["Poin 1", "Poin 2"] : [] };
    if (type === "image") {
      if (!(state.assets || []).length) { toast("Unggah gambar/logo dulu (tombol 🖼 Unggah gambar).", true); return; }
      Object.assign(item, { asset: state.assets[0], pos: "tr", scale: 0.18, opacity: 1, start: +state.sel.start.toFixed(1),
        end: +state.sel.end.toFixed(1) });
    }
    if (type === "hook") { item.start = +state.sel.start.toFixed(1); item.end = +Math.min(state.sel.end, state.sel.start + 2.8).toFixed(1); }
    if (type === "zoom") item.strength = 0.14;
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
  if (window.pwaClearCaches) await window.pwaClearCaches();
  location.replace("/login.html");
});
loadEditorConfig();
loadMe().then(() => {
  loadTemplates();
  loadUnitGuidebook();
  checkHealth();
  refreshProjects();
  loadServerUploads();
}).catch(() => { /* diarahkan ke halaman login */ });
