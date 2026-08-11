"""Notes API tests: scopes, privacy, search, tags, shared editing, audit."""
from __future__ import annotations

import sqlite3

import pytest

from app.config import settings
from tests.conftest import login_as

PASS = "notes-user-pass-1"


@pytest.fixture(autouse=True)
def wipe_notes():
    yield
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("DELETE FROM note_prefs")
        conn.execute("DELETE FROM notes")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def notes_user(client, make_user):
    make_user("notes-user", PASS)
    login_as(client, "notes-user", PASS)
    return client


@pytest.fixture()
def two_users(client, make_user):
    make_user("alice", PASS, display_name="Алиса")
    make_user("bob", PASS, display_name="Боб")

    def as_user(login: str):
        client.cookies.clear()
        login_as(client, login, PASS)
        return client

    return as_user


def test_note_crud_roundtrip(client, make_user, two_users):
    c = two_users("alice")
    r = c.post("/api/notes", json={
        "title": "Регламент сварки", "body": "## Текст\nпункт",
        "tags": ["сварка", "регламент", "сварка"], "scope": "personal",
    })
    assert r.status_code == 201
    note = r.json()
    assert note["tags"] == ["сварка", "регламент"]  # дубликат убран
    assert note["author_name"] == "Алиса"

    r = c.put(f"/api/notes/{note['id']}", json={"body": "новый текст"})
    assert r.status_code == 200
    assert r.json()["body"] == "новый текст"
    assert r.json()["title"] == "Регламент сварки"  # не тронут

    assert c.get(f"/api/notes/{note['id']}").json()["body"] == "новый текст"
    assert c.delete(f"/api/notes/{note['id']}").status_code == 200
    assert c.get(f"/api/notes/{note['id']}").status_code == 404


def test_personal_notes_are_private(client, make_user, two_users):
    c = two_users("alice")
    note_id = c.post("/api/notes", json={"title": "Личное", "scope": "personal"}).json()["id"]

    c = two_users("bob")
    assert c.get(f"/api/notes/{note_id}").status_code == 404
    assert c.put(f"/api/notes/{note_id}", json={"title": "x"}).status_code == 404
    assert c.delete(f"/api/notes/{note_id}").status_code == 404
    assert note_id not in [n["id"] for n in c.get("/api/notes").json()]


def test_shared_notes_editable_by_all_with_updated_by(client, make_user, two_users):
    c = two_users("alice")
    note_id = c.post("/api/notes", json={"title": "Общая", "scope": "shared"}).json()["id"]

    c = two_users("bob")
    r = c.put(f"/api/notes/{note_id}", json={"body": "дополнил Боб"})
    assert r.status_code == 200
    updated = r.json()
    assert updated["author_name"] == "Алиса"
    assert updated["updated_by_name"] == "Боб"

    # но сменить область общей заметки Боб не может — только автор
    assert c.put(f"/api/notes/{note_id}", json={"scope": "personal"}).status_code == 403
    c = two_users("alice")
    assert c.put(f"/api/notes/{note_id}", json={"scope": "personal"}).status_code == 200


def test_shared_note_delete_only_by_author(client, make_user, two_users):
    c = two_users("alice")
    note_id = c.post("/api/notes", json={"title": "Общая", "scope": "shared"}).json()["id"]

    # Боб видит общую заметку и может её править, но удалить чужую — нельзя
    c = two_users("bob")
    assert c.delete(f"/api/notes/{note_id}").status_code == 403
    assert c.get(f"/api/notes/{note_id}").status_code == 200  # на месте

    # автор — удаляет
    c = two_users("alice")
    assert c.delete(f"/api/notes/{note_id}").status_code == 200


def test_search_and_tag_filter(client, make_user, two_users):
    c = two_users("alice")
    c.post("/api/notes", json={"title": "Сварка швов", "body": "аргон", "tags": ["сварка"]})
    c.post("/api/notes", json={"title": "Литьё", "body": "формы и аргон", "tags": ["литьё", "цех"]})
    c.post("/api/notes", json={"title": "Отпуск", "body": "", "tags": []})

    titles = [n["title"] for n in c.get("/api/notes", params={"query": "аргон"}).json()]
    assert sorted(titles) == ["Литьё", "Сварка швов"]

    titles = [n["title"] for n in c.get("/api/notes", params={"tags": "цех"}).json()]
    assert titles == ["Литьё"]

    titles = [n["title"] for n in c.get("/api/notes", params={"tags": "литьё,цех"}).json()]
    assert titles == ["Литьё"]

    assert c.get("/api/notes", params={"tags": "нет-такого"}).json() == []


def test_scope_filter_and_shared_visible_to_others(client, make_user, two_users):
    c = two_users("alice")
    c.post("/api/notes", json={"title": "Моя личная", "scope": "personal"})
    c.post("/api/notes", json={"title": "Всем", "scope": "shared"})

    c = two_users("bob")
    all_titles = [n["title"] for n in c.get("/api/notes").json()]
    assert all_titles == ["Всем"]
    assert [n["title"] for n in c.get("/api/notes", params={"scope": "personal"}).json()] == []

    c = two_users("alice")
    assert len(c.get("/api/notes").json()) == 2
    personal = c.get("/api/notes", params={"scope": "personal"}).json()
    assert [n["title"] for n in personal] == ["Моя личная"]


def test_empty_title_rejected(client, make_user, two_users):
    c = two_users("alice")
    assert c.post("/api/notes", json={"title": "  "}).status_code == 400
    note_id = c.post("/api/notes", json={"title": "ок"}).json()["id"]
    assert c.put(f"/api/notes/{note_id}", json={"title": ""}).status_code == 400


def test_delete_writes_audit(client, make_user, two_users):
    c = two_users("alice")
    note_id = c.post("/api/notes", json={"title": "Удаляемая"}).json()["id"]
    c.delete(f"/api/notes/{note_id}")

    conn = sqlite3.connect(settings.db_path)
    try:
        row = conn.execute(
            "SELECT action, object_type, object_id FROM audit_log "
            "WHERE action = 'note_deleted' ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row == ("note_deleted", "note", str(note_id))


# --- Закрепление и порядок плиток (личные, note_prefs) ---

def test_pinned_notes_come_first(client, notes_user):
    ids = [client.post("/api/notes", json={"title": f"Заметка {i}"}).json()["id"]
           for i in range(3)]
    assert [n["id"] for n in client.get("/api/notes").json()] == ids[::-1]

    assert client.put(f"/api/notes/{ids[0]}/pin", json={"pinned": True}).status_code == 200
    listed = client.get("/api/notes").json()
    assert listed[0]["id"] == ids[0] and listed[0]["pinned"] is True
    # остальные — по-прежнему по дате изменения
    assert [n["id"] for n in listed[1:]] == ids[:0:-1]


def test_newly_pinned_goes_on_top(client, notes_user):
    a = client.post("/api/notes", json={"title": "А"}).json()["id"]
    b = client.post("/api/notes", json={"title": "Б"}).json()["id"]
    client.put(f"/api/notes/{a}/pin", json={"pinned": True})
    client.put(f"/api/notes/{b}/pin", json={"pinned": True})
    assert [n["id"] for n in client.get("/api/notes").json()][:2] == [b, a]


def test_unpin_returns_note_to_date_order(client, notes_user):
    a = client.post("/api/notes", json={"title": "А"}).json()["id"]
    client.post("/api/notes", json={"title": "Б"})
    client.put(f"/api/notes/{a}/pin", json={"pinned": True})
    assert client.get("/api/notes").json()[0]["id"] == a
    client.put(f"/api/notes/{a}/pin", json={"pinned": False})
    listed = client.get("/api/notes").json()
    assert listed[-1]["id"] == a and listed[-1]["pinned"] is False


def test_manual_order_of_pinned(client, notes_user):
    ids = [client.post("/api/notes", json={"title": f"З{i}"}).json()["id"] for i in range(3)]
    for i in ids:
        client.put(f"/api/notes/{i}/pin", json={"pinned": True})
    order = [ids[2], ids[0], ids[1]]
    assert client.put("/api/notes/order", json={"ids": order}).status_code == 200
    assert [n["id"] for n in client.get("/api/notes").json()] == order


def test_pinning_is_personal_for_shared_notes(client, make_user, notes_user):
    """Общую заметку видит весь отдел. Закрепил один — у остальных не изменилось."""
    shared = client.post("/api/notes", json={"title": "Общая", "scope": "shared"}).json()["id"]
    client.post("/api/notes", json={"title": "Своя"})
    client.put(f"/api/notes/{shared}/pin", json={"pinned": True})
    assert client.get("/api/notes").json()[0]["id"] == shared

    make_user("notes-other", PASS)
    client.cookies.clear()
    login_as(client, "notes-other", PASS)
    listed = client.get("/api/notes").json()
    mine = [n for n in listed if n["id"] == shared][0]
    assert mine["pinned"] is False


def test_pin_requires_visibility(client, make_user, notes_user):
    personal = client.post("/api/notes", json={"title": "Личная"}).json()["id"]
    make_user("notes-stranger", PASS)
    client.cookies.clear()
    login_as(client, "notes-stranger", PASS)
    assert client.put(f"/api/notes/{personal}/pin", json={"pinned": True}).status_code == 404


def test_order_ignores_foreign_ids(client, make_user, notes_user):
    """Список порядка приходит из интерфейса — чужой идентификатор в нём
    не должен ничего менять."""
    mine = client.post("/api/notes", json={"title": "Моя"}).json()["id"]
    client.put(f"/api/notes/{mine}/pin", json={"pinned": True})
    make_user("notes-alien", PASS)
    client.cookies.clear()
    login_as(client, "notes-alien", PASS)
    alien = client.post("/api/notes", json={"title": "Чужая"}).json()["id"]
    client.cookies.clear()
    login_as(client, "notes-user", PASS)
    assert client.put("/api/notes/order", json={"ids": [alien, mine]}).status_code == 200
    # Наша заметка на месте, чужая настройка не появилась
    assert [n["id"] for n in client.get("/api/notes").json()] == [mine]


def test_prefs_removed_with_note(client, notes_user):
    import sqlite3
    note_id = client.post("/api/notes", json={"title": "На удаление"}).json()["id"]
    client.put(f"/api/notes/{note_id}/pin", json={"pinned": True})
    client.delete(f"/api/notes/{note_id}")
    conn = sqlite3.connect(settings.db_path)
    try:
        left = conn.execute("SELECT COUNT(*) FROM note_prefs WHERE note_id = ?",
                            (note_id,)).fetchone()[0]
    finally:
        conn.close()
    assert left == 0
