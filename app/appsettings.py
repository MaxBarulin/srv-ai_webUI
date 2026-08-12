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

# Подключение к модели: администратор может увести всех на другой сервер или
# внешнего провайдера, не перезапуская сервис. Пустое значение = «как в .env»,
# поэтому на чистой установке всё работает как раньше.
LLM_BASE_URL = "llm_base_url"
LLM_MODEL = "llm_model"
LLM_API_KEY = "llm_api_key"
LLM_CONTEXT_SIZE = "llm_context_size"
# Ключ нельзя очистить пустой строкой — она означает «брать из .env». Явную
# очистку храним отдельным признаком.
LLM_API_KEY_CLEARED = "llm_api_key_cleared"
LLM_KEYS = (LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, LLM_CONTEXT_SIZE, LLM_API_KEY_CLEARED)

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


async def load_llm_connection(db: aiosqlite.Connection) -> None:
    """Поднять сохранённое администратором подключение при старте сервиса."""
    from app.llm import set_connection
    stored = {}
    for key in LLM_KEYS:
        value = await get_setting(db, key)
        if value:
            stored[key.removeprefix("llm_")] = value
    set_connection(stored)


def calc_allowed(access: str, user: dict) -> bool:
    """Доступен ли инструмент расчётов этому пользователю."""
    if access == CALC_ALL:
        return True
    if access == CALC_ADMIN:
        return user["role"] == "admin"
    return False


async def calc_allowed_for(db: aiosqlite.Connection, user: dict) -> bool:
    return calc_allowed(await get_setting(db, CALC_ACCESS), user)
