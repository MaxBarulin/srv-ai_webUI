"""Admin endpoints: user management, specializations, chat examples, feedback export."""
from __future__ import annotations

import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.audit import utcnow_iso, write_audit
from app.auth import client_ip, hash_password, require_admin, validate_password
from app.db import get_db
from app.metrics import metrics

router = APIRouter(prefix="/api/admin", tags=["admin"])


class CreateUserRequest(BaseModel):
    login: str
    password: str
    display_name: str = ""
    role: str = "user"


class SetActiveRequest(BaseModel):
    is_active: bool


class ResetPasswordRequest(BaseModel):
    new_password: str


async def _get_user_or_404(db: aiosqlite.Connection, user_id: int) -> aiosqlite.Row:
    cursor = await db.execute(
        "SELECT id, login, display_name, role, is_active, created_at FROM users WHERE id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return row


@router.get("/users")
async def list_users(
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    cursor = await db.execute(
        "SELECT id, login, display_name, role, is_active, created_at FROM users ORDER BY login"
    )
    return [dict(row) for row in await cursor.fetchall()]


@router.post("/users", status_code=201)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    login = payload.login.strip()
    if not login:
        raise HTTPException(status_code=400, detail="Логин не может быть пустым")
    if payload.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Недопустимая роль")
    validate_password(payload.password)

    cursor = await db.execute("SELECT id FROM users WHERE login = ?", (login,))
    if await cursor.fetchone() is not None:
        raise HTTPException(status_code=409, detail="Пользователь с таким логином уже существует")

    cursor = await db.execute(
        "INSERT INTO users (login, pass_hash, display_name, role, is_active, created_at) "
        "VALUES (?, ?, ?, ?, 1, ?)",
        (login, hash_password(payload.password), payload.display_name.strip() or login,
         payload.role, utcnow_iso()),
    )
    await db.commit()
    user_id = cursor.lastrowid
    await write_audit(db, user_id=admin["id"], action="user_created",
                      object_type="user", object_id=str(user_id),
                      details=f"login={login}, role={payload.role}", ip=client_ip(request))
    return dict(await _get_user_or_404(db, user_id))


@router.post("/users/{user_id}/active")
async def set_user_active(
    user_id: int,
    payload: SetActiveRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _get_user_or_404(db, user_id)
    if user_id == admin["id"] and not payload.is_active:
        raise HTTPException(status_code=400, detail="Нельзя заблокировать самого себя")

    await db.execute("UPDATE users SET is_active = ? WHERE id = ?", (int(payload.is_active), user_id))
    if not payload.is_active:
        # kill active sessions of the blocked user
        await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    await db.commit()
    await write_audit(db, user_id=admin["id"],
                      action="user_unblocked" if payload.is_active else "user_blocked",
                      object_type="user", object_id=str(user_id),
                      details=f"login={row['login']}", ip=client_ip(request))
    return dict(await _get_user_or_404(db, user_id))


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _get_user_or_404(db, user_id)
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    if row["role"] == "admin":
        cursor = await db.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1 AND id <> ?",
            (user_id,))
        if (await cursor.fetchone())[0] == 0:
            raise HTTPException(status_code=400,
                                detail="Нельзя удалить последнего активного администратора")

    # Каскад: всё, что владелец создал в системе. audit_log сохраняем, но
    # обнуляем ссылку — история действий не теряется.
    # Порядок важен: сначала зависимые таблицы (FK enforcement).
    await db.execute(
        "DELETE FROM feedback WHERE user_id = ? "
        "OR chat_id IN (SELECT id FROM chats WHERE user_id = ?) "
        "OR message_id IN (SELECT m.id FROM messages m "
        "  JOIN chats c ON c.id = m.chat_id WHERE c.user_id = ?)",
        (user_id, user_id, user_id))
    await db.execute(
        "DELETE FROM messages WHERE chat_id IN (SELECT id FROM chats WHERE user_id = ?)",
        (user_id,))
    await db.execute("DELETE FROM chats WHERE user_id = ?", (user_id,))
    await db.execute("DELETE FROM notes WHERE owner_id = ?", (user_id,))
    await db.execute("DELETE FROM events WHERE owner_id = ?", (user_id,))
    await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    await db.execute("UPDATE notes SET updated_by = NULL WHERE updated_by = ?", (user_id,))
    await db.execute("UPDATE events SET updated_by = NULL WHERE updated_by = ?", (user_id,))
    await db.execute("UPDATE audit_log SET user_id = NULL WHERE user_id = ?", (user_id,))
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()

    await write_audit(db, user_id=admin["id"], action="user_deleted",
                      object_type="user", object_id=str(user_id),
                      details=f"login={row['login']}, role={row['role']}",
                      ip=client_ip(request))
    return {"ok": True}


@router.post("/users/{user_id}/password")
async def reset_user_password(
    user_id: int,
    payload: ResetPasswordRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _get_user_or_404(db, user_id)
    validate_password(payload.new_password)
    await db.execute(
        "UPDATE users SET pass_hash = ? WHERE id = ?",
        (hash_password(payload.new_password), user_id),
    )
    # force re-login with the new password everywhere
    await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    await db.commit()
    await write_audit(db, user_id=admin["id"], action="user_password_reset",
                      object_type="user", object_id=str(user_id),
                      details=f"login={row['login']}", ip=client_ip(request))
    return {"ok": True}


# ===== Специализации (§15) =====

class SpecializationBody(BaseModel):
    name: str
    system_prompt: str = ""
    is_active: bool = True
    sort_order: int = 0


@router.get("/specializations")
async def admin_list_specializations(
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    cursor = await db.execute(
        "SELECT id, name, system_prompt, is_active, sort_order, created_at "
        "FROM specializations ORDER BY sort_order, id")
    return [dict(row) for row in await cursor.fetchall()]


@router.post("/specializations", status_code=201)
async def admin_create_specialization(
    payload: SpecializationBody,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")
    cursor = await db.execute(
        "INSERT INTO specializations (name, system_prompt, is_active, sort_order, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, payload.system_prompt, int(payload.is_active), payload.sort_order, utcnow_iso()))
    await db.commit()
    return {"id": cursor.lastrowid}


@router.put("/specializations/{spec_id}")
async def admin_update_specialization(
    spec_id: int,
    payload: SpecializationBody,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")
    cursor = await db.execute(
        "UPDATE specializations SET name = ?, system_prompt = ?, is_active = ?, sort_order = ? "
        "WHERE id = ?",
        (name, payload.system_prompt, int(payload.is_active), payload.sort_order, spec_id))
    await db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Специализация не найдена")
    return {"ok": True}


@router.delete("/specializations/{spec_id}")
async def admin_delete_specialization(
    spec_id: int,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    # Чаты, ссылавшиеся на специализацию, останутся с NULL (общий режим)
    await db.execute("UPDATE chats SET specialization_id = NULL WHERE specialization_id = ?",
                     (spec_id,))
    cursor = await db.execute("DELETE FROM specializations WHERE id = ?", (spec_id,))
    await db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Специализация не найдена")
    return {"ok": True}


# ===== Примеры запросов в пустом чате (§15) =====

class ExamplesBody(BaseModel):
    items: list[str]


@router.get("/examples")
async def admin_list_examples(
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    cursor = await db.execute("SELECT id, text FROM chat_examples ORDER BY sort_order, id")
    return [dict(row) for row in await cursor.fetchall()]


@router.put("/examples")
async def admin_set_examples(
    payload: ExamplesBody,
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    items = [t.strip() for t in payload.items if t.strip()]
    await db.execute("DELETE FROM chat_examples")
    for order, text in enumerate(items):
        await db.execute("INSERT INTO chat_examples (text, sort_order) VALUES (?, ?)", (text, order))
    await db.commit()
    return {"ok": True, "count": len(items)}


# ===== Выгрузка обратной связи в JSONL (§15) =====

@router.get("/feedback/export")
async def export_feedback(
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> StreamingResponse:
    cursor = await db.execute(
        "SELECT f.id, f.message_id, f.chat_id, f.rating, f.comment, f.specialization, "
        "       f.created_at, u.login AS user_login, "
        "       q.content AS prompt, m.content AS answer "
        "FROM feedback f "
        "JOIN messages m ON m.id = f.message_id "
        "JOIN users u ON u.id = f.user_id "
        "LEFT JOIN messages q ON q.id = ("
        "    SELECT MAX(id) FROM messages WHERE chat_id = f.chat_id "
        "    AND id < f.message_id AND role = 'user') "
        "ORDER BY f.id")
    rows = await cursor.fetchall()

    def generate():
        for row in rows:
            yield json.dumps(dict(row), ensure_ascii=False) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=feedback.jsonl"},
    )


# ===== Аудит-лог (§10, §13) — только просмотр админом =====

@router.get("/audit")
async def list_audit(
    limit: int = 100,
    offset: int = 0,
    action: str = "",
    admin: dict = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    limit = max(1, min(limit, 500))
    conditions = []
    params: list = []
    if action.strip():
        conditions.append("a.action = ?")
        params.append(action.strip())
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    count_cur = await db.execute(f"SELECT COUNT(*) FROM audit_log a {where}", params)
    total = (await count_cur.fetchone())[0]

    cursor = await db.execute(
        "SELECT a.id, a.action, a.object_type, a.object_id, a.details, a.created_at, a.ip, "
        "       u.login AS user_login "
        "FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
        f"{where} ORDER BY a.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset])
    items = [dict(row) for row in await cursor.fetchall()]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ===== Метрики (§13, п. 3.7) — без содержания запросов =====

@router.get("/metrics")
async def get_metrics(admin: dict = Depends(require_admin)) -> dict:
    return metrics.snapshot()
