"""Группы пользователей (отделы, бюро): общие проверки для роутеров и инструментов."""
from __future__ import annotations

import re

import aiosqlite
from fastapi import HTTPException

MAX_GROUP_NAME_LENGTH = 80
# Идентификатор строки в SQLite — знаковое 64-битное целое. Значение вне
# диапазона драйвер не может связать с параметром и падает OverflowError,
# поэтому отсекаем его до запроса.
MAX_ROWID = 2 ** 63 - 1

# Управляющие символы (в том числе NUL) в названии группы не несут смысла,
# зато ломают вывод в журналах и консолях — вычищаем их до пробела.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def normalize_group_name(name: str) -> str:
    cleaned = " ".join(_CONTROL_CHARS.sub(" ", name).split())  # «Отдел  17» → «Отдел 17»
    if not cleaned:
        raise HTTPException(status_code=400, detail="Название группы не может быть пустым")
    if len(cleaned) > MAX_GROUP_NAME_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Название группы не длиннее {MAX_GROUP_NAME_LENGTH} символов")
    return cleaned


async def ensure_name_free(
    db: aiosqlite.Connection, name: str, exclude_id: int | None = None,
) -> None:
    """Проверить, что названия «Отдел 1» и «отдел 1» не разъедутся.

    Сравниваем в Python: COLLATE NOCASE и lower() в SQLite приводят регистр
    только у латиницы, кириллические названия они считают разными.
    """
    cursor = await db.execute("SELECT id, name FROM groups")
    for row in await cursor.fetchall():
        if row["id"] != exclude_id and row["name"].casefold() == name.casefold():
            raise HTTPException(status_code=409, detail="Группа с таким названием уже есть")


async def ensure_group_exists(db: aiosqlite.Connection, group_id: int | None) -> int | None:
    """Проверить, что группа существует. None пропускаем — это «без группы»."""
    if group_id is None:
        return None
    if not 1 <= group_id <= MAX_ROWID:
        raise HTTPException(status_code=400, detail="Группа не найдена")
    cursor = await db.execute("SELECT id FROM groups WHERE id = ?", (group_id,))
    if await cursor.fetchone() is None:
        raise HTTPException(status_code=400, detail="Группа не найдена")
    return group_id
