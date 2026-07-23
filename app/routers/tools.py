"""Подтверждение деструктивных действий инструментов LLM (§7).

Отложенное действие зарегистрировано агентным циклом (app.tools.register_pending);
кнопка «Подтвердить» в чате вызывает этот эндпойнт. Действие исполняется с правами
подтверждающего пользователя (совпадает с инициатором — токен привязан к user_id).
"""
from __future__ import annotations

import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import client_ip, get_current_user
from app.db import get_db
from app.tools import ToolError, execute_tool, mark_resolved, pop_pending

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ConfirmRequest(BaseModel):
    token: str


async def _record_outcome(db: aiosqlite.Connection, user_id: int, token: str,
                          status: str, label: str) -> None:
    """Зафиксировать исход в сохранённом сообщении: запись активности с этим
    токеном получает статус done/error и теряет токен — после перечитывания
    истории вместо кнопки «Подтвердить» показывается плашка исхода."""
    mark_resolved(token, status, label)  # на случай гонки (сообщение ещё не в БД)
    # Экранируем спецсимволы LIKE (_ и % возможны в token_urlsafe)
    esc = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    cursor = await db.execute(
        "SELECT m.id, m.tool_calls_json FROM messages m JOIN chats c ON c.id = m.chat_id "
        "WHERE c.user_id = ? AND m.tool_calls_json LIKE ? ESCAPE '\\' ORDER BY m.id DESC LIMIT 5",
        (user_id, f"%{esc}%"))
    for row in await cursor.fetchall():
        try:
            activity = json.loads(row["tool_calls_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        changed = False
        for entry in activity:
            if isinstance(entry, dict) and entry.get("token") == token:
                entry.pop("token", None)
                entry["status"] = status
                entry["label"] = label
                changed = True
        if changed:
            await db.execute("UPDATE messages SET tool_calls_json = ? WHERE id = ?",
                             (json.dumps(activity, ensure_ascii=False), row["id"]))
            await db.commit()
            return


@router.post("/confirm")
async def confirm_action(
    payload: ConfirmRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    pending = pop_pending(payload.token, user["id"])
    if pending is None:
        raise HTTPException(status_code=404, detail="Действие не найдено или срок истёк")
    try:
        _, label = await execute_tool(user, pending["name"], pending["args"], client_ip(request))
    except ToolError as exc:
        await _record_outcome(db, user["id"], payload.token, "error", str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    await _record_outcome(db, user["id"], payload.token, "done", label)
    return {"status": "done", "label": label}
