"""LLM_API_KEY / RAG_API_KEY: Bearer-заголовок опционален и безопасен.

Сценарии:
- ключ задан → уходит заголовок Authorization: Bearer <key>;
- ключ пуст → заголовок не отправляется;
- ключ задан, но сервер аутентификацию не требует → всё работает (мок,
  как и llama.cpp без --api-key, игнорирует заголовок).
"""
from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app import llm as llm_module
from app import rag as rag_module
from app.config import settings
from tests.mock_llm import app as mock_llm_app

captured_headers: list[dict] = []

echo_app = FastAPI()


@echo_app.post("/v1/chat/completions")
async def echo_completions(request: Request):
    captured_headers.append(dict(request.headers))

    async def sse():
        yield 'data: {"choices": [{"delta": {"content": "ok"}}]}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@echo_app.post("/query")
async def echo_query(request: Request):
    captured_headers.append(dict(request.headers))
    return JSONResponse({"response": "контекст"})


@pytest.fixture(autouse=True)
def clear_captured():
    captured_headers.clear()


def _run_llm(monkeypatch, api_key: str, app=echo_app) -> list:
    monkeypatch.setattr(llm_module, "_transport", httpx.ASGITransport(app=app))
    monkeypatch.setattr(llm_module, "settings", replace(settings, llm_api_key=api_key))

    async def collect():
        return [pair async for pair in
                llm_module.stream_chat([{"role": "user", "content": "привет"}])]

    return asyncio.run(collect())


def test_llm_bearer_sent_when_key_set(monkeypatch):
    chunks = _run_llm(monkeypatch, api_key="secret-123")
    assert ("content", "ok") in chunks  # ответ дошёл
    assert captured_headers[0].get("authorization") == "Bearer secret-123"


def test_llm_no_header_when_key_empty(monkeypatch):
    chunks = _run_llm(monkeypatch, api_key="")
    assert ("content", "ok") in chunks
    assert "authorization" not in captured_headers[0]


def test_llm_key_harmless_when_server_ignores_it(monkeypatch):
    # mock_llm вообще не смотрит на заголовки — как llama.cpp без --api-key
    chunks = _run_llm(monkeypatch, api_key="unused-key", app=mock_llm_app)
    assert any(kind == "content" for kind, _ in chunks)  # генерация прошла


def _run_rag(monkeypatch, api_key: str) -> str:
    monkeypatch.setattr(rag_module, "_transport", httpx.ASGITransport(app=echo_app))
    monkeypatch.setattr(rag_module, "settings",
                        replace(settings, rag_base_url="http://rag.test", rag_api_key=api_key))
    return asyncio.run(rag_module.fetch_context("вопрос"))


def test_rag_bearer_sent_when_key_set(monkeypatch):
    assert _run_rag(monkeypatch, api_key="rag-key") == "контекст"
    assert captured_headers[0].get("authorization") == "Bearer rag-key"


def test_rag_no_header_when_key_empty(monkeypatch):
    _run_rag(monkeypatch, api_key="")
    assert "authorization" not in captured_headers[0]
