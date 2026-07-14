"""Tool calling tests (§7): агентный цикл с mock-LLM, права, подтверждение, fallback."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import httpx
import pytest

from app import llm as llm_module
from app.config import settings
from tests.conftest import login_as
from tests.mock_llm import app as mock_llm_app
from tests.test_chat import _parse_sse

PASS = "tools-user-pass-1"


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    monkeypatch.setattr(llm_module, "_transport", httpx.ASGITransport(app=mock_llm_app))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture(autouse=True)
def clean_data():
    yield
    conn = _connect()
    try:
        for table in ("messages", "chats", "notes", "events"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def tool_user(client, make_user):
    uid = make_user("tools-user", PASS)
    login_as(client, "tools-user", PASS)
    return uid


def _insert_note(owner_id: int, note_id: int = 1, title: str = "Тестовая заметка",
                 scope: str = "personal", body: str = "секретный текст") -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO notes (id, owner_id, scope, title, body, tags, "
            "created_at, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, '', datetime('now'), datetime('now'), ?)",
            (note_id, owner_id, scope, title, body, owner_id),
        )
        conn.commit()
    finally:
        conn.close()


def _send(client, chat_id: int, content: str, use_tools: bool = True):
    r = client.post(f"/api/chats/{chat_id}/messages",
                    json={"content": content, "use_tools": use_tools})
    assert r.status_code == 200
    return _parse_sse(r.text)


def _new_chat(client) -> int:
    return client.post("/api/chats", json={}).json()["id"]


def _events_of(events, name):
    return [data for event, data in events if event == name]


def test_agent_loop_creates_note(client, tool_user):
    chat_id = _new_chat(client)
    events = _send(client, chat_id, "TOOL_CREATE_NOTE создай заметку")

    tool_events = _events_of(events, "tool")
    assert tool_events == [{"label": "создана заметка «Тестовая заметка»"}]
    content = "".join(d["text"] for d in _events_of(events, "content"))
    assert "Готово" in content

    conn = _connect()
    try:
        note = conn.execute("SELECT * FROM notes WHERE title = 'Тестовая заметка'").fetchone()
        assert note is not None
        assert note["owner_id"] == tool_user
        assert note["body"] == "Содержимое от модели"
        audit = conn.execute(
            "SELECT * FROM audit_log WHERE action = 'llm_tool_call' AND user_id = ?",
            (tool_user,)).fetchall()
        assert len(audit) == 1
        assert audit[0]["details"] == "tool=notes_create"
    finally:
        conn.close()

    # Плашка сохранена в истории
    messages = client.get(f"/api/chats/{chat_id}/messages").json()
    assistant = [m for m in messages if m["role"] == "assistant"][-1]
    assert assistant["tool_activity"] == [
        {"label": "создана заметка «Тестовая заметка»", "status": "ok"}]


def test_tools_disabled_by_toggle(client, tool_user):
    chat_id = _new_chat(client)
    events = _send(client, chat_id, "TOOL_CREATE_NOTE", use_tools=False)
    assert _events_of(events, "tool") == []
    conn = _connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
    finally:
        conn.close()


def test_foreign_personal_note_inaccessible(client, make_user, tool_user):
    other = make_user("other-owner", PASS)
    _insert_note(other, note_id=1, scope="personal", body="секретный текст")

    chat_id = _new_chat(client)
    events = _send(client, chat_id, "TOOL_GET_NOTE прочитай заметку 1")

    tool_events = _events_of(events, "tool")
    assert len(tool_events) == 1
    assert tool_events[0]["error"] is True
    assert "не найдена или недоступна" in tool_events[0]["label"]
    # Содержимое чужой заметки не утекло в ответ
    full_text = json.dumps(events, ensure_ascii=False)
    assert "секретный текст" not in full_text


def test_shared_note_readable(client, make_user, tool_user):
    other = make_user("other-owner2", PASS)
    _insert_note(other, note_id=1, scope="shared", body="общий текст")

    chat_id = _new_chat(client)
    events = _send(client, chat_id, "TOOL_GET_NOTE")
    assert _events_of(events, "tool") == [{"label": "прочитана заметка «Тестовая заметка»"}]


def test_destructive_requires_confirmation(client, tool_user):
    _insert_note(tool_user, note_id=1)
    chat_id = _new_chat(client)
    events = _send(client, chat_id, "TOOL_DELETE_NOTE удали заметку")

    confirms = _events_of(events, "tool_confirm")
    assert len(confirms) == 1
    assert confirms[0]["label"] == "удалить заметку «Тестовая заметка»"
    token = confirms[0]["token"]

    conn = _connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1  # ещё не удалена
    finally:
        conn.close()

    r = client.post("/api/tools/confirm", json={"token": token})
    assert r.status_code == 200
    assert r.json()["label"] == "удалена заметка «Тестовая заметка»"

    conn = _connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
    finally:
        conn.close()

    # Повторное подтверждение — токен одноразовый
    assert client.post("/api/tools/confirm", json={"token": token}).status_code == 404


def test_note_rewrite_is_destructive_but_rename_is_not(client, tool_user):
    _insert_note(tool_user, note_id=1)
    chat_id = _new_chat(client)

    events = _send(client, chat_id, "TOOL_REWRITE_NOTE перепиши текст")
    assert len(_events_of(events, "tool_confirm")) == 1

    events = _send(client, chat_id, "TOOL_RENAME_NOTE переименуй")
    assert _events_of(events, "tool_confirm") == []
    assert _events_of(events, "tool") == [{"label": "обновлена заметка «Новый заголовок»"}]


def test_confirm_token_bound_to_user(client, make_user, tool_user):
    _insert_note(tool_user, note_id=1)
    chat_id = _new_chat(client)
    events = _send(client, chat_id, "TOOL_DELETE_NOTE")
    token = _events_of(events, "tool_confirm")[0]["token"]

    make_user("confirm-intruder", PASS)
    login_as(client, "confirm-intruder", PASS)
    assert client.post("/api/tools/confirm", json={"token": token}).status_code == 404

    # Владелец по-прежнему может подтвердить
    login_as(client, "tools-user", PASS)
    assert client.post("/api/tools/confirm", json={"token": token}).status_code == 200


def test_destructive_executes_directly_when_confirm_disabled(client, tool_user, monkeypatch):
    monkeypatch.setattr("app.routers.chat.settings",
                        replace(settings, tools_confirm_destructive=False))
    _insert_note(tool_user, note_id=1)
    chat_id = _new_chat(client)
    events = _send(client, chat_id, "TOOL_DELETE_NOTE")
    assert _events_of(events, "tool_confirm") == []
    assert _events_of(events, "tool") == [{"label": "удалена заметка «Тестовая заметка»"}]


def test_fallback_json_tool_call(client, tool_user):
    chat_id = _new_chat(client)
    events = _send(client, chat_id, "TOOL_FALLBACK создай заметку")

    assert _events_of(events, "tool") == [{"label": "создана заметка «Fallback заметка»"}]
    # Сырой JSON-блок не попал в контент ответа
    content = "".join(d["text"] for d in _events_of(events, "content"))
    assert "notes_create" not in content
    assert "Готово" in content

    conn = _connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM notes WHERE title = 'Fallback заметка'").fetchone()[0] == 1
    finally:
        conn.close()


def test_calendar_create_via_tool(client, tool_user):
    chat_id = _new_chat(client)
    events = _send(client, chat_id, "TOOL_CREATE_EVENT создай совещание")
    assert _events_of(events, "tool") == [
        {"label": "создано событие «Совещание» (15.07.2026 10:00)"}]
    conn = _connect()
    try:
        event = conn.execute("SELECT * FROM events").fetchone()
        assert event["title"] == "Совещание"
        assert event["starts_at"] == "2026-07-15T10:00:00+03:00"
        assert event["owner_id"] == tool_user
    finally:
        conn.close()


def test_get_current_datetime_tool(client, tool_user):
    chat_id = _new_chat(client)
    events = _send(client, chat_id, "TOOL_TIME который час")
    assert _events_of(events, "tool") == [{"label": "запрошены текущие дата и время"}]


def test_unknown_tool_returns_error_to_model(client, tool_user):
    chat_id = _new_chat(client)
    events = _send(client, chat_id, "TOOL_UNKNOWN")
    tool_events = _events_of(events, "tool")
    assert len(tool_events) == 1
    assert tool_events[0]["error"] is True
    # Цикл продолжился и модель дала финальный ответ
    assert any(event == "done" for event, _ in events)


def test_tool_iteration_limit(client, tool_user):
    chat_id = _new_chat(client)
    events = _send(client, chat_id, "TOOL_LOOP")
    errors = _events_of(events, "error")
    assert len(errors) == 1
    assert "лимит" in errors[0]["detail"].lower()
    assert len(_events_of(events, "tool")) == 6  # MAX_TOOL_ITERATIONS
