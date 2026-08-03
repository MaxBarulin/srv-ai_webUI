"""Настройки приложения, меняемые администратором из интерфейса.

В отличие от .env, эти значения правятся на ходу и вступают в силу со
следующего запроса — перезапускать сервис на закрытом контуре не нужно.
Сюда попадает только то, что действительно нужно переключать в работе:
всё остальное остаётся в .env, где его видно при аудите конфигурации.
"""
from __future__ import annotations

import aiosqlite

# Кому доступен инструмент расчётов (§17)
CALC_ACCESS = "calc_access"
CALC_OFF = "off"        # выключен у всех — аварийный рубильник
CALC_ADMIN = "admin"    # только администраторам — режим обкатки
CALC_ALL = "all"        # всем сотрудникам
CALC_ACCESS_VALUES = (CALC_OFF, CALC_ADMIN, CALC_ALL)

DEFAULTS: dict[str, str] = {
    # По умолчанию инструмент виден только администратору: сначала обкатка,
    # потом открытие на всех — переключателем, а не выкаткой новой версии.
    CALC_ACCESS: CALC_ADMIN,
}


async def get_setting(db: aiosqlite.Connection, key: str) -> str:
    cursor = await db.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    row = await cursor.fetchone()
    return row["value"] if row is not None else DEFAULTS.get(key, "")


async def set_setting(db: aiosqlite.Connection, key: str, value: str) -> None:
    await db.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value))


def calc_allowed(access: str, user: dict) -> bool:
    """Доступен ли инструмент расчётов этому пользователю."""
    if access == CALC_ALL:
        return True
    if access == CALC_ADMIN:
        return user["role"] == "admin"
    return False


async def calc_allowed_for(db: aiosqlite.Connection, user: dict) -> bool:
    return calc_allowed(await get_setting(db, CALC_ACCESS), user)
