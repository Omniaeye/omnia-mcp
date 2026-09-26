# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""Acceptance-runner unit and official SDK integration tests; no provider calls."""

from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent

from omnia_mcp.errors import OmniaError
from omnia_mcp.schemas import DecisionResult
from omnia_mcp.server import create_server
from omnia_mcp.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("evaluate_suite", ROOT / "scripts/evaluate_suite.py")
suite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(suite)

EXPECTED = {
    "route": {"type": "choice", "equals": "yes"},
    "binary": {"type": "noul", "equals": True, "threshold": 0.5},
    "urgency": {"type": "score", "min": 1.5, "max": 2},
}


def decision(*, choice="yes", noul=0.9, score=1.8, identifier="fixture"):
    return DecisionResult(
        decision_id=identifier,
        input_hash="fixture",
        provider="jev",
        model="fixture",
        identity={"fixture": True},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        answers={
            "route": {
                "type": "choice",
                "choice": choice,
                "probabilities": {
                    "yes": 0.9 if choice == "yes" else 0.1,
                    "no": 0.1 if choice == "yes" else 0.9,
                },
            },
            "binary": {"type": "noul", "noul": noul},
            "urgency": {
                "type": "score",
                "score": score,
                "probabilities": {"0": 0.0, "1": 0.2, "2": 0.8},
            },
        },
        status="needs_review",
        review_reasons=["fixture:review-required"],
        policy={},
        evidence=[],
        duration_ms=1.0,
    )


def record(result, *, round_number=1):
    checks = suite.compare_answers(result, EXPECTED)
    return {
        "case_id": "fixture",
        "family": "fixture_family",
        "language": "en",
        "round": round_number,
        "result": result.model_dump(mode="json"),
        "checks": checks,
        "rubric_passed": all(check["passed"] for check in checks),
        "latency_ms": 1.0,
    }


def test_authored_dataset_has_twenty_cases_and_complete_explicit_criteria():
    dataset, digest = suite.load_dataset(ROOT / "evals/acceptance.json")
    assert len(dataset["cases"]) == 20
    assert len(digest) == 64
    assert {case["language"] for case in dataset["cases"]} == {"en", "pt", "zh", "ja"}
    assert sum(len(case["expected"]) for case in dataset["cases"]) == 48
    for case in dataset["cases"]:
        assert set(case["expected"]) == set(case["request"]["questions"])
        assert "expected" not in case["request"]
        assert "family" not in case["request"]


@pytest.mark.parametrize("choice, passed", [("yes", True), ("no", False)])
def test_choice_requires_exact_label(choice, passed):
    checks = suite.compare_answers(decision(choice=choice), EXPECTED)
    assert checks[0]["passed"] is passed


@pytest.mark.parametrize("value, passed", [(0.49999, False), (0.5, True), (0.50001, True)])
def test_noul_uses_declared_inclusive_half_threshold(value, passed):
    checks = suite.compare_answers(decision(noul=value), EXPECTED)
    assert checks[1]["observed"] is passed
    assert checks[1]["passed"] is passed


@pytest.mark.parametrize("value, passed", [(1.49999, False), (1.5, True), (2.0, True)])
def test_score_band_is_inclusive_and_independent_of_review_status(value, passed):
    result = decision(score=value)
    checks = suite.compare_answers(result, EXPECTED)
    assert checks[2]["passed"] is passed
    assert result.status == "needs_review"
    assert result.review_reasons == ["fixture:review-required"]


def test_summary_separates_transport_failure_rubric_mismatch_and_review():
    first = record(decision(identifier="a"))
    second = record(decision(choice="no", identifier="b"), round_number=2)
    failure = {
        "case_id": "fixture",
        "family": "fixture_family",
        "language": "en",
        "round": 3,
        "error_code": "invalid_provider_response",
        "latency_ms": 9.0,
    }
    summary = suite.summarize([first, second, failure])
    assert summary["evaluation_calls_recorded"] == 3
    assert summary["completed_decisions"] == 2
    assert summary["failed_calls"] == 1
    assert summary["rubric_passes"] == 1
    assert summary["rubric_mismatches"] == 1
    assert summary["question_checks"] == 6
    assert summary["question_checks_passed"] == 5
    assert summary["decision_statuses"] == {"needs_review": 2}
    assert summary["error_codes"] == {"invalid_provider_response": 1}
    assert summary["unique_decision_ids"] == 2
    assert (
        summary["repeat_consistency"]["fixture"]["choice_noul_and_score_band_consistent"] is False
    )


def test_summary_does_not_claim_consistency_for_only_failures():
    summary = suite.summarize(
        [
            {
                "case_id": "fixture",
                "family": "fixture_family",
                "language": "en",
                "round": 1,
                "error_code": "provider_timeout",
                "latency_ms": 2.0,
            }
        ]
    )
    assert summary["repeat_consistency"]["fixture"]["choice_noul_and_score_band_consistent"] is None
    assert suite.summarize([])["latency_ms"]["median"] is None


@pytest.mark.parametrize(
    "message, expected",
    [
        ("invalid_provider_response: PRIVATE_PAYLOAD", "invalid_provider_response"),
        (
            "Error executing tool omnia_evaluate: invalid_provider_response: PRIVATE_PAYLOAD",
            "invalid_provider_response",
        ),
        (
            "Error executing tool omnia_evaluate: Error executing tool omnia_evaluate_batch: "
            "provider_timeout: PRIVATE_PAYLOAD",
            "provider_timeout",
        ),
        (
            "Error executing tool private-tool: invalid_provider_response: PRIVATE_PAYLOAD",
            "tool_error",
        ),
        ("PRIVATE_PAYLOAD: invalid_provider_response: secret", "tool_error"),
        ("private_payload: secret", "tool_error"),
        ("Unrecognized wrapper: provider_timeout: PRIVATE_PAYLOAD", "tool_error"),
        ("Mention in prose invalid_provider_response: PRIVATE_PAYLOAD", "tool_error"),
        ("Error executing tool omnia_evaluate", "tool_error"),
    ],
)
def test_error_extraction_accepts_only_known_codes_and_official_wrappers(message, expected):
    response = CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)
    extracted = suite.safe_tool_error(response)
    assert extracted == expected
    assert "PRIVATE_PAYLOAD" not in extracted
    assert "secret" not in extracted


def test_error_extraction_ignores_success_content_and_limits_wrapper_depth():
    text = "invalid_provider_response: PRIVATE_PAYLOAD"
    success = CallToolResult(content=[TextContent(type="text", text=text)], is_error=False)
    assert suite.safe_tool_error(success) == "tool_error"
    text = "Error executing tool omnia_evaluate: " * 5 + text
    nested = CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)
    assert suite.safe_tool_error(nested) == "tool_error"


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_real_sdk_error_wrapper_retains_public_code_without_provider(tmp_path, mode):
    class ErrorService:
        closed = False

        async def evaluate(self, request):
            raise OmniaError("invalid_provider_response", "Provider response failed validation.")

        async def aclose(self):
            self.closed = True

    service = ErrorService()
    server = create_server(
        Settings(database=tmp_path / "not-created.sqlite3"),
        service_factory=lambda _: service,
    )
    async with Client(server, mode=mode) as client:
        result = await client.call_tool(
            "omnia_evaluate",
            {
                "request": {
                    "state": "Explicit fixture.",
                    "questions": {"check": {"type": "noul", "instructions": "Is this a fixture?"}},
                }
            },
        )
        assert result.is_error
        assert result.content[0].text.startswith("Error executing tool omnia_evaluate: ")
        assert suite.safe_tool_error(result) == "invalid_provider_response"
    assert service.closed
    assert not (tmp_path / "not-created.sqlite3").exists()


def test_validation_only_never_constructs_client_or_creates_output(tmp_path, monkeypatch, capsys):
    def forbidden_client(*args, **kwargs):
        pytest.fail("Validation-only mode must not construct a provider client.")

    monkeypatch.setattr(suite, "Client", forbidden_client)
    output = tmp_path / "not-created" / "report.json"
    assert suite.main(["--provider", "jev", "--output", str(output)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["planned_evaluations"] == 100
    assert report["network_started"] is False
    assert not output.parent.exists()


def test_invalid_dataset_does_not_echo_content(tmp_path, capsys):
    source = tmp_path / "invalid.json"
    source.write_text('{"private": "SECRET_DATA"}', encoding="utf-8")
    assert suite.main(["--provider", "jev", "--dataset", str(source)]) == 2
    captured = capsys.readouterr()
    assert "SECRET_DATA" not in captured.out + captured.err


@pytest.mark.parametrize("corruption", ["duplicate", "choice", "noul", "score", "missing"])
def test_dataset_rejects_unusable_acceptance_rules(tmp_path, corruption):
    dataset, _ = suite.load_dataset(ROOT / "evals/acceptance.json")
    broken = deepcopy(dataset)
    case = broken["cases"][0]
    if corruption == "duplicate":
        broken["cases"][1]["id"] = case["id"]
    elif corruption == "choice":
        case["expected"]["event"]["equals"] = "not-a-choice"
    elif corruption == "noul":
        case["expected"]["published"]["equals"] = "true"
    elif corruption == "score":
        case["expected"]["urgency"]["max"] = 3
    else:
        del case["expected"]["published"]
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError):
        suite.load_dataset(source)


async def test_execution_preserves_existing_report_before_reading_credentials(
    tmp_path, monkeypatch
):
    output = tmp_path / "report.json"
    original = b'{"immutable": true}\n'
    output.write_bytes(original)

    def forbidden_environment(*args, **kwargs):
        pytest.fail("Existing reports must be rejected before reading provider configuration.")

    monkeypatch.setattr(suite, "child_environment", forbidden_environment)
    args = SimpleNamespace(output=output, dataset=ROOT / "evals/acceptance.json")
    with pytest.raises(ValueError):
        await suite.execute(args, {}, "unused")
    assert output.read_bytes() == original


async def test_execution_rejects_report_ledger_collision_without_side_effects(
    tmp_path, monkeypatch
):
    output = tmp_path / "not-created" / "report.sqlite3"

    def forbidden_environment(*args, **kwargs):
        pytest.fail("Colliding output paths must fail before provider configuration is read.")

    monkeypatch.setattr(suite, "child_environment", forbidden_environment)
    args = SimpleNamespace(output=output, dataset=ROOT / "evals/acceptance.json")
    with pytest.raises(ValueError, match="different output paths"):
        await suite.execute(args, {}, "unused")
    assert not output.parent.exists()
