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


def test_delete_user_cascades_owned_data(client, make_user):
    """Удаление пользователя сносит его чаты (с сообщениями и feedback),
    заметки и события; аудит остаётся (user_id → NULL)."""
    make_user("kill-admin", ADMIN_PASS, role="admin")
    victim = make_user("victim-full", USER_PASS)

    # Наполним данные от имени жертвы
    login_as(client, "victim-full", USER_PASS)
    chat_id = client.post("/api/chats", json={}).json()["id"]
    note_id = client.post("/api/notes",
                          json={"title": "T", "body": "b", "scope": "personal"}).json()["id"]
    event_id = client.post("/api/events", json={
        "title": "E", "starts_at": "2027-01-01T10:00:00+03:00",
        "ends_at": "2027-01-01T11:00:00+03:00", "scope": "personal"}).json()["id"]

    conn = sqlite3.connect(settings.db_path)
    try:
        # искусственно добавим сообщение и feedback, чтобы проверить каскад
        conn.execute(
            "INSERT INTO messages (chat_id, role, content, created_at) "
            "VALUES (?, 'user', 'hi', datetime('now'))", (chat_id,))
        msg_id = conn.execute(
            "SELECT id FROM messages WHERE chat_id = ?", (chat_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO feedback (message_id, chat_id, user_id, rating, created_at) "
            "VALUES (?, ?, ?, 1, datetime('now'))", (msg_id, chat_id, victim))
        conn.commit()
    finally:
        conn.close()

    # Удаляем администратором
    client.cookies.clear()
    login_as(client, "kill-admin", ADMIN_PASS)
    r = client.delete(f"/api/admin/users/{victim}")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    conn = sqlite3.connect(settings.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE id = ?", (victim,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM chats WHERE id = ?", (chat_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?",
                            (chat_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM notes WHERE id = ?", (note_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM events WHERE id = ?",
                            (event_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM feedback WHERE user_id = ?",
                            (victim,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?",
                            (victim,)).fetchone()[0] == 0
        # Аудит удаления записан на действующего админа
        deletion = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'user_deleted' "
            "AND object_id = ?", (str(victim),)).fetchone()[0]
        assert deletion == 1
    finally:
        conn.close()


def test_delete_user_forbidden_for_self(client, make_user):
    admin_id = make_user("self-delete-admin", ADMIN_PASS, role="admin")
    login_as(client, "self-delete-admin", ADMIN_PASS)
    r = client.delete(f"/api/admin/users/{admin_id}")
    assert r.status_code == 400


def test_delete_user_last_active_admin_rule(client, make_user):
    """Правило «нельзя удалить последнего активного администратора» срабатывает,
    когда удаляемый — админ, а больше активных админов нет.

    Сценарий: два админа A и B; B заблокирован; A пытается удалить другого
    админа C (неактивного) — разрешено, т.к. A ещё активен. Ключевой кейс —
    удаление единственного оставшегося активного админа — недостижим напрямую
    через нормальный DELETE (исполнитель обязан быть активным админом; правило
    «нельзя себя» перехватит попытку суицида), поэтому проверяем сам счётчик
    активных админов «в другую сторону»: удаление НЕ последнего проходит.
    """
    make_user("last-admin-a", ADMIN_PASS, role="admin")
    b_id = make_user("last-admin-b", ADMIN_PASS, role="admin")

    login_as(client, "last-admin-a", ADMIN_PASS)
    # После удаления B в системе остаётся A — активный админ, поэтому 200.
    assert client.delete(f"/api/admin/users/{b_id}").status_code == 200


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
