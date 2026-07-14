"""Chat tests: CRUD isolation, SSE streaming with mock LLM, history handling."""
from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from app import llm as llm_module
from app.config import settings
from tests.conftest import login_as
from tests.mock_llm import app as mock_llm_app

PASS = "chat-user-pass-1"


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    monkeypatch.setattr(llm_module, "_transport", httpx.ASGITransport(app=mock_llm_app))


@pytest.fixture()
def chat_user(client, make_user):
    make_user("chat-user", PASS)
    login_as(client, "chat-user", PASS)
    yield "chat-user"
    _wipe_chats()


def _wipe_chats():
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM chats")
        conn.commit()
    finally:
        conn.close()


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if event:
            events.append((event, data))
    return events


def test_chat_crud(client, chat_user):
    # create
    r = client.post("/api/chats", json={})
    assert r.status_code == 201
    chat = r.json()
    assert chat["title"] == "Новый чат"

    # list
    ids = [c["id"] for c in client.get("/api/chats").json()]
    assert chat["id"] in ids

    # rename
    r = client.put(f"/api/chats/{chat['id']}", json={"title": "Отчёт по сварке"})
    assert r.status_code == 200
    assert r.json()["title"] == "Отчёт по сварке"
    assert client.put(f"/api/chats/{chat['id']}", json={"title": "  "}).status_code == 400

    # delete
    assert client.delete(f"/api/chats/{chat['id']}").status_code == 200
    assert chat["id"] not in [c["id"] for c in client.get("/api/chats").json()]


def test_chats_are_private(client, make_user, chat_user):
    chat_id = client.post("/api/chats", json={"title": "Личный чат"}).json()["id"]

    make_user("intruder", PASS)
    client.cookies.clear()
    login_as(client, "intruder", PASS)

    assert client.get(f"/api/chats/{chat_id}/messages").status_code == 404
    assert client.put(f"/api/chats/{chat_id}", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/chats/{chat_id}").status_code == 404
    assert client.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"}).status_code == 404
    assert "Личный чат" not in [c["title"] for c in client.get("/api/chats").json()]


def test_send_message_streams_and_persists(client, chat_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]

    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "Привет, модель!"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(r.text)
    kinds = [e for e, _ in events]
    assert "reasoning" in kinds
    assert "content" in kinds
    assert kinds[-1] == "done"

    content = "".join(d["text"] for e, d in events if e == "content")
    assert "Привет, модель!" in content

    # обе записи в БД: user и assistant (assistant — с reasoning)
    msgs = client.get(f"/api/chats/{chat_id}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "Привет, модель!"
    assert msgs[1]["content"] == content
    assert msgs[1]["reasoning"]

    # автозаголовок из первого сообщения
    done = events[-1][1]
    assert done["title"] == "Привет, модель!"
    titles = {c["id"]: c["title"] for c in client.get("/api/chats").json()}
    assert titles[chat_id] == "Привет, модель!"


def test_reasoning_not_sent_back_to_llm(client, chat_user, monkeypatch):
    captured: list[list[dict]] = []
    orig = llm_module.stream_chat

    def spy(messages):
        captured.append(messages)
        return orig(messages)

    monkeypatch.setattr("app.routers.chat.stream_chat", spy)

    chat_id = client.post("/api/chats", json={}).json()["id"]
    client.post(f"/api/chats/{chat_id}/messages", json={"content": "первый"})
    client.post(f"/api/chats/{chat_id}/messages", json={"content": "второй"})

    second_call = captured[1]
    assert second_call[0]["role"] == "system"
    assert "Пользователь: chat-user" in second_call[0]["content"]
    # история: user/assistant/user, без ключей reasoning
    assert [m["role"] for m in second_call[1:]] == ["user", "assistant", "user"]
    assert all("reasoning" not in m for m in second_call)


def test_llm_error_reported_and_user_message_kept(client, chat_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "вызови ERROR500"})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events[0][0] == "error"
    assert "HTTP 500" in events[0][1]["detail"]

    msgs = client.get(f"/api/chats/{chat_id}/messages").json()
    assert [m["role"] for m in msgs] == ["user"]


def test_empty_message_rejected(client, chat_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    assert client.post(f"/api/chats/{chat_id}/messages", json={"content": "   "}).status_code == 400
