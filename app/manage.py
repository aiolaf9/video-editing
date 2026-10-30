"""Perintah administrasi dari terminal.

    python -m app.manage create-admin            (ditanya email & password)
    python -m app.manage ensure-admin            (buat admin hanya bila belum ada pengguna; dipakai start.bat)
    python -m app.manage reset-password EMAIL
    python -m app.manage list-users
    python -m app.manage media-migrate [--apply] [--grace-hours 72]   (proyek lama -> media sementara)
    python -m app.manage media-cleanup                                  (jalankan pembersihan sekali)
    python -m app.manage db-status                     (versi skema database vs aplikasi)
    python -m app.manage db-upgrade [--target REV]     (migrasi Alembic; buat backup dulu di server)
    python -m app.manage db-downgrade REV --allow-data-loss
    python -m app.manage db-check                      (skema database = model aplikasi? untuk CI)
"""
import argparse
import getpass
import sys
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select, update

from . import auth, db


def _ask_password() -> str:
    while True:
        pw = getpass.getpass(f"Password (min. {auth.MIN_PASSWORD} karakter): ")
        if len(pw) < auth.MIN_PASSWORD:
            print("Terlalu pendek.")
        elif getpass.getpass("Ulangi password: ") != pw:
            print("Tidak sama, coba lagi.")
        else:
            return pw


def create_admin(email: str = "", name: str = "", password: str = "") -> None:
    email = email or input("Email admin: ").strip()
    name = name or input("Nama (boleh kosong): ").strip() or "Admin"
    password = password or _ask_password()
    try:
        user = auth.create_user(email, name, password, "admin", None)
    except HTTPException as exc:
        sys.exit(f"Gagal: {exc.detail}")
    from .main import migrate_legacy
    migrate_legacy()
    print(f"Admin dibuat: {user['email']}. Silakan login di browser.")


def _db_command(args) -> int:
    """Perintah skema: TIDAK memanggil db.engine() (yang menolak start bila skema tertinggal)."""
    from . import migrate

    try:
        if args.cmd == "db-status":
            st = migrate.status()
            print(f"Database: {st['current'] or ('(tanpa versi, setara ' + st['legacy'] + ')' if st['legacy'] else '(kosong)')}")
            print(f"Aplikasi: {st['head']}  ->  {st['state']}")
            return 0 if st["state"] == "terbaru" else 2
        if args.cmd == "db-upgrade":
            r = migrate.upgrade(target=args.target)
            b, a = r["before"], r["after"]
            print(f"{b['current'] or b['legacy'] or 'kosong'} -> {a['current']} ({a['state']})")
            if r["backup"]:
                print(f"Backup SQLite: {r['backup']}")
            return 0
        if args.cmd == "db-downgrade":
            r = migrate.downgrade(args.target, allow_data_loss=args.allow_data_loss)
            print(f"{r['before']['current']} -> {r['after']['current']}")
            return 0
        if args.cmd == "db-check":
            st = migrate.status()
            diffs = migrate.drift() if st["state"] == "terbaru" else ["skema belum terbaru: " + st["state"]]
            for d in diffs:
                print("BEDA:", d)
            print("OK: skema database sama dengan model aplikasi." if not diffs else f"{len(diffs)} perbedaan.")
            return 1 if diffs else 0
    except migrate.SchemaError as exc:
        print(f"GAGAL: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin")
    p.add_argument("--email", default="")
    p.add_argument("--name", default="")
    p.add_argument("--password", default="", help="Hindari di server bersama: tersimpan di riwayat terminal")
    sub.add_parser("ensure-admin")
    p = sub.add_parser("reset-password")
    p.add_argument("email")
    sub.add_parser("list-users")
    p = sub.add_parser("media-migrate", help="Masukkan proyek lama ke siklus media sementara (default: dry-run)")
    p.add_argument("--apply", action="store_true", help="Benar-benar terapkan (tanpa ini hanya laporan)")
    p.add_argument("--grace-hours", type=float, default=72.0,
                   help="Masa tenggang sebelum media proyek lama boleh dihapus (jam sejak sekarang)")
    sub.add_parser("media-cleanup", help="Jalankan satu putaran pembersihan media sekarang")
    p = sub.add_parser("db-status", help="Versi skema database dibanding aplikasi")
    p = sub.add_parser("db-upgrade", help="Migrasi skema ke versi terbaru (atau --target)")
    p.add_argument("--target", default="head")
    p = sub.add_parser("db-downgrade", help="Turunkan skema (menghapus data tabel/kolom yang lebih baru)")
    p.add_argument("target")
    p.add_argument("--allow-data-loss", action="store_true")
    sub.add_parser("db-check", help="Gagal (exit 1) bila skema database berbeda dari model aplikasi")
    args = parser.parse_args(argv)
    if args.cmd.startswith("db-"):
        sys.exit(_db_command(args))
    db.engine()

    if args.cmd == "create-admin":
        create_admin(args.email, args.name, args.password)
    elif args.cmd == "ensure-admin":
        if auth.count_users() == 0:
            print("Belum ada akun. Buat akun Admin pertama untuk login ke aplikasi.")
            create_admin()
    elif args.cmd == "reset-password":
        user = auth.find_by_email(args.email)
        if not user:
            sys.exit("Email tidak ditemukan.")
        pw = _ask_password()
        with db.engine().begin() as c:
            c.execute(update(db.users).where(db.users.c.id == user["id"]).values(
                password_hash=auth.hash_password(pw), active=True))
        auth.end_user_sessions(user["id"])
        print("Password diganti.")
    elif args.cmd == "media-migrate":
        from . import lifecycle
        report = lifecycle.migrate_legacy(apply=args.apply, grace_hours=args.grace_hours)
        for item in report["projects"]:
            print(f"  {item['id']}  {item['bytes'] / 1e6:9.1f} MB  {item['name']}")
        print(f"Proyek lama: {report['count']} ({report['bytes'] / 1e6:.1f} MB).")
        if report["applied"]:
            print(f"Diterapkan. Media proyek lama aman sampai paling cepat {args.grace_hours:.0f} jam lagi, "
                  "setelah itu mengikuti masa simpan sementara.")
        elif report["count"]:
            print("Dry-run: belum ada yang diubah. Jalankan ulang dengan --apply untuk menerapkan.")
    elif args.cmd == "media-cleanup":
        from . import lifecycle
        print(lifecycle.sweep())
    elif args.cmd == "list-users":
        with db.engine().connect() as c:
            for r in c.execute(select(db.users.c.email, db.users.c.name, db.users.c.role, db.users.c.active)):
                print(f"{r.email:35} {r.name:25} {r.role:11} {'aktif' if r.active else 'nonaktif'}")


if __name__ == "__main__":
    main()
