"""Mock llama.cpp server (OpenAI-compatible /v1/chat/completions, SSE).

Used in tests via httpx.ASGITransport; can also run standalone for manual UI checks:
    python -m uvicorn tests.mock_llm:app --port 8000
"""
from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

REASONING_CHUNKS = ["Пользователь спрашивает. ", "Надо ответить ", "кратко и по делу."]


def _content_chunks(last_user_message: str) -> list[str]:
    if "MARKDOWN_DEMO" in last_user_message:
        return [
            "## Заголовок\n\n", "Пример **жирного** и `кода`.\n\n",
            "- пункт один\n", "- пункт два\n\n",
            "| Колонка А | Колонка Б |\n", "|---|---|\n", "| 1 | 2 |\n\n",
            "Норма времени $T_{баз} = 0{,}048$ ч/шт., интерполяция:\n\n",
            "$$T(0{,}05) = T(0{,}02) + \\frac{0{,}05-0{,}02}{0{,}01} \\times 0{,}006 = 0{,}060$$\n\n",
            "```python\n", "print('привет')\n", "```\n",
        ]
    return ["Ответ ", "на: ", f"«{last_user_message[:80]}»"]


# Триггеры tool calling: слово в сообщении → (инструмент, аргументы).
# Первый круг — модель «вызывает» инструмент, второй (в messages есть role=tool) —
# итоговый текст с содержимым результата.
TOOL_TRIGGERS: dict[str, tuple[str, dict]] = {
    "TOOL_CREATE_NOTE": ("notes_create", {
        "title": "Тестовая заметка", "text": "Содержимое от модели", "scope": "personal",
        "tags": ["тест"]}),
    "TOOL_SEARCH_NOTES": ("notes_search", {"query": "Тестовая"}),
    "TOOL_GET_NOTE": ("notes_get", {"id": 1}),
    "TOOL_DELETE_NOTE": ("notes_delete", {"id": 1}),
    "TOOL_REWRITE_NOTE": ("notes_update", {"id": 1, "text": "Новый текст"}),
    "TOOL_RENAME_NOTE": ("notes_update", {"id": 1, "title": "Новый заголовок"}),
    "TOOL_SHARE_NOTE": ("notes_update", {"id": 1, "scope": "shared"}),
    "TOOL_TAG_NOTE": ("notes_update", {"id": 1, "tags": ["новый"]}),
    "TOOL_SAME_TEXT_NOTE": ("notes_update", {"id": 1, "text": "секретный текст",
                                             "title": "Новый заголовок"}),
    "TOOL_CREATE_EVENT": ("calendar_create", {
        "title": "Совещание", "starts_at": "2026-07-15T10:00:00+03:00",
        "ends_at": "2026-07-15T11:00:00+03:00", "scope": "personal"}),
    "TOOL_LIST_EVENTS": ("calendar_list", {}),
    "TOOL_DELETE_EVENT": ("calendar_delete", {"id": 1}),
    "TOOL_MOVE_EVENT": ("calendar_update", {
        "id": 1, "starts_at": "2026-07-16T15:00:00+03:00",
        "ends_at": "2026-07-16T16:00:00+03:00"}),
    "TOOL_RENAME_EVENT": ("calendar_update", {"id": 1, "title": "Другое название"}),
    "TOOL_SHARE_EVENT": ("calendar_update", {"id": 1, "scope": "shared"}),
    "TOOL_SAME_EVENT": ("calendar_update", {
        "id": 1, "title": "Совещание", "starts_at": "2026-07-15T10:00:00+03:00",
        "ends_at": "2026-07-15T11:00:00+03:00"}),
    "TOOL_TIME": ("get_current_datetime", {}),
    "TOOL_UNKNOWN": ("no_such_tool", {}),
    "TOOL_LOOP": ("get_current_datetime", {}),  # вызывает инструмент бесконечно (лимит цикла)
}


def _find_trigger(last_user: str) -> tuple[str, dict] | None:
    for word, call in TOOL_TRIGGERS.items():
        if word in last_user:
            return call
    return None


@app.get("/props")
async def props():
    """llama.cpp-совместимый /props: реальный n_ctx запущенного сервера."""
    return {"default_generation_settings": {"n_ctx": 8192}, "total_slots": 1}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    last_user = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), "")

    if "ERROR500" in last_user:
        return JSONResponse({"error": "mock internal error"}, status_code=500)

    slow = "SLOW" in last_user  # для ручной проверки кнопки «Остановить»

    trigger = _find_trigger(last_user) if body.get("tools") else None
    has_tool_result = any(m.get("role") == "tool" for m in messages)
    # Обычный триггер срабатывает один раз; TOOL_LOOP — на каждом круге
    emit_tool_call = trigger is not None and (not has_tool_result or "TOOL_LOOP" in last_user)
    fallback = "TOOL_FALLBACK" in last_user and body.get("tools") and not has_tool_result

    async def sse():
        def chunk(delta: dict) -> str:
            return "data: " + json.dumps(
                {"choices": [{"delta": delta}]}, ensure_ascii=False) + "\n\n"

        # enable_thinking=False (chat_template_kwargs, как Qwen3 в llama.cpp) —
        # модель отвечает без блока размышлений
        thinking = (body.get("chat_template_kwargs") or {}).get("enable_thinking", True)
        if thinking is not False:
            for text in REASONING_CHUNKS:
                yield chunk({"reasoning_content": text})
                await asyncio.sleep(2.0 if slow else 0.01)

        if emit_tool_call:
            name, args = trigger
            args_json = json.dumps(args, ensure_ascii=False)
            # Имя и аргументы дробятся на дельты — как настоящий llama.cpp
            yield chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                         "function": {"name": name, "arguments": ""}}]})
            half = len(args_json) // 2
            for part in (args_json[:half], args_json[half:]):
                yield chunk({"tool_calls": [{"index": 0, "function": {"arguments": part}}]})
                await asyncio.sleep(0.01)
        elif fallback:
            block = ('```json\n{"name": "notes_create", "arguments": '
                     '{"title": "Fallback заметка", "text": "текст"}}\n```')
            for i in range(0, len(block), 15):
                yield chunk({"content": block[i:i + 15]})
                await asyncio.sleep(0.01)
        elif "ECHO_REQUEST" in last_user:
            # Отдать обратно то, что сервер реально получил: параметры шаблона
            # и размышления в истории — иначе проверить нечем
            yield chunk({"content": json.dumps({
                "chat_template_kwargs": body.get("chat_template_kwargs"),
                "reasoning_in_history": [
                    {"role": m.get("role"), "reasoning": m.get("reasoning_content")}
                    for m in messages if m.get("role") == "assistant"],
            }, ensure_ascii=False)})
        elif "XML_IN_THINK" in last_user and body.get("tools") and not has_tool_result:
            # Прецедент: модель выписала вызов текстом ВНУТРИ размышления,
            # в XML-диалекте — сервер его вызовом не признал. Ни content,
            # ни tool_calls до приложения не доходит.
            yield chunk({"reasoning_content":
                         "Drafting the Tool Call: <tool_call> <function=notes_get> "
                         "<parameter=id> 1 </parameter> </function> </tool_call>"})
        elif "TAGGED_JSON_CALL" in last_user and body.get("tools") and not has_tool_result:
            # Тот же вызов, но JSON внутри тегов и в обычном тексте ответа
            block = '<tool_call>\n{"name": "notes_get", "arguments": {"id": 1}}\n</tool_call>'
            for i in range(0, len(block), 15):
                yield chunk({"content": block[i:i + 15]})
                await asyncio.sleep(0.01)
        elif "NO_ANSWER" in last_user:
            # Ход кончается без единого символа ответа — только размышление.
            # CUT: сервер сообщает, что оборвал генерацию по лимиту токенов.
            if "NO_ANSWER_CUT" in last_user:
                yield "data: " + json.dumps(
                    {"choices": [{"delta": {}, "finish_reason": "length"}]}) + "\n\n"
        else:
            if has_tool_result:
                last_tool = next(
                    m["content"] for m in reversed(messages) if m.get("role") == "tool")
                for text in ("Готово. ", "Результат инструмента: ", last_tool[:200]):
                    yield chunk({"content": text})
                    await asyncio.sleep(0.01)
            else:
                for text in _content_chunks(last_user):
                    yield chunk({"content": text})
                    await asyncio.sleep(2.0 if slow else 0.01)
        # Финальный чанк со счётчиками — как llama.cpp (usage + timings)
        yield "data: " + json.dumps({
            "choices": [],
            "usage": {"prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125},
            "timings": {"prompt_n": 100, "predicted_n": 25, "predicted_per_second": 18.5},
        }) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
