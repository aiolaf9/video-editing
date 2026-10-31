"""Ringkas keluaran media_bench.py: median & p95 (nearest-rank) per aset & operasi, plus sinkronisasi/subtitle/kualitas.

    python bench/p552/summarize_media.py <media.jsonl> [--md]
"""
import json
import statistics
import sys


def p95(v):
    v = sorted(v)
    return v[max(0, -(-95 * len(v) // 100) - 1)]


def main():
    rows = [json.loads(line) for line in open(sys.argv[1])]
    out = []
    for key in sorted({(r["asset"], r["op"]) for r in rows}, key=lambda k: (k[0], k[1])):
        rs = [r for r in rows if (r["asset"], r["op"]) == key and "error" not in r]
        errs = [r for r in rows if (r["asset"], r["op"]) == key and "error" in r]
        if not rs:
            out.append({"asset": key[0], "op": key[1], "error": errs[0]["error"][-300:] if errs else "tidak ada run"})
            continue
        s = {"asset": key[0], "op": key[1], "n": len(rs), "errors": len(errs),
             "wall_s_median": round(statistics.median(r["wall_s"] for r in rs), 2),
             "wall_s_p95": round(p95([r["wall_s"] for r in rs]), 2),
             "rtf_median": round(statistics.median(r["rtf"] for r in rs), 4),
             "cpu_s_median": round(statistics.median(r["ffmpeg_cpu_s"] for r in rs), 1),
             "peak_rss_mb_max": max(r["ffmpeg_peak_rss_mb"] for r in rs),
             "output": rs[0]["output"]}
        sync = [r["sync"] for r in rs if r.get("sync", {}).get("pairs")]
        if sync:
            s["sync_pairs"] = f'{sync[0]["pairs"]}/{sync[0]["flash"]}'
            s["av_offset_ms_max_abs"] = max(x["offset_ms_max_abs"] for x in sync)
            s["drift_ms_per_min_max_abs"] = max(abs(x["drift_ms_per_min"]) for x in sync)
        else:
            s["sync"] = rs[0].get("sync")
        rel = [r["sync_vs_source"] for r in rs if r.get("sync_vs_source", {}).get("markers")]
        if rel:
            s["av_vs_source_ms_max_abs"] = max(x["max_abs_ms"] for x in rel)
        if "subtitles" in rs[0]:
            sub = rs[0]["subtitles"]
            s["subtitles"] = f'{sub["detected"]}/{sub["words"]}, maks {sub["err_ms_max_abs"]} ms'
            if "false_onsets" in sub:
                s["subtitle_false_onsets"] = sub["false_onsets"]
                s["subtitle_bright_frames_outside"] = sub["bright_frames_outside_words"]
        if "quality" in rs[0]:
            s["ssim"] = rs[0]["quality"]["ssim"]
            s["psnr"] = rs[0]["quality"]["psnr"]
        if "plan" in rs[0]:
            s["preview_mode"] = rs[0]["plan"].get("mode")
        out.append(s)
    print("RINGKASAN " + json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
