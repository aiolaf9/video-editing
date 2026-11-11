"""Uji jalur lengkap aplikasi desktop terhadap server lokal yang sedang berjalan (tanpa jendela).

masuk tanpa login (kunci peluncuran) -> upload video ucapan -> analisis (Whisper bawaan + YuNet) ->
transkrip berisi kata -> rekomendasi klip AI (skor + alasan) -> subtitle otomatis di resep -> ekspor massal
2 klip x (9:16, 1:1, 16:9) -> verifikasi MP4 (ffprobe: H.264, ukuran, durasi, ada audio) + salinan di folder ekspor.
Hanya pustaka standar Python (dipakai juga oleh exe hasil PyInstaller)."""
from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}


class Client:
    def __init__(self, base: str):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, method: str, path: str, body=None, headers=None, timeout=600):
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with self.opener.open(req, timeout=timeout) as r:  # nosec B310 - hanya 127.0.0.1
                data = r.read()
                return r.status, (json.loads(data) if data and "json" in r.headers.get("content-type", "") else data)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw

    def get(self, path):
        return self.request("GET", path)

    def json(self, method, path, payload):
        return self.request(method, path, json.dumps(payload).encode(), {"Content-Type": "application/json"})

    def upload(self, path: Path, language: str, quality: str = ""):
        boundary = uuid.uuid4().hex
        parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\n{language}\r\n".encode(),
                 f"--{boundary}\r\nContent-Disposition: form-data; name=\"quality\"\r\n\r\n{quality}\r\n".encode(),
                 (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
                  "Content-Type: video/mp4\r\n\r\n").encode(), path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()]
        return self.request("POST", "/api/projects", b"".join(parts),
                            {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _wait(c: Client, job_id: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, job = c.get(f"/api/jobs/{job_id}")
        _check(status == 200, f"status job {job_id}: {status}")
        if job["status"] in ("selesai", "gagal", "batal"):
            return job
        time.sleep(1)
    raise AssertionError(f"job {job_id} melewati batas waktu {timeout:.0f} dtk")


def probe(path: Path) -> dict:
    ffprobe = os.environ.get("FFPROBE_BIN") or "ffprobe"
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height",
                          "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    return json.loads(out)


def _norm(text: str) -> list[str]:
    import re
    import unicodedata
    t = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^\w\s]|_", " ", t).split()


def _dist(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer_cer(ref: str, hyp: str) -> dict:
    r, h = _norm(ref), _norm(hyp)
    rc, hc = list("".join(r)), list("".join(h))
    return {"wer": round(_dist(r, h) / max(1, len(r)), 4), "cer": round(_dist(rc, hc) / max(1, len(rc)), 4),
            "ref_words": len(r)}


def run(base: str, key: str, video: Path, export_dir: Path, language: str = "en",
        analysis_timeout: float = 1800, render_timeout: float = 1800, quality: str = "",
        reference: str = "") -> dict:
    t0 = time.time()
    report: dict = {"ok": False, "steps": []}
    step = report["steps"].append
    c = Client(base)

    status, health = c.get("/api/health")
    _check(status == 200 and health.get("ffmpeg") and health.get("ffprobe") and health.get("database"),
           f"health: {health}")
    status, _ = c.get("/api/auth/me")
    _check(status == 401, "tanpa kunci peluncuran seharusnya belum ada sesi")
    status, _ = c.get(f"/api/desktop/enter?key={urllib.parse.quote(key)}")
    status, me = c.get("/api/auth/me")
    _check(status == 200 and me.get("desktop") is True, f"masuk tanpa login gagal: {status} {me}")
    report["ffmpeg"] = os.environ.get("FFMPEG_BIN", "(PATH)")
    report["whisper_model_dir"] = os.environ.get("WHISPER_MODEL_DIR", "(tidak dibundel)")
    step("masuk tanpa login: OK")

    status, res = c.upload(video, language, quality)
    _check(status == 200, f"upload: {status} {res}")
    pid, t_up = res["project"]["id"], time.time()
    job = _wait(c, res["job"]["id"], analysis_timeout)
    _check(job["status"] == "selesai", f"analisis gagal: {job}")
    status, project = c.get(f"/api/projects/{pid}")
    duration = float(project.get("duration") or 0)
    report["analysis_seconds"] = round(time.time() - t_up, 1)
    report["video_seconds"] = round(duration, 1)
    step(f"analisis selesai dalam {report['analysis_seconds']} dtk untuk video {duration:.0f} dtk")

    status, transcript = c.get(f"/api/projects/{pid}/transcript")
    words = [w["word"] for s in transcript.get("segments", []) for w in s.get("words", [])]
    _check(len(words) >= 10, f"transkrip terlalu sedikit kata: {words}")
    report["transcript_words"] = len(words)
    report["transcript_sample"] = " ".join(words[:25])
    report["model"] = transcript.get("model")
    report["quality"] = transcript.get("quality")
    report["language"] = transcript.get("language")
    probs = [w.get("p") for s in transcript.get("segments", []) for w in s.get("words", []) if w.get("p") is not None]
    report["unsure_share"] = round(sum(p < 0.5 for p in probs) / len(probs), 3) if probs else None
    if reference:
        report["accuracy"] = wer_cer(reference, " ".join(words))
        step(f"akurasi vs rujukan: WER {report['accuracy']['wer']:.1%}, CER {report['accuracy']['cer']:.1%}")
    step(f"transkrip: {len(words)} kata")

    status, clips = c.get(f"/api/projects/{pid}/clips")
    if not isinstance(clips, list) or len(clips) < 2:      # video pendek: cari klip yang lebih pendek
        status, clips = c.json("POST", f"/api/projects/{pid}/clips/suggest", {"target": 15})
    _check(isinstance(clips, list) and len(clips) >= 2, f"rekomendasi klip kurang dari 2: {clips}")
    _check(all(isinstance(k.get("score"), (int, float)) and k.get("reasons") for k in clips), "klip tanpa skor/alasan")
    report["clips"] = [{"start": k["start"], "end": k["end"], "score": k["score"], "reasons": k["reasons"][:3]}
                       for k in clips[:5]]
    step(f"rekomendasi AI: {len(clips)} klip, skor teratas {clips[0]['score']}")

    status, state = c.get(f"/api/projects/{pid}/recipe")
    rec = state["recipe"]
    rec["subtitles"].update(enabled=True, style="pop", font="Poppins")     # font bawaan installer (OFL)
    status, saved = c.json("PUT", f"/api/projects/{pid}/recipe", {"version": state["version"], "recipe": rec})
    _check(status == 200, f"simpan resep: {status} {saved}")
    step("subtitle otomatis (gaya pop, font Poppins bawaan) disimpan di resep")

    chosen = sorted(clips[:2], key=lambda k: k["start"])
    body = {"version": saved["version"], "aspects": list(SIZES),
            "clips": [{"start": k["start"], "end": k["end"], "title": f"Klip #{i + 1}"} for i, k in enumerate(chosen)]}
    t_r = time.time()
    status, batch = c.json("POST", f"/api/projects/{pid}/render-batch", body)
    _check(status == 200 and batch["count"] == len(chosen) * len(SIZES), f"ekspor massal: {status} {batch}")
    for j in batch["jobs"]:
        done = _wait(c, j["id"], render_timeout)
        _check(done["status"] == "selesai", f"render gagal: {done}")
    report["render_seconds"] = round(time.time() - t_r, 1)

    project_dir = Path(os.environ["VIDEO_APP_DATA"]) / "projects" / pid / "renders"
    status, project = c.get(f"/api/projects/{pid}")
    outputs = []
    for item in project["renders"]:
        info = probe(project_dir / item["file"])
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
        size = SIZES[item["aspect"]]
        _check((v["width"], v["height"]) == size and v["codec_name"] == "h264" and has_audio,
               f"MP4 tidak sesuai: {item['title']} {info}")
        outputs.append({"title": item["title"], "size": f"{v['width']}x{v['height']}",
                        "duration": round(float(info["format"]["duration"]), 2), "bytes": item["size"]})
    _check(len(outputs) == len(chosen) * len(SIZES), "jumlah MP4 tidak sesuai")
    exported = sorted(export_dir.rglob("*.mp4"))
    _check(len(exported) >= len(outputs), f"salinan di folder ekspor kurang: {exported}")
    for p in exported:
        _check(any(s["codec_type"] == "video" for s in probe(p)["streams"]), f"salinan rusak: {p}")
    report["outputs"] = outputs
    report["exported"] = [str(p) for p in exported]
    step(f"{len(outputs)} MP4 terverifikasi + {len(exported)} salinan di {export_dir}")
    report["m2"] = m2_checks(c, pid, chosen[0], project_dir, render_timeout)
    step(f"M2: {report['m2']['presets']} preset, Motion Library {report['m2']['motion_library']} preset, bingkai {report['m2']['framing']}, motion "
         f"{report['m2']['motion_items']} elemen, pratinjau {report['m2']['preview_bytes']} B, render "
         f"{report['m2']['render_size']}")
    report["total_seconds"] = round(time.time() - t0, 1)
    report["ok"] = True
    return report


def m2_checks(c: "Client", pid: str, clip: dict, renders: Path, timeout: float) -> dict:
    """M2 di aplikasi terpasang (FFmpeg/libass/font bundel): galeri preset, rencana Smart Framing, motion graphics,
    pratinjau 10 dtk, dan render final dengan semuanya aktif."""
    out: dict = {}
    status, presets = c.get("/api/subtitle-presets")
    _check(status == 200 and len(presets) >= 10, f"preset subtitle: {status} {presets}")
    out["presets"] = len(presets)
    status, clip_bytes = c.get("/api/subtitle-presets/neon/preview.mp4")
    _check(status == 200 and isinstance(clip_bytes, bytes) and len(clip_bytes) > 2000, f"galeri preset: {status}")
    start, end = clip["start"], min(clip["end"], clip["start"] + 12)
    status, fr = c.get(f"/api/projects/{pid}/framing?start={start}&end={end}&mode=auto")
    _check(status == 200 and fr["segments"], f"rencana bingkai: {status} {fr}")
    out["framing"] = "+".join(s["layout"] for s in fr["segments"])
    status, state = c.get(f"/api/projects/{pid}/recipe")
    rec = state["recipe"]
    rec["subtitles"].update(style="highlight", font="Poppins", emphasis=True)
    rec["crop"].update(mode="auto")
    status, saved = c.json("PUT", f"/api/projects/{pid}/recipe", {"version": state["version"], "recipe": rec})
    _check(status == 200, f"simpan resep M2: {status} {saved}")
    items = [{"type": "hook", "text": "Selftest M2: hook pembuka", "start": start, "end": start + 2.5},
             {"type": "lower_third", "text": "Narasumber", "label": "NH AI Clip Studio", "start": start + 2.5,
              "end": start + 6},
             {"type": "counter", "text": "2,5 kg", "start": start + 6, "end": start + 8},
             {"type": "zoom", "start": start + 3, "end": start + 4.5, "strength": 0.2},
             {"type": "cta", "text": "Ikuti kajian berikutnya", "start": max(start, end - 3), "end": end}]
    # M2.5 Motion Library: galeri dari engine render bundel (satu preset per kategori) + item fx di render final
    status, lib = c.get("/api/motion-presets")
    _check(status == 200 and len(lib["presets"]) >= 15 and len(lib["categories"]) == 6, f"motion library: {status}")
    per_cat = {}
    for p in lib["presets"]:
        per_cat.setdefault(p["category"], p["id"])
    for pid_fx in per_cat.values():
        status, clip_bytes = c.get(f"/api/motion-presets/{pid_fx}/preview.mp4")
        _check(status == 200 and isinstance(clip_bytes, bytes) and len(clip_bytes) > 2000, f"galeri motion {pid_fx}: {status}")
    out["motion_library"] = len(lib["presets"])
    status, st = c.get("/api/motion-presets/status")
    _check(status == 200, f"status galeri: {status}")
    out["gallery_ready"] = sum(v == "siap" for v in st.values())
    if getattr(sys, "frozen", False):              # installer: galeri dirender saat build, langsung siap
        _check(out["gallery_ready"] == len(lib["presets"]), f"galeri bundel tidak lengkap: {st}")
    items += [{"type": "fx", "preset": "cap.highlight_box", "start": start, "end": end},
              {"type": "fx", "preset": "tr.flash", "start": start + 5.8, "end": start + 6.2},
              {"type": "fx", "preset": "cam.ken_burns", "start": start + 8, "end": start + 11},
              {"type": "fx", "preset": "gfx.progress", "start": start + 8.5, "end": start + 11.5,
               "params": {"value": 60, "text": "Selftest progres"}}]
    status, plan = c.json("PUT", f"/api/projects/{pid}/plan", items)
    _check(status == 200 and len(plan) == len(items), f"motion graphics: {status} {plan}")
    out["motion_items"] = len(plan)
    # M2.5.1 Area Dilindungi: kotak di atas tengah selama hook -> safe-zone memindahkan grafis, ikut render final
    status, state = c.get(f"/api/projects/{pid}/recipe")
    rec = state["recipe"]
    rec["protect"] = [{"id": "selftest", "start": start, "end": start + 3, "box": [0.25, 0.05, 0.5, 0.4],
                       "label": "Selftest"}]
    status, saved = c.json("PUT", f"/api/projects/{pid}/recipe", {"version": state["version"], "recipe": rec})
    _check(status == 200, f"simpan area dilindungi: {status} {saved}")
    status, lay = c.get(f"/api/projects/{pid}/motion-layout?start={start}&end={end}")
    _check(status == 200, f"safe-zone area dilindungi: {status}")
    out["protect_moved"] = sum(1 for v in lay["items"].values() if "area dilindungi" in (v.get("reason") or "")
                               or v.get("action") in ("dipindah", "diperkecil"))
    status, state = c.get(f"/api/projects/{pid}/recipe")
    _check(len(state["recipe"]["protect"]) == 1, "area dilindungi tidak tersimpan di resep")
    # saran area (eksperimental): model YOLOX-nano bundel termuat & berjalan offline
    status, sug = c.json("POST", f"/api/projects/{pid}/protect/suggest",
                         {"start": start, "end": start + 4, "groups": ["orang", "hewan"]})
    _check(status == 200 and sug.get("samples", 0) >= 4, f"saran area dilindungi: {status} {sug}")
    out["protect_suggest_samples"] = sug["samples"]
    status, prev = c.json("POST", f"/api/projects/{pid}/preview-clip",
                          {"version": state["version"], "start": start, "end": start + 10})
    _check(status == 200, f"pratinjau hasil: {status} {prev}")
    status, mp4 = c.get(prev["url"])
    _check(status == 200 and isinstance(mp4, bytes) and len(mp4) > 5000, f"unduh pratinjau: {status}")
    out["preview_bytes"] = len(mp4)
    status, job = c.json("POST", f"/api/projects/{pid}/render-recipe",
                         {"version": state["version"], "start": start, "end": end, "title": "Selftest M2"})
    _check(status == 200, f"render M2: {status} {job}")
    done = _wait(c, job["id"], timeout)
    _check(done["status"] == "selesai", f"render M2 gagal: {done}")
    info = probe(renders / done["result"]["file"])
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    _check((v["width"], v["height"]) == (1080, 1920), f"render M2 ukuran: {v['width']}x{v['height']}")
    out["render_size"] = f"{v['width']}x{v['height']}"
    out["render_file"] = done["result"]["file"]
    return out
