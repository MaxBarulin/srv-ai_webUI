"""Клиент LightRAG (§8): получение контекста из базы знаний перед запросом к LLM.

Бэкенд обращается только к RAG_BASE_URL (эндпойнт /query). Эмбеддинги использует
сам LightRAG — напрямую к :8001 мы не ходим. Недоступность LightRAG не должна
ломать чат: вызывающий код ловит RAGError и продолжает обычный режим.
"""
from __future__ import annotations

import httpx

from app.config import settings

RAG_TIMEOUT = 60  # секунд; получение контекста должно быть заметно быстрее генерации

# Тесты подменяют на httpx.ASGITransport с mock-сервером (как в app.llm)
_transport: httpx.AsyncBaseTransport | None = None

CONTEXT_INSTRUCTION = (
    "Ниже приведён контекст из базы знаний предприятия. Отвечай на основе этого "
    "контекста; если сведений недостаточно — прямо скажи об этом. Указывай источники, "
    "на которые опираешься.\n\n=== КОНТЕКСТ БАЗЫ ЗНАНИЙ ===\n{context}\n=== КОНЕЦ КОНТЕКСТА ==="
)


class RAGError(Exception):
    """LightRAG недоступен или вернул ошибку."""


async def fetch_context(query: str) -> str:
    """Запросить контекст по запросу пользователя. Пустая строка — ничего не найдено."""
    if not settings.rag_base_url:
        raise RAGError("База знаний не настроена (RAG_BASE_URL пуст)")
    payload = {
        "query": query,
        "mode": settings.rag_mode,
        "only_need_context": True,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.rag_base_url,
            timeout=httpx.Timeout(RAG_TIMEOUT, connect=10),
            transport=_transport,
        ) as client:
            response = await client.post("/query", json=payload)
    except httpx.HTTPError as exc:
        raise RAGError(f"База знаний недоступна: {exc}") from exc
    if response.status_code != 200:
        body = response.text[:300]
        raise RAGError(f"База знаний вернула HTTP {response.status_code}: {body}")
    try:
        data = response.json()
    except ValueError:
        raise RAGError("База знаний вернула некорректный ответ")
    context = data.get("response") or data.get("context") or ""
    if not isinstance(context, str):
        raise RAGError("База знаний вернула некорректный ответ")
    return context.strip()


def context_message(context: str) -> dict:
    """Системное сообщение с контекстом для вставки в промпт (§8)."""
    return {"role": "system", "content": CONTEXT_INSTRUCTION.format(context=context)}
