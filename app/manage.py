"""Perintah administrasi dari terminal.

    python -m app.manage create-admin            (ditanya email & password)
    python -m app.manage ensure-admin            (buat admin hanya bila belum ada pengguna; dipakai start.bat)
    python -m app.manage reset-password EMAIL
    python -m app.manage list-users
"""
import argparse
import getpass
import sys

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


def main() -> None:
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
    args = parser.parse_args()
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
    elif args.cmd == "list-users":
        with db.engine().connect() as c:
            for r in c.execute(select(db.users.c.email, db.users.c.name, db.users.c.role, db.users.c.active)):
                print(f"{r.email:35} {r.name:25} {r.role:11} {'aktif' if r.active else 'nonaktif'}")


if __name__ == "__main__":
    main()
