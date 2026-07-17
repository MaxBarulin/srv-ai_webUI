"""Автоочистка истории чатов (политика хранения, CHAT_RETENTION_DAYS).

Сообщения старше N дней удаляются вместе с их feedback; пустые чаты,
оставшиеся без сообщений, тоже убираются. 0 — очистка выключена.
Запускается при старте приложения и далее раз в сутки.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.db import get_connection

log = logging.getLogger("srv-ai.retention")

_CLEANUP_INTERVAL_SECONDS = 24 * 3600


async def cleanup_old_messages() -> int:
    """Удалить сообщения старше chat_retention_days. Возвращает число удалённых."""
    days = settings.chat_retention_days
    if days <= 0:
        return 0
    cutoff_expr = f"datetime('now', '-{int(days)} days')"
    async with get_connection() as db:
        await db.execute(
            "DELETE FROM feedback WHERE message_id IN "
            f"(SELECT id FROM messages WHERE created_at < {cutoff_expr})")
        cursor = await db.execute(
            f"DELETE FROM messages WHERE created_at < {cutoff_expr}")
        deleted = cursor.rowcount
        await db.execute(
            "DELETE FROM chats WHERE id NOT IN (SELECT DISTINCT chat_id FROM messages)")
        await db.commit()
    if deleted:
        log.info("retention: удалено сообщений старше %d дн.: %d", days, deleted)
    return deleted


async def retention_loop() -> None:
    while True:
        try:
            await cleanup_old_messages()
        except Exception:  # noqa: BLE001 — фоновая задача не должна умирать
            log.exception("retention: ошибка очистки")
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
