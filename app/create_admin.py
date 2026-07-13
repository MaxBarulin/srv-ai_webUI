"""CLI: create the first admin user. Usage: python -m app.create_admin"""
from __future__ import annotations

import asyncio
import getpass
import sys

from app.audit import utcnow_iso
from app.auth import MIN_PASSWORD_LENGTH, hash_password
from app.db import get_connection, init_db


async def create_admin(login: str, password: str, display_name: str) -> None:
    await init_db()
    async with get_connection() as db:
        cursor = await db.execute("SELECT id FROM users WHERE login = ?", (login,))
        if await cursor.fetchone() is not None:
            print(f"Ошибка: пользователь «{login}» уже существует.", file=sys.stderr)
            raise SystemExit(1)
        await db.execute(
            "INSERT INTO users (login, pass_hash, display_name, role, is_active, created_at) "
            "VALUES (?, ?, ?, 'admin', 1, ?)",
            (login, hash_password(password), display_name, utcnow_iso()),
        )
        await db.commit()
    print(f"Администратор «{login}» создан.")


def main() -> None:
    print("Создание администратора srv-ai webUI")
    login = input("Логин: ").strip()
    if not login:
        print("Ошибка: логин не может быть пустым.", file=sys.stderr)
        raise SystemExit(1)
    display_name = input("Отображаемое имя: ").strip() or login
    password = getpass.getpass(f"Пароль (мин. {MIN_PASSWORD_LENGTH} символов): ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Ошибка: пароль короче {MIN_PASSWORD_LENGTH} символов.", file=sys.stderr)
        raise SystemExit(1)
    if getpass.getpass("Пароль ещё раз: ") != password:
        print("Ошибка: пароли не совпадают.", file=sys.stderr)
        raise SystemExit(1)
    asyncio.run(create_admin(login, password, display_name))


if __name__ == "__main__":
    main()
