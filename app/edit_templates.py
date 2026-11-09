"""Template/preset editing (P1): gaya subtitle (+ opsional warna & rasio) yang bisa dipakai ulang.

Jenis:
- bawaan  : tetap di kode, hanya-baca, terlihat semua orang (gaya subtitle yang sudah ada sebelumnya);
- personal: milik satu pengguna, hanya ia yang melihat & mengubah;
- unit    : terlihat semua anggota unit; dikelola Admin Unit unit itu atau Admin;
- global  : terlihat semua pengguna; dikelola Admin.
Admin melihat semua template unit (seperti ia melihat semua proyek).
"""
from __future__ import annotations

import json
import time
from typing import Literal, Optional

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import delete, func, insert, or_, select, update

from . import db
from .recipe import Look, RecipeSubtitles, _Strict
from .storage import new_id

MAX_PERSONAL = 50
MAX_PER_UNIT = 100
MAX_GLOBAL = 100


class TemplateData(_Strict):
    subtitles: RecipeSubtitles
    look: Optional[Look] = None
    aspect: Optional[Literal["9:16", "1:1", "4:5", "16:9", "original"]] = None


class TemplateIn(_Strict):
    name: str = Field(min_length=1, max_length=80)
    scope: Literal["personal", "unit", "global"] = "personal"
    unit_id: Optional[str] = Field(None, max_length=12)   # hanya Admin: template unit untuk unit tertentu
    data: TemplateData


class TemplateUpdate(_Strict):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    data: Optional[TemplateData] = None


def _sub(**kw) -> dict:
    return RecipeSubtitles(**kw).model_dump()


BUILTIN = [
    {"id": "builtin:karaoke", "name": "Karaoke kuning", "data": {"subtitles": _sub()}},
    {"id": "builtin:pop", "name": "Pop hijau", "data": {"subtitles": _sub(style="pop", font="Poppins",
                                                                         highlight="#39FF14")}},
    {"id": "builtin:word", "name": "Satu kata besar", "data": {"subtitles": _sub(style="word", font="Anton",
                                                                                size=1.1)}},
    {"id": "builtin:simple", "name": "Minimal putih", "data": {"subtitles": _sub(style="simple", uppercase=False,
                                                                                words_per_line=5, size=0.85)}},
    {"id": "builtin:news", "name": "Berita (atas)", "data": {"subtitles": _sub(style="karaoke", font="Bebas Neue",
                                                                              position="top", highlight="#FF4D4D",
                                                                              words_per_line=4)}},
]
BUILTIN_IDS = {t["id"] for t in BUILTIN}


def can_see(user: dict, row: dict) -> bool:
    if row["scope"] == "global":
        return True
    if row["scope"] == "unit":
        return user["role"] == "admin" or (user["unit_id"] is not None and row["unit_id"] == user["unit_id"])
    return row["owner_id"] == user["id"]


def can_manage(user: dict, row: dict) -> bool:
    if row["scope"] == "global":
        return user["role"] == "admin"
    if row["scope"] == "unit":
        return user["role"] == "admin" or (user["role"] == "unit_admin" and row["unit_id"] == user["unit_id"])
    return row["owner_id"] == user["id"]


def _public(row: dict, user: dict, unit_names: dict) -> dict:
    return {"id": row["id"], "name": row["name"], "scope": row["scope"], "unit_id": row["unit_id"],
            "unit_name": unit_names.get(row["unit_id"]), "builtin": False, "can_edit": can_manage(user, row),
            "data": json.loads(row["data"])}


def list_for(user: dict) -> list[dict]:
    t = db.edit_templates.c
    cond = or_(t.scope == "global", (t.scope == "personal") & (t.owner_id == user["id"]))
    if user["role"] == "admin":
        cond = or_(cond, t.scope == "unit")
    elif user["unit_id"]:
        cond = or_(cond, (t.scope == "unit") & (t.unit_id == user["unit_id"]))
    with db.engine().connect() as c:
        rows = [db.row(r) for r in c.execute(select(db.edit_templates).where(cond).order_by(t.scope, t.name))]
        units = {r.id: r.name for r in c.execute(select(db.units.c.id, db.units.c.name))}
    rows = [r for r in rows if can_see(user, r)]   # personal milik orang lain tidak pernah lolos
    return ([{**b, "scope": "bawaan", "unit_id": None, "unit_name": None, "builtin": True, "can_edit": False}
             for b in BUILTIN] + [_public(r, user, units) for r in rows])


def get(template_id: str, user: dict) -> dict:
    if template_id in BUILTIN_IDS:
        return next({**b, "builtin": True} for b in BUILTIN if b["id"] == template_id)
    with db.engine().connect() as c:
        row = db.row(c.execute(select(db.edit_templates).where(db.edit_templates.c.id == template_id)).first())
    if row is None or not can_see(user, row):
        raise HTTPException(404, "Template tidak ditemukan.")
    return row


def create(body: TemplateIn, user: dict) -> dict:
    t = db.edit_templates.c
    unit_id = None
    if body.scope == "global":
        if user["role"] != "admin":
            raise HTTPException(403, "Hanya Admin yang boleh membuat template global.")
        limit, count_cond = MAX_GLOBAL, t.scope == "global"
    elif body.scope == "unit":
        if user["role"] == "admin":
            unit_id = body.unit_id or user["unit_id"]
            if not unit_id:
                raise HTTPException(400, "Pilih unit untuk template unit.")
        elif user["role"] == "unit_admin":
            if body.unit_id and body.unit_id != user["unit_id"]:
                raise HTTPException(403, "Admin Unit hanya boleh membuat template untuk unitnya sendiri.")
            unit_id = user["unit_id"]
        else:
            raise HTTPException(403, "Hanya Admin Unit atau Admin yang boleh membuat template unit.")
        if not unit_id:
            raise HTTPException(400, "Akun Anda belum masuk ke unit mana pun.")
        limit, count_cond = MAX_PER_UNIT, (t.scope == "unit") & (t.unit_id == unit_id)
    else:
        limit, count_cond = MAX_PERSONAL, (t.scope == "personal") & (t.owner_id == user["id"])
    now = time.time()
    row = {"id": new_id(), "scope": body.scope, "unit_id": unit_id, "owner_id": user["id"], "name": body.name.strip(),
           "data": json.dumps(body.data.model_dump(mode="json"), ensure_ascii=False), "created": now, "updated": now}
    with db.engine().begin() as c:
        if unit_id and c.execute(select(db.units.c.id).where(db.units.c.id == unit_id)).first() is None:
            raise HTTPException(404, "Unit tidak ditemukan.")
        if c.execute(select(func.count()).select_from(db.edit_templates).where(count_cond)).scalar() >= limit:
            raise HTTPException(409, f"Batas {limit} template untuk jenis ini tercapai. Hapus yang tidak dipakai.")
        c.execute(insert(db.edit_templates).values(**row))
    return _public(row, user, {})


def change(template_id: str, body: TemplateUpdate, user: dict) -> dict:
    if template_id in BUILTIN_IDS:
        raise HTTPException(403, "Template bawaan tidak dapat diubah. Simpan sebagai preset baru.")
    row = get(template_id, user)
    if not can_manage(user, row):
        raise HTTPException(403, "Anda tidak berhak mengubah template ini.")
    values = {"updated": time.time()}
    if body.name is not None:
        values["name"] = body.name.strip()
    if body.data is not None:
        values["data"] = json.dumps(body.data.model_dump(mode="json"), ensure_ascii=False)
    with db.engine().begin() as c:
        c.execute(update(db.edit_templates).where(db.edit_templates.c.id == template_id).values(**values))
    return _public({**row, **values}, user, {})


def remove(template_id: str, user: dict) -> None:
    if template_id in BUILTIN_IDS:
        raise HTTPException(403, "Template bawaan tidak dapat dihapus.")
    row = get(template_id, user)
    if not can_manage(user, row):
        raise HTTPException(403, "Anda tidak berhak menghapus template ini.")
    with db.engine().begin() as c:
        c.execute(delete(db.edit_templates).where(db.edit_templates.c.id == template_id))
