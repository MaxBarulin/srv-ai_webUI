"""Auth endpoints: login, logout, current user info, password change."""
from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app import auth
from app.audit import write_audit
from app.auth import (
    SESSION_COOKIE,
    client_ip,
    create_session,
    delete_session,
    get_current_user,
    hash_password,
    login_rate_limiter,
    validate_password,
    verify_password,
)
from app.db import get_db

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    login: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    ip = client_ip(request)
    if not login_rate_limiter.check(ip):
        raise HTTPException(status_code=429, detail="Слишком много попыток входа. Повторите через 5 минут.")

    cursor = await db.execute(
        "SELECT id, login, pass_hash, display_name, role, is_active FROM users WHERE login = ?",
        (payload.login,),
    )
    row = await cursor.fetchone()

    if row is None or not verify_password(payload.password, row["pass_hash"]):
        login_rate_limiter.register(ip)
        await write_audit(db, user_id=row["id"] if row else None, action="login_failed",
                          details=f"login={payload.login}", ip=ip)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    if not row["is_active"]:
        login_rate_limiter.register(ip)
        await write_audit(db, user_id=row["id"], action="login_blocked", ip=ip)
        raise HTTPException(status_code=403, detail="Учётная запись заблокирована")

    login_rate_limiter.reset(ip)
    token = await create_session(db, row["id"], ip)
    await write_audit(db, user_id=row["id"], action="login", ip=ip)

    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {
        "id": row["id"],
        "login": row["login"],
        "display_name": row["display_name"],
        "role": row["role"],
    }


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await delete_session(db, token)
    await write_audit(db, user_id=user["id"], action="logout", ip=client_ip(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    return user


@router.post("/me/password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cursor = await db.execute("SELECT pass_hash FROM users WHERE id = ?", (user["id"],))
    row = await cursor.fetchone()
    if row is None or not verify_password(payload.current_password, row["pass_hash"]):
        raise HTTPException(status_code=403, detail="Текущий пароль неверен")
    validate_password(payload.new_password)
    await db.execute(
        "UPDATE users SET pass_hash = ? WHERE id = ?",
        (hash_password(payload.new_password), user["id"]),
    )
    await db.commit()
    await write_audit(db, user_id=user["id"], action="password_changed", ip=client_ip(request))
    return {"ok": True}
