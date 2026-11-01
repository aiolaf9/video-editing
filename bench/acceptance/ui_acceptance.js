// Acceptance MVP lewat UI pada viewport HP (emulasi Pixel 7, sentuh): upload -> analisis -> klip -> trim ->
// koreksi subtitle -> render 9:16 -> download, plus penolakan video > batas durasi. Ditulis ke OUT/acceptance.json.
// Env: BASE, VIDEO, LONG_VIDEO, REF (reference.json), OUT, USER_EMAIL, USER_PASSWORD, CHROME (opsional).
let pw;
try { pw = require("playwright"); } catch { pw = require("playwright-core"); }
const fs = require("fs");
const path = require("path");
const E = process.env;
const OUT = E.OUT || ".";
const res = [];
const log = (step, ok, detail) => { res.push({ step, result: ok ? "PASS" : "FAIL", detail }); console.log(ok ? "PASS" : "FAIL", step, "-", detail); };

const norm = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);
function wer(ref, hyp) {   // jarak edit tingkat kata
  const r = norm(ref), h = norm(hyp);
  let prev = Array.from({ length: h.length + 1 }, (_, j) => j);
  for (let i = 1; i <= r.length; i++) {
    const cur = [i];
    for (let j = 1; j <= h.length; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r[i - 1] === h[j - 1] ? 0 : 1));
    prev = cur;
  }
  return { wer: +(prev[h.length] / Math.max(1, r.length)).toFixed(4), ref_words: r.length, hyp_words: h.length };
}

async function audit(page) {
  return page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    small: [...document.querySelectorAll("button, a.download-btn, select, .seg-edit")].filter((el) => {
      const r = el.getBoundingClientRect(); return el.offsetParent !== null && r.height > 0 && r.height < 32; }).length,
  }));
}

(async () => {
  const browser = await pw.chromium.launch(E.CHROME ? { executablePath: E.CHROME } : {});
  const ctx = await browser.newContext({ ...pw.devices["Pixel 7"], acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const layouts = [];
  const shot = async (n) => { await page.screenshot({ path: path.join(OUT, n) }); layouts.push({ n, ...(await audit(page)) }); };
  const ref = E.REF ? JSON.parse(fs.readFileSync(E.REF, "utf8")) : null;
  try {
    await page.goto(E.BASE);
    await page.waitForSelector("#loginForm, #dropzone", { timeout: 30000 });
    if (await page.$("#loginForm")) {
      await page.fill("#email", E.USER_EMAIL);
      await page.fill("#password", E.USER_PASSWORD);
      await page.tap("#loginBtn");
    }
    await page.waitForSelector("#dropzone", { state: "visible", timeout: 30000 });
    log("buka & login", true, E.BASE);

    if (E.LONG_VIDEO) {
      await page.setInputFiles("#fileInput", E.LONG_VIDEO);
      const t = await page.waitForSelector("#toast", { state: "visible", timeout: 30000 }).then(() => page.textContent("#toast"));
      log("tolak video > batas (UI)", /melebihi batas/.test(t), t.trim());
      await page.waitForTimeout(4500);
    }

    const t0 = Date.now();
    await page.setInputFiles("#fileInput", E.VIDEO);
    await shot("a01_upload.png");
    await page.waitForFunction(() => document.querySelectorAll("#projectList li[data-id]").length > 0, null, { timeout: 300000 });
    await page.tap("#projectList li[data-id]");
    const seen = new Set();
    let state = "";
    while (Date.now() - t0 < 40 * 60 * 1000) {
      const s = await page.evaluate(() => ({ st: typeof state !== "undefined" && state.project && state.project.status,
        txt: (document.querySelector("#analyzeStatus .status-text") || {}).textContent || "",
        vis: !document.getElementById("analyzeStatus").hidden }));
      if (s.vis && s.txt) seen.add(s.txt.replace(/\(\d+%\)/, "").trim());
      state = s.st;
      if (state === "siap" || state === "gagal") break;
      await page.waitForTimeout(2000);
    }
    await page.waitForTimeout(2500);
    await shot("a02_analisis.png");
    const analyzeS = (Date.now() - t0) / 1000;
    log("analisis selesai", state === "siap", `model=${(await page.evaluate(async () => (await fetch("/api/health")).json())).whisper_model} status=${state} ${analyzeS.toFixed(0)} dtk; tahap: ${[...seen].join(" | ")}`);

    const pid = await page.evaluate(() => state.project.id);
    const tr = await page.evaluate(async (p) => (await fetch(`/api/projects/${p}/transcript`)).json(), pid);
    const hyp = tr.segments.map((s) => s.text).join(" ");
    if (ref) {
      const w = wer(ref.text, hyp);
      res.push({ step: "kualitas transkrip vs referensi FLEURS", result: "INFO", detail: { ...w, language: tr.language, set: ref.set,
        segments: tr.segments.length, hyp_excerpt: hyp.slice(0, 400) } });
      console.log("INFO WER", JSON.stringify(w));
    }

    const n = await page.locator("#clipList li.clip").count();
    await page.locator("#clipList").scrollIntoViewIfNeeded();
    await shot("a03_klip.png");
    log("kandidat klip", n >= 1, `${n} klip`);

    await page.tap("#clipList li.clip[data-i='0'] .text");
    const s0 = +(await page.inputValue("#startInput"));
    const e0 = +(await page.inputValue("#endInput"));
    const end = Math.min(e0, s0 + 20);
    await page.fill("#endInput", end.toFixed(1));
    await page.dispatchEvent("#endInput", "change");
    await page.locator("#player").scrollIntoViewIfNeeded();
    await shot("a04_trim.png");
    log("pilih klip & trim", Math.abs(+(await page.inputValue("#endInput")) - end) < 0.2, `${s0}-${end.toFixed(1)}`);

    await page.tap(".tab[data-tab='transcriptTab']");
    const si = await page.evaluate(([s, e]) => {
      const el = [...document.querySelectorAll("#transcriptBox .seg[data-s]")].find((x) => { const w = x.querySelector(".w"); return w && +w.dataset.t >= s && +w.dataset.t < e; });
      return el ? el.dataset.s : null;
    }, [s0, end]);
    const KOREKSI = "Teks subtitle ini dikoreksi lewat uji acceptance.";
    if (si !== null) {
      await page.locator(`#transcriptBox .seg-edit[data-s="${si}"]`).scrollIntoViewIfNeeded();
      await page.tap(`#transcriptBox .seg-edit[data-s="${si}"]`);
      await page.fill(`#transcriptBox .seg[data-s="${si}"] textarea`, KOREKSI);
      await shot("a05_koreksi.png");
      await page.tap(`#transcriptBox .seg[data-s="${si}"] .seg-save`);
      await page.waitForFunction((k) => document.getElementById("transcriptBox").textContent.includes(k.split(" ")[0]), KOREKSI, { timeout: 15000 });
      const stored = (await page.evaluate(async (p) => (await fetch(`/api/projects/${p}/transcript`)).json(), pid)).segments[+si];
      log("koreksi subtitle", stored.text === KOREKSI, `segmen ${si} @${stored.start}`);
      fs.writeFileSync(path.join(OUT, "koreksi.json"), JSON.stringify({ clip_start: s0, clip_end: end, seg_start: stored.start, seg_end: stored.end, text: KOREKSI }));
    } else {
      log("koreksi subtitle", false, "tidak ada segmen ucapan di rentang klip");
    }

    await page.tap(".tab[data-tab='clipsTab']");
    await page.tap("#aspectSeg [data-v='9:16']");
    await page.locator("#renderBtn").scrollIntoViewIfNeeded();
    const r0 = Date.now();
    await page.tap("#renderBtn");
    await page.tap(".tab[data-tab='rendersTab']");
    await page.waitForSelector("#renderList a.download-btn", { timeout: 20 * 60 * 1000 });
    await page.locator("#renderList a.download-btn").first().scrollIntoViewIfNeeded();
    await shot("a06_render.png");
    log("render 9:16", true, `${((Date.now() - r0) / 1000).toFixed(0)} dtk`);
    const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 120000 }), page.tap("#renderList a.download-btn")]);
    const out = path.join(OUT, "render.mp4");
    await dl.saveAs(out);
    log("download MP4", fs.statSync(out).size > 50000, `${dl.suggestedFilename()} ${(fs.statSync(out).size / 1e6).toFixed(2)} MB`);
  } catch (e) {
    log("ERROR", false, e.message.split("\n")[0]);
    await page.screenshot({ path: path.join(OUT, "a99_error.png") }).catch(() => {});
  }
  for (const l of layouts) res.push({ step: "layout HP " + l.n, result: l.overflow <= 1 ? "PASS" : "FAIL", detail: `overflow=${l.overflow}px kontrol<32px=${l.small}` });
  res.push({ step: "error JavaScript", result: errors.length ? "FAIL" : "PASS", detail: errors.slice(0, 5).join(" || ") });
  fs.writeFileSync(path.join(OUT, "acceptance.json"), JSON.stringify(res, null, 1));
  await browser.close();
  process.exit(res.some((r) => r.result === "FAIL") ? 1 : 0);
})();
