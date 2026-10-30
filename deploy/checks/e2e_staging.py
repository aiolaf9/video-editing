"""Uji end-to-end staging lewat Caddy (HTTPS): impor video publik yang sah lewat worker-io + proxy keluar, dan
upaya melewati filter (alamat internal, redirect ke metadata, nama DNS yang resolve ke IP privat).

    ADMIN_EMAIL=... ADMIN_PASSWORD=... python3 deploy/checks/e2e_staging.py [--base https://localhost]

Mencetak JSON hasil; exit 1 bila ada yang tidak sesuai, exit 3 bila tidak ada satu pun sumber publik yang
dapat dijangkau (hasil positif TIDAK TERVERIFIKASI, bukan lolos)."""
import argparse
import http.cookiejar
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote

# Video publik kecil dari beberapa penyedia (tes tidak bergantung pada satu situs).
PUBLIC_MEDIA = [
    "https://www.w3schools.com/html/mov_bbb.mp4",
    "https://download.samplelib.com/mp4/sample-5s.mp4",
    "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4",
]
METADATA = "http://169.254.169.254/latest/meta-data/"
# Penanda unik: log squid membuktikan redirect INI yang diikuti lalu ditolak (bukan uji lain ke alamat yang sama).
REDIRECT_TARGET = "http://169.254.169.254/latest/meta-data/e2e-redirect-p551"
REDIRECTORS = [f"https://httpbin.org/redirect-to?url={quote(REDIRECT_TARGET, safe='')}",
               f"https://httpbingo.org/redirect-to?url={quote(REDIRECT_TARGET, safe='')}"]

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="https://localhost")
args = ap.parse_args()
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE      # sertifikat internal Caddy untuk "localhost"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), urllib.request.HTTPCookieProcessor(jar))
results = []


def call(method, path, body=None):
    req = urllib.request.Request(args.base + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def wait_job(job_id, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, job = call("GET", f"/api/jobs/{job_id}")
        if job["status"] not in ("antre", "jalan"):
            return job
        time.sleep(2)
    return job


def expect(name, value, ok):
    results.append({"uji": name, "hasil": value, "sesuai": bool(ok)})


code, _ = call("POST", "/api/auth/login", {"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]})
expect("login admin lewat Caddy", code, code == 200)

# Positif: video publik yang sah diunduh worker-io lewat proxy keluar.
downloaded = None
for url in PUBLIC_MEDIA:
    code, res = call("POST", "/api/projects/from-url", {"url": url})
    if code != 200:
        results.append({"uji": f"impor publik {url}", "hasil": f"API {code}: {res}", "sesuai": None})
        continue
    job = wait_job(res["job"]["id"])
    _, project = call("GET", f"/api/projects/{res['project']['id']}")
    if job["status"] == "selesai" and project.get("source"):
        downloaded = {"url": url, "project": project["id"], "source": project["source"]}
        expect(f"impor publik {url} (wajib berhasil)", f"{job['status']} {project['source']}", True)
        break
    results.append({"uji": f"impor publik {url}", "hasil": f"{job['status']}: {job.get('error')}", "sesuai": None})

# Negatif: alamat internal ditolak API; redirect publik -> metadata gagal di proxy; nama -> IP privat ditolak.
for url in [METADATA, "http://127.0.0.1:8000/api/health", "http://[::1]/", "http://db:5432/",
            "http://169.254.169.254.nip.io/latest/meta-data/", "http://10.0.0.1.nip.io/x.mp4", "http://localtest.me/x.mp4"]:
    code, res = call("POST", "/api/projects/from-url", {"url": url})
    expect(f"impor {url} (wajib ditolak API)", f"{code} {res.get('detail') if isinstance(res, dict) else res}",
           code == 400)
redirect_tested = False
for url in REDIRECTORS:
    code, res = call("POST", "/api/projects/from-url", {"url": url})
    if code != 200:
        continue
    job = wait_job(res["job"]["id"])
    _, project = call("GET", f"/api/projects/{res['project']['id']}")
    # Bukti bahwa redirect diikuti lalu ditolak proxy (TCP_DENIED ke 169.254.169.254) diperiksa dari log squid
    # oleh workflow; di sini cukup: tidak ada berkas & job gagal.
    redirect_tested = True
    expect(f"redirect publik -> metadata {url} (wajib gagal, tanpa berkas)", f"{job['status']}: {job.get('error')}",
           job["status"] == "gagal" and not project.get("source"))
    break
if not redirect_tested:
    results.append({"uji": "redirect publik -> metadata", "hasil": "API menolak semua URL redirect", "sesuai": None})

print(json.dumps({"hasil": results, "unduhan": downloaded}, indent=1, ensure_ascii=False))
bad = [r for r in results if r["sesuai"] is False]
print(f"{sum(r['sesuai'] is True for r in results)} sesuai, {len(bad)} tidak sesuai, "
      f"{sum(r['sesuai'] is None for r in results)} tidak dapat diuji")
if bad:
    sys.exit(1)
if downloaded is None or not redirect_tested:
    sys.exit(3)
