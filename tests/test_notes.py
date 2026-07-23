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
        conn.execute("DELETE FROM notes")
        conn.commit()
    finally:
        conn.close()


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
