// Menu "Uji akurasi" (aplikasi desktop, M1.1): uji transkripsi dengan video NH asli + transkrip rujukan buatan manusia,
// tanpa CMD. Backend: /api/desktop/eval* (app/desktop_eval.py -> desktop/nh_eval.py). Video tidak keluar dari komputer.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let qualities = [];
  let timer = null;

  async function call(method, url, body) {
    const res = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined, credentials: "same-origin" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      const err = new Error(typeof d === "string" ? d : (Array.isArray(d) ? d.map((x) => x.msg).join("; ") : `Gagal (${res.status})`));
      err.status = res.status;
      throw err;
    }
    return data;
  }

  const pct = (x) => (x == null ? "–" : `${(x * 100).toFixed(1)}%`);
  const label = (id) => (qualities.find((q) => q.id === id) || {}).label || id;

  function renderScan(info) {
    const ok = info.pairs.filter((n) => !info.empty_reference.includes(n));
    let html = `<p><b>${ok.length}</b> video siap diuji: ${ok.map(esc).join(", ") || "–"}</p>`;
    if (info.missing_reference.length) html += `<p class="warn">Tanpa transkrip rujukan .txt (dilewati): ${info.missing_reference.map(esc).join(", ")}</p>`;
    if (info.empty_reference.length) html += `<p class="warn">Transkrip .txt kosong (dilewati): ${info.empty_reference.map(esc).join(", ")}</p>`;
    if (ok.length && ok.length < 3) html += `<p class="hint">Disarankan 3–5 video agar hasilnya bermakna.</p>`;
    $("evalScan").innerHTML = html;
    $("evalStartBtn").disabled = !ok.length;
  }

  function renderSummary(st) {
    if (!st.summary) { $("evalResult").innerHTML = ""; return; }
    const rows = st.summary.map((r) => `<tr><td>${esc(label(r.quality))}</td><td>${esc(r.model)}</td>
      <td>${pct(r.wer_mean)} <span class="muted">(${pct(r.wer_min)}–${pct(r.wer_max)})</span></td><td>${pct(r.cer_mean)}</td>
      <td>${r.rtf_mean != null ? `±${(r.rtf_mean * 10).toFixed(1)} mnt / 10 mnt` : "–"}</td><td>${Math.round(r.peak_ram_mb_max)} MB</td>
      <td>${r.runs_identical == null ? "–" : (r.runs_identical ? "ya" : "<b>tidak</b>")}</td>
      <td>${r.term_accuracy == null ? "–" : `${Math.round(r.term_accuracy * 100)}% dari ${r.terms_in_reference}`}</td></tr>`).join("");
    const sys = st.system ? `Komputer ini: RAM ${st.system.ram_total_gb} GB, ${st.system.cpu_threads} thread CPU.` : "";
    $("evalResult").innerHTML = `
      ${st.cancelled ? '<p class="warn">Uji dibatalkan: angka hanya dari run yang sempat selesai.</p>' : ""}
      ${st.errors ? `<p class="warn">${st.errors} run gagal (lihat laporan).</p>` : ""}
      <table class="eval-table"><thead><tr><th>Mode</th><th>Model</th><th>Salah kata (WER)</th><th>Salah huruf (CER)</th>
        <th>Waktu</th><th>Puncak RAM</th><th>2 run sama</th><th>Istilah tepat</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="hint">${esc(sys)} WER 20% = kira-kira 1 dari 5 kata perlu dikoreksi. Angka hanya berlaku untuk video &amp; komputer ini,
        bukan jaminan untuk video lain. Istilah Arab/ayat/dakwah tetap wajib diperiksa manusia.</p>
      <p class="hint">Laporan lengkap: <code>${esc(st.report || "")}</code> (kirim <b>laporan-uji.md</b> saja, tanpa video).</p>`;
    $("evalOpenBtn").hidden = false;
  }

  function render(st) {
    const running = !!st.running;
    $("evalStartBtn").hidden = running;
    $("evalCancelBtn").hidden = !running;
    $("evalFolder").disabled = $("evalPickBtn").disabled = $("evalScanBtn").disabled = running;
    $("evalProgress").hidden = !running;
    if (running) {
      const total = st.total || 0;
      $("evalBar").max = total || 1;
      $("evalBar").value = st.done || 0;
      $("evalNow").textContent = total ? `${st.done} / ${total} transkripsi selesai${st.current ? ` · sekarang: ${st.current}` : ""}` : "Menyiapkan…";
    }
    if (st.error) $("evalResult").innerHTML = `<p class="warn">Uji gagal: ${esc(st.error)}</p>`;
    else if (!running) renderSummary(st);
  }

  async function poll() {
    clearTimeout(timer);
    try {
      const st = await call("GET", "/api/desktop/eval");
      render(st);
      if (st.running && $("evalDialog").open) timer = setTimeout(poll, 2000);
    } catch (err) { $("evalResult").innerHTML = `<p class="warn">${esc(err.message)}</p>`; }
  }

  async function scan() {
    const folder = $("evalFolder").value.trim();
    if (!folder) { $("evalScan").innerHTML = '<p class="warn">Isi atau pilih folder dulu.</p>'; return; }
    try { renderScan(await call("POST", "/api/desktop/eval/scan", { folder })); }
    catch (err) { $("evalScan").innerHTML = `<p class="warn">${esc(err.message)}</p>`; $("evalStartBtn").disabled = true; }
  }

  async function open() {
    $("evalModes").innerHTML = qualities.map((q) => `<label class="inline"><input type="checkbox" class="eval-mode" value="${esc(q.id)}" checked>
      ${esc(q.label)}</label>`).join("") || "<span class='muted'>Model standar</span>";
    $("evalDialog").showModal();
    try {
      const st = await call("GET", "/api/desktop/eval");
      if (st.folder && !$("evalFolder").value) $("evalFolder").value = st.folder;
      render(st);
      if (st.running) poll();
    } catch { /* tampilkan dialog kosong */ }
  }

  function setup(me) {
    if (!me || !me.desktop || !$("evalBtn")) return;
    qualities = (me.transcribe && me.transcribe.qualities) || [];
    $("evalBtn").hidden = false;
    $("evalBtn").addEventListener("click", open);
    $("evalCloseBtn").addEventListener("click", () => { clearTimeout(timer); $("evalDialog").close(); });
    $("evalScanBtn").addEventListener("click", scan);
    $("evalFolder").addEventListener("change", scan);
    $("evalPickBtn").addEventListener("click", async () => {
      try {
        const res = await call("POST", "/api/desktop/pick-folder");
        if (res.folder) { $("evalFolder").value = res.folder; scan(); }
      } catch (err) {
        if (err.status === 501) { $("evalPickBtn").hidden = true; $("evalFolder").focus(); }
        else $("evalScan").innerHTML = `<p class="warn">${esc(err.message)}</p>`;
      }
    });
    $("evalStartBtn").addEventListener("click", async () => {
      const modes = [...document.querySelectorAll(".eval-mode:checked")].map((el) => el.value);
      if (qualities.length && !modes.length) { $("evalScan").innerHTML = '<p class="warn">Pilih minimal satu mode.</p>'; return; }
      try {
        $("evalResult").innerHTML = "";
        render(await call("POST", "/api/desktop/eval", { folder: $("evalFolder").value.trim(), runs: Number($("evalRuns").value), qualities: modes }));
        poll();
      } catch (err) { $("evalScan").innerHTML = `<p class="warn">${esc(err.message)}</p>`; }
    });
    $("evalCancelBtn").addEventListener("click", async () => {
      try { await call("POST", "/api/desktop/eval/cancel"); $("evalNow").textContent = "Membatalkan setelah transkripsi yang sedang berjalan…"; }
      catch (err) { $("evalResult").innerHTML = `<p class="warn">${esc(err.message)}</p>`; }
    });
    $("evalOpenBtn").addEventListener("click", () => call("POST", "/api/desktop/eval/open").catch(() => {}));
  }

  window.EvalTool = { setup };
})();
