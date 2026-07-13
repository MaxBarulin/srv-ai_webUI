"""Shared test fixtures: isolated temp DATA_DIR, app client, helper users.

DATA_DIR must be set before app.config is imported (module-level singleton),
hence the env assignment at import time here.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

_TMP_DATA = tempfile.mkdtemp(prefix="srv-ai-ui-test-")
os.environ["DATA_DIR"] = _TMP_DATA

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_module
from app.auth import hash_password
from app.config import settings
from app.main import app


@pytest.fixture()
def client():
    auth_module.login_rate_limiter._hits.clear()
    with TestClient(app) as c:
        yield c


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def make_user():
    created_ids: list[int] = []

    def _make(login: str, password: str, role: str = "user", is_active: bool = True,
              display_name: str | None = None) -> int:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT INTO users (login, pass_hash, display_name, role, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (login, hash_password(password), display_name or login, role, int(is_active)),
            )
            conn.commit()
            created_ids.append(cur.lastrowid)
            return cur.lastrowid
        finally:
            conn.close()

    yield _make

    conn = _connect()
    try:
        for uid in created_ids:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM audit_log WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        conn.commit()
    finally:
        conn.close()


def login_as(client: TestClient, login: str, password: str):
    return client.post("/api/login", json={"login": login, "password": password})
