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
            "```python\n", "print('привет')\n", "```\n",
        ]
    return ["Ответ ", "на: ", f"«{last_user_message[:80]}»"]


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    last_user = next(
        (m["content"] for m in reversed(body.get("messages", [])) if m.get("role") == "user"), "")

    if "ERROR500" in last_user:
        return JSONResponse({"error": "mock internal error"}, status_code=500)

    slow = "SLOW" in last_user  # для ручной проверки кнопки «Остановить»

    async def sse():
        def chunk(delta: dict) -> str:
            return "data: " + json.dumps(
                {"choices": [{"delta": delta}]}, ensure_ascii=False) + "\n\n"

        for text in REASONING_CHUNKS:
            yield chunk({"reasoning_content": text})
            await asyncio.sleep(2.0 if slow else 0.01)
        for text in _content_chunks(last_user):
            yield chunk({"content": text})
            await asyncio.sleep(2.0 if slow else 0.01)
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
