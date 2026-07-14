"""Chat endpoints: per-user chat CRUD, message history, SSE streaming to LLM."""
from __future__ import annotations

import asyncio
import contextlib
import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.audit import utcnow_iso
from app.auth import get_current_user
from app.db import get_connection, get_db
from app.llm import LLMError, build_system_prompt, stream_chat

router = APIRouter(prefix="/api/chats", tags=["chat"])

DEFAULT_TITLE = "Новый чат"
AUTO_TITLE_MAX_LEN = 60


class CreateChatRequest(BaseModel):
    title: str = ""


class RenameChatRequest(BaseModel):
    title: str


class SendMessageRequest(BaseModel):
    content: str


async def _get_own_chat(db: aiosqlite.Connection, chat_id: int, user_id: int) -> aiosqlite.Row:
    cursor = await db.execute(
        "SELECT id, title, created_at, updated_at FROM chats WHERE id = ? AND user_id = ?",
        (chat_id, user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return row


@router.get("")
async def list_chats(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    cursor = await db.execute(
        "SELECT id, title, created_at, updated_at FROM chats "
        "WHERE user_id = ? ORDER BY updated_at DESC",
        (user["id"],),
    )
    return [dict(row) for row in await cursor.fetchall()]


@router.post("", status_code=201)
async def create_chat(
    payload: CreateChatRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    now = utcnow_iso()
    title = payload.title.strip() or DEFAULT_TITLE
    cursor = await db.execute(
        "INSERT INTO chats (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (user["id"], title, now, now),
    )
    await db.commit()
    return {"id": cursor.lastrowid, "title": title, "created_at": now, "updated_at": now}


@router.put("/{chat_id}")
async def rename_chat(
    chat_id: int,
    payload: RenameChatRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    await _get_own_chat(db, chat_id, user["id"])
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")
    await db.execute(
        "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
        (title, utcnow_iso(), chat_id),
    )
    await db.commit()
    return dict(await _get_own_chat(db, chat_id, user["id"]))


@router.delete("/{chat_id}")
async def delete_chat(
    chat_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    await _get_own_chat(db, chat_id, user["id"])
    await db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    await db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    await db.commit()
    return {"ok": True}


@router.get("/{chat_id}/messages")
async def list_messages(
    chat_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> list[dict]:
    await _get_own_chat(db, chat_id, user["id"])
    cursor = await db.execute(
        "SELECT id, role, content, reasoning, created_at FROM messages "
        "WHERE chat_id = ? ORDER BY id",
        (chat_id,),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def _save_assistant_message(
    chat_id: int, content: str, reasoning: str, auto_title: str | None
) -> int:
    """Persist the assistant reply on its own connection (survives client disconnect)."""
    async with get_connection() as db:
        now = utcnow_iso()
        cursor = await db.execute(
            "INSERT INTO messages (chat_id, role, content, reasoning, created_at) "
            "VALUES (?, 'assistant', ?, ?, ?)",
            (chat_id, content, reasoning or None, now),
        )
        if auto_title:
            await db.execute(
                "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
                (auto_title, now, chat_id),
            )
        else:
            await db.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
        await db.commit()
        return cursor.lastrowid


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: int,
    payload: SendMessageRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> StreamingResponse:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Пустое сообщение")

    chat = await _get_own_chat(db, chat_id, user["id"])

    # История для LLM: system + прежние сообщения без reasoning (§4 ТЗ).
    # Пустые ответы (генерация остановлена на этапе размышлений) не включаем.
    cursor = await db.execute(
        "SELECT role, content FROM messages WHERE chat_id = ? AND content != '' ORDER BY id",
        (chat_id,),
    )
    history = [{"role": row["role"], "content": row["content"]} for row in await cursor.fetchall()]

    llm_messages = [
        {"role": "system", "content": build_system_prompt(user["display_name"])},
        *history,
        {"role": "user", "content": content},
    ]

    await db.execute(
        "INSERT INTO messages (chat_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
        (chat_id, content, utcnow_iso()),
    )
    await db.commit()

    auto_title = None
    if chat["title"] == DEFAULT_TITLE:
        auto_title = " ".join(content.split())[:AUTO_TITLE_MAX_LEN]

    async def event_stream():
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        finished = False
        try:
            async for kind, text in stream_chat(llm_messages):
                (reasoning_parts if kind == "reasoning" else content_parts).append(text)
                yield _sse(kind, {"text": text})
            finished = True
        except LLMError as exc:
            yield _sse("error", {"detail": str(exc)})
            finished = True
        finally:
            saved_id = None
            if content_parts or reasoning_parts:
                # При разрыве стрима (кнопка «Остановить») сохраняем частичный ответ;
                # create_task переживает отмену этого генератора.
                save = asyncio.create_task(_save_assistant_message(
                    chat_id, "".join(content_parts), "".join(reasoning_parts), auto_title))
                with contextlib.suppress(asyncio.CancelledError):
                    saved_id = await asyncio.shield(save)
            if finished:
                yield _sse("done", {"message_id": saved_id, "title": auto_title})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
