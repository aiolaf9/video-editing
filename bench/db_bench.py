"""Biaya database untuk penjadwalan & monitoring (P3) pada riwayat job yang besar.

Mengisi database KOSONG dengan N job historis + M job menunggu, lalu mengukur latensi:
  claim()                 (setiap worker, setiap ~1 dtk saat antrean kosong)
  monitor.report()        (setiap worker, tiap WORKER_REPORT_INTERVAL)
  monitor.snapshot()      (halaman Status admin tiap 10 dtk saat dibuka, /metrics saat di-scrape)
  jobs.check_capacity()   (setiap pengajuan job)

    python bench/db_bench.py --db postgresql+psycopg://... --history 200000 --queued 1000
JANGAN dijalankan ke database produksi: tabel dihapus & dibuat ulang.
"""
import argparse
import json
import os
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def timed(fn, n):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    s = sorted(out)
    return {"n": n, "p50_ms": round(s[len(s) // 2], 2), "p95_ms": round(s[int(len(s) * 0.95) - 1], 2),
            "mean_ms": round(statistics.mean(out), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--history", type=int, default=200_000)
    ap.add_argument("--queued", type=int, default=1000)
    ap.add_argument("--units", type=int, default=50)
    ap.add_argument("--drop-index", default="", help="indeks yang dihapus dulu (pembanding), mis. ix_jobs_queue_finished")
    ap.add_argument("--out")
    args = ap.parse_args()
    os.environ.update(DATABASE_URL=args.db, VIDEO_APP_DATA=tempfile.mkdtemp(prefix="ve-dbbench-"), EMBEDDED_WORKERS="0", AUTO_MIGRATE="1")
    sys.path.insert(0, str(ROOT))
    from sqlalchemy import insert

    from app import db, jobs, monitor

    from sqlalchemy import text as _text

    eng = db.engine()  # database benchmark khusus: kosongkan lalu buat ulang lewat migrasi Alembic
    db.metadata.drop_all(eng)
    with eng.begin() as c:
        c.execute(_text("DROP TABLE IF EXISTS alembic_version"))
    eng.dispose()
    db._engine = None
    db.engine()
    now = time.time()
    units = [uuid.uuid4().hex[:12] for _ in range(args.units)]
    t = time.time()
    batch = []
    with db.engine().begin() as c:
        for i in range(args.history):
            created = now - 30 * 86400 * (i / args.history)  # 30 hari riwayat
            q = ("analisis", "render", "io")[i % 3]
            batch.append(dict(id=uuid.uuid4().hex[:12], task={"analisis": "analyze", "render": "render", "io": "download"}[q],
                              kind="x", queue=q, project_id="p" * 12, unit_id=units[i % len(units)], label="", payload="{}",
                              status=("selesai", "selesai", "selesai", "gagal", "batal")[i % 5], progress=100, message="",
                              attempts=1, created=created, started=created + 5, finished=created + 60, priority=10))
            if len(batch) == 5000:
                c.execute(insert(db.jobs), batch)
                batch = []
        for i in range(args.queued):
            q = ("analisis", "render")[i % 2]
            batch.append(dict(id=uuid.uuid4().hex[:12], task="render" if q == "render" else "analyze", kind="x",
                              queue=q, project_id="p" * 12, unit_id=units[i % len(units)], label="", payload="{}",
                              status="antre", progress=0, message="", attempts=0, created=now - i, priority=10))
        c.execute(insert(db.jobs), batch)
    fill_s = time.time() - t
    if args.drop_index:
        from sqlalchemy import text
        with db.engine().begin() as c:
            c.execute(text(f"DROP INDEX IF EXISTS {args.drop_index}"))
    with db.engine().begin() as c:
        if db.is_postgres():
            from sqlalchemy import text
            c.execute(text("ANALYZE jobs"))
    for i in range(12):
        monitor.register(f"bench-w{i}", ("render",))

    claimed = []

    def claim():
        j = jobs.claim(("render",), "bench-w0")
        if j:
            claimed.append(j["id"])
            jobs.finish(j["id"], result={})

    def check_capacity():
        try:  # dengan 500 render menunggu, batas MAX_QUEUE_DEPTH memang menolak: yang diukur biaya pemeriksaannya
            jobs.check_capacity("render", units[0])
        except jobs.QueueFull:
            pass

    res = {
        "db": db.engine().dialect.name, "dropped_index": args.drop_index or None, "history": args.history, "queued": args.queued, "units": args.units,
        "fill_s": round(fill_s, 1),
        "claim_plus_finish": timed(claim, 200),
        "claim_empty_queue": timed(lambda: jobs.claim(("io",), "bench-w1"), 200),
        "worker_report": timed(lambda: monitor.report("bench-w2", "idle", usage={"cpu_pct": 1, "rss_mb": 1}), 200),
        "check_capacity": timed(check_capacity, 200),
        "metrics_snapshot": timed(monitor.snapshot, 30),
    }
    print(json.dumps(res, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
