"""Admin endpoints: user management (list/create/block/unblock/reset password)."""
from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.audit import utcnow_iso, write_audit
from app.auth import client_ip, hash_password, require_admin, validate_password
from app.db import get_db

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
