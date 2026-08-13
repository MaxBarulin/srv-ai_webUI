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


# --- Ключ не должен утекать через «Проверить» ---

def test_check_does_not_lend_the_key_to_another_host(client, edge_admin, monkeypatch):
    """Ключ мы наружу не отдаём — значит и «Проверить» не должно быть способом
    его вытащить: подставил свой адрес, посмотрел заголовок."""
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"data": []})

    client.put("/api/admin/llm", json={"base_url": PROVIDER, "model": "gpt-x",
                                       "api_key": "sk-secret"})
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))

    # свой адрес, ключ не передан — сохранённый подставлять нельзя
    client.post("/api/admin/llm/test",
                json={"base_url": "https://чужой.example/v1", "model": "m"})
    assert seen[-1] == ""

    # тот же адрес — ключ берём, иначе проверку сохранённого не сделать
    client.post("/api/admin/llm/test", json={"base_url": PROVIDER, "model": "gpt-x"})
    assert seen[-1] == "Bearer sk-secret"


def test_check_hints_missing_v1(client, edge_admin, monkeypatch):
    """Адрес без /v1 — самая частая опечатка, и 404 сам по себе о ней молчит."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    r = client.post("/api/admin/llm/test",
                    json={"base_url": "https://api.provider.example", "model": "m"}).json()
    assert r["ok"] is False
    assert "/v1" in r["error"]


@pytest.mark.parametrize("base", [PROVIDER, PROVIDER + "/"])
def test_trailing_slash_does_not_break_the_path(client, edge_admin, monkeypatch, base):
    """Адрес копируют по-разному; лишняя косая черта не должна давать
    //chat/completions."""
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _sse('{"choices":[{"delta":{"content":"ок"}}]}')

    users = client.get("/api/admin/llm").json()["users"]
    uid = [u["id"] for u in users if u["login"] == "edge-admin"][0]
    client.put("/api/admin/llm", json={"base_url": base, "model": "gpt-x",
                                       "user_ids": [uid]})
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    _ask(client)
    assert seen[-1] == PROVIDER + "/chat/completions", seen


# --- Параметры шаблона чата, которых внешний провайдер не знает ---

def test_template_kwargs_dropped_after_refusal(client, edge_admin, monkeypatch):
    """PRESERVE_THINKING включён по умолчанию, и chat_template_kwargs уходит в
    КАЖДОМ запросе. Провайдер вправе отвергнуть лишнее поле — тогда у
    отмеченных сотрудников не работало бы вообще ничего. Повторяем запрос без
    параметра и больше не шлём его этому серверу."""
    bodies: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        import json
        payload = json.loads(request.content)
        bodies.append(payload)
        if "chat_template_kwargs" in payload:
            return httpx.Response(400, json={"error": {"message":
                "Unrecognized request argument supplied: chat_template_kwargs"}})
        return _sse('{"choices":[{"delta":{"content":"ответ"}}]}')

    llm_module._TEMPLATE_KWARGS_UNSUPPORTED.clear()
    _enable(client, "edge-admin")
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))

    body = _ask(client)
    assert "ответ" in body, "повтор без параметра не сработал"
    assert "chat_template_kwargs" in bodies[0]
    assert "chat_template_kwargs" not in bodies[1]

    # Второй вопрос уходит сразу без параметра — лишнего круга больше нет
    bodies.clear()
    _ask(client)
    assert len(bodies) == 1 and "chat_template_kwargs" not in bodies[0]
    llm_module._TEMPLATE_KWARGS_UNSUPPORTED.clear()


def test_local_server_still_gets_template_kwargs(client, edge_admin, monkeypatch):
    """Отказ одного сервера не должен лишать параметра локальную llama.cpp:
    на ней он и держит режим размышлений."""
    bodies: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        import json
        bodies.append(json.loads(request.content))
        return _sse('{"choices":[{"delta":{"content":"ок"}}]}')

    llm_module._TEMPLATE_KWARGS_UNSUPPORTED.add(PROVIDER)
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    _ask(client)                       # админ не отмечен — идёт на .env
    assert "chat_template_kwargs" in bodies[0]
    llm_module._TEMPLATE_KWARGS_UNSUPPORTED.clear()


def test_other_400_is_not_mistaken_for_template_kwargs(client, edge_admin, monkeypatch):
    """Обычная четырёхсотая не должна молча повторяться и терять причину."""
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "model not found"}})

    llm_module._TEMPLATE_KWARGS_UNSUPPORTED.clear()
    _enable(client, "edge-admin")
    monkeypatch.setattr(llm_module, "_transport", httpx.MockTransport(handler))
    body = _ask(client)
    assert "model not found" in body
    assert len(calls) == 1, "повтор не нужен"


# --- Возврат на локальную модель: «снял, а он всё равно там» ---

def _where(client) -> str:
    """Куда фактически ушёл запрос этого сотрудника."""
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _sse('{"choices":[{"delta":{"content":"ок"}}]}')

    saved, llm_module._transport = llm_module._transport, httpx.MockTransport(handler)
    try:
        _ask(client)
    finally:
        llm_module._transport = saved
    return seen[-1]


def test_unchecking_applies_to_the_next_request(client, make_user):
    """Сняли отметку — следующий же вопрос идёт на локальную модель. Ничего
    не кэшируется на сессию: подключение выбирается на каждом запросе."""
    make_user("back-adm", PASS, role="admin")
    make_user("back-user", PASS)
    login_as(client, "back-adm", PASS)
    _enable(client, "back-user")

    client.cookies.clear()
    login_as(client, "back-user", PASS)          # сессию НЕ пересоздаём дальше
    assert _where(client).startswith(PROVIDER)

    # админ снимает отметку, а сотрудник продолжает работать в СВОЕЙ сессии:
    # запоминаем её куки и возвращаем после правки.
    cookies = dict(client.cookies)
    client.cookies.clear()
    login_as(client, "back-adm", PASS)
    client.put("/api/admin/llm", json={"base_url": PROVIDER, "model": "gpt-x",
                                       "user_ids": []})
    client.cookies.clear()
    for k, v in cookies.items():                 # возвращаем ТУ ЖЕ сессию
        client.cookies.set(k, v)
    assert _where(client).startswith(settings.llm_base_url)


def test_reset_returns_everyone_at_once(client, make_user):
    """«Сбросить к .env» возвращает всех сразу, не по одному."""
    make_user("back-adm2", PASS, role="admin")
    make_user("back-a", PASS)
    make_user("back-b", PASS)
    login_as(client, "back-adm2", PASS)
    users = client.get("/api/admin/llm").json()["users"]
    ids = [u["id"] for u in users if u["login"] in ("back-a", "back-b")]
    client.put("/api/admin/llm", json={"base_url": PROVIDER, "model": "gpt-x",
                                       "user_ids": ids})
    client.post("/api/admin/llm/reset")

    for login in ("back-a", "back-b"):
        client.cookies.clear()
        login_as(client, login, PASS)
        assert _where(client).startswith(settings.llm_base_url), login


def test_blocking_clears_the_mark(client, make_user):
    """Заблокированного в списке подключения нет — отметка снимается сразу,
    иначе после разблокировки он молча вернулся бы на внешний сервер."""
    make_user("back-adm3", PASS, role="admin")
    blocked = make_user("back-blocked", PASS)
    login_as(client, "back-adm3", PASS)
    _enable(client, "back-blocked")
    client.post(f"/api/admin/users/{blocked}/active", json={"is_active": False})
    client.post(f"/api/admin/users/{blocked}/active", json={"is_active": True})

    assert client.get("/api/admin/llm").json()["enabled_count"] == 0
    client.cookies.clear()
    login_as(client, "back-blocked", PASS)
    assert _where(client).startswith(settings.llm_base_url)


def test_forgotten_argument_falls_back_to_local(client):
    """Свойство, на котором всё держится: подключение по умолчанию — из .env.
    Забытый аргумент где угодно в коде уводит на локальную модель, а не на
    внешний сервер. Ошибиться в опасную сторону нельзя."""
    llm_module.set_connection({"base_url": PROVIDER, "model": "gpt-x",
                               "api_key": "sk-secret"})
    assert llm_module.connection()["base_url"] == settings.llm_base_url
    assert llm_module.connection(None)["base_url"] == settings.llm_base_url
    assert llm_module.connection({})["base_url"] == settings.llm_base_url
    assert llm_module.connection({"login": "нет-поля"})["base_url"] == settings.llm_base_url
    assert llm_module.connection({"llm_custom": 0})["base_url"] == settings.llm_base_url
    assert llm_module.connection({"llm_custom": False})["base_url"] == settings.llm_base_url
    # и ключ провайдера неотмеченному не достаётся
    assert llm_module.connection({"llm_custom": 0})["api_key"] == settings.llm_api_key
    # клиент без подключения — тоже локальный
    request = llm_module.make_client().build_request("GET", "/models")
    assert str(request.url).startswith(settings.llm_base_url)


def test_default_install_is_local_without_key(client, make_user):
    """Чистая установка: подключение не настроено, ключа нет, отмеченных нет."""
    make_user("back-adm4", PASS, role="admin")
    login_as(client, "back-adm4", PASS)
    view = client.get("/api/admin/llm").json()
    assert view["custom"] is False
    assert view["enabled_count"] == 0
    assert view["has_key"] is bool(settings.llm_api_key)
    assert all(u["enabled"] is False for u in view["users"])
