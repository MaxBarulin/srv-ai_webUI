"""Stage 6 tests (§15): специализации, обратная связь, очередь, примеры, масштаб."""
from __future__ import annotations

import asyncio
import sqlite3

import httpx
import pytest

from app import llm as llm_module
from app.config import settings
from app.queue import LLMQueue
from tests.conftest import login_as
from tests.mock_llm import app as mock_llm_app
from tests.test_chat import _parse_sse

PASS = "ergo-user-pass-01"


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    monkeypatch.setattr(llm_module, "_transport", httpx.ASGITransport(app=mock_llm_app))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture(autouse=True)
def clean():
    yield
    conn = _connect()
    try:
        for t in ("feedback", "messages", "chats"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def ergo_user(client, make_user):
    uid = make_user("ergo-user", PASS)
    login_as(client, "ergo-user", PASS)
    return uid


@pytest.fixture()
def ergo_admin(client, make_user):
    make_user("ergo-admin", PASS, role="admin")
    login_as(client, "ergo-admin", PASS)


# --- Специализации ---

def test_default_specializations_seeded(client, ergo_user):
    specs = client.get("/api/specializations").json()
    names = [s["name"] for s in specs]
    assert "Общий" in names
    assert "Сварка" in names


def test_chat_uses_specialization_prompt(client, ergo_user, monkeypatch):
    captured = []
    orig = llm_module.stream_chat

    def spy(messages, tools=None):
        captured.append(messages)
        return orig(messages, tools=tools)

    monkeypatch.setattr("app.routers.chat.stream_chat", spy)

    specs = client.get("/api/specializations").json()
    welding = next(s for s in specs if s["name"] == "Сварка")
    chat = client.post("/api/chats", json={"specialization_id": welding["id"]}).json()
    assert chat["specialization_id"] == welding["id"]

    r = client.post(f"/api/chats/{chat['id']}/messages",
                    json={"content": "вопрос", "use_tools": False})
    assert r.status_code == 200
    system_prompt = captured[0][0]["content"]
    assert "сварочному производству" in system_prompt


def test_unknown_specialization_falls_back_to_general(client, ergo_user):
    chat = client.post("/api/chats", json={"specialization_id": 99999}).json()
    assert chat["specialization_id"] is None


def _spy_llm(monkeypatch):
    captured = []
    orig = llm_module.stream_chat

    def spy(messages, tools=None):
        captured.append(messages)
        return orig(messages, tools=tools)

    monkeypatch.setattr("app.routers.chat.stream_chat", spy)
    return captured


def test_custom_prompt_overrides_specialization(client, ergo_user, monkeypatch):
    captured = _spy_llm(monkeypatch)
    specs = client.get("/api/specializations").json()
    welding = next(s for s in specs if s["name"] == "Сварка")

    chat = client.post("/api/chats", json={
        "specialization_id": welding["id"],
        "custom_prompt": "Отвечай только стихами про судостроение.",
    }).json()
    assert chat["custom_prompt"] == "Отвечай только стихами про судостроение."

    client.post(f"/api/chats/{chat['id']}/messages",
                json={"content": "вопрос", "use_tools": False})
    system_prompt = captured[0][0]["content"]
    assert "стихами про судостроение" in system_prompt
    assert "сварочному производству" not in system_prompt  # свой промпт замещает режим


def test_chat_prompt_update_and_clear(client, ergo_user, monkeypatch):
    captured = _spy_llm(monkeypatch)
    specs = client.get("/api/specializations").json()
    welding = next(s for s in specs if s["name"] == "Сварка")
    chat_id = client.post("/api/chats", json={"specialization_id": welding["id"]}).json()["id"]

    # Задать свой промпт через PUT (частичное обновление — title не передаём)
    r = client.put(f"/api/chats/{chat_id}", json={"custom_prompt": "Ты — аудитор ИБ."})
    assert r.status_code == 200
    assert r.json()["custom_prompt"] == "Ты — аудитор ИБ."
    assert r.json()["title"]  # название не потерялось

    client.post(f"/api/chats/{chat_id}/messages", json={"content": "а", "use_tools": False})
    assert "аудитор ИБ" in captured[0][0]["content"]

    # Очистить свой промпт — снова действует специализация
    client.put(f"/api/chats/{chat_id}", json={"custom_prompt": ""})
    client.post(f"/api/chats/{chat_id}/messages", json={"content": "б", "use_tools": False})
    assert "сварочному производству" in captured[1][0]["content"]

    # Смена специализации через PUT; несуществующая — 400
    r = client.put(f"/api/chats/{chat_id}", json={"specialization_id": None})
    assert r.status_code == 200 and r.json()["specialization_id"] is None
    assert client.put(f"/api/chats/{chat_id}",
                      json={"specialization_id": 99999}).status_code == 400


def test_admin_specialization_crud(client, ergo_admin):
    created = client.post("/api/admin/specializations",
                          json={"name": "Гальваника", "system_prompt": "Ты — технолог-гальваник."})
    assert created.status_code == 201
    spec_id = created.json()["id"]

    got = [s for s in client.get("/api/admin/specializations").json() if s["id"] == spec_id][0]
    assert got["name"] == "Гальваника"

    upd = client.put(f"/api/admin/specializations/{spec_id}",
                     json={"name": "Гальваника-2", "system_prompt": "x", "is_active": False})
    assert upd.status_code == 200

    # is_active=False — не появляется в публичном списке
    assert spec_id not in [s["id"] for s in client.get("/api/specializations").json()]

    assert client.delete(f"/api/admin/specializations/{spec_id}").status_code == 200
    assert spec_id not in [s["id"] for s in client.get("/api/admin/specializations").json()]


def test_specialization_admin_only(client, ergo_user):
    assert client.get("/api/admin/specializations").status_code == 403
    assert client.post("/api/admin/specializations", json={"name": "x"}).status_code == 403


# --- Обратная связь ---

def _make_assistant_message(client, chat_id: int) -> int:
    r = client.post(f"/api/chats/{chat_id}/messages",
                    json={"content": "привет", "use_tools": False})
    assert r.status_code == 200
    msgs = client.get(f"/api/chats/{chat_id}/messages").json()
    return [m for m in msgs if m["role"] == "assistant"][-1]["id"]


def test_feedback_submit_and_update(client, ergo_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    msg_id = _make_assistant_message(client, chat_id)

    r = client.post(f"/api/chats/{chat_id}/messages/{msg_id}/feedback",
                    json={"rating": -1, "comment": "неточно"})
    assert r.status_code == 200

    msgs = client.get(f"/api/chats/{chat_id}/messages").json()
    assert [m for m in msgs if m["id"] == msg_id][0]["feedback_rating"] == -1

    # Повторная оценка перезаписывает (UNIQUE message_id+user_id)
    client.post(f"/api/chats/{chat_id}/messages/{msg_id}/feedback", json={"rating": 1})
    conn = _connect()
    try:
        rows = conn.execute("SELECT rating, comment FROM feedback WHERE message_id = ?",
                            (msg_id,)).fetchall()
        assert len(rows) == 1
        assert rows[0]["rating"] == 1
    finally:
        conn.close()


def test_feedback_invalid_rating(client, ergo_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    msg_id = _make_assistant_message(client, chat_id)
    assert client.post(f"/api/chats/{chat_id}/messages/{msg_id}/feedback",
                       json={"rating": 5}).status_code == 400


def test_feedback_only_own_chat(client, make_user, ergo_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    msg_id = _make_assistant_message(client, chat_id)

    make_user("ergo-intruder", PASS)
    login_as(client, "ergo-intruder", PASS)
    r = client.post(f"/api/chats/{chat_id}/messages/{msg_id}/feedback", json={"rating": 1})
    assert r.status_code == 404


def test_feedback_export_jsonl(client, ergo_user, make_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    msg_id = _make_assistant_message(client, chat_id)
    client.post(f"/api/chats/{chat_id}/messages/{msg_id}/feedback",
                json={"rating": 1, "comment": "хорошо"})

    make_user("ergo-admin2", PASS, role="admin")
    login_as(client, "ergo-admin2", PASS)
    r = client.get("/api/admin/feedback/export")
    assert r.status_code == 200
    lines = [ln for ln in r.text.splitlines() if ln.strip()]
    assert len(lines) == 1
    import json
    record = json.loads(lines[0])
    assert record["rating"] == 1
    assert record["comment"] == "хорошо"
    assert record["answer"]  # текст ответа модели попал в выгрузку
    assert record["prompt"] == "привет"  # предыдущее сообщение пользователя


# --- Примеры запросов ---

def test_examples_seeded_and_editable(client, ergo_user):
    examples = client.get("/api/examples").json()
    assert len(examples) >= 3

    # not admin — 403 on edit
    assert client.put("/api/admin/examples", json={"items": ["a"]}).status_code == 403


def test_admin_set_examples(client, ergo_admin):
    r = client.put("/api/admin/examples", json={"items": ["Пример 1", "  ", "Пример 2"]})
    assert r.status_code == 200
    assert r.json()["count"] == 2
    texts = [e["text"] for e in client.get("/api/examples").json()]
    assert texts == ["Пример 1", "Пример 2"]


# --- Масштаб шрифта ---

def test_font_scale_setting(client, ergo_user):
    assert client.get("/api/me").json()["font_scale"] == 1
    assert client.post("/api/me/settings", json={"font_scale": 2}).status_code == 200
    assert client.get("/api/me").json()["font_scale"] == 2
    assert client.post("/api/me/settings", json={"font_scale": 7}).status_code == 400


# --- Очередь ---

def test_queue_serializes_and_reports_position():
    async def scenario():
        q = LLMQueue()
        order = []

        async def worker(name, hold):
            ticket = q.enqueue()
            positions = []
            async for pos in ticket.wait_turn(timeout=5):
                positions.append(pos)
            order.append(("start", name))
            await asyncio.sleep(hold)
            order.append(("end", name))
            ticket.release()
            return positions

        # a стартует сразу; b и c ждут
        t_a = asyncio.create_task(worker("a", 0.2))
        await asyncio.sleep(0.05)
        t_b = asyncio.create_task(worker("b", 0.1))
        await asyncio.sleep(0.05)
        t_c = asyncio.create_task(worker("c", 0.1))

        pos_a, pos_b, pos_c = await asyncio.gather(t_a, t_b, t_c)
        return order, pos_a, pos_b, pos_c

    order, pos_a, pos_b, pos_c = asyncio.run(scenario())

    # Строго последовательная обработка: никакие два не пересекаются
    assert order == [("start", "a"), ("end", "a"),
                     ("start", "b"), ("end", "b"),
                     ("start", "c"), ("end", "c")]
    # a обработан сразу (без ожидания), b и c видели положительную позицию
    assert pos_a == []
    assert pos_b and pos_b[0] >= 1
    assert pos_c and pos_c[0] >= 1


def test_queue_timeout():
    async def scenario():
        q = LLMQueue()
        blocker = q.enqueue()
        # blocker занимает место и не освобождает
        async for _ in blocker.wait_turn(timeout=5):
            pass
        waiter = q.enqueue()
        from app.queue import QueueTimeout
        try:
            async for _ in waiter.wait_turn(timeout=0.5):
                pass
        except QueueTimeout:
            return True
        return False

    assert asyncio.run(scenario()) is True
