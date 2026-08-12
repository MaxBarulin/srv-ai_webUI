"""Подключение к модели, настраиваемое администратором.

.env остаётся основой: пустая настройка означает «как в .env», поэтому чистая
установка ведёт себя ровно как раньше. Ключ доступа наружу не отдаётся ни при
каких условиях — это главное, что здесь проверяется.
"""
from __future__ import annotations

import httpx
import pytest

from app import llm as llm_module
from app.config import settings
from tests.conftest import login_as

PASS = "conn-pass-2026"


@pytest.fixture()
def conn_admin(client, make_user):
    make_user("conn-admin", PASS, role="admin")
    login_as(client, "conn-admin", PASS)


@pytest.fixture(autouse=True)
def _restore_connection():
    """База в наборе тестов общая, а подключение поднимается из неё при старте
    приложения — чистим и её, и настройки в памяти, иначе сохранённый адрес
    утечёт в соседние тесты."""
    import sqlite3

    yield
    llm_module.set_connection({})
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'llm_%'")
        conn.commit()
    finally:
        conn.close()


def test_defaults_come_from_env(client, conn_admin):
    r = client.get("/api/admin/llm").json()
    assert r["base_url"] == settings.llm_base_url
    assert r["model"] == settings.llm_model
    assert r["custom"] is False
    assert r["env"]["base_url"] == settings.llm_base_url


def test_key_never_returned(client, conn_admin):
    """Ключ провайдера не должен уходить в браузер: наружу — только признак."""
    client.put("/api/admin/llm", json={"base_url": "https://api.example.com/v1",
                                       "model": "gpt-x", "api_key": "sk-secret-123"})
    body = client.get("/api/admin/llm").text
    assert "sk-secret-123" not in body
    assert client.get("/api/admin/llm").json()["has_key"] is True


def test_key_not_in_audit(client, conn_admin):
    client.put("/api/admin/llm", json={"base_url": "https://api.example.com/v1",
                                       "model": "gpt-x", "api_key": "sk-secret-123"})
    entries = client.get("/api/admin/audit").json()["items"]
    changed = [e for e in entries if e["action"] == "llm_connection_changed"]
    assert changed, "смена подключения должна попадать в аудит"
    assert "sk-secret-123" not in changed[0]["details"]
    assert "api.example.com" in changed[0]["details"]


def test_saved_connection_applies_to_requests(client, conn_admin):
    client.put("/api/admin/llm", json={"base_url": "https://api.example.com/v1",
                                       "model": "gpt-x", "api_key": "k",
                                       "context_size": 32768})
    conn = llm_module.connection()
    assert conn["base_url"] == "https://api.example.com/v1"
    assert conn["model"] == "gpt-x"
    assert conn["context_size"] == 32768
    assert conn["custom"] is True
    # и клиент собирается уже с новым адресом и заголовком
    request = llm_module.make_client().build_request("GET", "/models")
    assert str(request.url).startswith("https://api.example.com/v1")
    assert request.headers["Authorization"] == "Bearer k"


def test_empty_key_keeps_previous(client, conn_admin):
    """Пустое поле в форме значит «не трогать ключ», а не «стереть»."""
    client.put("/api/admin/llm", json={"base_url": "https://api.example.com/v1",
                                       "model": "gpt-x", "api_key": "sk-first"})
    client.put("/api/admin/llm", json={"base_url": "https://api.example.com/v1",
                                       "model": "gpt-y"})          # api_key не передан
    assert llm_module.connection()["api_key"] == "sk-first"
    assert client.get("/api/admin/llm").json()["has_key"] is True


def test_key_can_be_cleared_explicitly(client, conn_admin):
    client.put("/api/admin/llm", json={"base_url": "https://api.example.com/v1",
                                       "model": "gpt-x", "api_key": "sk-first"})
    client.put("/api/admin/llm", json={"base_url": "https://api.example.com/v1",
                                       "model": "gpt-x", "api_key": ""})
    assert llm_module.connection()["api_key"] == ""
    assert client.get("/api/admin/llm").json()["has_key"] is False


def test_reset_returns_to_env(client, conn_admin):
    client.put("/api/admin/llm", json={"base_url": "https://api.example.com/v1",
                                       "model": "gpt-x", "api_key": "sk-secret"})
    r = client.post("/api/admin/llm/reset").json()
    assert r["base_url"] == settings.llm_base_url
    assert r["custom"] is False
    assert r["has_key"] == bool(settings.llm_api_key)
    assert llm_module.connection()["api_key"] == settings.llm_api_key


def test_context_cache_reset_on_switch(client, conn_admin, monkeypatch):
    """n_ctx относится к конкретному серверу: после переключения кэш обязан
    сброситься, иначе счётчик контекста считает проценты от чужой модели."""
    llm_module._server_ctx_cache = 8192
    llm_module._server_ctx_probed = True
    client.put("/api/admin/llm", json={"base_url": "https://api.example.com/v1",
                                       "model": "gpt-x"})
    assert llm_module._server_ctx_probed is False
    assert llm_module._server_ctx_cache is None


@pytest.mark.parametrize("payload,detail", [
    ({"base_url": "ftp://хост/v1", "model": "m"}, "http"),
    ({"base_url": "просто текст", "model": "m"}, "http"),
    ({"base_url": "https://a/v1", "model": "  "}, "модель"),
    ({"base_url": "https://a/v1", "model": "m", "context_size": -1}, "контекст"),
])
def test_bad_input_rejected(client, conn_admin, payload, detail):
    r = client.put("/api/admin/llm", json=payload)
    assert r.status_code == 400
    assert detail.lower() in r.json()["detail"].lower()


def test_only_admin(client, make_user):
    make_user("conn-user", PASS)
    login_as(client, "conn-user", PASS)
    assert client.get("/api/admin/llm").status_code == 403
    assert client.put("/api/admin/llm", json={"base_url": "https://a/v1",
                                              "model": "m"}).status_code == 403


# --- Проверка соединения ---

def test_check_reports_models(client, conn_admin, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "gpt-x"}, {"id": "gpt-y"}]})

    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    r = client.post("/api/admin/llm/test",
                    json={"base_url": "https://api.example.com/v1", "model": "gpt-x"}).json()
    assert r["ok"] is True and r["model_found"] is True
    assert r["models"] == ["gpt-x", "gpt-y"]


def test_check_notices_missing_model(client, conn_admin, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "gpt-y"}]})

    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    r = client.post("/api/admin/llm/test",
                    json={"base_url": "https://api.example.com/v1", "model": "нет-такой"}).json()
    assert r["ok"] is True and r["model_found"] is False


def test_check_reports_unreachable(client, conn_admin, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет маршрута")

    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    r = client.post("/api/admin/llm/test",
                    json={"base_url": "https://api.example.com/v1", "model": "m"}).json()
    assert r["ok"] is False
    assert "соединение" in r["error"].lower()


def test_check_reports_rejected_key(client, conn_admin, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    r = client.post("/api/admin/llm/test",
                    json={"base_url": "https://api.example.com/v1", "model": "m",
                          "api_key": "wrong-key"}).json()
    assert r["ok"] is False
    assert "ключ" in r["error"].lower()


def test_cyrillic_key_rejected(client, conn_admin):
    """Ключ уходит в HTTP-заголовок: кириллица в нём роняет запрос уже в httpx,
    и администратор получил бы 500 вместо объяснения."""
    r = client.put("/api/admin/llm", json={"base_url": "https://api.example.com/v1",
                                           "model": "m", "api_key": "секретный-ключ"})
    assert r.status_code == 400
    assert "кирилл" in r.json()["detail"].lower()


def test_check_does_not_save(client, conn_admin, monkeypatch):
    """Проверка — это только проверка: подключение остаётся прежним."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    client.post("/api/admin/llm/test",
                json={"base_url": "https://api.example.com/v1", "model": "gpt-x"})
    assert llm_module.connection()["base_url"] == settings.llm_base_url


# --- Что видит пользователь, когда сервер модели не отвечает ---

def test_network_failure_is_explained_in_russian(client, conn_admin, monkeypatch):
    """Сообщение читает нормировщик в цехе, а не разработчик: класс исключения
    httpx ему ничего не говорит."""
    from app.llm import _network_error_text

    text = _network_error_text(httpx.ConnectError("All connection attempts failed"))
    assert "соединение не установлено" in text.lower()
    assert "администратору" in text
    assert "connection" not in text.lower()

    slow = _network_error_text(httpx.ReadTimeout("timed out"))
    assert "вовремя" in slow


def test_dead_connection_reports_error_to_chat(client, conn_admin, monkeypatch):
    """Переключились на недоступный адрес — чат обязан сказать об этом, а не
    молча проглотить вопрос."""
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет маршрута")

    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    client.put("/api/admin/llm", json={"base_url": "https://api.provider.example/v1",
                                       "model": "gpt-x"})
    chat_id = client.post("/api/chats", json={}).json()["id"]
    with client.stream("POST", f"/api/chats/{chat_id}/messages",
                       json={"content": "привет", "use_tools": False}) as r:
        body = "".join(r.iter_text())
    assert "event: error" in body
    assert "Сервер модели не отвечает" in body
