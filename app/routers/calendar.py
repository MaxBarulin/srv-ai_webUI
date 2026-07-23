"""Calendar endpoints (§6): personal/shared events, date-range filter, full CRUD.

Datetimes — ISO 8601 с часовым поясом (Europe/Moscow на клиенте).
Этот же API используют инструменты LLM (§7).
"""
from __future__ import annotations

from datetime import datetime

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.audit import utcnow_iso, write_audit
from app.auth import client_ip, get_current_user
from app.db import get_db

router = APIRouter(prefix="/api/events", tags=["calendar"])

VISIBLE = "(e.scope = 'shared' OR e.owner_id = ?)"

SELECT_EVENT = """
SELECT e.id, e.owner_id, e.scope, e.title, e.description, e.location,
       e.starts_at, e.ends_at, e.all_day, e.created_at, e.updated_at,
       a.display_name AS author_name,
       u.display_name AS updated_by_name
FROM events e
JOIN users a ON a.id = e.owner_id
LEFT JOIN users u ON u.id = e.updated_by
"""


class EventCreate(BaseModel):
    title: str
    description: str = ""
    location: str = ""
    starts_at: str
    ends_at: str
    all_day: bool = False
    scope: str = Field("personal", pattern="^(personal|shared)$")


class EventUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    location: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    all_day: bool | None = None
    scope: str | None = Field(None, pattern="^(personal|shared)$")


def _parse_iso(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Некорректная дата/время в поле {field}")


def _validate_range(starts_at: str, ends_at: str) -> None:
    start = _parse_iso(starts_at, "starts_at")
    end = _parse_iso(ends_at, "ends_at")
    if (start.tzinfo is None) != (end.tzinfo is None):
        raise HTTPException(status_code=400, detail="Даты начала и конца должны быть в одном формате")
    if end < start:
        raise HTTPException(status_code=400, detail="Окончание раньше начала")


async def _get_visible_event(db: aiosqlite.Connection, event_id: int, user_id: int) -> aiosqlite.Row:
    cursor = await db.execute(
        SELECT_EVENT + f"WHERE e.id = ? AND {VISIBLE}",
        (event_id, user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return row


@router.get("")
async def list_events(
    date_from: str = Query("", description="ISO 8601; события, заканчивающиеся не раньше"),
    date_to: str = Query("", description="ISO 8601; события, начинающиеся не позже"),
    scope: str = Query("all", pattern="^(personal|shared|all)$"),
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    conditions = [VISIBLE]
    params: list = [user["id"]]

    if scope != "all":
        conditions.append("e.scope = ?")
        params.append(scope)
    if date_from:
        _parse_iso(date_from, "date_from")
        conditions.append("e.ends_at >= ?")
        params.append(date_from)
    if date_to:
        _parse_iso(date_to, "date_to")
        conditions.append("e.starts_at <= ?")
        params.append(date_to)

    cursor = await db.execute(
        SELECT_EVENT + "WHERE " + " AND ".join(conditions) + " ORDER BY e.starts_at",
        params,
    )
    return [dict(row) for row in await cursor.fetchall()]


@router.get("/{event_id}")
async def get_event(
    event_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    return dict(await _get_visible_event(db, event_id, user["id"]))


@router.post("", status_code=201)
async def create_event(
    payload: EventCreate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")
    _validate_range(payload.starts_at, payload.ends_at)
    now = utcnow_iso()
    cursor = await db.execute(
        "INSERT INTO events (owner_id, scope, title, description, location, "
        "starts_at, ends_at, all_day, created_at, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user["id"], payload.scope, title, payload.description, payload.location,
         payload.starts_at, payload.ends_at, int(payload.all_day), now, now, user["id"]),
    )
    await db.commit()
    return dict(await _get_visible_event(db, cursor.lastrowid, user["id"]))


@router.put("/{event_id}")
async def update_event(
    event_id: int,
    payload: EventUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _get_visible_event(db, event_id, user["id"])

    if payload.scope is not None and payload.scope != row["scope"] and row["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Менять область может только автор события")

    title = row["title"] if payload.title is None else payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")

    starts_at = row["starts_at"] if payload.starts_at is None else payload.starts_at
    ends_at = row["ends_at"] if payload.ends_at is None else payload.ends_at
    _validate_range(starts_at, ends_at)

    await db.execute(
        "UPDATE events SET title = ?, description = ?, location = ?, starts_at = ?, "
        "ends_at = ?, all_day = ?, scope = ?, updated_at = ?, updated_by = ? WHERE id = ?",
        (
            title,
            row["description"] if payload.description is None else payload.description,
            row["location"] if payload.location is None else payload.location,
            starts_at,
            ends_at,
            row["all_day"] if payload.all_day is None else int(payload.all_day),
            row["scope"] if payload.scope is None else payload.scope,
            utcnow_iso(),
            user["id"],
            event_id,
        ),
    )
    await db.commit()
    return dict(await _get_visible_event(db, event_id, user["id"]))


@router.delete("/{event_id}")
async def delete_event(
    event_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _get_visible_event(db, event_id, user["id"])
    # Удалить может только автор (общее событие видят все, но чужое не удаляют)
    if row["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Удалить событие может только его автор")
    await db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    await db.commit()
    await write_audit(db, user_id=user["id"], action="event_deleted",
                      object_type="event", object_id=str(event_id),
                      details=f"title={row['title'][:80]}, scope={row['scope']}",
                      ip=client_ip(request))
    return {"ok": True}
