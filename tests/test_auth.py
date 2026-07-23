"""Auth tests: login/logout, session protection, rate limit, password change."""
from __future__ import annotations

from tests.conftest import login_as

PASSWORD = "correct-horse-battery"


def test_login_success_sets_cookie(client, make_user):
    make_user("ivanov", PASSWORD)
    r = login_as(client, "ivanov", PASSWORD)
    assert r.status_code == 200
    assert r.json()["login"] == "ivanov"
    assert "session" in r.cookies


def test_login_wrong_password(client, make_user):
    make_user("petrov", PASSWORD)
    r = login_as(client, "petrov", "wrong-password-123")
    assert r.status_code == 401


def test_login_unknown_user(client):
    r = login_as(client, "no-such-user", "whatever-pass")
    assert r.status_code == 401


def test_login_blocked_user(client, make_user):
    make_user("blocked", PASSWORD, is_active=False)
    r = login_as(client, "blocked", PASSWORD)
    assert r.status_code == 403


def test_api_requires_session(client):
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/logout").status_code == 401


def test_me_returns_user(client, make_user):
    make_user("sidorov", PASSWORD, display_name="Сидоров С.С.")
    login_as(client, "sidorov", PASSWORD)
    r = client.get("/api/me")
    assert r.status_code == 200
    body = r.json()
    assert body["login"] == "sidorov"
    assert body["display_name"] == "Сидоров С.С."
    assert body["role"] == "user"


def test_logout_invalidates_session(client, make_user):
    make_user("logouter", PASSWORD)
    login_as(client, "logouter", PASSWORD)
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_rate_limit_after_five_failures(client, make_user):
    make_user("bruteforced", PASSWORD)
    for _ in range(5):
        assert login_as(client, "bruteforced", "bad-password-xx").status_code == 401
    # 6th attempt — rate limited even with the correct password
    assert login_as(client, "bruteforced", PASSWORD).status_code == 429


def test_successful_login_resets_rate_limit(client, make_user):
    make_user("resetter", PASSWORD)
    for _ in range(4):
        login_as(client, "resetter", "bad-password-xx")
    assert login_as(client, "resetter", PASSWORD).status_code == 200
    # counter reset — failures start from zero again
    assert login_as(client, "resetter", "bad-password-xx").status_code == 401


def test_change_password(client, make_user):
    make_user("changer", PASSWORD)
    login_as(client, "changer", PASSWORD)

    r = client.post("/api/me/password",
                    json={"current_password": "wrong-one-123", "new_password": "new-password-123"})
    assert r.status_code == 403

    r = client.post("/api/me/password",
                    json={"current_password": PASSWORD, "new_password": "short"})
    assert r.status_code == 400

    r = client.post("/api/me/password",
                    json={"current_password": PASSWORD, "new_password": "new-password-123"})
    assert r.status_code == 200

    client.post("/api/logout")
    assert login_as(client, "changer", PASSWORD).status_code == 401
    # previous failure counts against the limiter; successful login clears it
    assert login_as(client, "changer", "new-password-123").status_code == 200


def test_csrf_origin_rejected(client, make_user):
    make_user("csrf-user", PASSWORD)
    r = client.post("/api/login",
                    json={"login": "csrf-user", "password": PASSWORD},
                    headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_csrf_missing_origin_rejected(make_user):
    # Мутация вообще без заголовка Origin — тоже отклоняется (raw-клиент без дефолта)
    from fastapi.testclient import TestClient
    from app.main import app

    make_user("no-origin-user", PASSWORD)
    with TestClient(app) as raw:
        r = raw.post("/api/login", json={"login": "no-origin-user", "password": PASSWORD})
        assert r.status_code == 403


def test_get_requests_need_no_origin(client, make_user):
    # GET (немутация) работает без Origin — не ломаем чтение
    make_user("reader", PASSWORD)
    login_as(client, "reader", PASSWORD)
    r = client.get("/api/me", headers={"origin": ""})
    assert r.status_code == 200


def test_oversized_body_rejected(client, monkeypatch):
    # тело больше лимита отклоняется до парсинга (защита от OOM/переполнения диска)
    import app.main as main
    monkeypatch.setattr(main, "MAX_BODY_BYTES", 50)
    r = client.post("/api/login", json={"login": "x" * 100, "password": "y" * 100})
    assert r.status_code == 413


def test_password_change_invalidates_other_sessions(client, make_user):
    from fastapi.testclient import TestClient
    from app.main import app

    make_user("multi", PASSWORD)
    login_as(client, "multi", PASSWORD)                       # сессия A (текущая)
    with TestClient(app) as other:
        other.headers["origin"] = "http://testserver"        # мутации требуют Origin
        login_as(other, "multi", PASSWORD)                   # сессия B (др. устройство)
        assert other.get("/api/me").status_code == 200

        r = client.post("/api/me/password",
                        json={"current_password": PASSWORD, "new_password": "brand-new-pass-1"})
        assert r.status_code == 200

        assert client.get("/api/me").status_code == 200      # A — жива
        assert other.get("/api/me").status_code == 401       # B — выкинута
