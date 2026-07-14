"""Подтверждение деструктивных действий инструментов LLM (§7).

Отложенное действие зарегистрировано агентным циклом (app.tools.register_pending);
кнопка «Подтвердить» в чате вызывает этот эндпойнт. Действие исполняется с правами
подтверждающего пользователя (совпадает с инициатором — токен привязан к user_id).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import client_ip, get_current_user
from app.tools import ToolError, execute_tool, pop_pending

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ConfirmRequest(BaseModel):
    token: str


@router.post("/confirm")
async def confirm_action(
    payload: ConfirmRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    pending = pop_pending(payload.token, user["id"])
    if pending is None:
        raise HTTPException(status_code=404, detail="Действие не найдено или срок истёк")
    try:
        _, label = await execute_tool(user, pending["name"], pending["args"], client_ip(request))
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"label": label}
