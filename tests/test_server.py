# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""MCP contract tests using the official SDK and explicit service fixtures."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from mcp import Client

from omnia_mcp.errors import OmniaError
from omnia_mcp.schemas import (
    BatchItemResult,
    BatchResult,
    ChoiceAnswer,
    DecisionPolicy,
    DecisionResult,
)
from omnia_mcp.server import TOOL_NAMES, create_server
from omnia_mcp.settings import Settings

REQUEST = {
    "state": {"message": "Fixture for protocol testing."},
    "questions": {
        "route": {
            "type": "choice",
            "instructions": "Choose a route.",
            "criteria": {"review": "Review required", "skip": "No review required"},
        }
    },
    "evidence": ["fixture:protocol"],
}


def fixture_result() -> DecisionResult:
    return DecisionResult(
        decision_id="fixture-decision",
        input_hash="fixture-hash",
        provider="laya",
        model="fixture",
        identity={"fixture": True},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        answers={
            "route": ChoiceAnswer(
                type="choice", choice="review", probabilities={"review": 0.9, "skip": 0.1}
            )
        },
        status="accepted",
        review_reasons=[],
        policy=DecisionPolicy(),
        evidence=["fixture:protocol"],
        duration_ms=1,
    )


class FixtureService:
    def __init__(self, settings):
        self.settings = settings
        self.closed = False
        self.calls = []
        self.failure = None

    async def evaluate(self, request):
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        return fixture_result()

    async def evaluate_batch(self, request):
        return BatchResult(
            items=[
                BatchItemResult(index=i, result=await self.evaluate(item))
                for i, item in enumerate(request.items)
            ]
        )

    def capabilities(self):
        return {"providers": ["laya"], "inference_tested": False}

    def get_decision(self, decision_id):
        return fixture_result() if decision_id == "fixture-decision" else None

    async def aclose(self):
        self.closed = True


@pytest.fixture
def app(tmp_path):
    service = FixtureService(Settings(database=tmp_path / "unused.sqlite3"))
    server = create_server(service.settings, service_factory=lambda _: service)
    return server, service


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_tool_contracts_and_resources(app, mode):
    server, service = app
    assert not service.closed
    async with Client(server, mode=mode) as client:
        listed = await client.list_tools()
        by_name = {tool.name: tool for tool in listed.tools}
        assert set(by_name) == TOOL_NAMES
        for name, tool in by_name.items():
            assert tool.output_schema is not None
            assert "ctx" not in tool.input_schema.get("properties", {})
            if name in {"omnia_evaluate", "omnia_evaluate_batch"}:
                assert tool.annotations.read_only_hint is False
                assert tool.annotations.destructive_hint is True
                assert tool.annotations.idempotent_hint is False
                assert "request" in tool.input_schema["properties"]
            else:
                assert tool.annotations.read_only_hint is True
        serialized = json.dumps(by_name["omnia_evaluate"].input_schema)
        assert "model_path" not in serialized
        assert "base_url" not in serialized

        resources = await client.list_resources()
        assert {str(resource.uri) for resource in resources.resources} == {
            "omnia://guide",
            "omnia://schemas",
        }
        guide = await client.read_resource("omnia://guide")
        assert "persist" in guide.contents[0].text
        schemas = await client.read_resource("omnia://schemas")
        assert "EvaluationRequest" in json.loads(schemas.contents[0].text)
    assert service.closed


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_structured_decision_batch_and_lookup(app, mode):
    server, _ = app
    async with Client(server, mode=mode) as client:
        result = await client.call_tool("omnia_evaluate", {"request": REQUEST})
        assert not result.is_error
        assert result.structured_content["decision_id"] == "fixture-decision"
        DecisionResult.model_validate(result.structured_content)
        batch = await client.call_tool(
            "omnia_evaluate_batch", {"request": {"items": [REQUEST, REQUEST]}}
        )
        assert not batch.is_error
        assert len(BatchResult.model_validate(batch.structured_content).items) == 2
        lookup = await client.call_tool("omnia_get_decision", {"decision_id": "fixture-decision"})
        assert lookup.structured_content == result.structured_content
        capabilities = await client.call_tool("omnia_capabilities", {})
        assert capabilities.structured_content["inference_tested"] is False


async def test_missing_decision_is_error_not_success(app):
    async with Client(app[0]) as client:
        result = await client.call_tool("omnia_get_decision", {"decision_id": "missing"})
        assert result.is_error
        assert result.structured_content is None
        assert "decision_not_found" in result.content[0].text


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_validation_errors_do_not_echo_inputs(app, mode):
    secret = "private-provider-token-that-must-not-leak"
    server, service = app
    async with Client(server, mode=mode) as client:
        result = await client.call_tool("omnia_evaluate", {"request": secret})
        assert result.is_error
        assert "invalid_request" in result.content[0].text
        assert secret not in result.model_dump_json()
    assert service.calls == []


async def test_unknown_failure_is_sanitized_and_cleanup_runs(app, caplog):
    server, service = app
    secret = "upstream-secret-never-in-protocol-or-log"
    service.failure = RuntimeError(secret)
    async with Client(server) as client:
        result = await client.call_tool("omnia_evaluate", {"request": REQUEST})
        assert result.is_error
        assert "evaluation_failed" in result.content[0].text
        assert secret not in result.model_dump_json()
    assert secret not in caplog.text
    assert service.closed


async def test_safe_domain_error_retains_actionable_code(app):
    server, service = app
    service.failure = OmniaError(
        "provider_unavailable", "Configured provider is unavailable.", retryable=True
    )
    async with Client(server) as client:
        result = await client.call_tool("omnia_evaluate", {"request": REQUEST})
        assert result.is_error
        assert "provider_unavailable" in result.content[0].text


async def test_output_schema_failure_does_not_leak_provider_value(app, monkeypatch, caplog):
    server, service = app
    secret = "invalid-secret-provider-output"

    async def bad_output(_request):
        return {"unexpected": secret}

    monkeypatch.setattr(service, "evaluate", bad_output)
    async with Client(server) as client:
        result = await client.call_tool("omnia_evaluate", {"request": REQUEST})
        assert result.is_error
        assert "internal_error" in result.content[0].text
        assert secret not in result.model_dump_json()
    assert secret not in caplog.text


class AdversarialProvider:
    """Explicit fixture: exercises service boundaries without inference or network."""

    def __init__(self):
        self.calls = 0
        self.closed = False
        self.answer = {
            "model": "fixture",
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "review",
                    "probabilities": {"review": 0.9, "skip": 0.1},
                }
            },
        }

    def identity(self):
        return {"provider": "laya", "model": "fixture", "revision": "fixture"}

    def capabilities(self):
        return {"fixture": True}

    async def evaluate(self, state, questions):
        self.calls += 1
        return self.answer

    async def aclose(self):
        self.closed = True


@pytest.fixture
def integrated_app(tmp_path):
    from omnia_mcp.service import DecisionService

    settings = Settings(
        database=tmp_path / "ledger.sqlite3",
        max_input_bytes=1024,
        max_batch_items=1,
    )
    provider = AdversarialProvider()
    service = DecisionService(settings, providers={"laya": provider})
    return create_server(settings, service_factory=lambda _: service), service, provider, settings


@pytest.mark.parametrize("field", ["model", "model_path", "base_url", "url", "command"])
async def test_runtime_configuration_cannot_be_supplied_as_evaluation_arguments(
    integrated_app, field
):
    server, _, provider, settings = integrated_app
    request = {**REQUEST, field: "private-forbidden-destination"}
    async with Client(server) as client:
        result = await client.call_tool("omnia_evaluate", {"request": request})
        assert result.is_error
        assert "private-forbidden-destination" not in result.model_dump_json()
    assert provider.calls == 0
    assert not settings.database.exists()


async def test_disabled_provider_fails_before_storage_and_inference(integrated_app):
    server, _, provider, settings = integrated_app
    async with Client(server) as client:
        result = await client.call_tool(
            "omnia_evaluate", {"request": {**REQUEST, "provider": "jev"}}
        )
        assert result.is_error
        assert "provider_disabled" in result.content[0].text
    assert provider.calls == 0
    assert not settings.database.exists()


async def test_byte_and_batch_limits_fail_before_storage(integrated_app):
    server, _, provider, settings = integrated_app
    async with Client(server) as client:
        oversized = await client.call_tool(
            "omnia_evaluate", {"request": {**REQUEST, "state": "x" * 2048}}
        )
        assert oversized.is_error
        assert "input_limit" in oversized.content[0].text
        batch = await client.call_tool(
            "omnia_evaluate_batch", {"request": {"items": [REQUEST, REQUEST]}}
        )
        assert batch.is_error
        assert "batch_limit" in batch.content[0].text
    assert provider.calls == 0
    assert not settings.database.exists()


async def test_corrupt_provider_output_is_sanitized_and_never_saved_as_decision(
    integrated_app, caplog
):
    import sqlite3
    from contextlib import closing

    server, _, provider, settings = integrated_app
    secret = "secret-upstream-response-content"
    provider.answer["answers"]["route"]["probabilities"] = {"review": secret, "skip": 0.1}
    async with Client(server) as client:
        result = await client.call_tool("omnia_evaluate", {"request": REQUEST})
        assert result.is_error
        assert "invalid_provider_response" in result.content[0].text
        assert secret not in result.model_dump_json()
    assert secret not in caplog.text
    with closing(sqlite3.connect(settings.database)) as connection:
        assert connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
        failures = connection.execute("SELECT code FROM failures").fetchall()
        assert failures == [("invalid_provider_response",)]


async def test_get_decision_rejects_paths(integrated_app):
    server, _, provider, settings = integrated_app
    async with Client(server) as client:
        result = await client.call_tool(
            "omnia_get_decision", {"decision_id": "../../private-secret"}
        )
        assert result.is_error
        assert "invalid_request" in result.content[0].text
        assert "private-secret" not in result.model_dump_json()
    assert provider.calls == 0
    assert not settings.database.exists()


def test_debug_configuration_does_not_enable_sdk_debug_logs(tmp_path):
    import logging

    application = logging.getLogger("omnia_mcp")
    previous = application.level
    sdk_level = logging.getLogger("mcp").getEffectiveLevel()
    try:
        create_server(Settings(database=tmp_path / "unused.sqlite3", log_level="DEBUG"))
        assert application.level == logging.DEBUG
        assert logging.getLogger("mcp").getEffectiveLevel() == sdk_level
    finally:
        application.setLevel(previous)
