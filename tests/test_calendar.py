"""Calendar API tests: CRUD, privacy, date-range filter, validation, audit."""
from __future__ import annotations

import sqlite3

import pytest

from app.config import settings
from tests.conftest import login_as

PASS = "cal-user-pass-12"


@pytest.fixture(autouse=True)
def wipe_events():
    yield
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("DELETE FROM events")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def two_users(client, make_user):
    make_user("carol", PASS, display_name="Кэрол")
    make_user("dave", PASS, display_name="Дэйв")

    def as_user(login: str):
        client.cookies.clear()
        login_as(client, login, PASS)
        return client

    return as_user


def _event(title: str, start: str, end: str, **extra) -> dict:
    return {"title": title, "starts_at": start, "ends_at": end, **extra}


def test_event_crud_roundtrip(client, make_user, two_users):
    c = two_users("carol")
    r = c.post("/api/events", json=_event(
        "Совещание по нормированию", "2026-07-16T10:00:00+03:00", "2026-07-16T11:00:00+03:00",
        location="каб. 314", scope="shared"))
    assert r.status_code == 201
    ev = r.json()
    assert ev["author_name"] == "Кэрол"
    assert ev["all_day"] == 0

    r = c.put(f"/api/events/{ev['id']}", json={"location": "актовый зал"})
    assert r.status_code == 200
    assert r.json()["location"] == "актовый зал"
    assert r.json()["title"] == "Совещание по нормированию"

    assert c.delete(f"/api/events/{ev['id']}").status_code == 200
    assert c.get(f"/api/events/{ev['id']}").status_code == 404


def test_personal_events_private_shared_visible(client, make_user, two_users):
    c = two_users("carol")
    personal_id = c.post("/api/events", json=_event(
        "Личная встреча", "2026-07-16T10:00:00+03:00", "2026-07-16T11:00:00+03:00")).json()["id"]
    shared_id = c.post("/api/events", json=_event(
        "Общее совещание", "2026-07-17T10:00:00+03:00", "2026-07-17T11:00:00+03:00",
        scope="shared")).json()["id"]

    c = two_users("dave")
    assert c.get(f"/api/events/{personal_id}").status_code == 404
    assert c.put(f"/api/events/{personal_id}", json={"title": "x"}).status_code == 404
    assert c.delete(f"/api/events/{personal_id}").status_code == 404

    titles = [e["title"] for e in c.get("/api/events").json()]
    assert titles == ["Общее совещание"]

    # общее событие может редактировать любой, фиксируется updated_by
    r = c.put(f"/api/events/{shared_id}", json={"description": "перенесено"})
    assert r.json()["updated_by_name"] == "Дэйв"
    # но не менять область
    assert c.put(f"/api/events/{shared_id}", json={"scope": "personal"}).status_code == 403


def test_date_range_filter(client, make_user, two_users):
    c = two_users("carol")
    c.post("/api/events", json=_event("Раннее", "2026-07-01T09:00:00+03:00", "2026-07-01T10:00:00+03:00"))
    c.post("/api/events", json=_event("Среднее", "2026-07-15T09:00:00+03:00", "2026-07-15T10:00:00+03:00"))
    c.post("/api/events", json=_event("Позднее", "2026-08-01T09:00:00+03:00", "2026-08-01T10:00:00+03:00"))
    # событие, пересекающее границу диапазона
    c.post("/api/events", json=_event("Долгое", "2026-07-10T09:00:00+03:00", "2026-07-20T10:00:00+03:00"))

    r = c.get("/api/events", params={
        "date_from": "2026-07-14T00:00:00+03:00",
        "date_to": "2026-07-31T23:59:59+03:00",
    })
    titles = [e["title"] for e in r.json()]
    assert titles == ["Долгое", "Среднее"]  # сортировка по началу

    r = c.get("/api/events", params={"date_from": "2026-08-01T00:00:00+03:00"})
    assert [e["title"] for e in r.json()] == ["Позднее"]


def test_all_day_event(client, make_user, two_users):
    c = two_users("carol")
    r = c.post("/api/events", json=_event(
        "Инвентаризация", "2026-07-20T00:00:00+03:00", "2026-07-20T23:59:59+03:00",
        all_day=True))
    assert r.status_code == 201
    assert r.json()["all_day"] == 1


def test_validation(client, make_user, two_users):
    c = two_users("carol")
    # конец раньше начала
    assert c.post("/api/events", json=_event(
        "х", "2026-07-16T11:00:00+03:00", "2026-07-16T10:00:00+03:00")).status_code == 400
    # мусор вместо даты
    assert c.post("/api/events", json=_event("х", "не-дата", "2026-07-16T10:00:00+03:00")).status_code == 400
    # пустой заголовок
    assert c.post("/api/events", json=_event("  ", "2026-07-16T10:00:00+03:00",
                                             "2026-07-16T11:00:00+03:00")).status_code == 400
    # смешение naive и aware дат
    assert c.post("/api/events", json=_event(
        "х", "2026-07-16T10:00:00", "2026-07-16T11:00:00+03:00")).status_code == 400
    # некорректный диапазон в фильтре
    assert c.get("/api/events", params={"date_from": "мусор"}).status_code == 400


def test_delete_writes_audit(client, make_user, two_users):
    c = two_users("carol")
    event_id = c.post("/api/events", json=_event(
        "Удаляемое", "2026-07-16T10:00:00+03:00", "2026-07-16T11:00:00+03:00")).json()["id"]
    c.delete(f"/api/events/{event_id}")

    conn = sqlite3.connect(settings.db_path)
    try:
        row = conn.execute(
            "SELECT action, object_type, object_id FROM audit_log "
            "WHERE action = 'event_deleted' ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row == ("event_deleted", "event", str(event_id))
