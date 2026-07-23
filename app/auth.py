"""Authentication: bcrypt password hashing, DB-backed sessions, login rate limiting."""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone

import aiosqlite
import bcrypt
from fastapi import Depends, HTTPException, Request

from app.audit import utcnow_iso
from app.config import settings
from app.db import get_db

SESSION_COOKIE = "session"
MIN_PASSWORD_LENGTH = 10

RATE_LIMIT_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 300


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


# Фиктивный хеш для входа несуществующего пользователя: bcrypt всё равно
# выполняется, чтобы время ответа не выдавало, существует логин или нет
# (защита от перечисления логинов по таймингу).
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


def verify_password(password: str, pass_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), pass_hash.encode("ascii"))
    except ValueError:
        return False


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов",
        )


class LoginRateLimiter:
    """In-memory per-IP limiter: N attempts per window. Resets on restart."""

    def __init__(self, attempts: int = RATE_LIMIT_ATTEMPTS, window: int = RATE_LIMIT_WINDOW_SECONDS):
        self.attempts = attempts
        self.window = window
        self._hits: dict[str, list[float]] = {}

    def check(self, ip: str) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits.get(ip, []) if now - t < self.window]
        self._hits[ip] = hits
        return len(hits) < self.attempts

    def register(self, ip: str) -> None:
        self._hits.setdefault(ip, []).append(time.monotonic())

    def reset(self, ip: str) -> None:
        self._hits.pop(ip, None)


login_rate_limiter = LoginRateLimiter()


async def create_session(db: aiosqlite.Connection, user_id: int, ip: str | None) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=settings.session_ttl_hours)
    await db.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at, ip) VALUES (?, ?, ?, ?, ?)",
        (token, user_id, now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds"), ip),
    )
    await db.commit()
    return token


async def delete_session(db: aiosqlite.Connection, token: str) -> None:
    await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
    await db.commit()


async def _load_session_user(db: aiosqlite.Connection, token: str) -> aiosqlite.Row | None:
    cursor = await db.execute(
        """SELECT s.token, s.expires_at, u.id, u.login, u.display_name, u.role, u.is_active
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ?""",
        (token,),
    )
    return await cursor.fetchone()


async def _touch_session(db: aiosqlite.Connection, token: str) -> None:
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    await db.execute(
        "UPDATE sessions SET expires_at = ? WHERE token = ?",
        (expires.isoformat(timespec="seconds"), token),
    )
    await db.commit()


async def get_current_user(request: Request, db: aiosqlite.Connection = Depends(get_db)) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Не выполнен вход")
    row = await _load_session_user(db, token)
    if row is None:
        raise HTTPException(status_code=401, detail="Сессия недействительна")
    if row["expires_at"] <= utcnow_iso():
        await delete_session(db, token)
        raise HTTPException(status_code=401, detail="Сессия истекла")
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Учётная запись заблокирована")
    await _touch_session(db, token)
    return {
        "id": row["id"],
        "login": row["login"],
        "display_name": row["display_name"],
        "role": row["role"],
    }


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
