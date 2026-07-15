"""Chat endpoints: per-user chat CRUD, message history, SSE streaming to LLM."""
from __future__ import annotations

import asyncio
import contextlib
import json
import time

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.audit import utcnow_iso
from app.auth import client_ip, get_current_user
from app.config import settings
from app.db import get_connection, get_db
from app.llm import LLMError, build_system_prompt, stream_chat
from app.metrics import metrics
from app.pii import mask_text
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
    custom_prompt: str = ""  # свой системный промпт чата (§15, замещает режим)


class ChatUpdateRequest(BaseModel):
    """Частичное обновление чата: переданные поля меняются, остальные — нет."""
    title: str | None = None
    specialization_id: int | None = None
    custom_prompt: str | None = None


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
        "SELECT id, title, specialization_id, custom_prompt, created_at, updated_at "
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
        "SELECT id, title, specialization_id, custom_prompt, created_at, updated_at FROM chats "
        "WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
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
    custom_prompt = payload.custom_prompt.strip()
    cursor = await db.execute(
        "INSERT INTO chats (user_id, title, specialization_id, custom_prompt, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user["id"], title, spec_id, custom_prompt, now, now),
    )
    await db.commit()
    return {"id": cursor.lastrowid, "title": title, "specialization_id": spec_id,
            "custom_prompt": custom_prompt, "created_at": now, "updated_at": now}


@router.put("/{chat_id}")
async def update_chat(
    chat_id: int,
    payload: ChatUpdateRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await _get_own_chat(db, chat_id, user["id"])
    provided = payload.model_fields_set

    title = row["title"]
    if "title" in provided:
        title = (payload.title or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Название не может быть пустым")

    spec_id = row["specialization_id"]
    if "specialization_id" in provided:
        spec_id = payload.specialization_id
        if spec_id is not None:
            cursor = await db.execute(
                "SELECT id FROM specializations WHERE id = ? AND is_active = 1", (spec_id,))
            if await cursor.fetchone() is None:
                raise HTTPException(status_code=400, detail="Специализация не найдена")

    custom_prompt = row["custom_prompt"]
    if "custom_prompt" in provided:
        custom_prompt = (payload.custom_prompt or "").strip()

    await db.execute(
        "UPDATE chats SET title = ?, specialization_id = ?, custom_prompt = ?, updated_at = ? "
        "WHERE id = ?",
        (title, spec_id, custom_prompt, utcnow_iso(), chat_id),
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
    # Сначала зависимые записи (FK): оценки → сообщения → чат
    await db.execute("DELETE FROM feedback WHERE chat_id = ?", (chat_id,))
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
        parsed = json.loads(raw) if raw else None
        # У ассистента в колонке — список плашек инструментов,
        # у пользователя — {"attachments": [...]} с текстами документов
        if isinstance(parsed, dict):
            msg["tool_activity"] = []
            msg["attachments"] = parsed.get("attachments", [])
        else:
            msg["tool_activity"] = parsed or []
            msg["attachments"] = []
        result.append(msg)
    return result


@router.delete("/{chat_id}/messages/{message_id}")
async def delete_message(
    chat_id: int,
    message_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """Удалить одно сообщение из чата (и его оценки) — как в web UI llama.cpp."""
    await _get_own_chat(db, chat_id, user["id"])
    cursor = await db.execute(
        "SELECT id FROM messages WHERE id = ? AND chat_id = ?", (message_id, chat_id))
    if await cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    await db.execute("DELETE FROM feedback WHERE message_id = ?", (message_id,))
    await db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
    await db.commit()
    return {"ok": True}


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


async def _build_history(db: aiosqlite.Connection, chat_id: int) -> list[dict]:
    """История для LLM: без reasoning (§4 ТЗ); текст документов из вложений
    восстанавливается в сообщение (контекст сохраняется); пустые — пропускаются."""
    cursor = await db.execute(
        "SELECT role, content, tool_calls_json FROM messages WHERE chat_id = ? ORDER BY id",
        (chat_id,),
    )
    history = []
    for row in await cursor.fetchall():
        text = row["content"]
        if row["role"] == "user" and row["tool_calls_json"]:
            try:
                meta = json.loads(row["tool_calls_json"])
            except json.JSONDecodeError:
                meta = {}
            if isinstance(meta, dict):
                for att in meta.get("attachments", []):
                    if att.get("image"):
                        text += f"\n[приложено изображение: {att.get('filename', '')}]"
                    elif att.get("text"):
                        text += f"\n\n[Документ: {att.get('filename', 'файл')}]\n{att['text']}"
        if text.strip():
            history.append({"role": row["role"], "content": text})
    return history


async def _chat_spec_prompt(db: aiosqlite.Connection, chat: aiosqlite.Row) -> str:
    """Свой промпт чата (§15) имеет приоритет над специализацией."""
    spec_prompt = (chat["custom_prompt"] or "").strip()
    if not spec_prompt and chat["specialization_id"] is not None:
        cursor = await db.execute(
            "SELECT system_prompt FROM specializations WHERE id = ?", (chat["specialization_id"],))
        spec_row = await cursor.fetchone()
        if spec_row is not None:
            spec_prompt = spec_row["system_prompt"]
    return spec_prompt


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

    # Фильтр ПДн (§13): маскирование ДО отправки в LLM и ДО записи в историю.
    # Изображения не обрабатываются (известное исключение vision-пути).
    pii_total = 0
    pii_by_type: dict[str, int] = {}

    def _mask(text: str) -> str:
        nonlocal pii_total
        result = mask_text(text)
        if result.total:
            pii_total += result.total
            for k, v in result.counts.items():
                pii_by_type[k] = pii_by_type.get(k, 0) + v
        return result.text

    content = _mask(content)

    # Извлечённый текст документов уходит модели вместе с сообщением (§16), но в БД
    # хранится отдельно от текста пользователя (attachments в tool_calls_json) —
    # чтобы UI показывал документ сворачиваемым блоком, а не «стеной» в сообщении.
    # Оценка длины ~3 символа/токен; при переполнении — усечение с пометкой.
    doc_texts: list[str] = []
    doc_warnings: list[str] = []
    attachments_meta: list[dict] = []
    remaining = MAX_ATTACHMENT_CHARS
    for att in payload.attachments:
        if att.images:
            # Изображения в БД не хранятся (§16) — только пометка об имени файла
            attachments_meta.append({"filename": att.filename, "image": True})
            continue
        text = _mask(att.text.strip())
        if not text:
            continue
        if len(text) > remaining:
            text = text[:remaining]
            doc_warnings.append(f"документ «{att.filename}» обрезан по лимиту контекста")
        remaining = max(0, remaining - len(text))
        doc_texts.append(f"[Документ: {att.filename}]\n{text}")
        attachments_meta.append({"filename": att.filename, "text": text})

    if pii_total:
        metrics.record_pii(pii_by_type)

    image_urls = [url for att in payload.attachments for url in att.images]
    image_names = [att.filename for att in payload.attachments if att.images]

    # Полный текст для модели: сообщение пользователя + документы
    text_for_model = content
    if doc_texts:
        text_for_model = (content + "\n\n" + "\n\n".join(doc_texts)).strip()

    history = await _build_history(db, chat_id)
    spec_prompt = await _chat_spec_prompt(db, chat)

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
        "INSERT INTO messages (chat_id, role, content, tool_calls_json, created_at) "
        "VALUES (?, 'user', ?, ?, ?)",
        (chat_id, content,
         json.dumps({"attachments": attachments_meta}, ensure_ascii=False)
         if attachments_meta else None,
         utcnow_iso()),
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
        had_error = False
        gen_start = None
        msgs = list(llm_messages)
        # Счётчики сервера (llama.cpp usage/timings) — суммируются по итерациям цикла
        total_completion = 0
        total_gen_seconds = 0.0
        last_prompt_tokens = 0
        server_stats_seen = False
        ticket = llm_queue.enqueue()
        try:
            # Ждём своей очереди к модели, отдавая честную позицию (§15)
            try:
                async for position in ticket.wait_turn():
                    yield _sse("queued", {"position": position})
            except QueueTimeout as exc:
                yield _sse("error", {"detail": str(exc)})
                metrics.record_request(success=False)
                return
            yield _sse("queue_ready", {})
            gen_start = time.monotonic()

            if pii_total:
                yield _sse("pii_masked", {"count": pii_total})
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
                    elif kind == "stats":
                        step_stats = json.loads(text)
                        server_stats_seen = True
                        completion = step_stats.get("completion_tokens", 0)
                        total_completion += completion
                        last_prompt_tokens = step_stats.get("prompt_tokens", last_prompt_tokens)
                        tps = step_stats.get("tokens_per_second", 0)
                        if completion and tps:
                            total_gen_seconds += completion / tps

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
                had_error = True
        except LLMError as exc:
            yield _sse("error", {"detail": str(exc)})
            finished = True
            had_error = True
        finally:
            ticket.release()
            # Метрики (§13): предпочитаем счётчики самого сервера (llama.cpp
            # usage/timings — тот же способ, что в её web UI). Fallback — грубая
            # оценка по символам (включая reasoning) за время генерации.
            if gen_start is not None:
                if server_stats_seen and total_completion and total_gen_seconds:
                    metrics.record_request(success=not had_error,
                                           tokens=total_completion,
                                           seconds=total_gen_seconds)
                else:
                    elapsed = time.monotonic() - gen_start
                    tokens = (len("".join(content_parts))
                              + len("".join(reasoning_parts))) // 3
                    metrics.record_request(success=not had_error,
                                           tokens=tokens, seconds=elapsed)
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
                if server_stats_seen:
                    context_used = last_prompt_tokens + total_completion
                    stats_payload = {
                        "completion_tokens": total_completion,
                        "tokens_per_second": round(total_completion / total_gen_seconds, 1)
                        if total_gen_seconds else 0,
                        "context_used": context_used,
                        "context_size": settings.llm_context_size,
                        "context_percent": round(context_used / settings.llm_context_size * 100)
                        if settings.llm_context_size else None,
                    }
                    yield _sse("stats", stats_payload)
                yield _sse("done", {"message_id": saved_id, "title": auto_title})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


CONTINUE_INSTRUCTION = (
    "Продолжи свой предыдущий ответ ровно с того места, где он оборвался. "
    "Не повторяй уже написанное и не добавляй вступлений — просто продолжай текст."
)


async def _append_to_message(message_id: int, chat_id: int, extra: str) -> None:
    """Дописать продолжение к существующему ответу (своё соединение — переживает разрыв)."""
    async with get_connection() as db:
        await db.execute("UPDATE messages SET content = content || ? WHERE id = ?",
                         (extra, message_id))
        await db.execute("UPDATE chats SET updated_at = ? WHERE id = ?",
                         (utcnow_iso(), chat_id))
        await db.commit()


@router.post("/{chat_id}/continue")
async def continue_generation(
    chat_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
) -> StreamingResponse:
    """Возобновить генерацию последнего ответа (кнопка «Продолжить», как в llama.cpp).

    Продолжение дописывается к тому же сообщению в БД. Инструменты и RAG в
    продолжении не используются — только дописывание текста.
    """
    chat = await _get_own_chat(db, chat_id, user["id"])
    cursor = await db.execute(
        "SELECT id, role, content FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
        (chat_id,))
    last = await cursor.fetchone()
    if last is None or last["role"] != "assistant" or not last["content"].strip():
        raise HTTPException(status_code=400,
                            detail="Продолжать нечего: последний ответ отсутствует или пуст")
    message_id = last["id"]

    spec_prompt = await _chat_spec_prompt(db, chat)
    history = await _build_history(db, chat_id)
    llm_messages = [
        {"role": "system", "content": build_system_prompt(user["display_name"], spec_prompt)},
        *history,  # история уже заканчивается продолжаемым ответом ассистента
        {"role": "user", "content": CONTINUE_INSTRUCTION},
    ]

    async def event_stream():
        content_parts: list[str] = []
        finished = False
        had_error = False
        gen_start = None
        total_completion = 0
        total_gen_seconds = 0.0
        last_prompt_tokens = 0
        server_stats_seen = False
        ticket = llm_queue.enqueue()
        try:
            try:
                async for position in ticket.wait_turn():
                    yield _sse("queued", {"position": position})
            except QueueTimeout as exc:
                yield _sse("error", {"detail": str(exc)})
                metrics.record_request(success=False)
                return
            yield _sse("queue_ready", {})
            gen_start = time.monotonic()

            async for kind, text in stream_chat(llm_messages):
                if kind == "reasoning":
                    yield _sse("reasoning", {"text": text})
                elif kind == "content":
                    content_parts.append(text)
                    yield _sse("content", {"text": text})
                elif kind == "stats":
                    step_stats = json.loads(text)
                    server_stats_seen = True
                    total_completion = step_stats.get("completion_tokens", 0)
                    last_prompt_tokens = step_stats.get("prompt_tokens", 0)
                    tps = step_stats.get("tokens_per_second", 0)
                    if total_completion and tps:
                        total_gen_seconds = total_completion / tps
            finished = True
        except LLMError as exc:
            yield _sse("error", {"detail": str(exc)})
            finished = True
            had_error = True
        finally:
            ticket.release()
            if gen_start is not None:
                if server_stats_seen and total_completion and total_gen_seconds:
                    metrics.record_request(success=not had_error,
                                           tokens=total_completion,
                                           seconds=total_gen_seconds)
                else:
                    metrics.record_request(
                        success=not had_error,
                        tokens=len("".join(content_parts)) // 3,
                        seconds=time.monotonic() - gen_start)
            if content_parts:
                extra = "".join(content_parts)
                save = asyncio.create_task(_append_to_message(message_id, chat_id, extra))
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(save)
            if finished:
                if server_stats_seen:
                    context_used = last_prompt_tokens + total_completion
                    yield _sse("stats", {
                        "completion_tokens": total_completion,
                        "tokens_per_second": round(total_completion / total_gen_seconds, 1)
                        if total_gen_seconds else 0,
                        "context_used": context_used,
                        "context_size": settings.llm_context_size,
                        "context_percent": round(context_used / settings.llm_context_size * 100)
                        if settings.llm_context_size else None,
                    })
                yield _sse("done", {"message_id": message_id, "title": None})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
