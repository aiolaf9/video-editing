"""P5.5.2 W5 (review CI #38): diagnostik per potongan, memori terisolasi, dan konkurensi dengan model asli.

w5d   : jalur potongan dengan perekam per potongan (bench-only, memasang pembungkus pada modul app.chunked di proses
        anak; kode produksi tidak berubah). Per potongan: jenis (speech/gap), durasi, waktu sumber, segmen mentah
        Whisper (no_speech_prob, avg_logprob, compression ratio, suhu, teks <= 120 karakter), segmen yang dibuang
        saringan, kata yang dibuang rekonsiliasi; per putaran buffer: wilayah VAD & potongan (waktu sumber); jumlah
        sampel yang dibaca decoder. Varian kandidat koreksi dipilih lewat environment (lihat VARIANTS).
        Ucapan hilang ditelusuri: dibaca decoder? terdeteksi VAD? ada potongan? Whisper menghasilkan segmen? dibuang
        saringan/rekonsiliasi?
w5mem : satu skenario memori per container (dibuat oleh workflow): proses ini hanya menjalankan transkripsi lalu
        membaca memory.peak/stat/events cgroup-nya sendiri; OOM dibuktikan dari `docker inspect` di host.
w5conc: dua job analisis lewat worker aplikasi asli (2 proses worker) dalam satu container 3 GiB.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

RATE = 16000
EXCERPT = 120
VARIANTS = {
    "legacy": {"WHISPER_CHUNKED": "0"},
    "per_utt": {"WHISPER_CHUNKED": "0"},
    "chunked": {"WHISPER_CHUNKED": "1"},
    "chunked_merge": {"WHISPER_CHUNKED": "1", "WHISPER_CHUNK_MIN_SECONDS": "2.0"},
    "chunked_nogap": {"WHISPER_CHUNKED": "1", "WHISPER_GAP_FALLBACK_SECONDS": "1000000"},
    "chunked_filter": {"WHISPER_CHUNKED": "1", "WHISPER_SEGMENT_FILTER": "1"},
    "chunked_combo": {"WHISPER_CHUNKED": "1", "WHISPER_CHUNK_MIN_SECONDS": "2.0", "WHISPER_SEGMENT_FILTER": "1"},
    "chunked_w60": {"WHISPER_CHUNKED": "1", "WHISPER_DECODE_WINDOW_SECONDS": "60"},
}


def _r(x):
    return round(float(x), 2)


# ------------------------------------------------------------------ perekam (dipasang di proses anak)

def install_spies(model) -> dict:
    """Pasang pembungkus pada app.chunked & model.transcribe; kembalikan dict yang terisi selama transkripsi."""
    from app import chunked

    diag = {"decoded_samples": 0, "plans": [], "chunks": [], "reconcile": []}
    state = {"buf_start": 0.0, "regions": []}

    orig_stream = chunked.stream_audio

    def stream_spy(src, block_seconds):
        for blk in orig_stream(src, block_seconds):
            diag["decoded_samples"] += len(blk)
            yield blk
    chunked.stream_audio = stream_spy

    orig_regions = chunked._speech_regions

    def regions_spy(buf):
        r = orig_regions(buf)
        state["regions"] = r
        return r
    chunked._speech_regions = regions_spy

    orig_plan = chunked.plan

    def plan_spy(buf, final):
        chunks, consumed = orig_plan(buf, final)
        bs = state["buf_start"]
        diag["plans"].append({"buf_start": _r(bs), "buf_s": _r(len(buf) / RATE), "final": final,
                              "vad": [[_r(bs + s / RATE), _r(bs + e / RATE)] for s, e in state["regions"]],
                              "chunks": [[_r(bs + s / RATE), _r(bs + e / RATE), k] for s, e, k in chunks],
                              "consumed_s": _r(consumed / RATE)})
        if not final:
            state["buf_start"] += consumed / RATE
        return chunks, consumed
    chunked.plan = plan_spy

    raw_box = {}
    orig_model_transcribe = model.transcribe

    def model_spy(audio, **kw):
        segs, info = orig_model_transcribe(audio, **kw)
        segs = list(segs)
        raw_box["segs"] = segs
        raw_box["kw"] = {k: (bool(v) if k == "initial_prompt" else v) for k, v in kw.items()}
        return iter(segs), info
    model.transcribe = model_spy

    orig_piece = chunked._transcribe_piece

    def piece_spy(model_, audio, language, offset, kind):
        raw_box.clear()
        out, lang = orig_piece(model_, audio, language, offset, kind)
        raw = raw_box.get("segs", [])
        diag["chunks"].append({
            "id": len(diag["chunks"]), "kind": kind, "start": _r(offset), "dur": _r(len(audio) / RATE),
            "language": lang, "decoding": raw_box.get("kw"),
            "raw": [{"start": _r(s.start + offset), "end": _r(s.end + offset), "text": s.text.strip()[:EXCERPT],
                     "no_speech_prob": round(s.no_speech_prob, 3), "avg_logprob": round(s.avg_logprob, 3),
                     "compression_ratio": round(getattr(s, "compression_ratio", 0.0) or 0.0, 2),
                     "temperature": getattr(s, "temperature", None), "n_words": len(s.words or [])} for s in raw],
            "kept_segments": len(out), "dropped_segments": len(raw) - len(out)})
        return out, lang
    chunked._transcribe_piece = piece_spy

    orig_rec = chunked._reconcile

    def rec_spy(prev, cur, cut):
        kp, kc = orig_rec(prev, cur, cut)
        n = lambda segs: sum(len(s["words"]) for s in segs)  # noqa: E731
        diag["reconcile"].append({"cut": _r(cut), "words_before": n(prev) + n(cur), "words_after": n(kp) + n(kc)})
        return kp, kc
    chunked._reconcile = rec_spy
    return diag


# ------------------------------------------------------------------ analisis di proses induk

def chunk_report(diag: dict, timeline: list, words: list, norm, edit_ops) -> dict:
    """Atribusi kesalahan per potongan & kategori (pendek < 2 dtk, gap/non-VAD, tanpa ucapan acuan, normal)."""
    def words_in(a, b):
        return [w["word"] for w in words if a - 0.05 <= (w["start"] + w["end"]) / 2 < b + 0.05]
    cats = {}
    rows = []
    for c in diag["chunks"]:
        a, b = c["start"], c["start"] + c["dur"]
        refs = [u for u in timeline if a <= (u["start"] + u["end"]) / 2 < b]
        overlap = any(min(b, u["end"]) - max(a, u["start"]) > 0.3 for u in timeline)
        hyp = " ".join(words_in(a, b))
        ops = edit_ops(norm(" ".join(u["text"] for u in refs)), norm(hyp))
        cat = ("gap" if c["kind"] == "gap" else "tanpa_acuan" if not overlap
               else "pendek" if c["dur"] < 2.0 else "normal")
        agg = cats.setdefault(cat, {"chunks": 0, "sub": 0, "ins": 0, "del": 0, "dropped_segments": 0})
        agg["chunks"] += 1
        for k in ("sub", "ins", "del"):
            agg[k] += ops[k]
        agg["dropped_segments"] += c["dropped_segments"]
        if ops["ins"] or c["dropped_segments"] or cat != "normal":
            rows.append({**{k: c[k] for k in ("id", "kind", "start", "dur", "dropped_segments")}, "category": cat,
                         "ops": ops, "ref": " ".join(u["text"] for u in refs)[:EXCERPT], "hyp": hyp[:EXCERPT],
                         "raw": c["raw"][:3]})
    rows.sort(key=lambda r: -r["ops"]["ins"])
    return {"chunks": len(diag["chunks"]), "by_category": cats, "reconcile": diag["reconcile"][:20],
            "problem_chunks": rows[:25], "decoding": diag["chunks"][0]["decoding"] if diag["chunks"] else None}


def trace_missing(diag: dict, timeline: list, words: list, missing: list) -> list:
    """Telusuri tiap ucapan hilang: decoder -> VAD -> potongan -> Whisper -> saringan/rekonsiliasi."""
    decoded_s = diag["decoded_samples"] / RATE
    out = []
    for idx in missing:
        u = timeline[idx]
        ov = lambda a, b: min(b, u["end"]) - max(a, u["start"]) > 0.3  # noqa: E731
        vad = [v for p in diag["plans"] for v in p["vad"] if ov(v[0], v[1])]
        planned = [c for p in diag["plans"] for c in p["chunks"] if ov(c[0], c[1])]
        sent = [c for c in diag["chunks"] if ov(c["start"], c["start"] + c["dur"])]
        raw = [s for c in sent for s in c["raw"] if ov(s["start"], s["end"])]
        raw_words = sum(s["n_words"] for s in raw)
        dropped = sum(c["dropped_segments"] for c in sent)
        if decoded_s < u["end"] - 0.5:
            stage = "decoder"
        elif not planned:
            stage = "segmenter (tidak ada potongan)" + ("" if vad else "; VAD tidak mendeteksi")
        elif not sent:
            stage = "segmenter (potongan direncanakan tetapi tidak dikirim)"
        elif not raw or raw_words == 0:
            stage = "model (tidak ada segmen/kata pada rentang ucapan)"
        elif dropped:
            stage = "saringan (segmen dibuang)"
        else:
            stage = "rekonsiliasi/penempatan kata (segmen ada, kata tidak sampai di rentang ucapan)"
        out.append({"idx": idx, "start": u["start"], "end": u["end"], "decoded_s": _r(decoded_s),
                    "vad_regions": vad[:4], "planned_chunks": planned[:4],
                    "sent_chunks": [{k: c[k] for k in ("id", "kind", "start", "dur", "kept_segments",
                                                       "dropped_segments")} for c in sent][:4],
                    "raw_segments": raw[:4], "stage": stage, "ref": u["text"][:EXCERPT]})
    return out


def exp_w5d(args, sets, out, emit, norm, edit_ops, wer_cer):
    from w5_validate import run_child, score, utterance_timeline
    work = Path(tempfile.mkdtemp())
    modes = [m for m in args.variants.split(",") if m]
    bad = [m for m in modes if m not in VARIANTS]
    if bad:
        raise SystemExit(f"varian tidak dikenal: {bad}")
    failed = []
    for name in args.sets.split(","):
        joined, tl, is_utts = utterance_timeline(name, sets[name], work)
        dur = tl[-1]["end"] + (0.4 if is_utts else 0.0)
        for mode in modes:
            if mode == "per_utt" and not is_utts:
                continue
            emit(out, {"exp": "w5d", "set": name, "mode": mode, "status": "started"})
            files = [[u["file"], u["end"] - u["start"]] for u in tl] if mode == "per_utt" else [[str(joined), dur]]
            r = run_child({"files": files, "language": None, "diag": mode.startswith("chunked")}, VARIANTS[mode])
            row = {"exp": "w5d", "set": name, "mode": mode, "env": VARIANTS[mode],
                   **{k: v for k, v in r.items() if k not in ("segments", "diag")}}
            if r["status"] == "ok":
                words = []
                for i, segs in enumerate(r["segments"]):
                    off = tl[i]["start"] if mode == "per_utt" else 0.0
                    words += [{**w, "start": w["start"] + off, "end": w["end"] + off} for s in segs for w in s["words"]]
                sc = score(tl, words, 60.0 if mode == "chunked_w60" else 300.0, norm, edit_ops, wer_cer)
                row.update(sc)
                row["rtf"] = round(r["transcribe_s"] / dur, 4)
                if r.get("diag"):
                    row["chunk_report"] = chunk_report(r["diag"], tl, words, norm, edit_ops)
                    missing = [e["idx"] for e in sc["examples"] if e["status"] == "missing"]
                    row["missing_trace"] = trace_missing(r["diag"], tl, words, missing)
            else:
                failed.append(f"{name}/{mode}:{r['status']}")
            emit(out, row)
    emit(out, {"exp": "w5d", "what": "ringkasan", "failed": failed, "result": "OK" if not failed else "NOT_OK"})
    if failed:
        raise SystemExit(f"w5d: {failed}")


# ------------------------------------------------------------------ memori terisolasi (satu skenario per container)

def mem_prepare(corpus: Path, minutes: int, mode: str, out_dir: Path) -> None:
    """Dijalankan di container TANPA batas memori: siapkan audio & spesifikasi skenario (bukan bagian pengukuran)."""
    from whisper_experiments import repeated
    sets = json.loads((corpus / "corpus.json").read_text())["sets"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = repeated(Path(sets["id_long"]["file"]), minutes, out_dir)
    from app.config import WHISPER_MODEL, analysis_need_mb
    from faster_whisper.utils import download_model
    if not os.environ.get("W5_FAKE_MODEL"):
        download_model(WHISPER_MODEL)  # unduh di sini agar container ukur (HF_HUB_OFFLINE=1) tak menulis cache model
    spec = {"model": WHISPER_MODEL, "file": str(path), "duration": minutes * 60.0, "minutes": minutes, "mode": mode,
            "admission_need_mb": analysis_need_mb(minutes * 60, chunked=mode == "chunked")}
    (out_dir / "spec.json").write_text(json.dumps(spec))
    print(json.dumps({"prepared": spec}))


def _cgroup_snapshot() -> dict:
    cg = Path("/sys/fs/cgroup")
    out = {}
    for f, k in (("memory.peak", "cgroup_peak_mb"), ("memory.current", "cgroup_current_mb"),
                 ("memory.max", "cgroup_limit_mb")):
        if (cg / f).exists():
            raw = (cg / f).read_text().strip()
            out[k] = raw if raw == "max" else round(int(raw) / 2**20, 1)
    if (cg / "memory.stat").exists():
        st = dict(line.split() for line in (cg / "memory.stat").read_text().splitlines())
        out.update({"anon_mb": round(int(st.get("anon", 0)) / 2**20, 1),
                    "file_mb": round(int(st.get("file", 0)) / 2**20, 1)})
    if (cg / "memory.events").exists():
        ev = dict(line.split() for line in (cg / "memory.events").read_text().splitlines())
        out["oom_kill"] = int(ev.get("oom_kill", 0))
    return out


def mem_measure(spec_path: Path, out_path: Path) -> None:
    """Dijalankan sebagai SATU-SATUNYA proses kerja di container ber-batas: transkripsi jalur aplikasi, lalu catat
    cgroup. memory.peak container = puncak skenario ini saja (tidak kumulatif lintas skenario)."""
    spec = json.loads(spec_path.read_text())
    os.environ["WHISPER_CHUNKED"] = "1" if spec["mode"] == "chunked" else "0"
    from app import transcribe
    if os.environ.get("W5_FAKE_MODEL"):        # hanya uji lokal instrumen (tanpa unduhan model)
        import importlib.util
        ms = importlib.util.spec_from_file_location("w5_fake", os.environ["W5_FAKE_MODEL"])
        fake = importlib.util.module_from_spec(ms)
        ms.loader.exec_module(fake)
        model = fake.Model()
        transcribe._get_model = lambda force_cpu=False: model
        fake.patch()
    t0 = time.perf_counter()
    transcribe._get_model()
    t1 = time.perf_counter()
    transcribe.transcribe(Path(spec["file"]), spec["duration"], "id")
    t2 = time.perf_counter()
    rss = int(Path("/proc/self/status").read_text().split("VmHWM:")[1].split()[0]) / 1024
    from app import config
    out_path.write_text(json.dumps({**spec, "status": "ok", "chunked_flag": bool(config.WHISPER_CHUNKED), "load_s": round(t1 - t0, 1),
                                    "transcribe_s": round(t2 - t1, 1), "rtf": round((t2 - t1) / spec["duration"], 4),
                                    "peak_rss_mb": round(rss, 1), **_cgroup_snapshot()}))


# ------------------------------------------------------------------ konkurensi: dua job lewat worker aplikasi asli

def conc_run(corpus: Path, minutes: int, mode: str, out: Path, emit, timeout_s: int = 7200) -> None:
    """Dua job analyze (video sintetis + ucapan nyata id_long, `minutes` menit) diproses 2 proses worker aplikasi di
    container ini. Dicatat tiap 5 dtk: status & pesan job, memory.current; di akhir: memory.peak, oom_kill, peristiwa
    admission dari log worker. Job selesai/gagal/antre dibaca dari DB aplikasi (SQLite sementara)."""
    data = Path(tempfile.mkdtemp())
    env = {**os.environ, "VIDEO_APP_DATA": str(data), "WHISPER_CHUNKED": "1" if mode == "chunked" else "0",
           "EMBEDDED_WORKERS": "0", "HF_HUB_OFFLINE": "0", "WORKER_REPORT_INTERVAL": "5"}
    os.environ.update({k: v for k, v in env.items() if k in ("VIDEO_APP_DATA", "WHISPER_CHUNKED", "EMBEDDED_WORKERS")})
    from whisper_experiments import repeated
    sets = json.loads((corpus / "corpus.json").read_text())["sets"]
    audio = repeated(Path(sets["id_long"]["file"]), minutes, data)
    video = data / "src.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=320x240:rate=5:duration={minutes * 60}", "-i", str(audio), "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "64k", str(video)], check=True)
    from app import config, jobs, storage
    if config.DATA_DIR != data.resolve():  # app sudah terimpor sebelum env diset -> jangan sentuh data lain
        raise SystemExit(f"w5conc: DATA_DIR {config.DATA_DIR} bukan folder sementara {data}")
    jids = []
    for i in range(2):
        pid = storage.create_project(f"konkurensi-{i}")["id"]
        dst = storage.project_dir(pid) / "source.mp4"
        dst.write_bytes(video.read_bytes())
        storage.update_project(pid, source="source.mp4", status="antre")  # sama seperti jalur upload API
        jids.append(jobs.submit("analyze", "analisis", pid, {"language": "id"}, "Analisis")["id"])
    logs = [data / f"worker{i}.log" for i in range(2)]
    if not os.environ.get("W5_FAKE_MODEL"):  # unduh sekali sebelum worker jalan: tak ada unduhan ganda/terukur
        from faster_whisper.utils import download_model
        download_model(config.WHISPER_MODEL)
    before = _cgroup_snapshot()  # puncak ini termasuk encode video sintetis -> dipisah dari puncak saat worker jalan
    procs = [subprocess.Popen([sys.executable, "-m", "app.worker", "--queues", "analisis"], env=env,
                              stdout=open(logs[i], "w"), stderr=subprocess.STDOUT) for i in range(2)]
    t0, timeline, hwm = time.time(), [], [0, 0]
    try:
        while time.time() - t0 < timeout_s:
            snap = _cgroup_snapshot()
            for i, p in enumerate(procs):  # VmHWM = puncak RSS proses worker (bukan cgroup kumulatif)
                try:
                    st = Path(f"/proc/{p.pid}/status").read_text()
                    hwm[i] = max(hwm[i], int(st.split("VmHWM:")[1].split()[0]) // 1024)
                except (OSError, IndexError, ValueError):
                    pass
            states = [jobs.get(j) for j in jids]
            timeline.append({"t": round(time.time() - t0), "mem_mb": snap.get("cgroup_current_mb"),
                             "jobs": [(s["status"], (s.get("message") or "")[:60]) for s in states]})
            if all(s["status"] in ("selesai", "gagal", "batal") for s in states):
                break
            time.sleep(5)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(30)
            except subprocess.TimeoutExpired:
                p.kill()
    states = [jobs.get(j) for j in jids]
    started = [s.get("started") for s in states]
    events = []
    KEYS = ("dikembalikan ke antrean", "menunggu RAM", "terlalu besar", "tidak akan pernah", "] mulai ",
            "Penahanan antrean", "menunda mengambil job")
    for lg in logs:
        for line in lg.read_text(errors="replace").splitlines():
            if "SIGTERM" not in line and any(k in line for k in KEYS):
                events.append(line[-200:])
    cur = [x["mem_mb"] for x in timeline if isinstance(x.get("mem_mb"), (int, float))]
    emit(out, {"exp": "w5conc", "minutes": minutes, "mode": mode, **_cgroup_snapshot(),
               "peak_before_workers_mb": before.get("cgroup_peak_mb"), "oom_kill_before": before.get("oom_kill"),
               "sampled_current_max_mb": max(cur) if cur else None, "worker_rss_hwm_mb": hwm,
               "jobs": [{"status": s["status"], "error": (s.get("error") or "")[:200], "attempts": s.get("attempts"),
                         "started_rel_s": round(s["started"] - t0, 1) if s.get("started") else None,
                         "finished_rel_s": round(s["finished"] - t0, 1) if s.get("finished") else None}
                        for s in states],
               "second_job_delay_s": (round(max(started) - min(started), 1) if all(started) else None),
               "admission_counts": {k.strip(" ]"): sum(k in e for e in events) for k in KEYS},
               "admission_events": events[:20] + events[-10:] if len(events) > 30 else events, "timeline": timeline[:: max(1, len(timeline) // 60)],
               "result": "OK" if all(s["status"] == "selesai" for s in states) and not _cgroup_snapshot().get(
                   "oom_kill") else "NOT_OK"})


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "--mem-prep":
        mem_prepare(Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4], Path(sys.argv[5]))
    elif len(sys.argv) == 4 and sys.argv[1] == "--mem-measure":
        mem_measure(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raise SystemExit("pemakaian: --mem-prep <korpus> <menit> <legacy|chunked> <folder> | --mem-measure <spec> <out>")
