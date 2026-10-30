"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ROLE_LABELS = { admin: "Admin", unit_admin: "Admin Unit", editor: "Editor" };
let me = null;
let units = [];
let aiSettings = null;
let editingUnit = null;

function toast(msg, isError = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (isError ? " error" : "");
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (el.hidden = true), isError ? 7000 : 3000);
}

async function api(path, opts = {}) {
  if (opts.json !== undefined) {
    opts = { ...opts, headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.json) };
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    location.replace("/login.html?next=" + encodeURIComponent("/admin.html" + location.hash));
    throw new Error("Silakan login.");
  }
  let data = null;
  try { data = await res.json(); } catch { /* kosong */ }
  if (!res.ok) {
    let msg = data && data.detail ? data.detail : `Error ${res.status}`;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    throw new Error(msg);
  }
  return data;
}

const fmtTime = (t) => (t ? new Date(t * 1000).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" }) : "–");
const quota = (used, limit) => `${used}${limit ? " / " + limit : ""}`;
const fmtBytes = (b) => (b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.round((b || 0) / 1e6) + " MB");
const numOrNull = (v) => (v === "" || v === null ? null : Math.max(0, +v));

// ------------------------------------------------------------ navigasi
function allowed(el) {
  const r = el.dataset.role;
  if (!r) return true;
  if (r === "admin") return me.role === "admin";
  return me.role !== "editor";  // manager
}

function show(sec) {
  const btn = document.querySelector(`#nav [data-sec="${sec}"]`);
  if (!btn || btn.hidden) sec = me.role === "editor" ? "akun" : "status";
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.sec === sec));
  document.querySelectorAll("section[id^=sec-]").forEach((s) => (s.hidden = s.id !== "sec-" + sec));
  history.replaceState(null, "", "#" + sec);
  ({ status: loadStatus, units: loadUnits, users: loadUsers, ai: loadAi }[sec] || (() => {}))();
}

// ------------------------------------------------------------ status
async function loadStatus() {
  try {
    const [st, us] = await Promise.all([api("/api/admin/stats"), api("/api/admin/units")]);
    units = us;
    const q = st.queues;
    const count = (name, key) => (q[name] || {})[key] || 0;
    $("queueStats").innerHTML = [
      ["Analisis antre", count("analisis", "antre")], ["Render antre", count("render", "antre")],
      ["Unduhan antre", count("io", "antre")], ["Worker sibuk", q.busy_workers],
    ].map(([label, n]) => `<div class="stat"><b>${n}</b><span>${label}</span></div>`).join("");
    if (me.role === "admin") loadMetrics();
    const name = (id) => (units.find((u) => u.id === id) || {}).name || (id === "-" ? "Tanpa unit" : "Unit terhapus");
    const sto = st.storage || {};
    const states = sto.projects_by_state || {};
    const stats = [["Media sementara terpakai", fmtBytes(sto.media_bytes)],
      ["Proyek dengan media", states.tersedia || 0], ["Media sudah dihapus", states.dihapus || 0]];
    if (sto.disk) stats.push(["Sisa disk server", `${fmtBytes(sto.disk.free)} dari ${fmtBytes(sto.disk.total)}`]);
    $("storageStats").innerHTML = stats.map(([l, v]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
    $("legacyHint").hidden = !sto.legacy_projects;
    $("legacyHint").innerHTML = `${sto.legacy_projects} proyek lama belum masuk penyimpanan sementara dan tidak akan dihapus otomatis.
      Untuk memindahkannya (dengan masa tenggang), jalankan di server: <code>python -m app.manage media-migrate</code> (lihat DEPLOY.md).`;
    const rows = Object.entries(st.last_30_days);
    $("usageTable").innerHTML = "<tr><th>Unit</th><th>Video dianalisis</th><th>Klip dirender</th><th>Permintaan AI</th></tr>" +
      (rows.map(([id, k]) => `<tr><td>${esc(name(id))}</td><td>${k.analyze || 0}</td><td>${k.render || 0}</td><td>${k.ai || 0}</td></tr>`).join("")
        || '<tr><td colspan="4" class="muted">Belum ada pemakaian.</td></tr>');
  } catch (e) { toast(e.message, true); }
}

// Monitoring rinci (admin pusat): mesin, antrean, dan worker. Diperbarui tiap 10 detik selama halaman Status dibuka.
const QUEUE_NAMES = { analisis: "Analisis (Whisper)", render: "Render (FFmpeg)", io: "Unduhan dari link" };
const WORKER_STATE = { idle: "siap", sibuk: "bekerja", menunggu: "menunggu resource", berhenti: "berhenti" };
const CAPACITY = {
  siap: ["siap", ""], sementara: ["menunggu RAM/CPU (sementara)", "muted"],
  tanpa_worker: ["tidak ada worker hidup (offline/start)", "exp-gone"],
  mampu_offline: ["worker yang mampu sedang offline", "exp-gone"],
  konfigurasi: ["tidak dapat diproses: RAM worker tidak cukup (konfigurasi)", "exp-gone"],
};
const secs = (v) => (v === null || v === undefined ? "–" : v < 90 ? `${Math.round(v)} dtk` : `${(v / 60).toFixed(1)} mnt`);
const capacityTxt = (c) => {
  if (!c) return "–";
  const [label, cls] = CAPACITY[c.state] || [c.state, ""];
  return `<span class="${cls}">${esc(label)}</span>${c.reason ? `<div class="muted">${esc(c.reason)}</div>` : ""}`;
};
const pctTxt = (v) => (v === null || v === undefined ? "–" : `${Math.round(v)}%`);
let metricsTimer = null;

async function loadMetrics() {
  $("metricsBox").hidden = false;
  try {
    const m = await api("/api/admin/metrics");
    const web = m.web || {};
    const gpu = (web.gpus || []).map((g) => `${esc(g.name)}: ${pctTxt(g.util_pct)}, VRAM ${g.mem_used_mb}/${g.mem_total_mb} MB`).join("<br>") || "Tidak ada (CPU saja)";
    $("systemStats").innerHTML = [
      ["CPU server web", `${pctTxt(web.cpu_pct)} · ${web.cpus} core`],
      ["Load 1 menit", web.load ? web.load[0] : "–"],
      ["RAM tersedia", web.mem_total ? `${fmtBytes(web.mem_available)} dari ${fmtBytes(web.mem_total)}` : "–"],
      ["Disk data tersisa", `${fmtBytes(m.disk.free)} dari ${fmtBytes(m.disk.total)}`],
      ["GPU", gpu],
    ].map(([l, v]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
    $("queueTable").innerHTML = "<tr><th>Antrean</th><th>Menunggu</th><th>Berjalan</th><th>Tertua menunggu</th><th>Tunggu p50 / p95</th><th>Durasi p95</th><th>Selesai / gagal (1 jam)</th><th>Kapasitas</th></tr>" +
      Object.entries(m.queues).map(([name, s]) => {
        const dur = Object.values(s.duration || {})[0];
        return `<tr><td><b>${QUEUE_NAMES[name] || esc(name)}</b></td><td>${s.queued}</td><td>${s.running}</td>
          <td>${s.queued ? secs(s.oldest_wait) : "–"}</td><td>${secs(s.wait_p50)} / ${secs(s.wait_p95)}</td>
          <td>${dur ? secs(dur.p95) : "–"}</td><td>${s.done} / <span class="${s.failed ? "exp-gone" : ""}">${s.failed}</span></td>
          <td>${capacityTxt(s.capacity)}</td></tr>`;
      }).join("");
    const ws = m.workers || [];
    $("workerTable").innerHTML = "<tr><th>Worker</th><th>Antrean</th><th>Status</th><th>CPU</th><th>RAM</th><th>Thread</th><th>Selesai / gagal</th><th>Kabar terakhir</th></tr>" +
      (ws.map((w) => `<tr class="${w.alive ? "" : "muted"}">
        <td>${esc(w.name)}<div class="muted">${esc(w.host)}</div></td><td>${esc(w.queues)}</td>
        <td>${w.alive ? (WORKER_STATE[w.state] || esc(w.state)) : "<span class='exp-gone'>tidak merespons</span>"}${w.blocked ? ` <span class="exp-gone">tertahan ${secs(w.blocked_seconds)}</span>` : ""}${w.reason ? `<div class="muted">${esc(w.reason)}</div>` : ""}</td>
        <td>${pctTxt(w.cpu_pct)}</td><td>${w.rss_mb ? Math.round(w.rss_mb) + " MB" : "–"}</td><td>${w.threads || "–"}</td>
        <td>${w.jobs_ok} / ${w.jobs_failed}</td><td>${secs(w.heartbeat_age)} lalu</td></tr>`).join("")
        || '<tr><td colspan="8" class="muted">Belum ada worker yang melapor.</td></tr>');
  } catch (e) { toast(e.message, true); }
  clearTimeout(metricsTimer);
  metricsTimer = setTimeout(() => { if (!$("sec-status").hidden) loadMetrics(); }, 10000);
}

// ------------------------------------------------------------ unit
async function loadUnits() {
  try {
    units = await api("/api/admin/units");
    $("unitTable").innerHTML = "<tr><th>Unit</th><th>Anggota</th><th>Render hari ini</th><th>AI hari ini</th><th>Media sementara</th><th>Guidebook</th><th></th></tr>" +
      (units.map((u) => `<tr>
        <td><b>${esc(u.name)}</b></td>
        <td>${u.members}</td>
        <td>${quota(u.renders_today, u.daily_render_quota)}</td>
        <td>${quota(u.ai_today, u.daily_ai_quota)}</td>
        <td>${fmtBytes(u.storage_used)}${u.storage_quota ? " / " + fmtBytes(u.storage_quota) : ""}</td>
        <td class="muted">${u.guidebook ? esc(u.guidebook.slice(0, 60)) + (u.guidebook.length > 60 ? "…" : "") : "–"}</td>
        <td style="white-space:nowrap"><button class="ghost" data-edit="${u.id}">Atur</button>
          ${me.role === "admin" ? `<button class="ghost danger" data-del="${u.id}">Hapus</button>` : ""}</td>
      </tr>`).join("") || '<tr><td colspan="7" class="muted">Belum ada unit. Tambahkan di bawah.</td></tr>');
  } catch (e) { toast(e.message, true); }
}

function openUnitEditor(id) {
  editingUnit = units.find((u) => u.id === id);
  if (!editingUnit) return;
  $("unitEditor").hidden = false;
  $("unitEditorTitle").textContent = "Atur unit: " + editingUnit.name;
  $("editUnitName").value = editingUnit.name;
  $("editUnitRender").value = editingUnit.daily_render_quota;
  $("editUnitAi").value = editingUnit.daily_ai_quota;
  $("editUnitStorage").value = editingUnit.storage_quota_mb ?? "";
  $("editUnitGuidebook").value = editingUnit.guidebook || "";
  $("unitEditor").scrollIntoView({ behavior: "smooth" });
}

// ------------------------------------------------------------ pengguna
async function loadUsers() {
  try {
    const [users, us] = await Promise.all([api("/api/admin/users"), api("/api/admin/units")]);
    units = us;
    const unitOptions = (sel) => units.map((u) => `<option value="${u.id}" ${u.id === sel ? "selected" : ""}>${esc(u.name)}</option>`).join("");
    $("newUnit").innerHTML = '<option value="">– pilih unit –</option>' + unitOptions(null);
    const isAdmin = me.role === "admin";
    $("userTable").innerHTML = "<tr><th>Nama</th><th>Email</th><th>Peran</th><th>Unit</th><th>Status</th><th>Login terakhir</th><th></th></tr>" +
      users.map((u) => {
        const self = u.id === me.id;
        const roleSel = `<select data-f="role" data-id="${u.id}" ${self ? "disabled" : ""}>` +
          Object.entries(ROLE_LABELS).filter(([k]) => isAdmin || k !== "admin")
            .map(([k, v]) => `<option value="${k}" ${k === u.role ? "selected" : ""}>${v}</option>`).join("") + "</select>";
        const unitSel = isAdmin
          ? `<select data-f="unit_id" data-id="${u.id}"><option value="">– tanpa unit –</option>${unitOptions(u.unit_id)}</select>`
          : esc(u.unit_name || "–");
        return `<tr>
          <td>${esc(u.name)}${self ? ' <span class="badge">Anda</span>' : ""}</td>
          <td>${esc(u.email)}</td>
          <td>${roleSel}</td>
          <td>${unitSel}</td>
          <td>${u.active ? '<span class="badge ok">aktif</span>' : '<span class="badge off">nonaktif</span>'}</td>
          <td class="muted">${fmtTime(u.last_login)}</td>
          <td style="white-space:nowrap">
            <button class="ghost" data-reset="${u.id}" data-name="${esc(u.name)}">Reset password</button>
            ${self ? "" : `<button class="ghost ${u.active ? "danger" : ""}" data-active="${u.id}" data-to="${u.active ? "0" : "1"}">${u.active ? "Nonaktifkan" : "Aktifkan"}</button>`}
          </td></tr>`;
      }).join("");
  } catch (e) { toast(e.message, true); }
}

async function updateUser(id, values, okMsg) {
  try {
    await api(`/api/admin/users/${id}`, { method: "PUT", json: values });
    toast(okMsg || "Disimpan.");
  } catch (e) { toast(e.message, true); }
  loadUsers();
}

// ------------------------------------------------------------ AI
function showProvider() {
  const name = $("aiProvider").value;
  const p = aiSettings.providers[name];
  $("apiModel").value = p.model;
  $("apiModel").placeholder = p.default_model || "Kosongkan = dipilih otomatis";
  $("apiBaseUrl").value = p.base_url;
  $("apiBaseUrl").placeholder = p.default_base_url || "https://.../v1";
  $("baseUrlField").hidden = !(name === "custom" || name === "ollama");
  $("apiKey").value = "";
  $("apiKey").placeholder = p.needs_key ? (p.has_key ? `Tersimpan (${p.key_hint}), isi untuk mengganti` : "Tempel API key di sini") : "Tidak perlu (opsional)";
  $("keyStatus").textContent = !p.needs_key ? "" : p.has_key ? `Kunci tersimpan (${p.key_hint})` : "Belum ada API key";
  $("providerHint").innerHTML = name === "ollama"
    ? 'AI lokal: pasang <a href="https://ollama.com/download" target="_blank" rel="noopener">Ollama</a> di server, lalu misalnya <code>ollama pull qwen2.5:3b</code>.'
    : p.key_url ? `Buat API key di <a href="${esc(p.key_url)}" target="_blank" rel="noopener">${esc(p.key_url.replace(/^https?:\/\//, ""))}</a>.` : "";
}

async function loadAi() {
  try {
    aiSettings = await api("/api/settings");
    $("aiProvider").innerHTML = Object.entries(aiSettings.providers)
      .map(([k, p]) => `<option value="${k}">${esc(p.label)}</option>`).join("");
    $("aiProvider").value = aiSettings.provider;
    showProvider();
  } catch (e) { toast(e.message, true); }
}

async function saveAi(showToast = true) {
  try {
    aiSettings = await api("/api/settings", { method: "PUT", json: {
      provider: $("aiProvider").value, api_key: $("apiKey").value.trim() || null,
      model: $("apiModel").value.trim(), base_url: $("apiBaseUrl").value.trim(),
    } });
    showProvider();
    if (showToast) toast("Pengaturan AI disimpan.");
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------ event
function bind() {
  $("nav").addEventListener("click", (e) => { if (e.target.dataset.sec) show(e.target.dataset.sec); });
  $("logoutBtn").addEventListener("click", async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch { /* abaikan */ }
    location.replace("/login.html");
  });

  $("unitForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/admin/units", { method: "POST", json: { name: $("unitName").value.trim(),
        daily_render_quota: +$("unitRenderQuota").value || 0, daily_ai_quota: +$("unitAiQuota").value || 0,
        storage_quota_mb: numOrNull($("unitStorageQuota").value) } });
      $("unitName").value = "";
      toast("Unit ditambahkan.");
      loadUnits();
    } catch (err) { toast(err.message, true); }
  });
  $("unitTable").addEventListener("click", async (e) => {
    if (e.target.dataset.edit) openUnitEditor(e.target.dataset.edit);
    if (e.target.dataset.del) {
      const u = units.find((x) => x.id === e.target.dataset.del);
      if (!u || !confirm(`Hapus unit "${u.name}"?`)) return;
      try { await api(`/api/admin/units/${u.id}`, { method: "DELETE" }); toast("Unit dihapus."); loadUnits(); }
      catch (err) { toast(err.message, true); }
    }
  });
  $("unitCancelBtn").addEventListener("click", () => { $("unitEditor").hidden = true; });
  $("unitSaveBtn").addEventListener("click", async () => {
    if (!editingUnit) return;
    try {
      await api(`/api/admin/units/${editingUnit.id}`, { method: "PUT", json: {
        name: $("editUnitName").value.trim() || editingUnit.name, daily_render_quota: +$("editUnitRender").value || 0,
        daily_ai_quota: +$("editUnitAi").value || 0, guidebook: $("editUnitGuidebook").value,
        ...(me.role === "admin" ? { storage_quota_mb: numOrNull($("editUnitStorage").value) } : {}) } });
      toast("Unit disimpan.");
      $("unitEditor").hidden = true;
      loadUnits();
    } catch (err) { toast(err.message, true); }
  });

  $("userForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/admin/users", { method: "POST", json: { email: $("newEmail").value.trim(), name: $("newName").value.trim(),
        password: $("newPassword").value, role: $("newRole").value, unit_id: $("newUnit").value || null } });
      toast(`Akun ${$("newEmail").value} dibuat.`);
      $("userForm").reset();
      loadUsers();
    } catch (err) { toast(err.message, true); }
  });
  $("userTable").addEventListener("change", (e) => {
    const f = e.target.dataset.f;
    if (f) updateUser(e.target.dataset.id, { [f]: e.target.value }, "Perubahan disimpan.");
  });
  $("userTable").addEventListener("click", (e) => {
    const t = e.target;
    if (t.dataset.reset) {
      const pw = prompt(`Password baru untuk ${t.dataset.name} (min. 8 karakter):`);
      if (pw) updateUser(t.dataset.reset, { password: pw }, "Password diganti. Pengguna perlu login ulang.");
    }
    if (t.dataset.active) updateUser(t.dataset.active, { active: t.dataset.to === "1" });
  });

  $("aiProvider").addEventListener("change", () => { $("modelList").innerHTML = ""; showProvider(); saveAi(false); });
  $("settingsSaveBtn").addEventListener("click", () => saveAi());
  $("modelsBtn").addEventListener("click", async () => {
    await saveAi(false);
    try {
      const models = await api("/api/ai/models");
      $("modelList").innerHTML = models.map((m) => `<option value="${esc(m)}">`).join("");
      toast(`${models.length} model ditemukan. Klik kolom Model untuk memilih.`);
      $("apiModel").focus();
    } catch (err) { toast(err.message, true); }
  });

  $("pwForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if ($("newPw").value !== $("newPw2").value) { toast("Password baru tidak sama.", true); return; }
    try {
      await api("/api/auth/password", { method: "POST", json: { old_password: $("oldPw").value, new_password: $("newPw").value } });
      $("pwForm").reset();
      toast("Password berhasil diganti.");
    } catch (err) { toast(err.message, true); }
  });
}

(async () => {
  try {
    me = await api("/api/auth/me");
  } catch { return; }
  $("whoami").innerHTML = `<b>${esc(me.name)}</b><br>${esc(me.role_label)}${me.unit_name ? " · " + esc(me.unit_name) : ""}`;
  document.querySelectorAll("[data-role]").forEach((el) => { if (!allowed(el)) el.hidden = true; });
  bind();
  show((location.hash || "#status").slice(1));
})();
