# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in real CPU LAYA inference over MCP stdio; requires the pinned local snapshot."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from omnia_mcp.schemas import BatchResult, DecisionResult

ROOT = Path(__file__).resolve().parents[1]
REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("OMNIA_RUN_LIVE_LAYA") != "1",
    reason="Set OMNIA_RUN_LIVE_LAYA=1 to run the real local LAYA inference gate.",
)
async def test_live_laya_evaluation_lookup_and_cache_over_stdio(tmp_path):
    database = tmp_path / "live-laya.sqlite3"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OMNIA_") and key != "TYPESAFE_API_KEY"
    }
    environment.update(
        {
            "OMNIA_DATABASE": str(database),
            "OMNIA_DEFAULT_PROVIDER": "laya",
            "OMNIA_ALLOWED_PROVIDERS": "laya",
            "OMNIA_LAYA_MODEL": "english",
            "OMNIA_LAYA_REVISION": REVISION,
            "OMNIA_LAYA_DEVICE": "cpu",
            "OMNIA_LAYA_THREADS": "2",
            "OMNIA_LAYA_MAX_LEN": "512",
            "OMNIA_LAYA_HEAD_MAX_LEN": "192",
            "OMNIA_LAYA_OFFLINE": "true",
            "OMNIA_REQUEST_TIMEOUT_SECONDS": "90",
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_PROGRESS_BARS": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYTHONPATH": str(ROOT / "src"),
        }
    )
    target = StdioServerParameters(
        command=sys.executable,
        args=["-m", "omnia_mcp", "serve"],
        env=environment,
        cwd=ROOT,
    )
    request = {
        "state": "The library maintainer published a new stable software release today.",
        "questions": {
            "route": {
                "type": "choice",
                "instructions": "Classify the message.",
                "criteria": {"release": "Software release announcement", "other": "Other topic"},
            },
            "is_release": {"type": "noul", "instructions": "Is a software release announced?"},
            "urgency": {
                "type": "score",
                "instructions": "Rate the need for immediate human action.",
                "criteria": ["Low", "Moderate", "High"],
            },
        },
        "evidence": ["test:live-laya-stdio"],
    }
    started = time.perf_counter()
    async with Client(target, mode="2026-07-28", read_timeout_seconds=120) as client:
        capabilities = await client.call_tool("omnia_capabilities", {})
        assert not capabilities.is_error
        assert capabilities.structured_content["execution_authorized"] is False
        evaluated = await client.call_tool("omnia_evaluate", {"request": request})
        assert not evaluated.is_error, evaluated.content
        result = DecisionResult.model_validate(evaluated.structured_content)
        first_seconds = time.perf_counter() - started
        assert set(result.answers) == {"route", "is_release", "urgency"}
        assert result.provider == "laya"
        assert result.identity["revision"] == REVISION
        assert result.identity["device"] == "cpu"
        assert result.identity["runtime_version"] == "0.3.20"
        assert not result.cached
        looked_up = await client.call_tool(
            "omnia_get_decision", {"decision_id": result.decision_id}
        )
        assert not looked_up.is_error
        assert looked_up.structured_content == evaluated.structured_content
        cache_started = time.perf_counter()
        replayed = await client.call_tool("omnia_evaluate", {"request": request})
        cache_seconds = time.perf_counter() - cache_started
        assert not replayed.is_error
        replay = DecisionResult.model_validate(replayed.structured_content)
        assert replay.cached
        assert replay.decision_id == result.decision_id
        assert replay.model == result.model
        assert replay.answers == result.answers
    total_seconds = time.perf_counter() - started
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM failures").fetchone()[0] == 0
        assert request["state"] not in "\n".join(connection.iterdump())
    report = {
        "validated_at": datetime.now(UTC).isoformat(),
        "test": "real_laya_cpu_offline_mcp_stdio",
        "protocol": "2026-07-28",
        "revision": REVISION,
        "runtime_version": result.identity["runtime_version"],
        "device": "cpu",
        "question_types": ["choice", "noul", "score"],
        "decision_status": result.status,
        "structured_output_valid": True,
        "lookup_matches": True,
        "cache_replay_matches": True,
        "single_persisted_decision": True,
        "client_context_closed": True,
        "first_evaluation_seconds": round(first_seconds, 3),
        "cache_replay_seconds": round(cache_seconds, 3),
        "total_seconds_including_cleanup": round(total_seconds, 3),
        "quality_accuracy_measured": False,
    }
    if report_path := os.environ.get("OMNIA_LIVE_REPORT_PATH"):
        Path(report_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


@pytest.mark.skipif(
    os.environ.get("OMNIA_RUN_LIVE_JEV") != "1",
    reason="Set OMNIA_RUN_LIVE_JEV=1 to run the authorized real JEV API gate.",
)
async def test_live_jev_evaluation_lookup_cache_and_batch_over_stdio(tmp_path):
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        pytest.fail("The explicitly enabled JEV live gate requires TYPESAFE_API_KEY.")
    database = tmp_path / "live-jev.sqlite3"
    environment = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
        if name in os.environ
    }
    environment.update(
        {
            "TYPESAFE_API_KEY": os.environ["TYPESAFE_API_KEY"],
            "OMNIA_DATABASE": str(database),
            "OMNIA_DEFAULT_PROVIDER": "jev",
            "OMNIA_ALLOWED_PROVIDERS": "jev",
            "OMNIA_REQUEST_TIMEOUT_SECONDS": "90",
            "PYTHONPATH": str(ROOT / "src"),
        }
    )
    target = StdioServerParameters(
        command=sys.executable,
        args=["-m", "omnia_mcp", "serve"],
        env=environment,
        cwd=ROOT,
    )
    request = {
        "state": "The library maintainer published a new stable software release today.",
        "questions": {
            "route": {
                "type": "choice",
                "instructions": "Classify the message.",
                "criteria": {"release": "Software release announcement", "other": "Other topic"},
            },
            "is_release": {"type": "noul", "instructions": "Is a software release announced?"},
            "urgency": {
                "type": "score",
                "instructions": "Rate the need for immediate human action.",
                "criteria": ["Low", "Moderate", "High"],
            },
        },
        "evidence": ["test:live-jev-stdio"],
    }
    started = time.perf_counter()
    async with Client(target, mode="2026-07-28", read_timeout_seconds=120) as client:
        capabilities = await client.call_tool("omnia_capabilities", {})
        assert not capabilities.is_error
        assert capabilities.structured_content["providers"]["jev"]["configured"] is True
        evaluated = await client.call_tool("omnia_evaluate", {"request": request})
        assert not evaluated.is_error, evaluated.content
        result = DecisionResult.model_validate(evaluated.structured_content)
        first_seconds = time.perf_counter() - started
        assert result.provider == "jev"
        assert set(result.answers) == {"route", "is_release", "urgency"}
        assert result.model == result.identity["model"]
        assert result.status in {"accepted", "needs_review"}
        assert not result.cached
        looked_up = await client.call_tool(
            "omnia_get_decision", {"decision_id": result.decision_id}
        )
        assert not looked_up.is_error
        assert looked_up.structured_content == evaluated.structured_content
        cache_started = time.perf_counter()
        replayed = await client.call_tool("omnia_evaluate", {"request": request})
        cache_seconds = time.perf_counter() - cache_started
        assert not replayed.is_error
        replay = DecisionResult.model_validate(replayed.structured_content)
        assert replay.cached
        assert replay.decision_id == result.decision_id
        assert replay.answers == result.answers
        batched = await client.call_tool(
            "omnia_evaluate_batch", {"request": {"items": [request, request]}}
        )
        assert not batched.is_error
        batch = BatchResult.model_validate(batched.structured_content)
        assert len(batch.items) == 2
        for item in batch.items:
            assert item.error is None
            assert item.result.cached
            assert item.result.decision_id == result.decision_id
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM failures").fetchone()[0] == 0
        assert request["state"] not in "\n".join(connection.iterdump())
    report = {
        "validated_at": datetime.now(UTC).isoformat(),
        "test": "real_jev_api_mcp_stdio",
        "protocol": "2026-07-28",
        "provider": "jev",
        "model": result.model,
        "question_types": ["choice", "noul", "score"],
        "decision_status": result.status,
        "structured_output_valid": True,
        "lookup_matches": True,
        "cache_replay_matches": True,
        "batch_cache_replay_matches": True,
        "single_persisted_decision": True,
        "client_context_closed": True,
        "first_evaluation_seconds": round(first_seconds, 3),
        "cache_replay_seconds": round(cache_seconds, 3),
        "total_seconds_including_cleanup": round(time.perf_counter() - started, 3),
        "quality_accuracy_measured": False,
    }
    if report_path := os.environ.get("OMNIA_LIVE_REPORT_PATH"):
        Path(report_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
