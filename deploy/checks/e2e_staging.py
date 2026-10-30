"""Uji end-to-end staging lewat Caddy (HTTPS): impor video lewat worker-io + proxy keluar, dan upaya bypass.

    ADMIN_EMAIL=... ADMIN_PASSWORD=... COMPOSE="docker compose -f ... -f ..." \
        python3 deploy/checks/e2e_staging.py fixtures      # bukti utama: server & DNS yang dikendalikan
    ... python3 deploy/checks/e2e_staging.py thirdparty    # pelengkap: situs internet sungguhan

Mode fixtures membuktikan: berkas hasil unduhan identik byte-per-byte (sha256) dengan sumber dan berisi video+audio;
nama DNS -> IP privat, redirect ke nama privat/metadata/loopback ditolak; server internal menerima 0 permintaan.
Exit 1 bila ada yang tidak sesuai; exit 3 (mode thirdparty) bila sumber internet tidak terjangkau.
"""
import http.cookiejar
import json
import os
import shlex
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote

MODE = sys.argv[1] if len(sys.argv) > 1 else "fixtures"
BASE = os.environ.get("BASE", "https://localhost")
COMPOSE = shlex.split(os.environ.get("COMPOSE", "docker compose"))
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE      # sertifikat internal Caddy untuk "localhost"
opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                     urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
results = []


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def sh(*args, service="web"):
    return subprocess.run([*COMPOSE, "exec", "-T", service, *args], capture_output=True, text=True, timeout=120).stdout


def wait_job(job_id, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, job = call("GET", f"/api/jobs/{job_id}")
        if job["status"] not in ("antre", "jalan"):
            return job
        time.sleep(2)
    return job


def expect(name, value, ok):
    results.append({"uji": name, "hasil": value, "sesuai": None if ok is None else bool(ok)})


def import_url(url):
    code, res = call("POST", "/api/projects/from-url", {"url": url})
    if code != 200:
        return code, res, None, None
    job = wait_job(res["job"]["id"])
    _, project = call("GET", f"/api/projects/{res['project']['id']}")
    return code, res, job, project


def verify_file(project, expected_sha=None):
    path = f"/data/projects/{project['id']}/{project['source']}"
    sha = sh("sha256sum", path).split(" ")[0]
    streams = sh("ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries", "stream=codec_type",
                 "-of", "csv=p=0", path).split()
    leftovers = sh("sh", "-c", f"ls /data/projects/{project['id']} | grep -E '\\.part|\\.ytdl|\\.temp' || true").split()
    kinds = set(streams)
    if expected_sha is not None:   # fixture terkendali: isi diketahui persis (video + audio), sha256 harus identik
        streams_ok = kinds == {"audio", "video"}
    else:                          # situs pihak ketiga: wajib video + audio; trek timecode/teks (data, subtitle) boleh
        streams_ok = {"audio", "video"} <= kinds <= {"audio", "video", "data", "subtitle"}
    ok = streams_ok and not leftovers and (expected_sha is None or sha == expected_sha)
    return ok, {"sha256": sha, "sama_dengan_sumber": None if expected_sha is None else sha == expected_sha,
                "stream": sorted(set(streams)), "sisa_berkas_sementara": leftovers}


def get_json(url, service="web"):
    out = sh("python", "-c", f"import urllib.request;print(urllib.request.urlopen('{url}').read().decode())",
             service=service)
    return json.loads(out or "null")


def login():
    code, _ = call("POST", "/api/auth/login", {"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]})
    expect("login admin lewat Caddy", code, code == 200)


def report_and_exit(done=True):
    print(json.dumps(results, indent=1, ensure_ascii=False))
    bad = [r for r in results if r["sesuai"] is False]
    print(f"{sum(r['sesuai'] is True for r in results)} sesuai, {len(bad)} tidak sesuai, "
          f"{sum(r['sesuai'] is None for r in results)} tidak dapat diuji")
    sys.exit(1 if bad else (0 if done else 3))


def main():
    login()
    done = True

    if MODE == "fixtures":
        src_sha = sh("python", "-c", "import urllib.request;print(urllib.request.urlopen("
                     "'http://fixture/media/ok.mp4.sha256').read().decode())").strip()
        code, res, job, project = import_url("http://fixture/media/ok.mp4")
        ok = bool(code == 200 and job and job["status"] == "selesai" and project.get("source"))
        detail = verify_file(project, src_sha) if ok else (False, {"api": code, "error": job and job.get("error")})
        expect("impor fixture ok.mp4 lewat proxy: selesai, sha256 identik, video+audio, tanpa sisa sementara",
               {"job": job and job["status"], **detail[1]}, ok and detail[0])

        # Ditolak API (alamat/nama internal; web me-resolve nama dengan DNS Docker yang sama dengan proxy).
        for url in ["http://internal-target/secret-p551", "http://169.254.169.254/latest/meta-data/",
                    "http://127.0.0.1:8000/api/health", "http://[::1]/", "http://[::ffff:10.250.0.2]/",
                    "http://db:5432/", "http://web:8000/api/health"]:
            code, res = call("POST", "/api/projects/from-url", {"url": url})
            expect(f"impor {url} (wajib ditolak API)", f"{code} {res.get('detail') if isinstance(res, dict) else res}",
                   code == 400)

        # Lolos pemeriksaan API (host "publik") tetapi redirect menuju internal: wajib gagal di proxy, tanpa berkas.
        for path in ["/redir/internal", "/redir/metadata", "/redir/loopback"]:
            code, res, job, project = import_url(f"http://fixture{path}")
            expect(f"redirect fixture {path} (wajib gagal tanpa berkas)",
                   f"{code} {job and job['status']}: {job and str(job.get('error'))[:160]}",
                   code == 200 and job["status"] == "gagal" and not project.get("source"))

        hits = get_json("http://127.0.0.1/hits", service="internal-target")
        expect("server internal-target tidak pernah dihubungi", hits, hits == [])
        fixture_hits = get_json("http://fixture/hits")
        expect("fixture menerima unduhan & redirect", sorted({h["path"] for h in fixture_hits}),
               {"/media/ok.mp4", "/redir/internal", "/redir/metadata", "/redir/loopback"}
               <= {h["path"] for h in fixture_hits})

    else:
        done = False
        for url in ["https://www.w3schools.com/html/mov_bbb.mp4", "https://download.samplelib.com/mp4/sample-5s.mp4",
                    "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4"]:
            code, res, job, project = import_url(url)
            if job and job["status"] == "selesai" and project.get("source"):
                ok, detail = verify_file(project)
                expect(f"impor publik {url}", {"job": "selesai", **detail}, ok)
                done = True
                break
            expect(f"impor publik {url}", f"{code} {job and job['status']}: {job and job.get('error')}", None)
        target = "http://169.254.169.254/latest/meta-data/thirdparty-p551"
        for redirector in [f"https://httpbin.org/redirect-to?url={quote(target, safe='')}",
                           f"https://httpbingo.org/redirect-to?url={quote(target, safe='')}"]:
            code, res, job, project = import_url(redirector)
            if code == 200:
                expect(f"redirect pihak ketiga -> metadata {redirector}", f"{job['status']}: {str(job.get('error'))[:160]}",
                       job["status"] == "gagal" and not project.get("source"))
                break

    report_and_exit(done)


if __name__ == "__main__":
    main()
