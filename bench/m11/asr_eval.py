"""M1.1: evaluasi transkripsi bahasa Indonesia lewat fungsi aplikasi (app.transcribe.transcribe).

Per model x varian pengaturan, tiap kombinasi dijalankan di subprocess sendiri (puncak RAM terukur terpisah):
  WER & CER (NFKC, huruf kecil, tanpa tanda baca), RTF, waktu muat model, puncak RSS, bahasa terdeteksi, dan
  ketepatan penanda "perlu ditinjau" (kata dengan p < 0,5): presisi (berapa yang memang salah) & recall (berapa
  kata salah yang tertandai). Kata salah = kata hipotesis yang tidak cocok dengan rujukan (penyelarasan difflib).

Korpus: bench/p552/fleurs_corpus.py (FLEURS id_id, CC-BY 4.0: set id_nodup & id_long) dan/atau folder sampel sendiri
(--samples DIR berisi pasangan <nama>.<wav|mp3|mp4|m4a> + <nama>.txt transkrip rujukan manual).

    python bench/m11/asr_eval.py <korpus|-> <keluaran.jsonl> --models base,small --variants m1,lang_id,noprev
"""
import argparse
import difflib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench" / "p552"))

# Varian pengaturan. "m1" = perilaku rilis M1 (deteksi bahasa dari 30 dtk pertama, conditioning teks sebelumnya).
VARIANTS = {
    "m1": {"language": None, "decode": {"detect_windows": 1, "condition_on_previous_text": True}},
    "auto3": {"language": None, "decode": {"detect_windows": 3, "condition_on_previous_text": True}},
    "lang_id": {"language": "id", "decode": {"condition_on_previous_text": True}},
    "noprev": {"language": "id", "decode": {"condition_on_previous_text": False}},
    "noprev_t0": {"language": "id", "decode": {"condition_on_previous_text": False, "temperature": 0.0}},
    "noprev_hst": {"language": "id", "decode": {"condition_on_previous_text": False,
                                                "hallucination_silence_threshold": 2.0}},
    "noprev_vad": {"language": "id", "decode": {"condition_on_previous_text": False,
                                                "vad": {"min_silence_duration_ms": 500, "speech_pad_ms": 300}}},
    # kosakata bawaan aplikasi sebagai hotwords: tidak boleh memperburuk ucapan umum (FLEURS bukan ceramah)
    "noprev_vocab": {"language": "id", "decode": {"condition_on_previous_text": False}, "vocab": True},
    # M1.1 bawaan (DEFAULT_DECODE di app/transcribe.py), bahasa Indonesia. "m11#2" = run ulang (uji determinisme).
    "m11": {"language": "id", "decode": {}},
    # Putaran 3: bawaan M1.1 + transkripsi per potongan ucapan VAD <= 20 dtk (W5, WHISPER_CHUNKED) -> apakah teks yang
    # hilang pada audio panjang (turbo: CER 15,5 % vs 2,5 % kalimat) berasal dari jendela decoding 30 dtk.
    "m11_chunk": {"language": "id", "decode": {}, "env": {"WHISPER_CHUNKED": "1"}},
    "lang_id_vocab": {"language": "id", "decode": {"condition_on_previous_text": True}, "vocab": True},
}
UNSURE_P = 0.5


def norm_words(text: str) -> list[str]:
    from whisper_quality import norm
    return norm(text)


def score(ref: str, hyp_words: list[dict]) -> dict:
    """WER/CER + ketepatan penanda p < UNSURE_P terhadap kesalahan kata."""
    from whisper_quality import wer_cer
    hyp_text = " ".join(w["word"] for w in hyp_words)
    res = wer_cer(ref, hyp_text)
    r = norm_words(ref)
    # satu token hipotesis bisa jadi beberapa token ternormalisasi ("Al-Qur'an"): pakai token pertama per kata
    hyp_tok = [(norm_words(w["word"]) or [""])[0] for w in hyp_words]
    sm = difflib.SequenceMatcher(a=r, b=hyp_tok, autojunk=False)
    wrong = [True] * len(hyp_tok)
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for j in range(j1, j2):
                wrong[j] = False
    flagged = [float(w.get("p", 1.0)) < UNSURE_P for w in hyp_words]
    res.update(hyp_words=len(hyp_words), wrong_words=sum(wrong), flagged=sum(flagged),
               flagged_wrong=sum(f and x for f, x in zip(flagged, wrong)))
    return res


def items_for(corpus: Path, samples: Path, sets: list[str]) -> list[dict]:
    out = []
    if corpus and str(corpus) != "-":
        data = json.loads((corpus / "corpus.json").read_text())["sets"]
        for name in sets:
            if name not in data:
                continue
            d = data[name]
            if d["kind"] == "utterances":
                out += [{"set": name, "file": it["file"], "text": it["text"], "duration": it["duration"]}
                        for it in d["items"]]
            else:
                out.append({"set": name, "file": d["file"], "duration": d.get("duration") or 0,
                            "text": " ".join(b["text"] for b in d["bounds"])})
    if samples and samples.is_dir():
        for ref in sorted(samples.glob("*.txt")):
            media = next((p for p in sorted(samples.glob(ref.stem + ".*")) if p.suffix != ".txt"), None)
            if media:
                out.append({"set": "sampel", "name": ref.stem, "file": str(media), "duration": 0,
                            "text": ref.read_text(encoding="utf-8")})
    return out


def child(model: str, run_name: str, items_json: Path, out: Path, vocab: list[str]):
    variant = run_name.split("#")[0]
    os.environ["WHISPER_MODEL"] = model
    os.environ["WHISPER_DECODE"] = json.dumps(VARIANTS[variant]["decode"])
    os.environ.update(VARIANTS[variant].get("env", {}))      # sebelum app diimpor (config dibaca saat impor)
    from app import media, transcribe

    if VARIANTS[variant].get("vocab"):
        from app import vocab as app_vocab
        vocab = app_vocab.hotwords(app_vocab.defaults())
    items = json.loads(items_json.read_text())
    t0 = time.perf_counter()
    transcribe._get_model(name=model)
    load_s = time.perf_counter() - t0
    rows = []
    for it in items:
        dur = it["duration"] or media.probe(Path(it["file"]))["duration"]
        t1 = time.perf_counter()
        r = transcribe.transcribe(Path(it["file"]), dur, VARIANTS[variant]["language"], vocabulary=vocab or None)
        took = time.perf_counter() - t1
        words = [w for s in r["segments"] for w in s.get("words", [])]
        rows.append({"set": it["set"], "name": it.get("name"), "duration": dur, "seconds": took,
                     "language": r.get("language"), **score(it["text"], words),
                     "hyp_sample": " ".join(w["word"] for w in words)[:400]})
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    out.write_text(json.dumps({"model": model, "variant": run_name, "load_s": round(load_s, 2),
                               "peak_rss_mb": round(peak, 1), "rows": rows}, ensure_ascii=False))


def summarize(res: dict) -> list[dict]:
    out = []
    for name in sorted({r["set"] for r in res["rows"]}):
        rows = [r for r in res["rows"] if r["set"] == name]
        tot = {k: sum(r[k] for r in rows) for k in ("ref_words", "word_errors", "ref_chars", "char_errors",
                                                   "hyp_words", "wrong_words", "flagged", "flagged_wrong")}
        audio = sum(r["duration"] for r in rows)
        out.append({
            "model": res["model"], "variant": res["variant"], "set": name, "items": len(rows),
            "audio_s": round(audio, 1), "wer": round(tot["word_errors"] / max(1, tot["ref_words"]), 4),
            "cer": round(tot["char_errors"] / max(1, tot["ref_chars"]), 4),
            "rtf": round(sum(r["seconds"] for r in rows) / max(audio, 1e-9), 3),
            "load_s": res["load_s"], "peak_rss_mb": res["peak_rss_mb"],
            "lang": sorted({r["language"] for r in rows if r["language"]}),
            "flag_precision": round(tot["flagged_wrong"] / tot["flagged"], 3) if tot["flagged"] else None,
            "flag_recall": round(tot["flagged_wrong"] / tot["wrong_words"], 3) if tot["wrong_words"] else None,
            "flagged_share": round(tot["flagged"] / max(1, tot["hyp_words"]), 3),
            "hyp_hash": __import__("hashlib").sha256("\n".join(r["hyp_sample"] for r in rows).encode()).hexdigest()[:12],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("out")
    ap.add_argument("--models", default="base,small")
    ap.add_argument("--variants", default="m1,lang_id,noprev")
    ap.add_argument("--sets", default="id_nodup,id_long")
    ap.add_argument("--samples", default="")
    ap.add_argument("--vocab", default="", help="berkas teks: satu istilah per baris (dipakai sebagai hotwords)")
    ap.add_argument("--child", nargs=4, metavar=("MODEL", "VARIANT", "ITEMS", "OUT"))
    a = ap.parse_args()
    vocab = [ln.strip() for ln in Path(a.vocab).read_text(encoding="utf-8").splitlines() if ln.strip()] \
        if a.vocab else []
    if a.child:
        child(a.child[0], a.child[1], Path(a.child[2]), Path(a.child[3]), vocab)
        return
    items = items_for(Path(a.corpus) if a.corpus != "-" else None, Path(a.samples) if a.samples else None,
                      a.sets.split(","))
    if not items:
        raise SystemExit("Tidak ada item uji.")
    out = Path(a.out)
    work = out.parent / "asr_tmp"
    work.mkdir(parents=True, exist_ok=True)
    items_json = work / "items.json"
    items_json.write_text(json.dumps(items, ensure_ascii=False))
    with out.open("a", encoding="utf-8") as fh:
        for model in a.models.split(","):
            for variant in a.variants.split(","):
                res_file = work / f"{model}_{variant.replace('#', '_')}.json"
                cmd = [sys.executable, __file__, a.corpus, a.out, "--child", model, variant, str(items_json),
                       str(res_file)]
                if a.vocab:
                    cmd += ["--vocab", a.vocab]
                t = time.perf_counter()
                p = subprocess.run(cmd, capture_output=True, text=True)
                if p.returncode != 0:
                    row = {"model": model, "variant": variant, "error": p.stderr[-2000:]}
                    print(json.dumps(row, ensure_ascii=False), flush=True)
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    continue
                res = json.loads(res_file.read_text())
                for row in summarize(res):
                    print(json.dumps(row, ensure_ascii=False), flush=True)
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.write(json.dumps({"detail": res}, ensure_ascii=False) + "\n")
                print(f"# {model}/{variant}: {time.perf_counter() - t:.0f} dtk", flush=True)


if __name__ == "__main__":
    main()
