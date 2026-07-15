"""LLM client: streaming chat completions from llama.cpp (OpenAI-compatible API).

Parses SSE stream and yields (kind, text) deltas, where kind is
"reasoning" (thinking-режим, reasoning_content в формате deepseek) or "content".
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.config import BASE_DIR, settings

try:
    APP_TZ = ZoneInfo("Europe/Moscow")
except ZoneInfoNotFoundError:  # нет системной tzdata (Windows dev-машина)
    APP_TZ = timezone(timedelta(hours=3), "MSK")

# Тесты подменяют на httpx.ASGITransport с mock-сервером.
_transport: httpx.AsyncBaseTransport | None = None

_WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


class LLMError(Exception):
    """LLM backend is unreachable or returned an error."""


def build_system_prompt(user_display_name: str, specialization_prompt: str = "") -> str:
    path = Path(settings.system_prompt_file)
    if not path.is_absolute():
        path = BASE_DIR / path
    try:
        template = path.read_text(encoding="utf-8")
    except OSError:
        template = "Ты — ИИ-ассистент. Текущая дата и время: {datetime}. Пользователь: {user_name}."
    now = datetime.now(APP_TZ)
    dt = f"{now.strftime('%d.%m.%Y %H:%M')} ({_WEEKDAYS_RU[now.weekday()]})"
    prompt = template.replace("{datetime}", dt).replace("{user_name}", user_display_name)
    if specialization_prompt.strip():
        prompt = f"{prompt}\n\n{specialization_prompt.strip()}"
    return prompt


def make_client() -> httpx.AsyncClient:
    # API-ключ (LLM_API_KEY) опционален: если задан — шлём Bearer-заголовок
    # (llama.cpp с --api-key, сторонние OpenAI-совместимые провайдеры).
    # Сервер без аутентификации такой заголовок просто игнорирует.
    headers = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    return httpx.AsyncClient(
        base_url=settings.llm_base_url,
        headers=headers,
        timeout=httpx.Timeout(settings.llm_timeout, connect=10),
        transport=_transport,
    )


def _merge_tool_call_delta(acc: dict[int, dict], deltas: list[dict]) -> None:
    """Собрать стриминговые дельты tool_calls (OpenAI формат) по индексам."""
    for tc in deltas:
        index = tc.get("index", 0)
        entry = acc.setdefault(index, {
            "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
        if tc.get("id"):
            entry["id"] = tc["id"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            entry["function"]["name"] += fn["name"]
        if fn.get("arguments"):
            entry["function"]["arguments"] += fn["arguments"]


async def stream_chat(
    messages: list[dict], tools: list[dict] | None = None
) -> AsyncIterator[tuple[str, str]]:
    """Yield ("reasoning" | "content", delta_text) from a streaming completion.

    Если модель вернула вызовы инструментов — последним элементом отдаётся
    ("tool_calls", JSON-список в формате OpenAI).
    """
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
    tool_calls: dict[int, dict] = {}
    try:
        async with make_client() as client:
            async with client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")[:500]
                    raise LLMError(f"LLM вернул HTTP {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    reasoning = delta.get("reasoning_content")
                    if reasoning:
                        yield "reasoning", reasoning
                    content = delta.get("content")
                    if content:
                        yield "content", content
                    if delta.get("tool_calls"):
                        _merge_tool_call_delta(tool_calls, delta["tool_calls"])
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM недоступен: {exc}") from exc
    if tool_calls:
        calls = [tool_calls[i] for i in sorted(tool_calls)]
        calls = [c for c in calls if c["function"]["name"]]
        if calls:
            yield "tool_calls", json.dumps(calls, ensure_ascii=False)
