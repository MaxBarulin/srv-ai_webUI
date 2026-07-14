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
from app.auth import client_ip, get_current_user
from app.config import settings
from app.db import get_connection, get_db
from app.llm import LLMError, build_system_prompt, stream_chat
from app.queue import QueueTimeout, llm_queue
from app.rag import RAGError, context_message, fetch_context
from app.tools import (
    MAX_TOOL_ITERATIONS,
    TOOLS_SPEC,
    ToolError,
    execute_tool,
    is_destructive,
    parse_fallback_tool_calls,
    preview_destructive,
    register_pending,
)

router = APIRouter(prefix="/api/chats", tags=["chat"])

DEFAULT_TITLE = "Новый чат"
AUTO_TITLE_MAX_LEN = 60
# Бюджет символов на извлечённый текст всех вложений (~3 симв/токен, §16)
MAX_ATTACHMENT_CHARS = 60000


class CreateChatRequest(BaseModel):
    title: str = ""
    specialization_id: int | None = None


class RenameChatRequest(BaseModel):
    title: str


class Attachment(BaseModel):
    filename: str = "file"
    text: str = ""
    images: list[str] = []  # data-URL, только на время генерации (§16)


class SendMessageRequest(BaseModel):
    content: str
    use_tools: bool = True  # переключатель «Заметки/Календарь» в шапке чата (§4)
    use_rag: bool = False   # переключатель «База знаний» (§8)
    attachments: list[Attachment] = []


async def _get_own_chat(db: aiosqlite.Connection, chat_id: int, user_id: int) -> aiosqlite.Row:
    cursor = await db.execute(
        "SELECT id, title, specialization_id, created_at, updated_at "
        "FROM chats WHERE id = ? AND user_id = ?",
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
        "SELECT id, title, specialization_id, created_at, updated_at FROM chats "
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
    spec_id = payload.specialization_id
    if spec_id is not None:
        cursor = await db.execute(
            "SELECT id FROM specializations WHERE id = ? AND is_active = 1", (spec_id,))
        if await cursor.fetchone() is None:
            spec_id = None  # неизвестная/выключенная специализация — просто общий режим
    cursor = await db.execute(
        "INSERT INTO chats (user_id, title, specialization_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user["id"], title, spec_id, now, now),
    )
    await db.commit()
    return {"id": cursor.lastrowid, "title": title, "specialization_id": spec_id,
            "created_at": now, "updated_at": now}


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
        "SELECT m.id, m.role, m.content, m.reasoning, m.tool_calls_json, m.created_at, "
        "       f.rating AS feedback_rating "
        "FROM messages m "
        "LEFT JOIN feedback f ON f.message_id = m.id AND f.user_id = ? "
        "WHERE m.chat_id = ? ORDER BY m.id",
        (user["id"], chat_id),
    )
    result = []
    for row in await cursor.fetchall():
        msg = dict(row)
        raw = msg.pop("tool_calls_json", None)
        msg["tool_activity"] = json.loads(raw) if raw else []
        result.append(msg)
    return result


class FeedbackRequest(BaseModel):
    rating: int  # 1 (👍) или -1 (👎)
    comment: str = ""


@router.post("/{chat_id}/messages/{message_id}/feedback")
async def submit_feedback(
    chat_id: int,
    message_id: int,
    payload: FeedbackRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    if payload.rating not in (1, -1):
        raise HTTPException(status_code=400, detail="Оценка должна быть 1 или -1")
    await _get_own_chat(db, chat_id, user["id"])
    cursor = await db.execute(
        "SELECT m.id FROM messages m WHERE m.id = ? AND m.chat_id = ? AND m.role = 'assistant'",
        (message_id, chat_id))
    if await cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")

    # Специализация чата (для будущего датасета) — по имени
    cursor = await db.execute(
        "SELECT s.name FROM chats c LEFT JOIN specializations s ON s.id = c.specialization_id "
        "WHERE c.id = ?", (chat_id,))
    spec_row = await cursor.fetchone()
    specialization = spec_row["name"] if spec_row and spec_row["name"] else None

    await db.execute(
        "INSERT INTO feedback (message_id, chat_id, user_id, rating, comment, specialization, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(message_id, user_id) DO UPDATE SET "
        "rating = excluded.rating, comment = excluded.comment, created_at = excluded.created_at",
        (message_id, chat_id, user["id"], payload.rating,
         payload.comment.strip() or None, specialization, utcnow_iso()),
    )
    await db.commit()
    return {"ok": True, "rating": payload.rating}


async def _save_assistant_message(
    chat_id: int, content: str, reasoning: str, auto_title: str | None,
    tool_activity: list[dict] | None = None,
) -> int:
    """Persist the assistant reply on its own connection (survives client disconnect)."""
    async with get_connection() as db:
        now = utcnow_iso()
        cursor = await db.execute(
            "INSERT INTO messages (chat_id, role, content, reasoning, tool_calls_json, created_at) "
            "VALUES (?, 'assistant', ?, ?, ?, ?)",
            (chat_id, content, reasoning or None,
             json.dumps(tool_activity, ensure_ascii=False) if tool_activity else None, now),
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
    has_images = any(a.images for a in payload.attachments)
    has_doc_text = any(a.text.strip() for a in payload.attachments)
    if not content and not has_images and not has_doc_text:
        raise HTTPException(status_code=400, detail="Пустое сообщение")

    chat = await _get_own_chat(db, chat_id, user["id"])

    # Извлечённый текст документов присоединяется к сообщению (§16). Оценка длины
    # ~3 символа/токен; при переполнении — усечение с пометкой «документ обрезан».
    doc_texts: list[str] = []
    doc_warnings: list[str] = []
    remaining = MAX_ATTACHMENT_CHARS
    for att in payload.attachments:
        text = att.text.strip()
        if not text:
            continue
        if len(text) > remaining:
            text = text[:remaining]
            doc_warnings.append(f"документ «{att.filename}» обрезан по лимиту контекста")
        remaining -= len(text)
        doc_texts.append(f"[Документ: {att.filename}]\n{text}")
        if remaining <= 0:
            break

    image_urls = [url for att in payload.attachments for url in att.images]
    image_names = [att.filename for att in payload.attachments if att.images]

    # Текст сообщения для модели и для истории (изображения в БД не хранятся, §16)
    text_for_model = content
    if doc_texts:
        text_for_model = (content + "\n\n" + "\n\n".join(doc_texts)).strip()
    stored_content = text_for_model
    if image_names:
        marker = " ".join(f"[приложено изображение: {n}]" for n in image_names)
        stored_content = (text_for_model + "\n" + marker).strip()

    # История для LLM: system + прежние сообщения без reasoning (§4 ТЗ).
    # Пустые ответы (генерация остановлена на этапе размышлений) не включаем.
    cursor = await db.execute(
        "SELECT role, content FROM messages WHERE chat_id = ? AND content != '' ORDER BY id",
        (chat_id,),
    )
    history = [{"role": row["role"], "content": row["content"]} for row in await cursor.fetchall()]

    spec_prompt = ""
    if chat["specialization_id"] is not None:
        cursor = await db.execute(
            "SELECT system_prompt FROM specializations WHERE id = ?", (chat["specialization_id"],))
        spec_row = await cursor.fetchone()
        if spec_row is not None:
            spec_prompt = spec_row["system_prompt"]

    # Изображения передаются модели через OpenAI-формат image_url (§16)
    if image_urls:
        user_content: object = [{"type": "text", "text": text_for_model or "Проанализируй вложение."}]
        user_content += [{"type": "image_url", "image_url": {"url": url}} for url in image_urls]
    else:
        user_content = text_for_model

    llm_messages = [
        {"role": "system", "content": build_system_prompt(user["display_name"], spec_prompt)},
        *history,
        {"role": "user", "content": user_content},
    ]

    await db.execute(
        "INSERT INTO messages (chat_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
        (chat_id, stored_content, utcnow_iso()),
    )
    await db.commit()

    auto_title = None
    if chat["title"] == DEFAULT_TITLE:
        base = content or (image_names[0] if image_names else
                           (payload.attachments[0].filename if payload.attachments else ""))
        auto_title = " ".join(base.split())[:AUTO_TITLE_MAX_LEN] or DEFAULT_TITLE

    user_ip = client_ip(request)
    tools = TOOLS_SPEC if payload.use_tools else None

    def _may_be_tool_json(text: str) -> bool:
        # Пока накопленный контент похож на начало JSON-вызова (fallback §7) — придерживаем
        return (not text or text.startswith("{")
                or text.startswith("```json") or "```json".startswith(text))

    async def run_tool_call(tc: dict):
        """Исполнить один вызов инструмента. Возвращает (результат для модели, SSE-событие)."""
        name = tc.get("function", {}).get("name", "")
        try:
            args = json.loads(tc.get("function", {}).get("arguments") or "{}")
            if not isinstance(args, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            return ({"error": "Некорректный JSON в аргументах инструмента"},
                    ("tool", {"label": f"{name}: некорректные аргументы", "error": True},
                     {"label": f"{name}: некорректные аргументы", "status": "error"}))
        try:
            if is_destructive(name, args) and settings.tools_confirm_destructive:
                label = await preview_destructive(user, name, args)
                token = register_pending(user, name, args, label)
                result = {
                    "status": "requires_confirmation",
                    "message": "Действие требует подтверждения пользователя — кнопка показана "
                               "в интерфейсе. Не вызывай инструмент повторно, сообщи пользователю, "
                               "что ожидается подтверждение.",
                }
                return (result, ("tool_confirm", {"token": token, "label": label},
                                 {"label": label, "status": "confirm"}))
            result, label = await execute_tool(user, name, args, user_ip)
            return (result, ("tool", {"label": label}, {"label": label, "status": "ok"}))
        except ToolError as exc:
            return ({"error": str(exc)},
                    ("tool", {"label": f"{name}: {exc}", "error": True},
                     {"label": f"{name}: {exc}", "status": "error"}))

    async def event_stream():
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_activity: list[dict] = []
        finished = False
        msgs = list(llm_messages)
        ticket = llm_queue.enqueue()
        try:
            # Ждём своей очереди к модели, отдавая честную позицию (§15)
            try:
                async for position in ticket.wait_turn():
                    yield _sse("queued", {"position": position})
            except QueueTimeout as exc:
                yield _sse("error", {"detail": str(exc)})
                return
            yield _sse("queue_ready", {})

            for warning in doc_warnings:
                yield _sse("doc_warning", {"detail": warning})

            if payload.use_rag and settings.rag_enabled:
                # Контекст из базы знаний — перед сообщением пользователя (§8).
                # Недоступность LightRAG не прерывает обычный режим.
                try:
                    context = await fetch_context(content)
                    if context:
                        msgs.insert(-1, context_message(context))
                        tool_activity.append(
                            {"label": "База знаний", "status": "sources", "text": context})
                        yield _sse("sources", {"text": context})
                    else:
                        yield _sse("rag_error",
                                   {"detail": "База знаний не вернула контекст по этому запросу"})
                except RAGError as exc:
                    yield _sse("rag_error", {"detail": str(exc)})

            for _ in range(MAX_TOOL_ITERATIONS):
                step_parts: list[str] = []
                held: list[str] = []      # придержанный контент (возможный fallback-JSON)
                holding = tools is not None
                tool_calls = None
                async for kind, text in stream_chat(msgs, tools=tools):
                    if kind == "reasoning":
                        reasoning_parts.append(text)
                        yield _sse("reasoning", {"text": text})
                    elif kind == "content":
                        step_parts.append(text)
                        if holding:
                            held.append(text)
                            if not _may_be_tool_json("".join(held).lstrip()):
                                holding = False
                                yield _sse("content", {"text": "".join(held)})
                                held = []
                        else:
                            yield _sse("content", {"text": text})
                    elif kind == "tool_calls":
                        tool_calls = json.loads(text)

                step_text = "".join(step_parts)
                fallback_used = False
                if tool_calls is None and holding and step_text.strip():
                    tool_calls = parse_fallback_tool_calls(step_text)
                    fallback_used = tool_calls is not None

                if tool_calls is None:
                    if held:  # буфер так и не оказался вызовом инструмента
                        yield _sse("content", {"text": "".join(held)})
                    content_parts.append(step_text)
                    finished = True
                    break

                # Текст вокруг структурного вызова сохраняем; fallback-JSON — нет
                if not fallback_used and not holding and step_text:
                    content_parts.append(step_text)

                msgs.append({"role": "assistant", "content": step_text or None,
                             "tool_calls": tool_calls})
                for tc in tool_calls:
                    result, (event, data, activity) = await run_tool_call(tc)
                    tool_activity.append(activity)
                    yield _sse(event, data)
                    msgs.append({"role": "tool", "tool_call_id": tc.get("id") or "",
                                 "content": json.dumps(result, ensure_ascii=False)})
            else:
                yield _sse("error", {"detail": "Достигнут лимит вызовов инструментов "
                                               f"({MAX_TOOL_ITERATIONS}) — ответ прерван"})
                finished = True
        except LLMError as exc:
            yield _sse("error", {"detail": str(exc)})
            finished = True
        finally:
            ticket.release()
            saved_id = None
            if content_parts or reasoning_parts or tool_activity:
                # При разрыве стрима (кнопка «Остановить») сохраняем частичный ответ;
                # create_task переживает отмену этого генератора.
                save = asyncio.create_task(_save_assistant_message(
                    chat_id, "".join(content_parts), "".join(reasoning_parts), auto_title,
                    tool_activity))
                with contextlib.suppress(asyncio.CancelledError):
                    saved_id = await asyncio.shield(save)
            if finished:
                yield _sse("done", {"message_id": saved_id, "title": auto_title})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
