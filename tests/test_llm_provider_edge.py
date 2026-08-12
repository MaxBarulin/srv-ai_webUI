"""Внешний провайдер: неожиданные ответы и попытки обойти круг включённых.

Отдельный набор от test_llm_connection.py: там — что фича работает, здесь —
что она не ломается и не расширяет круг сама по себе.
"""
from __future__ import annotations

import sqlite3

import httpx
import pytest

from app import llm as llm_module
from app.config import settings
from tests.conftest import login_as

PASS = "edge-pass-2026"
PROVIDER = "https://api.provider.example/v1"


@pytest.fixture()
def edge_admin(client, make_user):
    make_user("edge-admin", PASS, role="admin")
    login_as(client, "edge-admin", PASS)


@pytest.fixture(autouse=True)
def _clean():
    yield
    llm_module.set_connection({})
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'llm_%'")
        conn.execute("UPDATE users SET llm_custom = 0")
        conn.commit()
    finally:
        conn.close()


def _sse(*chunks: str) -> httpx.Response:
    body = "".join(f"data: {c}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _enable(client, login: str) -> int:
    users = client.get("/api/admin/llm").json()["users"]
    uid = [u["id"] for u in users if u["login"] == login][0]
    client.put("/api/admin/llm", json={"base_url": PROVIDER, "model": "gpt-x",
                                       "user_ids": [uid]})
    return uid


def _ask(client) -> str:
    chat_id = client.post("/api/chats", json={}).json()["id"]
    with client.stream("POST", f"/api/chats/{chat_id}/messages",
                       json={"content": "вопрос", "use_tools": False}) as r:
        return "".join(r.iter_text())


# --- Круг включённых не расширяется сам ---

def test_user_cannot_switch_himself(client, make_user, edge_admin):
    """Отметку ставит только администратор: сотрудник не должен уметь
    перевести себя на внешнего провайдера через свои же настройки."""
    make_user("edge-self", PASS)
    _enable(client, "edge-admin")          # админ включает себя, не сотрудника
    client.cookies.clear()
    login_as(client, "edge-self", PASS)

    assert client.post("/api/me/settings", json={"llm_custom": True}).status_code == 200
    assert client.get("/api/me").json()["llm_custom"] is False
    assert client.put("/api/admin/llm", json={"base_url": PROVIDER,
                                              "model": "m"}).status_code == 403


def test_unknown_user_ids_are_ignored(client, edge_admin, make_user):
    """Список приходит из интерфейса; посторонний идентификатор не должен ни
    ломать сохранение, ни кого-то включать."""
    make_user("edge-real", PASS)
    users = client.get("/api/admin/llm").json()["users"]
    real = [u["id"] for u in users if u["login"] == "edge-real"][0]
    r = client.put("/api/admin/llm", json={"base_url": PROVIDER, "model": "gpt-x",
                                           "user_ids": [real, 999_999, -1]})
    assert r.status_code == 200
    assert r.json()["enabled_count"] == 1


def test_blocked_user_loses_the_mark_on_next_save(client, edge_admin, make_user):
    """Заблокированного в списке нет — и следующее сохранение снимает с него
    отметку, а не оставляет её невидимой."""
    user_id = make_user("edge-blocked", PASS)
    _enable(client, "edge-blocked")
    client.post(f"/api/admin/users/{user_id}/active", json={"is_active": False})
    view = client.get("/api/admin/llm").json()
    assert all(u["login"] != "edge-blocked" for u in view["users"])
    client.put("/api/admin/llm", json={"base_url": PROVIDER, "model": "gpt-x",
                                       "user_ids": []})
    conn = sqlite3.connect(settings.db_path)
    try:
        left = conn.execute("SELECT llm_custom FROM users WHERE id = ?",
                            (user_id,)).fetchone()[0]
    finally:
        conn.close()
    assert left == 0


def test_new_user_is_not_switched(client, edge_admin, make_user):
    """Заведённый после переключения сотрудник начинает с .env."""
    _enable(client, "edge-admin")
    make_user("edge-fresh", PASS)
    view = client.get("/api/admin/llm").json()
    fresh = [u for u in view["users"] if u["login"] == "edge-fresh"][0]
    assert fresh["enabled"] is False


# --- Неожиданные ответы провайдера ---

@pytest.mark.parametrize("response,expect", [
    (httpx.Response(400, json={"error": {"message": "Unrecognized request argument: "
                                                    "chat_template_kwargs"}}),
     "chat_template_kwargs"),
    (httpx.Response(429, json={"error": {"message": "Rate limit reached"}}), "429"),
    (httpx.Response(500, text="<html><body>502 Bad Gateway</body></html>"), "500"),
    (httpx.Response(403, json={"error": "region not supported"}), "403"),
])
def test_provider_errors_reach_the_user(client, edge_admin, monkeypatch, response, expect):
    """Что бы провайдер ни ответил, в чате должна появиться причина, а не
    молчание."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return response

    _enable(client, "edge-admin")
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    body = _ask(client)
    assert "event: error" in body
    assert expect in body


def test_openai_context_overflow_is_translated(client, edge_admin, monkeypatch):
    """Формулировка переполнения контекста у провайдеров своя — плашка должна
    остаться человеческой, а не показывать английский JSON."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {
            "message": "This model's maximum context length is 128000 tokens. "
                       "However, your messages resulted in 130000 tokens.",
            "code": "context_length_exceeded"}})

    _enable(client, "edge-admin")
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    body = _ask(client)
    assert "не поместился в контекст" in body


def test_plain_json_instead_of_stream(client, edge_admin, monkeypatch):
    """Провайдер проигнорировал stream и ответил обычным JSON: пользователь
    должен получить причину, а не пустоту вместо ответа."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ответ"}}]},
                              headers={"content-type": "application/json"})

    _enable(client, "edge-admin")
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    body = _ask(client)
    assert "event: error" in body
    assert "в неизвестном формате" in body


def test_broken_sse_line_does_not_kill_the_turn(client, edge_admin, monkeypatch):
    """Битый кусок в потоке пропускаем: остальной ответ должен дойти."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return _sse("{не json}", '{"choices":[{"delta":{"content":"живой текст"}}]}')

    _enable(client, "edge-admin")
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    body = _ask(client)
    assert "живой текст" in body


def test_reasoning_field_of_other_providers(client, edge_admin, monkeypatch):
    """Размышления приходят в своём поле у каждого провайдера — читаем все
    известные, иначе у отмеченных пропал бы блок «Размышления»."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return _sse('{"choices":[{"delta":{"reasoning":"думаю"}}]}',
                    '{"choices":[{"delta":{"content":"итог"}}]}')

    _enable(client, "edge-admin")
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    body = _ask(client)
    assert "event: reasoning" in body and "думаю" in body


# --- Разные подключения одновременно ---

def test_two_users_go_to_their_own_servers(client, make_user, monkeypatch):
    """Отмеченный и неотмеченный работают одновременно: адреса не должны
    перепутаться (подключение передаётся ходу, а не хранится на процесс)."""
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _sse('{"choices":[{"delta":{"content":"ок"}}]}')

    make_user("edge-adm2", PASS, role="admin")
    make_user("edge-plain", PASS)
    login_as(client, "edge-adm2", PASS)
    _enable(client, "edge-plain")
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))

    _ask(client)                                  # админ не отмечен
    assert seen[-1].startswith(settings.llm_base_url)
    client.cookies.clear()
    login_as(client, "edge-plain", PASS)
    _ask(client)                                  # отмеченный
    assert seen[-1].startswith(PROVIDER)
    client.cookies.clear()
    login_as(client, "edge-adm2", PASS)
    _ask(client)                                  # и обратно
    assert seen[-1].startswith(settings.llm_base_url)


def test_context_size_is_per_connection(client, make_user, monkeypatch):
    """У отмеченного счётчик контекста считается от размера ЕГО подключения."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/props"):
            return httpx.Response(200, json={"default_generation_settings": {"n_ctx": 8192}})
        return _sse('{"choices":[{"delta":{"content":"ок"}}],'
                    '"usage":{"prompt_tokens":10,"completion_tokens":5}}')

    make_user("edge-adm3", PASS, role="admin")
    make_user("edge-ctx", PASS)
    login_as(client, "edge-adm3", PASS)
    users = client.get("/api/admin/llm").json()["users"]
    uid = [u["id"] for u in users if u["login"] == "edge-ctx"][0]
    client.put("/api/admin/llm", json={"base_url": PROVIDER, "model": "gpt-x",
                                       "context_size": 128000, "user_ids": [uid]})
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    llm_module._reset_server_ctx_cache_for_tests()

    client.cookies.clear()
    login_as(client, "edge-ctx", PASS)
    assert '"context_size": 128000' in _ask(client)


def test_image_transcription_follows_the_user(client, make_user, monkeypatch):
    """Расшифровка картинок идёт тем же подключением, что и сам ответ: иначе
    чертёж отмеченного сотрудника ушёл бы на один сервер, а его пересказ — на
    другой, и никто бы этого не заметил."""
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _sse('{"choices":[{"delta":{"content":"на чертеже вал"}}]}')

    make_user("edge-adm4", PASS, role="admin")
    make_user("edge-img", PASS)
    login_as(client, "edge-adm4", PASS)
    _enable(client, "edge-img")
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))

    client.cookies.clear()
    login_as(client, "edge-img", PASS)
    chat_id = client.post("/api/chats", json={}).json()["id"]
    png = ("data:image/png;base64,"
           "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    with client.stream("POST", f"/api/chats/{chat_id}/messages",
                       json={"content": "что на чертеже?", "use_tools": False,
                             "attachments": [{"filename": "чертёж.png", "images": [png]}]}) as r:
        "".join(r.iter_text())

    assert seen, "расшифровка не пошла в модель"
    assert all(url.startswith(PROVIDER) for url in seen), seen
