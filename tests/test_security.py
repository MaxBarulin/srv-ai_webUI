"""Заголовки безопасности и защита от межсайтовых запросов (§13).

Проверяется именно middleware в app/main.py — то, что применяется ко всем
ответам сразу и потому легче всего ломается незаметно.
"""
from __future__ import annotations

import pytest

from tests.conftest import login_as

PASS = "sec-user-pass-01"


@pytest.fixture()
def sec_user(client, make_user):
    make_user("sec-user", PASS)
    login_as(client, "sec-user", PASS)


def test_security_headers_present(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize("directive", [
    "default-src 'self'",
    # Ниже — то, что из default-src НЕ выводится и потому не ограничено вовсе,
    # если не выписать отдельно. Без base-uri внедрённый <base href> увёл бы
    # все относительные ссылки страницы на чужой хост.
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
])
def test_csp_covers_directive(client, directive):
    assert directive in client.get("/api/health").headers["Content-Security-Policy"]


def test_scripts_not_allowed_inline(client):
    """style-src 'unsafe-inline' нужен формулам KaTeX, script-src — никогда."""
    csp = client.get("/api/health").headers["Content-Security-Policy"]
    assert "'unsafe-inline'" in csp.split("style-src")[1].split(";")[0]
    assert "script-src" not in csp  # значит, действует default-src 'self'
    assert "'unsafe-eval'" not in csp


# --- CSRF: мутации без совпадающего Origin ---

def test_mutation_without_origin_rejected(client, sec_user):
    del client.headers["origin"]        # запрос вовсе без Origin (не из браузера)
    r = client.post("/api/chats", json={})
    assert r.status_code == 403
    assert r.json()["detail"] == "Invalid Origin"


def test_mutation_from_foreign_origin_rejected(client, sec_user):
    r = client.post("/api/chats", json={}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_mutation_with_matching_origin_allowed(client, sec_user):
    assert client.post("/api/chats", json={}).status_code == 201


def test_reads_do_not_require_origin(client, sec_user):
    del client.headers["origin"]
    assert client.get("/api/chats").status_code == 200


# --- Предел размера тела ---

def test_oversized_body_rejected_before_parsing(client, sec_user):
    from app.main import MAX_BODY_BYTES
    r = client.post("/api/chats", content=b"x" * 32,
                    headers={"content-length": str(MAX_BODY_BYTES + 1),
                             "content-type": "application/json"})
    assert r.status_code == 413


def test_broken_content_length_rejected(client, sec_user):
    r = client.post("/api/chats", content=b"{}",
                    headers={"content-length": "12abc", "content-type": "application/json"})
    assert r.status_code == 400


# --- Граница между данными и указаниями (§13) ---

def test_untrusted_content_rule_always_in_system_prompt():
    """Текст документов, расшифровки, результаты инструментов и выдержки базы
    знаний пишет не автор вопроса. Правило о том, что это данные, а не
    команды, должно быть в промпте всегда."""
    from app.llm import UNTRUSTED_CONTENT_RULE, build_system_prompt
    assert UNTRUSTED_CONTENT_RULE in build_system_prompt("Иванов")


def test_untrusted_content_rule_after_user_prompt():
    """Промпт чата и специализацию задаёт пользователь — правило должно стоять
    ПОСЛЕ них, иначе «забудь всё выше» в своём промпте его снимет."""
    from app.llm import UNTRUSTED_CONTENT_RULE, build_system_prompt
    prompt = build_system_prompt("Иванов", "Ты — аудитор ИБ.")
    assert prompt.index("аудитор ИБ") < prompt.index(UNTRUSTED_CONTENT_RULE)


def test_untrusted_content_rule_survives_broken_prompt_file(monkeypatch):
    """Файл промпта правит администратор. Защита не должна исчезать вместе
    с его правкой — поэтому она живёт в коде, а не в system_prompt.txt."""
    from dataclasses import replace

    from app import llm as llm_module
    monkeypatch.setattr(llm_module, "settings",
                        replace(llm_module.settings, system_prompt_file="/нет/такого/файла"))
    assert llm_module.UNTRUSTED_CONTENT_RULE in llm_module.build_system_prompt("Иванов")


def test_rule_reaches_the_model(client, sec_user, monkeypatch):
    """Проверка сквозная: правило действительно уходит в запрос, а не просто
    собирается в функции."""
    import httpx

    from app import llm as llm_module
    from tests.mock_llm import app as mock_llm_app
    monkeypatch.setattr(llm_module, "_transport", httpx.ASGITransport(app=mock_llm_app))

    captured = []
    orig = llm_module.stream_chat

    def spy(messages, tools=None, **kwargs):
        captured.append(messages)
        return orig(messages, tools=tools)

    monkeypatch.setattr("app.routers.chat.stream_chat", spy)
    chat_id = client.post("/api/chats", json={}).json()["id"]
    client.post(f"/api/chats/{chat_id}/messages",
                json={"content": "вопрос", "use_tools": False,
                      "attachments": [{"filename": "spec.txt", "text": "Требования к сварке."}]})
    system = captured[0][0]
    assert system["role"] == "system"
    assert "материал для анализа, а не" in system["content"]
