"""Notes endpoints (§5): personal/shared scopes, substring+tag search, full CRUD.

Этот же API используют инструменты LLM (§7) — права всегда текущего пользователя.
"""
from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.audit import utcnow_iso, write_audit
from app.auth import client_ip, get_current_user
from app.db import get_db
from app.groups import ensure_group_exists
from app.scoping import (
    may_retarget,
    new_group_id,
    update_group_id,
    visible_params,
    visible_sql,
)

router = APIRouter(prefix="/api/notes", tags=["notes"])

VISIBLE = visible_sql("n")

SELECT_NOTE = """
SELECT n.id, n.owner_id, n.scope, n.title, n.body, n.tags, n.group_id,
       n.created_at, n.updated_at,
       a.display_name AS author_name,
       u.display_name AS updated_by_name,
       g.name AS group_name
FROM notes n
JOIN users a ON a.id = n.owner_id
LEFT JOIN users u ON u.id = n.updated_by
LEFT JOIN groups g ON g.id = n.group_id
"""


class NoteCreate(BaseModel):
    title: str
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    scope: str = Field("personal", pattern="^(personal|shared)$")
    # Кому адресована общая заметка. Учитывается только у автора без группы;
    # автор в группе всегда публикует в свою (см. app/scoping.py).
    group_id: int | None = None


class NoteUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    tags: list[str] | None = None
    scope: str | None = Field(None, pattern="^(personal|shared)$")
    group_id: int | None = None


def _tags_to_str(tags: list[str]) -> str:
    cleaned = [t.strip() for t in tags if t.strip()]
    return ",".join(dict.fromkeys(cleaned))  # без дубликатов, порядок сохранён


def _note_dict(row: aiosqlite.Row) -> dict:
    d = dict(row)
    d["tags"] = [t for t in d["tags"].split(",") if t]
    return d


async def _get_visible_note(db: aiosqlite.Connection, note_id: int, user: dict) -> aiosqlite.Row:
    cursor = await db.execute(
        SELECT_NOTE + f"WHERE n.id = ? AND {VISIBLE}",
        [note_id, *visible_params(user)],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return row


@router.get("")
async def list_notes(
    query: str = "",
    tags: str = Query("", description="через запятую; заметка должна содержать все указанные"),
    scope: str = Query("all", pattern="^(personal|shared|all)$"),
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    conditions = [VISIBLE]
    params: list = visible_params(user)

    if scope != "all":
        conditions.append("n.scope = ?")
        params.append(scope)

    if query.strip():
        conditions.append("(n.title LIKE ? OR n.body LIKE ?)")
        like = f"%{query.strip()}%"
        params.extend([like, like])

    for tag in [t.strip() for t in tags.split(",") if t.strip()]:
        conditions.append("(',' || n.tags || ',') LIKE ?")
        params.append(f"%,{tag},%")

    cursor = await db.execute(
        SELECT_NOTE + "WHERE " + " AND ".join(conditions) + " ORDER BY n.updated_at DESC",
        params,
    )
    return [_note_dict(row) for row in await cursor.fetchall()]


@router.get("/{note_id}")
async def get_note(
    note_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    return _note_dict(await _get_visible_note(db, note_id, user))


@router.post("", status_code=201)
async def create_note(
    payload: NoteCreate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Заголовок не может быть пустым")
    group_id = await ensure_group_exists(db, new_group_id(
        user, payload.scope, payload.group_id, "group_id" in payload.model_fields_set))
    now = utcnow_iso()
    cursor = await db.execute(
        "INSERT INTO notes (owner_id, scope, title, body, tags, group_id, "
        "created_at, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user["id"], payload.scope, title, payload.body, _tags_to_str(payload.tags),
         group_id, now, now, user["id"]),
    )
    await db.commit()
    return _note_dict(await _get_visible_note(db, cursor.lastrowid, user))


@router.put("/{note_id}")
async def update_note(
    note_id: int,
    payload: NoteUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _get_visible_note(db, note_id, user)

    # Сменить область может только владелец (иначе кто угодно утащит общую в личные)
    if payload.scope is not None and payload.scope != row["scope"] and row["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Менять область может только автор заметки")

    title = row["title"] if payload.title is None else payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Заголовок не может быть пустым")

    scope = row["scope"] if payload.scope is None else payload.scope
    explicit_group = "group_id" in payload.model_fields_set
    if explicit_group and row["scope"] == "shared" and not may_retarget(user, row["owner_id"]):
        raise HTTPException(status_code=403,
                            detail="Менять группу может автор заметки или администратор")
    group_id = await ensure_group_exists(db, update_group_id(
        user, row["scope"], row["group_id"], scope, payload.group_id, explicit_group))

    await db.execute(
        "UPDATE notes SET title = ?, body = ?, tags = ?, scope = ?, group_id = ?, "
        "updated_at = ?, updated_by = ? WHERE id = ?",
        (
            title,
            row["body"] if payload.body is None else payload.body,
            row["tags"] if payload.tags is None else _tags_to_str(payload.tags),
            scope,
            group_id,
            utcnow_iso(),
            user["id"],
            note_id,
        ),
    )
    await db.commit()
    return _note_dict(await _get_visible_note(db, note_id, user))


@router.delete("/{note_id}")
async def delete_note(
    note_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _get_visible_note(db, note_id, user)
    # Удалить может только автор (общую заметку видят все, но чужую не удаляют)
    if row["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Удалить заметку может только её автор")
    await db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    await db.commit()
    await write_audit(db, user_id=user["id"], action="note_deleted",
                      object_type="note", object_id=str(note_id),
                      details=f"title={row['title'][:80]}, scope={row['scope']}",
                      ip=client_ip(request))
    return {"ok": True}
