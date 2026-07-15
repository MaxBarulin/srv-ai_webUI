"""Статистика генерации (usage/timings сервера), «Продолжить», удаление сообщения."""
from __future__ import annotations

import sqlite3
from dataclasses import replace

import httpx
import pytest

from app import llm as llm_module
from app.config import settings
from app.metrics import Metrics
from tests.conftest import login_as
from tests.mock_llm import app as mock_llm_app
from tests.test_chat import _parse_sse

PASS = "ctrl-user-pass-01"


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    monkeypatch.setattr(llm_module, "_transport", httpx.ASGITransport(app=mock_llm_app))


@pytest.fixture()
def ctrl_user(client, make_user):
    make_user("ctrl-user", PASS)
    login_as(client, "ctrl-user", PASS)
    yield
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("DELETE FROM feedback")
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM chats")
        conn.commit()
    finally:
        conn.close()


def _send(client, chat_id: int, content: str):
    r = client.post(f"/api/chats/{chat_id}/messages",
                    json={"content": content, "use_tools": False})
    assert r.status_code == 200
    return _parse_sse(r.text)


# --- Статистика генерации (метод llama.cpp: счётчики сервера) ---

def test_stats_event_from_server_counters(client, ctrl_user, monkeypatch):
    monkeypatch.setattr("app.routers.chat.settings",
                        replace(settings, llm_context_size=500))
    chat_id = client.post("/api/chats", json={}).json()["id"]
    events = _send(client, chat_id, "привет")

    stats = [d for e, d in events if e == "stats"]
    assert len(stats) == 1
    # Мок отдаёт timings как llama.cpp: 25 токенов, 18.5 ток/с, prompt 100
    assert stats[0]["completion_tokens"] == 25
    assert stats[0]["tokens_per_second"] == 18.5
    assert stats[0]["context_used"] == 125
    assert stats[0]["context_percent"] == 25  # 125 из 500


def test_stats_without_context_size(client, ctrl_user, monkeypatch):
    monkeypatch.setattr("app.routers.chat.settings",
                        replace(settings, llm_context_size=0))
    chat_id = client.post("/api/chats", json={}).json()["id"]
    events = _send(client, chat_id, "привет")
    stats = [d for e, d in events if e == "stats"][0]
    assert stats["context_percent"] is None  # размер контекста неизвестен


def test_metrics_use_server_speed(client, ctrl_user, monkeypatch):
    fresh = Metrics()
    monkeypatch.setattr("app.routers.chat.metrics", fresh)
    chat_id = client.post("/api/chats", json={}).json()["id"]
    _send(client, chat_id, "привет")
    snap = fresh.snapshot()
    # Скорость взята из timings сервера (18.5), а не из грубой оценки по символам
    assert snap["avg_tokens_per_sec"] == 18.5
    assert snap["requests_total"] == 1


# --- «Продолжить генерацию» ---

def test_continue_appends_to_last_answer(client, ctrl_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    _send(client, chat_id, "начни рассказ")
    before = client.get(f"/api/chats/{chat_id}/messages").json()
    original = [m for m in before if m["role"] == "assistant"][-1]

    r = client.post(f"/api/chats/{chat_id}/continue")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    done = [d for e, d in events if e == "done"][0]
    assert done["message_id"] == original["id"]  # дописали то же сообщение

    after = client.get(f"/api/chats/{chat_id}/messages").json()
    updated = [m for m in after if m["id"] == original["id"]][0]
    assert updated["content"].startswith(original["content"])
    assert len(updated["content"]) > len(original["content"])
    # Новых сообщений не появилось
    assert len(after) == len(before)


def test_continue_requires_assistant_message(client, ctrl_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    assert client.post(f"/api/chats/{chat_id}/continue").status_code == 400


def test_continue_foreign_chat_404(client, make_user, ctrl_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    _send(client, chat_id, "привет")
    make_user("ctrl-intruder", PASS)
    login_as(client, "ctrl-intruder", PASS)
    assert client.post(f"/api/chats/{chat_id}/continue").status_code == 404


# --- Удаление сообщения ---

def test_delete_message(client, ctrl_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    _send(client, chat_id, "привет")
    messages = client.get(f"/api/chats/{chat_id}/messages").json()
    assert len(messages) == 2
    last = messages[-1]

    r = client.delete(f"/api/chats/{chat_id}/messages/{last['id']}")
    assert r.status_code == 200
    remaining = client.get(f"/api/chats/{chat_id}/messages").json()
    assert [m["id"] for m in remaining] == [messages[0]["id"]]

    # Несуществующее сообщение — 404
    assert client.delete(f"/api/chats/{chat_id}/messages/{last['id']}").status_code == 404


def test_delete_message_with_feedback(client, ctrl_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    _send(client, chat_id, "привет")
    msg = client.get(f"/api/chats/{chat_id}/messages").json()[-1]
    client.post(f"/api/chats/{chat_id}/messages/{msg['id']}/feedback", json={"rating": 1})
    # Удаление не падает на внешнем ключе feedback
    assert client.delete(f"/api/chats/{chat_id}/messages/{msg['id']}").status_code == 200


def test_delete_message_foreign_chat_404(client, make_user, ctrl_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    _send(client, chat_id, "привет")
    msg = client.get(f"/api/chats/{chat_id}/messages").json()[-1]
    make_user("ctrl-intruder2", PASS)
    login_as(client, "ctrl-intruder2", PASS)
    assert client.delete(f"/api/chats/{chat_id}/messages/{msg['id']}").status_code == 404