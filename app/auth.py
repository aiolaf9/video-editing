"""Akun, password, sesi login, dan hak akses.

Peran:
- admin      : semua unit & pengguna, pengaturan AI, statistik
- unit_admin : pengguna & guidebook di unitnya sendiri, semua proyek unitnya
- editor     : membuat & mengedit proyek di unitnya
"""
import base64
import hashlib
import hmac
import secrets
import time
from typing import Optional

from fastapi import HTTPException, Request, Response
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from . import db
from .config import COOKIE_SECURE, SESSION_DAYS
from .storage import new_id

COOKIE = "ve_session"
ROLES = ("admin", "unit_admin", "editor")
ROLE_LABELS = {"admin": "Admin", "unit_admin": "Admin Unit", "editor": "Editor"}
MIN_PASSWORD = 8
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


# ------------------------------------------------------------------ password

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt$%d$%d$%d$%s$%s" % (_SCRYPT["n"], _SCRYPT["r"], _SCRYPT["p"],
                                      base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), dklen=32,
                              n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def check_password_rules(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise HTTPException(400, f"Password minimal {MIN_PASSWORD} karakter.")


# ------------------------------------------------------------------ pengguna

def public_user(u: dict) -> dict:
    return {k: u.get(k) for k in ("id", "email", "name", "role", "unit_id", "active", "created", "last_login")} | {
        "role_label": ROLE_LABELS.get(u["role"], u["role"]), "unit_name": u.get("unit_name")}


def get_user(user_id: str) -> Optional[dict]:
    q = (select(db.users, db.units.c.name.label("unit_name"))
         .select_from(db.users.outerjoin(db.units, db.units.c.id == db.users.c.unit_id))
         .where(db.users.c.id == user_id))
    with db.engine().connect() as c:
        return db.row(c.execute(q).first())


def find_by_email(email: str) -> Optional[dict]:
    with db.engine().connect() as c:
        r = c.execute(select(db.users.c.id).where(db.users.c.email == email.strip().lower())).first()
    return get_user(r[0]) if r else None


def create_user(email: str, name: str, password: str, role: str, unit_id: Optional[str]) -> dict:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 200:
        raise HTTPException(400, "Email tidak valid.")
    if role not in ROLES:
        raise HTTPException(400, "Peran tidak dikenal.")
    if role != "admin" and not unit_id:
        raise HTTPException(400, "Pengguna selain Admin harus masuk ke salah satu unit.")
    check_password_rules(password)
    if find_by_email(email):
        raise HTTPException(409, "Email sudah terdaftar.")
    uid = new_id()
    try:
        with db.engine().begin() as c:
            c.execute(insert(db.users).values(id=uid, email=email, name=(name or email.split("@")[0])[:120],
                                              password_hash=hash_password(password), role=role, unit_id=unit_id,
                                              active=True, created=time.time()))
    except IntegrityError:
        raise HTTPException(409, "Email sudah terdaftar.")
    return get_user(uid)


def count_users() -> int:
    with db.engine().connect() as c:
        return c.execute(select(func.count()).select_from(db.users)).scalar() or 0


# ------------------------------------------------------------------ sesi & login

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _client_ip(request: Request) -> str:
    # Reverse proxy (Caddy) menambahkan IP asli di AKHIR header; nilai di depan bisa dipalsukan klien.
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "?"))[:60]


def _too_many_failures(email: str, ip: str) -> bool:
    since = time.time() - 15 * 60
    with db.engine().connect() as c:
        by_email = c.execute(select(func.count()).select_from(db.login_failures).where(
            db.login_failures.c.key == f"e:{email}", db.login_failures.c.at >= since)).scalar() or 0
        by_ip = c.execute(select(func.count()).select_from(db.login_failures).where(
            db.login_failures.c.key == f"i:{ip}", db.login_failures.c.at >= since)).scalar() or 0
    return by_email >= 8 or by_ip >= 30


def login(request: Request, response: Response, email: str, password: str) -> dict:
    email = (email or "").strip().lower()
    ip = _client_ip(request)
    if _too_many_failures(email, ip):
        raise HTTPException(429, "Terlalu banyak percobaan login gagal. Coba lagi 15 menit lagi.")
    user = find_by_email(email)
    if not user or not user["active"] or not verify_password(password or "", user["password_hash"]):
        with db.engine().begin() as c:
            now = time.time()
            c.execute(insert(db.login_failures), [{"key": f"e:{email}", "at": now}, {"key": f"i:{ip}", "at": now}])
            c.execute(delete(db.login_failures).where(db.login_failures.c.at < now - 86400))
        raise HTTPException(401, "Email atau password salah.")
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db.engine().begin() as c:
        c.execute(insert(db.sessions).values(token_hash=_token_hash(token), user_id=user["id"], created=now,
                                             expires=now + SESSION_DAYS * 86400))
        c.execute(update(db.users).where(db.users.c.id == user["id"]).values(last_login=now))
        c.execute(delete(db.sessions).where(db.sessions.c.expires < now))
        c.execute(delete(db.login_failures).where(db.login_failures.c.key == f"e:{email}"))
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=COOKIE_SECURE, path="/")
    return public_user(user)


def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        with db.engine().begin() as c:
            c.execute(delete(db.sessions).where(db.sessions.c.token_hash == _token_hash(token)))
    response.delete_cookie(COOKIE, path="/")


def end_user_sessions(user_id: str) -> None:
    with db.engine().begin() as c:
        c.execute(delete(db.sessions).where(db.sessions.c.user_id == user_id))


def current_user(request: Request) -> dict:
    """Dependency FastAPI: pengguna yang sedang login, atau 401."""
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Silakan login terlebih dahulu.")
    # Satu query: sesi + pengguna + unit (endpoint ini dipanggil di setiap permintaan).
    q = (select(db.users, db.units.c.name.label("unit_name"), db.sessions.c.expires.label("session_expires"))
         .select_from(db.sessions.join(db.users, db.users.c.id == db.sessions.c.user_id)
                      .outerjoin(db.units, db.units.c.id == db.users.c.unit_id))
         .where(db.sessions.c.token_hash == _token_hash(token)))
    with db.engine().connect() as c:
        user = db.row(c.execute(q).first())
    if not user or user.pop("session_expires") < time.time():
        raise HTTPException(401, "Sesi berakhir. Silakan login kembali.")
    if not user["active"]:
        raise HTTPException(401, "Akun tidak aktif.")
    return user


def require_admin(user: dict) -> None:
    if user["role"] != "admin":
        raise HTTPException(403, "Hanya Admin yang boleh melakukan ini.")


def require_manager(user: dict) -> None:
    if user["role"] not in ("admin", "unit_admin"):
        raise HTTPException(403, "Hanya Admin atau Admin Unit yang boleh melakukan ini.")


# ------------------------------------------------------------------ akses proyek

def can_view_project(user: dict, project: dict) -> bool:
    return user["role"] == "admin" or (user["unit_id"] is not None and project.get("unit_id") == user["unit_id"])


def can_delete_project(user: dict, project: dict) -> bool:
    if user["role"] == "admin":
        return True
    if not can_view_project(user, project):
        return False
    return user["role"] == "unit_admin" or project.get("owner_id") == user["id"]


def can_manage_user(actor: dict, target: dict) -> bool:
    if actor["role"] == "admin":
        return True
    return (actor["role"] == "unit_admin" and target["unit_id"] == actor["unit_id"]
            and target["role"] != "admin")
