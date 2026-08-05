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
