"""Audit log & metrics tests (§13)."""
from __future__ import annotations

import httpx
import pytest

from app import llm as llm_module
from app.metrics import Metrics
from tests.conftest import login_as
from tests.mock_llm import app as mock_llm_app

PASS = "audit-user-pass-1"


@pytest.fixture()
def admin(client, make_user):
    make_user("audit-admin", PASS, role="admin")
    login_as(client, "audit-admin", PASS)


def test_audit_records_login(client, make_user):
    make_user("audit-viewer", PASS, role="admin")
    login_as(client, "audit-viewer", PASS)
    data = client.get("/api/admin/audit").json()
    assert data["total"] >= 1
    assert any(item["action"] == "login" for item in data["items"])


def test_audit_filter_by_action(client, admin):
    # Неудачный вход создаёт запись login_failed
    login_as(client, "audit-admin", "wrong-password-xx")
    login_as(client, "audit-admin", PASS)  # снова залогиниться для доступа
    data = client.get("/api/admin/audit?action=login_failed").json()
    assert all(item["action"] == "login_failed" for item in data["items"])
    assert data["total"] >= 1


def test_audit_pagination(client, admin):
    data = client.get("/api/admin/audit?limit=1&offset=0").json()
    assert len(data["items"]) <= 1
    assert data["limit"] == 1


def test_audit_admin_only(client, make_user):
    make_user("audit-plain", PASS)
    login_as(client, "audit-plain", PASS)
    assert client.get("/api/admin/audit").status_code == 403
    assert client.get("/api/admin/metrics").status_code == 403


def test_metrics_endpoint_shape(client, admin):
    m = client.get("/api/admin/metrics").json()
    for key in ("requests_total", "requests_success", "requests_failed",
                "avg_tokens_per_sec", "pii_masked_by_type"):
        assert key in m


def test_metrics_counter_logic():
    m = Metrics()
    m.record_request(success=True, tokens=100, seconds=2.0)
    m.record_request(success=False)
    m.record_pii({"EMAIL": 2, "ФИО": 1})
    snap = m.snapshot()
    assert snap["requests_total"] == 2
    assert snap["requests_success"] == 1
    assert snap["requests_failed"] == 1
    assert snap["avg_tokens_per_sec"] == 50.0
    assert snap["pii_masked_by_type"] == {"EMAIL": 2, "ФИО": 1}


def test_metrics_updated_by_chat(client, admin, monkeypatch):
    monkeypatch.setattr(llm_module, "_transport", httpx.ASGITransport(app=mock_llm_app))
    before = client.get("/api/admin/metrics").json()["requests_total"]
    chat_id = client.post("/api/chats", json={}).json()["id"]
    client.post(f"/api/chats/{chat_id}/messages", json={"content": "привет", "use_tools": False})
    after = client.get("/api/admin/metrics").json()["requests_total"]
    assert after == before + 1
