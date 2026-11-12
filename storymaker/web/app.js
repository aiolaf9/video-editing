"use strict";
const $ = (s, el = document) => el.querySelector(s);
const ROLE = {hook: "Hook", problem: "Masalah", solution: "Proses / Solusi", impact: "Dampak", closing: "Penutup"};
const ROLES = Object.keys(ROLE);
const S = {cfg: null, pid: null, p: null, catalog: null, poll: null, el: null};

async function api(path, opts = {}) {
  const o = {...opts};
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers = {"Content-Type": "application/json"}; delete o.json; }
  const r = await fetch(path, o);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : r.statusText);
  return data;
}
const file = (path) => `/api/projects/${S.pid}/files/${path}`;
const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

async function init() {
  S.cfg = await api("/api/config");
  $("#aiBadge").textContent = S.cfg.claude ? "✅ Claude AI aktif" : "Mode lokal (set ANTHROPIC_API_KEY untuk Claude)";
  $("#newForm").onsubmit = async (e) => {
    e.preventDefault();
    const p = await api("/api/projects", {method: "POST", json: {name: $("#newName").value || "Cerita baru"}});
    $("#newName").value = "";
    await loadList(); open(p.id);
  };
  await loadList();
  const last = localStorage.getItem("story.pid");
  if (last) open(last).catch(() => {});
}

async function loadList() {
  const list = await api("/api/projects");
  $("#projectList").innerHTML = list.map((p) =>
    `<li data-id="${p.id}" class="${p.id === S.pid ? "on" : ""}">${esc(p.name)}<small>${p.clips} video · ${esc(p.status)}</small></li>`).join("");
  $("#projectList").querySelectorAll("li").forEach((li) => li.onclick = () => open(li.dataset.id));
}

async function open(pid) {
  S.pid = pid; localStorage.setItem("story.pid", pid);
  S.catalog = null;
  const main = $("#main"); main.innerHTML = "";
  main.append($("#tplProject").content.cloneNode(true));
  S.el = main;
  bindProject();
  await refresh();
  loadList();
}

function bindProject() {
  const el = S.el;
  const drop = $(".drop", el);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files); };
  $(".fileIn", el).onchange = (e) => upload(e.target.files);
  $(".target", el).onchange = (e) => api(`/api/projects/${S.pid}`, {method: "PATCH", json: {target: +e.target.value}});
  $(".guide", el).onchange = (e) => api(`/api/projects/${S.pid}`, {method: "PATCH", json: {guidance: e.target.value}});
  $(".btnAnalyze", el).onclick = () => run(`/api/projects/${S.pid}/analyze`, {planner: $(".planner", el).value});
  $(".btnReplan", el).onclick = () => run(`/api/projects/${S.pid}/plan`, {planner: $(".planner", el).value});
  $(".btnRender", el).onclick = () => render();
  $(".btnCopyPrompt", el).onclick = async () => {
    const {prompt} = await api(`/api/projects/${S.pid}/prompt`);
    await navigator.clipboard.writeText(prompt);
    alert("Prompt disalin. Tempel di Claude.ai, lalu salin jawaban JSON-nya dan klik 'Tempel jawaban'.");
  };
  $(".btnPaste", el).onclick = async () => {
    const text = window.prompt("Tempel jawaban JSON dari Claude:");
    if (!text) return;
    try { await api(`/api/projects/${S.pid}/plan/paste`, {method: "POST", json: {text}}); await refresh(); }
    catch (e) { alert(e.message); }
  };
  $(".planTitle", el).onchange = (e) => { S.p.plan.title = e.target.value; savePlan(); };
  $(".musicIn", el).onchange = async (e) => {
    const fd = new FormData(); fd.append("file", e.target.files[0]);
    try { const r = await api(`/api/projects/${S.pid}/music`, {method: "POST", body: fd}); S.music = r.music; }
    catch (err) { alert(err.message); }
  };
}

async function upload(files) {
  if (!files || !files.length) return;
  const fd = new FormData();
  [...files].forEach((f) => fd.append("files", f));
  const bar = $(".uploadBar", S.el); bar.classList.remove("hidden");
  await new Promise((resolve) => {
    const x = new XMLHttpRequest();
    x.open("POST", `/api/projects/${S.pid}/clips`);
    x.upload.onprogress = (e) => { bar.firstElementChild.style.width = `${100 * e.loaded / e.total}%`; };
    x.onload = () => {
      try { const r = JSON.parse(x.responseText); if (r.errors?.length) alert(r.errors.join("\n")); if (r.detail) alert(r.detail); } catch (_) {}
      resolve();
    };
    x.onerror = () => { alert("Upload gagal."); resolve(); };
    x.send(fd);
  });
  bar.classList.add("hidden");
  await refresh(); loadList();
}

async function run(url, body) {
  try { await api(url, {method: "POST", json: body}); } catch (e) { alert(e.message); }
  await refresh();
}

async function refresh() {
  const p = await api(`/api/projects/${S.pid}`);
  S.p = p;
  const el = S.el;
  $(".pname", el).textContent = p.name;
  const tsel = $(".target", el);
  if (![...tsel.options].some((o) => o.value === String(p.target))) tsel.add(new Option(String(p.target), String(p.target)));
  tsel.value = String(p.target);
  $(".guide", el).value = p.guidance || "";
  $(".clipcount", el).textContent = `(${p.clips.length}/${S.cfg.max_clips}, minimal ${S.cfg.min_clips})`;
  $(".clips", el).innerHTML = p.clips.sort((a, b) => a.order - b.order).map((c) => `
    <div class="clip">
      ${c.status === "siap" ? `<video src="${file(`clips/${c.id}/preview.mp4`)}" preload="metadata" controls></video>` : `<video></video>`}
      <div class="meta"><span class="name" title="${esc(c.name)}"><b>${c.id}</b> ${esc(c.name)}</span>
      <span>${fmt(c.info.duration)}</span><button data-del="${c.id}" title="Hapus">✕</button></div>
    </div>`).join("");
  el.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
    if (!confirm("Hapus video ini dari proyek?")) return;
    try { await api(`/api/projects/${S.pid}/clips/${b.dataset.del}`, {method: "DELETE"}); } catch (e) { alert(e.message); }
    refresh();
  });
  $(".btnAnalyze", el).disabled = p.running || p.clips.length < S.cfg.min_clips;
  $(".btnRender", el).disabled = p.running || !p.plan;
  $(".btnReplan", el).disabled = p.running;

  const job = $(".job", el);
  if (p.job) {
    job.classList.remove("hidden");
    $(".bar div", job).style.width = `${p.job.progress ?? 0}%`;
    const msg = $(".jobMsg", job);
    msg.textContent = p.job.error ? `❌ ${p.job.error}` : p.job.message;
    msg.classList.toggle("err", !!p.job.error);
  }
  if (p.plan) { await ensureCatalog(); drawPlan(); }
  $(".planCard", el).classList.toggle("hidden", !p.plan);
  $(".renderCard", el).classList.toggle("hidden", !p.plan);
  drawOutputs();

  clearTimeout(S.poll);
  if (p.running) S.poll = setTimeout(refresh, 1500);
  else if (S.wasRunning) { S.catalog = null; S.wasRunning = false; loadList(); if (p.plan) { await ensureCatalog(); drawPlan(); } }
  if (p.running) S.wasRunning = true;
}

async function ensureCatalog() { if (!S.catalog) S.catalog = await api(`/api/projects/${S.pid}/catalog`); }
const shotById = (id) => S.catalog.shots.find((s) => s.id === id);
const thumb = (s) => file(`clips/${s.clip_id}/thumbs/${s.id}.jpg`);

function drawPlan() {
  const el = S.el, plan = S.p.plan;
  $(".planTitle", el).value = plan.title || "";
  const src = {claude: "disusun Claude AI", heuristic: "disusun otomatis lokal", manual: "diedit manual"}[plan.source] || plan.source;
  $(".planInfo", el).textContent = `· ${plan.duration} detik (target ${plan.target}) · ${src}`;
  $(".warnings", el).innerHTML = (plan.warnings || []).map((w) => `<div>⚠ ${esc(w)}</div>`).join("");
  const hookRef = plan.sections.find((s) => s.role === "hook")?.items[0]?.ref;
  $(".hooks", el).innerHTML = `<b>Pilihan hook</b> <span class="hint">(klik untuk mengganti)</span>` + S.p.hooks.map((h) =>
    `<span class="h ${h.id === hookRef ? "on" : ""}" data-h="${h.id}">${(h.score * 100).toFixed(0)} · “${esc(h.text)}” <span class="hint">${h.duration}s · ${h.id}</span></span>`).join("");
  el.querySelectorAll("[data-h]").forEach((n) => n.onclick = () => setHook(n.dataset.h));

  const box = $(".sections", el);
  box.innerHTML = "";
  for (const role of ROLES) {
    const sec = plan.sections.find((s) => s.role === role) || {role, items: []};
    const div = document.createElement("div");
    div.className = `sec ${role}`;
    div.innerHTML = `<h4>${ROLE[role]}</h4>` + sec.items.map((it, i) => `
      <div class="item">
        <div>
          <div class="txt" data-edit="${it.kind === "speech" ? it.ref : ""}" title="${it.kind === "speech" ? "Klik untuk koreksi teks subtitle" : ""}">${it.kind === "speech" ? "“" + esc(it.text) + "”" : "🎞 " + esc(it.text)}</div>
          <div class="src">${it.ref} · ${it.clip_id} ${fmt(it.start)}–${fmt(it.end)} · ${it.duration}s</div>
          <div class="broll">${(it.broll || []).map((b) => { const s = shotById(b); return s ? `<figure><img src="${thumb(s)}"><figcaption>${esc(s.description || b)}</figcaption><button class="x" data-rb="${role}|${i}|${b}">✕</button></figure>` : ""; }).join("")}
          ${it.kind === "speech" ? `<button class="addBtn" data-ab="${role}|${i}">+ B-roll</button>` : ""}</div>
        </div>
        <div class="ops">
          <button data-mv="${role}|${i}|-1" title="Naik">↑</button><button data-mv="${role}|${i}|1" title="Turun">↓</button>
          <button data-rm="${role}|${i}" title="Hapus">✕</button>
        </div>
      </div>`).join("") + `<button class="addBtn" data-add="${role}">+ Tambah kalimat</button>`;
    box.append(div);
  }
  box.querySelectorAll("[data-mv]").forEach((b) => b.onclick = () => { const [r, i, d] = b.dataset.mv.split("|"); move(r, +i, +d); });
  box.querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => { const [r, i] = b.dataset.rm.split("|"); sec(r).items.splice(+i, 1); savePlan(); });
  box.querySelectorAll("[data-rb]").forEach((b) => b.onclick = () => { const [r, i, id] = b.dataset.rb.split("|"); const it = sec(r).items[+i]; it.broll = it.broll.filter((x) => x !== id); savePlan(); });
  box.querySelectorAll("[data-ab]").forEach((b) => b.onclick = () => { const [r, i] = b.dataset.ab.split("|"); pickShot((id) => { sec(r).items[+i].broll.push(id); savePlan(); }); });
  box.querySelectorAll("[data-add]").forEach((b) => b.onclick = () => pickUtterance((id) => { sec(b.dataset.add, true).items.push({kind: "speech", ref: id, broll: []}); savePlan(); }));
  box.querySelectorAll("[data-edit]").forEach((n) => { if (n.dataset.edit) n.onclick = () => editText(n.dataset.edit); });
}

function sec(role, create = false) {
  const plan = S.p.plan;
  let s = plan.sections.find((x) => x.role === role);
  if (!s && create) { s = {role, items: []}; plan.sections.push(s); plan.sections.sort((a, b) => ROLES.indexOf(a.role) - ROLES.indexOf(b.role)); }
  return s;
}

function move(role, i, d) {
  const s = sec(role);
  const j = i + d;
  if (j >= 0 && j < s.items.length) { [s.items[i], s.items[j]] = [s.items[j], s.items[i]]; return savePlan(); }
  // pindah ke bagian sebelumnya/sesudahnya
  const k = ROLES.indexOf(role) + d;
  if (k < 0 || k >= ROLES.length) return;
  const [it] = s.items.splice(i, 1);
  const t = sec(ROLES[k], true);
  d < 0 ? t.items.push(it) : t.items.unshift(it);
  savePlan();
}

function setHook(id) {
  const plan = S.p.plan;
  plan.sections.forEach((s) => s.items = s.items.filter((it) => it.ref !== id));
  const h = sec("hook", true);
  h.items = [{kind: "speech", ref: id, broll: []}];
  savePlan();
}

async function savePlan() {
  const plan = S.p.plan;
  const clean = {title: plan.title, hook_reason: plan.hook_reason, source: plan.source, target: plan.target,
    sections: plan.sections.map((s) => ({role: s.role, items: s.items.map((it) => ({kind: it.kind, ref: it.ref, broll: it.broll || []}))}))};
  try { S.p.plan = await api(`/api/projects/${S.pid}/plan`, {method: "PUT", json: clean}); } catch (e) { alert(e.message); }
  drawPlan();
}

async function editText(uid) {
  const u = S.catalog.utterances.find((x) => x.id === uid);
  const text = window.prompt("Koreksi teks subtitle (hanya perbaiki salah dengar, jangan ubah makna):", u.text);
  if (!text || text === u.text) return;
  await api(`/api/projects/${S.pid}/utterances/${uid}`, {method: "PUT", json: {text}});
  S.catalog = null; await refresh();
}

function dialog(html, onPick) {
  const d = document.createElement("dialog");
  d.innerHTML = `<div class="row"><b>Pilih</b><button class="close">Tutup</button></div><div class="list">${html}</div>`;
  document.body.append(d);
  d.querySelector(".close").onclick = () => d.remove();
  d.querySelectorAll("[data-pick]").forEach((n) => n.onclick = () => { d.remove(); onPick(n.dataset.pick); });
  d.showModal();
}

function usedRefs() {
  const used = new Set();
  S.p.plan.sections.forEach((s) => s.items.forEach((it) => { used.add(it.ref); (it.broll || []).forEach((b) => used.add(b)); }));
  return used;
}

function pickUtterance(cb) {
  const used = usedRefs();
  dialog(S.catalog.utterances.filter((u) => !used.has(u.id)).map((u) =>
    `<div class="opt" data-pick="${u.id}">“${esc(u.text)}” <span class="hint">${u.id} · ${u.duration}s</span></div>`).join(""), cb);
}

function pickShot(cb) {
  const used = usedRefs();
  dialog([...S.catalog.shots].filter((s) => !used.has(s.id)).sort((a, b) => b.quality - a.quality).map((s) =>
    `<div class="opt" data-pick="${s.id}"><img src="${thumb(s)}" loading="lazy">${esc(s.description || "")} <span class="hint">${s.id} · ${s.duration}s · kualitas ${(s.quality * 100).toFixed(0)}</span></div>`).join(""), cb);
}

async function render() {
  const el = $(".renderCard", S.el);
  const v = (n) => el.querySelector(`[name=${n}]`);
  const body = {framing: v("framing").value, transition: v("transition").value, subtitle_style: v("subtitle_style").value,
    subtitle_size: +v("subtitle_size").value, highlight: v("highlight").value.toUpperCase(), uppercase: v("uppercase").checked,
    show_title: v("show_title").checked, music_volume: +v("music_volume").value, music: S.music || null};
  await run(`/api/projects/${S.pid}/render`, body);
}

function drawOutputs() {
  $(".outputs", S.el).innerHTML = (S.p.outputs || []).map((o) => `
    <div class="out"><video src="${file(`outputs/${o.file}`)}" controls preload="metadata"></video>
    <div>${esc(o.title || "")} · ${o.duration.toFixed(1)} detik · <a href="${file(`outputs/${o.file}`)}" download>Unduh</a></div></div>`).join("");
}

init();
