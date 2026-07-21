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
    # /props кэшируется — сбрасываем перед каждым тестом, чтобы получить
    # текущий mock (или monkeypatched вариант) на каждом запуске.
    llm_module._reset_server_ctx_cache_for_tests()


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
    """Знаменатель процента — заданный админом LLM_CONTEXT_SIZE (.env): показываем
    заполнение относительно ВЫБРАННОГО лимита (500), а /props (8192) — лишь запас.
    Также приходит elapsed_seconds — полное время ответа."""
    monkeypatch.setattr("app.routers.chat.settings",
                        replace(settings, llm_context_size=500))
    chat_id = client.post("/api/chats", json={}).json()["id"]
    events = _send(client, chat_id, "привет")

    stats = [d for e, d in events if e == "stats"]
    assert len(stats) == 1
    # Мок отдаёт timings как llama.cpp: 25 токенов, 18.5 ток/с, prompt 100
    assert stats[0]["completion_tokens"] == 25
    assert stats[0]["tokens_per_second"] == 18.5
    # context_used = last_prompt_tokens + last_completion_tokens = 100 + 25
    assert stats[0]["context_used"] == 125
    assert stats[0]["context_size"] == 500          # из .env, а не /props (8192)
    assert stats[0]["context_percent"] == 25        # round(125/500*100)
    assert stats[0]["elapsed_seconds"] >= 0         # полное время ответа


def test_stats_falls_back_to_env_when_props_unavailable(client, ctrl_user, monkeypatch):
    """Если /props недоступен — берём LLM_CONTEXT_SIZE из .env."""
    async def no_props():
        return None
    monkeypatch.setattr("app.routers.chat.get_server_context_size", no_props)
    monkeypatch.setattr("app.routers.chat.settings",
                        replace(settings, llm_context_size=500))
    chat_id = client.post("/api/chats", json={}).json()["id"]
    events = _send(client, chat_id, "привет")

    stats = [d for e, d in events if e == "stats"][0]
    assert stats["context_size"] == 500
    assert stats["context_percent"] == 25  # 125 из 500


def test_stats_context_no_double_count_on_tool_loop(client, ctrl_user):
    """Регресс: в чатах с tool calling context_used считался как
    last_prompt_tokens + sum(completion) по итерациям, а prompt_tokens
    последней итерации уже содержит все предыдущие ответы → был двойной
    счёт. Теперь context_used = prompt + completion только последней итерации."""
    chat_id = client.post("/api/chats", json={}).json()["id"]
    # TOOL_CREATE_NOTE → две итерации к mock-LLM. Обе отдают prompt=100,
    # completion=25. Корректный context_used — 100+25=125 (последняя итерация),
    # а НЕ 100+25+25=150 (буквальная сумма всех completion).
    r = client.post(f"/api/chats/{chat_id}/messages",
                    json={"content": "TOOL_CREATE_NOTE создай заметку", "use_tools": True})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    stats = [d for e, d in events if e == "stats"][0]
    assert stats["context_used"] == 125
    # completion_tokens в статистике — суммарно по turn'у (для «сколько
    # реально сгенерировано» и скорости), тут действительно 25 + 25 = 50.
    assert stats["completion_tokens"] == 50


def test_stats_without_any_context_size(client, ctrl_user, monkeypatch):
    """Ни /props, ни .env — процент неизвестен, показываем только tokens."""
    async def no_props():
        return None
    monkeypatch.setattr("app.routers.chat.get_server_context_size", no_props)
    monkeypatch.setattr("app.routers.chat.settings",
                        replace(settings, llm_context_size=0))
    chat_id = client.post("/api/chats", json={}).json()["id"]
    events = _send(client, chat_id, "привет")
    stats = [d for e, d in events if e == "stats"][0]
    assert stats["context_percent"] is None


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

# --- Переключатель «Размышления» (enable_thinking) ---

def test_enable_thinking_false_suppresses_reasoning(client, ctrl_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    r = client.post(f"/api/chats/{chat_id}/messages",
                    json={"content": "привет", "use_tools": False,
                          "enable_thinking": False})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert [d for e, d in events if e == "reasoning"] == []
    assert any(e == "content" for e, _ in events)


def test_enable_thinking_default_true(client, ctrl_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    events = _send(client, chat_id, "привет")
    assert len([d for e, d in events if e == "reasoning"]) > 0


# --- Пер-чатовые тумблеры (use_tools / enable_thinking) ---

def test_chat_toggles_persisted(client, ctrl_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    # дефолты: оба включены
    chat = [c for c in client.get("/api/chats").json() if c["id"] == chat_id][0]
    assert chat["use_tools"] == 1 and chat["enable_thinking"] == 1

    r = client.put(f"/api/chats/{chat_id}",
                   json={"use_tools": False, "enable_thinking": False})
    assert r.status_code == 200
    assert r.json()["use_tools"] == 0 and r.json()["enable_thinking"] == 0

    # состояние пережило «перезагрузку страницы» (новый GET списка)
    chat = [c for c in client.get("/api/chats").json() if c["id"] == chat_id][0]
    assert chat["use_tools"] == 0 and chat["enable_thinking"] == 0

    # частичное обновление другого поля не сбрасывает тумблеры
    client.put(f"/api/chats/{chat_id}", json={"title": "Переименован"})
    chat = [c for c in client.get("/api/chats").json() if c["id"] == chat_id][0]
    assert chat["use_tools"] == 0 and chat["enable_thinking"] == 0


def test_pdf_mode_persisted(client, ctrl_user):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    # дефолт — vision (PDF как картинка)
    chat = [c for c in client.get("/api/chats").json() if c["id"] == chat_id][0]
    assert chat["pdf_mode"] == "vision"

    assert client.put(f"/api/chats/{chat_id}",
                      json={"pdf_mode": "text"}).status_code == 200
    chat = [c for c in client.get("/api/chats").json() if c["id"] == chat_id][0]
    assert chat["pdf_mode"] == "text"

    # недопустимое значение отвергается
    assert client.put(f"/api/chats/{chat_id}",
                      json={"pdf_mode": "neon"}).status_code == 400


def test_stats_persisted_on_assistant_message(client, ctrl_user):
    """Статистика генерации сохраняется в сообщении и возвращается в истории —
    показывается компактно под ответом и переживает перезагрузку."""
    chat_id = client.post("/api/chats", json={}).json()["id"]
    _send(client, chat_id, "привет")
    messages = client.get(f"/api/chats/{chat_id}/messages").json()
    answer = [m for m in messages if m["role"] == "assistant"][-1]
    assert answer["stats"] is not None
    assert answer["stats"]["completion_tokens"] == 25
    assert answer["stats"]["tokens_per_second"] == 18.5
    user_msg = [m for m in messages if m["role"] == "user"][-1]
    assert user_msg["stats"] is None
