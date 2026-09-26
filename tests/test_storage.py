# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
from datetime import datetime, timezone
import pytest
from omnia_mcp.errors import OmniaError
from omnia_mcp.schemas import DecisionPolicy, DecisionResult
from omnia_mcp.storage import Ledger, read_decision


def result():
    return DecisionResult(decision_id="decision-1", input_hash="abc", provider="laya",
        model="laya-rl-agent", identity={}, created_at=datetime.now(timezone.utc),
        answers={"q": {"type": "noul", "noul": .9}}, status="accepted",
        review_reasons=[], policy=DecisionPolicy(), evidence=[], duration_ms=1)


def test_durable_reopen_and_read_only(tmp_path):
    path = tmp_path / "db.sqlite3"
    ledger = Ledger(path)
    owner = ledger.claim("key", 60)
    ledger.complete("key", owner, result())
    ledger.close()
    assert read_decision(path, "decision-1").answers["q"].noul == .9
    reopened = Ledger(path)
    assert reopened.cached("key", 60).decision_id == "decision-1"
    reopened.close()
    missing = tmp_path / "absent" / "db.sqlite"
    assert read_decision(missing, "x") is None
    assert not missing.parent.exists()


def test_expired_owner_cannot_commit(tmp_path):
    now = [1000.]
    ledger = Ledger(tmp_path / "db", clock=lambda: now[0])
    old = ledger.claim("key", 1)
    assert ledger.claim("key", 1) is None
    now[0] += 2
    new = ledger.claim("key", 60)
    with pytest.raises(OmniaError, match="expired"):
        ledger.complete("key", old, result())
    ledger.complete("key", new, result())
    ledger.close()


def test_budget_is_atomic_across_connections(tmp_path):
    a = Ledger(tmp_path / "db", clock=lambda: 120.)
    b = Ledger(tmp_path / "db", clock=lambda: 120.)
    a.reserve_usage(1, 10)
    with pytest.raises(OmniaError) as error:
        b.reserve_usage(1, 10)
    assert error.value.code == "rate_limited"
    a.close()
    b.close()


def test_failure_log_never_stores_payload(tmp_path):
    ledger = Ledger(tmp_path / "db")
    ledger.failure("hash", "provider_timeout")
    columns = [row[1] for row in ledger.connection.execute("PRAGMA table_info(failures)")]
    assert columns == ["id", "input_hash", "code", "created"]
    ledger.close()

