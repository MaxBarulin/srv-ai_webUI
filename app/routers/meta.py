"""Справочные данные для UI (§15): активные специализации и примеры запросов.

Доступны любому авторизованному пользователю (только чтение). Редактирование —
в админском роутере.
"""
from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.db import get_db

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/specializations")
async def list_specializations(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    cursor = await db.execute(
        "SELECT id, name FROM specializations WHERE is_active = 1 "
        "ORDER BY sort_order, id")
    return [dict(row) for row in await cursor.fetchall()]


@router.get("/examples")
async def list_examples(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    cursor = await db.execute(
        "SELECT id, text FROM chat_examples ORDER BY sort_order, id")
    return [dict(row) for row in await cursor.fetchall()]
