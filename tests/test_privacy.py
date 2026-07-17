"""Приватность: retention-очистка старых сообщений и защита от тихой
деградации шифрования (DB_KEY без sqlcipher3)."""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace

import pytest

from app import retention as retention_module
from app.config import settings
from app.db import _ensure_cipher_driver
import app.db as db_module


def _conn():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_retention_deletes_only_old_messages(client, make_user, monkeypatch):
    monkeypatch.setattr(retention_module, "settings",
                        replace(settings, chat_retention_days=90))
    conn = _conn()
    try:
        uid = make_user("retention-user", "retention-pass-1")
        conn.execute(
            "INSERT INTO chats (id, user_id, title, created_at, updated_at) "
            "VALUES (900, ?, 'old', datetime('now'), datetime('now'))", (uid,))
        conn.execute(
            "INSERT INTO messages (chat_id, role, content, created_at) "
            "VALUES (900, 'user', 'старое', datetime('now', '-120 days'))")
        conn.execute(
            "INSERT INTO messages (chat_id, role, content, created_at) "
            "VALUES (900, 'user', 'свежее', datetime('now'))")
        old_id = conn.execute(
            "SELECT id FROM messages WHERE content='старое'").fetchone()[0]
        conn.execute(
            "INSERT INTO feedback (message_id, chat_id, user_id, rating, created_at) "
            "VALUES (?, 900, ?, 1, datetime('now', '-120 days'))", (old_id, uid))
        conn.commit()
    finally:
        conn.close()

    deleted = asyncio.run(retention_module.cleanup_old_messages())
    assert deleted == 1

    conn = _conn()
    try:
        rows = [r["content"] for r in
                conn.execute("SELECT content FROM messages WHERE chat_id=900")]
        assert rows == ["свежее"]
        assert conn.execute("SELECT COUNT(*) FROM feedback WHERE message_id=?",
                            (old_id,)).fetchone()[0] == 0
        # чат с оставшимся сообщением жив
        assert conn.execute("SELECT COUNT(*) FROM chats WHERE id=900").fetchone()[0] == 1
        # очистка
        conn.execute("DELETE FROM messages WHERE chat_id=900")
        conn.execute("DELETE FROM chats WHERE id=900")
        conn.commit()
    finally:
        conn.close()


def test_retention_disabled_by_default(monkeypatch):
    monkeypatch.setattr(retention_module, "settings",
                        replace(settings, chat_retention_days=0))
    assert asyncio.run(retention_module.cleanup_old_messages()) == 0


def test_db_key_without_sqlcipher_fails_loudly(monkeypatch):
    """DB_KEY задан, sqlcipher3 не установлен — приложение обязано упасть,
    а не молча писать нешифрованную базу."""
    monkeypatch.setattr(db_module, "settings", replace(settings, db_key="secret-key"))
    monkeypatch.setattr(db_module, "_cipher_ready", False)
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sqlcipher3":
            raise ImportError("No module named 'sqlcipher3'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="sqlcipher3"):
        _ensure_cipher_driver()
