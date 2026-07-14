"""RAG tests (§8): контекст LightRAG в промпте, блок источников, деградация при сбое."""
from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import llm as llm_module
from app import rag as rag_module
from app.config import settings
from tests.conftest import login_as
from tests.mock_llm import app as mock_llm_app
from tests.test_chat import _parse_sse

PASS = "rag-user-pass-01"

mock_rag = FastAPI()


@mock_rag.post("/query")
async def rag_query(request: Request):
    body = await request.json()
    assert body.get("only_need_context") is True
    query = body.get("query", "")
    if "RAG_DOWN" in query:
        return JSONResponse({"detail": "boom"}, status_code=500)
    if "RAG_EMPTY" in query:
        return {"response": ""}
    return {"response": f"[Документ 1] Контекст по запросу: {query[:50]}"}


@pytest.fixture(autouse=True)
def mocks(monkeypatch):
    monkeypatch.setattr(llm_module, "_transport", httpx.ASGITransport(app=mock_llm_app))
    monkeypatch.setattr(rag_module, "_transport", httpx.ASGITransport(app=mock_rag))
    monkeypatch.setattr(rag_module, "settings",
                        replace(settings, rag_enabled=True, rag_base_url="http://rag.test"))
    monkeypatch.setattr("app.routers.chat.settings",
                        replace(settings, rag_enabled=True, rag_base_url="http://rag.test"))


@pytest.fixture()
def rag_user(client, make_user):
    make_user("rag-user", PASS)
    login_as(client, "rag-user", PASS)
    yield
    import sqlite3
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM chats")
        conn.commit()
    finally:
        conn.close()


def _send(client, content: str, use_rag: bool = True):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    r = client.post(f"/api/chats/{chat_id}/messages",
                    json={"content": content, "use_rag": use_rag, "use_tools": False})
    assert r.status_code == 200
    return chat_id, _parse_sse(r.text)


def test_rag_context_inserted_and_sources_shown(client, rag_user, monkeypatch):
    captured: list[list[dict]] = []
    orig = llm_module.stream_chat

    def spy(messages, tools=None):
        captured.append(messages)
        return orig(messages, tools=tools)

    monkeypatch.setattr("app.routers.chat.stream_chat", spy)

    chat_id, events = _send(client, "Какие требования к сварке корпусов?")

    sources = [d for e, d in events if e == "sources"]
    assert len(sources) == 1
    assert "Контекст по запросу" in sources[0]["text"]

    # Контекст вставлен отдельным system-сообщением перед сообщением пользователя
    msgs = captured[0]
    assert msgs[-1]["role"] == "user"
    assert msgs[-2]["role"] == "system"
    assert "КОНТЕКСТ БАЗЫ ЗНАНИЙ" in msgs[-2]["content"]
    assert "указывай источники" in msgs[-2]["content"].lower()

    # Источники сохранены в истории
    messages = client.get(f"/api/chats/{chat_id}/messages").json()
    assistant = [m for m in messages if m["role"] == "assistant"][-1]
    assert assistant["tool_activity"][0]["status"] == "sources"


def test_rag_disabled_toggle(client, rag_user):
    _, events = _send(client, "обычный вопрос", use_rag=False)
    assert [e for e, _ in events if e in ("sources", "rag_error")] == []


def test_rag_down_degrades_gracefully(client, rag_user):
    _, events = _send(client, "RAG_DOWN вопрос")
    rag_errors = [d for e, d in events if e == "rag_error"]
    assert len(rag_errors) == 1
    assert "База знаний" in rag_errors[0]["detail"]
    # Обычный ответ всё равно пришёл
    content = "".join(d["text"] for e, d in events if e == "content")
    assert "Ответ" in content
    assert any(e == "done" for e, _ in events)


def test_rag_empty_context_notice(client, rag_user):
    _, events = _send(client, "RAG_EMPTY вопрос")
    rag_errors = [d for e, d in events if e == "rag_error"]
    assert len(rag_errors) == 1
    assert "не вернула контекст" in rag_errors[0]["detail"]
    assert any(e == "done" for e, _ in events)


def test_me_exposes_rag_flag(client, rag_user, monkeypatch):
    monkeypatch.setattr("app.routers.auth.settings",
                        replace(settings, rag_enabled=True, rag_base_url="http://rag.test"))
    assert client.get("/api/me").json()["rag_enabled"] is True
    monkeypatch.setattr("app.routers.auth.settings",
                        replace(settings, rag_enabled=False, rag_base_url="http://rag.test"))
    assert client.get("/api/me").json()["rag_enabled"] is False
