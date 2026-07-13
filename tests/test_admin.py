"""Admin users API tests: access control, create/block/reset-password."""
from __future__ import annotations

import sqlite3

from app.config import settings
from tests.conftest import login_as

ADMIN_PASS = "admin-secret-pass"
USER_PASS = "user-secret-pass!"


def _cleanup_user(login: str) -> None:
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute(
            "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE login = ?)", (login,))
        conn.execute(
            "DELETE FROM audit_log WHERE user_id IN (SELECT id FROM users WHERE login = ?)", (login,))
        conn.execute("DELETE FROM users WHERE login = ?", (login,))
        conn.commit()
    finally:
        conn.close()


def test_admin_endpoints_require_admin_role(client, make_user):
    make_user("plain-user", USER_PASS, role="user")
    login_as(client, "plain-user", USER_PASS)
    assert client.get("/api/admin/users").status_code == 403
    assert client.post("/api/admin/users", json={"login": "x", "password": "0123456789"}).status_code == 403


def test_admin_endpoints_require_session(client):
    assert client.get("/api/admin/users").status_code == 401


def test_admin_can_list_and_create_users(client, make_user):
    make_user("the-admin", ADMIN_PASS, role="admin")
    login_as(client, "the-admin", ADMIN_PASS)

    r = client.post("/api/admin/users", json={
        "login": "new-employee", "password": "employee-pass1",
        "display_name": "Новый Сотрудник", "role": "user",
    })
    try:
        assert r.status_code == 201
        created = r.json()
        assert created["login"] == "new-employee"
        assert created["display_name"] == "Новый Сотрудник"
        assert created["is_active"] == 1

        logins = [u["login"] for u in client.get("/api/admin/users").json()]
        assert "new-employee" in logins

        # duplicate login rejected
        r2 = client.post("/api/admin/users", json={"login": "new-employee", "password": "employee-pass1"})
        assert r2.status_code == 409

        # short password rejected
        r3 = client.post("/api/admin/users", json={"login": "another", "password": "short"})
        assert r3.status_code == 400

        # bad role rejected
        r4 = client.post("/api/admin/users", json={"login": "another", "password": "0123456789", "role": "root"})
        assert r4.status_code == 400
    finally:
        _cleanup_user("new-employee")


def test_block_user_kills_sessions(client, make_user):
    make_user("the-admin2", ADMIN_PASS, role="admin")
    victim_id = make_user("victim", USER_PASS)

    login_as(client, "victim", USER_PASS)
    victim_cookies = dict(client.cookies)
    assert client.get("/api/me").status_code == 200

    client.cookies.clear()
    login_as(client, "the-admin2", ADMIN_PASS)
    r = client.post(f"/api/admin/users/{victim_id}/active", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] == 0

    # victim session is gone, and login is rejected
    client.cookies.clear()
    client.cookies.update(victim_cookies)
    assert client.get("/api/me").status_code == 401
    assert login_as(client, "victim", USER_PASS).status_code == 403

    # unblock — login works again
    client.cookies.clear()
    login_as(client, "the-admin2", ADMIN_PASS)
    r = client.post(f"/api/admin/users/{victim_id}/active", json={"is_active": True})
    assert r.json()["is_active"] == 1
    client.cookies.clear()
    assert login_as(client, "victim", USER_PASS).status_code == 200


def test_admin_cannot_block_self(client, make_user):
    admin_id = make_user("self-admin", ADMIN_PASS, role="admin")
    login_as(client, "self-admin", ADMIN_PASS)
    r = client.post(f"/api/admin/users/{admin_id}/active", json={"is_active": False})
    assert r.status_code == 400


def test_reset_password(client, make_user):
    make_user("the-admin3", ADMIN_PASS, role="admin")
    target_id = make_user("forgetful", USER_PASS)

    login_as(client, "the-admin3", ADMIN_PASS)
    r = client.post(f"/api/admin/users/{target_id}/password", json={"new_password": "brand-new-pass1"})
    assert r.status_code == 200

    client.cookies.clear()
    assert login_as(client, "forgetful", USER_PASS).status_code == 401
    assert login_as(client, "forgetful", "brand-new-pass1").status_code == 200

    # nonexistent user → 404
    client.cookies.clear()
    login_as(client, "the-admin3", ADMIN_PASS)
    r = client.post("/api/admin/users/999999/password", json={"new_password": "brand-new-pass1"})
    assert r.status_code == 404


def test_audit_log_written_for_admin_actions(client, make_user):
    admin_id = make_user("audit-admin", ADMIN_PASS, role="admin")
    login_as(client, "audit-admin", ADMIN_PASS)
    r = client.post("/api/admin/users", json={"login": "audited-user", "password": "0123456789"})
    assert r.status_code == 201
    try:
        conn = sqlite3.connect(settings.db_path)
        try:
            rows = conn.execute(
                "SELECT action, object_id FROM audit_log WHERE user_id = ? ORDER BY id", (admin_id,)
            ).fetchall()
        finally:
            conn.close()
        actions = [a for a, _ in rows]
        assert "login" in actions
        assert "user_created" in actions
    finally:
        _cleanup_user("audited-user")
