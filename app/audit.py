"""Audit log writer — records facts of actions only (who/what/when), no content."""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def write_audit(
    db: aiosqlite.Connection,
    *,
    user_id: int | None,
    action: str,
    object_type: str | None = None,
    object_id: str | None = None,
    details: str | None = None,
    ip: str | None = None,
) -> None:
    await db.execute(
        "INSERT INTO audit_log (user_id, action, object_type, object_id, details, created_at, ip) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, action, object_type, object_id, details, utcnow_iso(), ip),
    )
    await db.commit()
