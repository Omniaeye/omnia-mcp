# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""Run authored acceptance cases through real MCP stdio, only with explicit --run.

This measures agreement with a small synthetic rubric, not production accuracy.
No evaluation, provider process, database, or network is started without --run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from omnia_mcp.schemas import DecisionResult, EvaluationRequest  # noqa: E402

PROTOCOL = "2026-07-28"


def load_dataset(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    dataset = json.loads(raw.decode("utf-8-sig"))
    if (
        dataset.get("schema_version") != "omnia.acceptance.v1"
        or dataset.get("provenance") != "authored_acceptance_fixture"
        or not isinstance(dataset.get("cases"), list)
        or not 1 <= len(dataset["cases"]) <= 100
    ):
        raise ValueError("Invalid authored acceptance dataset.")
    identifiers = set()
    for case in dataset["cases"]:
        identifier = case.get("id", "")
        if not re.fullmatch(r"[a-z0-9_-]{1,80}", identifier) or identifier in identifiers:
            raise ValueError("Invalid or duplicate case identifier.")
        identifiers.add(identifier)
        if not all(isinstance(case.get(field), str) for field in ("family", "language")):
            raise ValueError("Missing case classification metadata.")
        request = EvaluationRequest.model_validate(case["request"])
        expected = case["expected"]
        if set(expected) != set(request.questions):
            raise ValueError("Every question must have exactly one acceptance rule.")
        for name, question in request.questions.items():
            rule = expected[name]
            if rule.get("type") != question.type:
                raise ValueError("Acceptance rule type differs from the question.")
            if question.type == "choice":
                if rule.get("equals") not in question.criteria:
                    raise ValueError("Expected choice is not a criterion.")
            elif question.type == "noul":
                if type(rule.get("equals")) is not bool or rule.get("threshold") != 0.5:
                    raise ValueError(
                        "Noul acceptance requires an explicit boolean and threshold 0.5."
                    )
            elif not (
                type(rule.get("min")) in (int, float)
                and type(rule.get("max")) in (int, float)
                and 0 <= rule["min"] <= rule["max"] <= len(question.criteria) - 1
            ):
                raise ValueError("Invalid inclusive ordinal score band.")
    return dataset, hashlib.sha256(raw).hexdigest()


def compare_answers(result: DecisionResult, expected: dict) -> list[dict]:
    checks = []
    for name, rule in expected.items():
        answer = result.answers[name]
        if rule["type"] == "choice":
            observed = answer.choice
            passed = observed == rule["equals"]
        elif rule["type"] == "noul":
            observed = answer.noul >= rule["threshold"]
            passed = observed == rule["equals"]
        else:
            observed = answer.score
            passed = rule["min"] <= observed <= rule["max"]
        checks.append({"question": name, "expected": rule, "observed": observed, "passed": passed})
    return checks


def child_environment(args, database: Path) -> dict[str, str]:
    names = (
        "PATH",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "HF_HOME",
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
    )
    environment = {name: os.environ[name] for name in names if name in os.environ}
    environment.update(
        {
            "PYTHONPATH": str(ROOT / "src"),
            "OMNIA_DATABASE": str(database),
            "OMNIA_DEFAULT_PROVIDER": args.provider,
            "OMNIA_ALLOWED_PROVIDERS": args.provider,
            "OMNIA_CACHE_TTL_SECONDS": "0",
            "OMNIA_REQUEST_TIMEOUT_SECONDS": "90",
            "OMNIA_MAX_RETRIES": "0",
            "OMNIA_MAX_CONCURRENT_REQUESTS": str(args.concurrency),
            "OMNIA_MAX_QUEUE_SIZE": str(args.concurrency),
            "OMNIA_LOG_LEVEL": "WARNING",
        }
    )
    if args.provider == "jev":
        key = os.environ.get("TYPESAFE_API_KEY", "")
        if not key.strip():
            raise ValueError("JEV execution requires TYPESAFE_API_KEY in the process environment.")
        environment["TYPESAFE_API_KEY"] = key
        if model := os.environ.get("OMNIA_JEV_MODEL"):
            environment["OMNIA_JEV_MODEL"] = model
    else:
        if args.concurrency != 1:
            raise ValueError("The local LAYA provider requires --concurrency 1.")
        revision = os.environ.get("OMNIA_LAYA_REVISION", "")
        if not re.fullmatch("[0-9a-f]{40}", revision):
            raise ValueError(
                "LAYA execution requires OMNIA_LAYA_REVISION as a complete commit SHA."
            )
        for field in ("MODEL", "REVISION", "DEVICE", "THREADS", "MAX_LEN", "HEAD_MAX_LEN"):
            key = "OMNIA_LAYA_" + field
            if key in os.environ:
                environment[key] = os.environ[key]
        environment.update(
            {
                "OMNIA_LAYA_OFFLINE": "true",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_PROGRESS_BARS": "1",
            }
        )
    return environment


# Only known public codes may enter reports. Free-form text, even before a colon,
# can contain secrets. Unknown future codes deliberately retain the generic label.
PUBLIC_ERROR_CODES = frozenset(
    "batch_limit calibration_requires_review checkpoint_incomplete "
    "checkpoint_revision_mismatch claim_expired context_budget_exceeded "
    "device_fallback_rejected device_unavailable evaluation_timeout evaluation_failed "
    "incompatible_checkpoint incompatible_runtime input_limit internal_error "
    "invalid_decision_id invalid_provider_response invalid_request option_limit "
    "precision_fallback_rejected provider_authentication provider_busy provider_closed "
    "provider_disabled provider_failure provider_model_mismatch provider_not_configured "
    "provider_not_installed provider_rate_limited provider_rejected provider_timeout "
    "provider_unavailable question_limit queue_full rate_limited request_too_large "
    "response_too_large server_closed storage_schema_mismatch storage_unavailable "
    "unsupported_configuration unsupported_request local_inference_failed "
    "local_worker_failed batch_failed capabilities_unavailable decision_unavailable "
    "decision_not_found unknown_tool".split()
)
SDK_TOOL_PREFIX = re.compile(
    r"^Error executing tool omnia_(?:evaluate(?:_batch)?|capabilities|get_decision): "
)


def safe_tool_error(result) -> str:
    """Extract an allowlisted code from direct errors or the official SDK wrapper."""
    if not result.is_error:
        return "tool_error"
    for content in result.content:
        text = getattr(content, "text", None)
        if not isinstance(text, str):
            continue
        text = text[:4096].strip()
        # SDK 2.2 wraps anticipated errors as 'Error executing tool <name>: ...'.
        # Limit nesting and accept only this server's fixed tool names.
        for _ in range(4):
            prefix = SDK_TOOL_PREFIX.match(text)
            if prefix is None:
                break
            text = text[prefix.end() :]
        match = re.match(r"^([a-z][a-z0-9_]{0,63}):", text)
        if match and match[1] in PUBLIC_ERROR_CODES:
            return match[1]
    return "tool_error"


def summarize(records: list[dict]) -> dict:
    successful = [record for record in records if record.get("result")]
    latencies = sorted(record["latency_ms"] for record in records)
    groups = {}
    for dimension in ("family", "language"):
        groups[dimension] = {}
        for label in sorted({record[dimension] for record in records}):
            members = [record for record in records if record[dimension] == label]
            groups[dimension][label] = {
                "calls": len(members),
                "rubric_passes": sum(record.get("rubric_passed", False) for record in members),
                "failed_calls": sum(bool(record.get("error_code")) for record in members),
            }
    repeats = {}
    for identifier in sorted({record["case_id"] for record in records}):
        members = [record for record in successful if record["case_id"] == identifier]
        signatures = []
        for record in members:
            signatures.append(
                tuple(
                    (
                        check["question"],
                        check["observed"]
                        if check["expected"]["type"] != "score"
                        else check["passed"],
                    )
                    for check in record["checks"]
                )
            )
        repeats[identifier] = {
            "completed": len(members),
            "rubric_passes": sum(record["rubric_passed"] for record in members),
            "choice_noul_and_score_band_consistent": len(set(signatures)) <= 1 if members else None,
        }
    return {
        "evaluation_calls_recorded": len(records),
        "completed_decisions": len(successful),
        "failed_calls": sum(bool(record.get("error_code")) for record in records),
        "uncached_decisions": sum(not record["result"]["cached"] for record in successful),
        "unique_decision_ids": len({record["result"]["decision_id"] for record in successful}),
        "rubric_passes": sum(record.get("rubric_passed", False) for record in records),
        "rubric_mismatches": sum(not record["rubric_passed"] for record in successful),
        "question_checks": sum(len(record["checks"]) for record in successful),
        "question_checks_passed": sum(
            check["passed"] for record in successful for check in record["checks"]
        ),
        "decision_statuses": dict(Counter(record["result"]["status"] for record in successful)),
        "error_codes": dict(
            Counter(record["error_code"] for record in records if record.get("error_code"))
        ),
        "latency_ms": {
            "min": min(latencies) if latencies else None,
            "median": round(statistics.median(latencies), 3) if latencies else None,
            "p95": latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "by": groups,
        "repeat_consistency": repeats,
    }


def save_report(path: Path, report: dict) -> None:
    report["summary"] = summarize(report["records"])
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def execute(args, dataset: dict, digest: str) -> int:
    output = args.output.resolve()
    database = output.with_suffix(".sqlite3")
    if output == database:
        raise ValueError("The report and SQLite ledger must use different output paths.")
    if output == args.dataset.resolve() or output.exists() or database.exists():
        raise ValueError("Choose a fresh output path; existing reports and ledgers are preserved.")
    environment = child_environment(args, database)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "omnia.acceptance-report.v1",
        "provenance": dataset["provenance"],
        "dataset_sha256": digest,
        "provider": args.provider,
        "protocol": PROTOCOL,
        "repeats": args.repeats,
        "concurrency": args.concurrency,
        "planned_evaluations": len(dataset["cases"]) * args.repeats,
        "cache_ttl_seconds": 0,
        "provider_automatic_retries": 0,
        "production_accuracy_claimed": False,
        "interpretation": dataset["interpretation"],
        "started_at": datetime.now(UTC).isoformat(),
        "run_status": "running",
        "records": [],
    }
    save_report(output, report)
    target = StdioServerParameters(
        command=sys.executable, args=["-m", "omnia_mcp", "serve"], env=environment, cwd=ROOT
    )
    started = time.perf_counter()
    limiter = asyncio.Semaphore(args.concurrency)
    try:
        async with Client(target, mode=PROTOCOL, read_timeout_seconds=120) as client:
            capabilities = await client.call_tool("omnia_capabilities", {})
            if capabilities.is_error:
                raise RuntimeError("Server capability check failed.")
            if capabilities.structured_content["cache_ttl_seconds"] != 0:
                raise RuntimeError("Uncached evaluation was not configured.")

            async def evaluate_case(round_number, case):
                async with limiter:
                    beginning = time.perf_counter()
                    record = {
                        "case_id": case["id"],
                        "family": case["family"],
                        "language": case["language"],
                        "round": round_number,
                    }
                    try:
                        request = {**case["request"], "provider": args.provider}
                        response = await client.call_tool("omnia_evaluate", {"request": request})
                        if response.is_error:
                            record["error_code"] = safe_tool_error(response)
                        else:
                            result = DecisionResult.model_validate(response.structured_content)
                            checks = compare_answers(result, case["expected"])
                            record.update(
                                {
                                    "result": result.model_dump(mode="json"),
                                    "checks": checks,
                                    "rubric_passed": all(check["passed"] for check in checks),
                                }
                            )
                            if result.cached:
                                record["error_code"] = "unexpected_cache_hit"
                            if result.provider != args.provider:
                                record["error_code"] = "unexpected_provider"
                    except Exception:
                        record["error_code"] = "evaluation_or_validation_failed"
                    record["latency_ms"] = round((time.perf_counter() - beginning) * 1000, 3)
                    report["records"].append(record)
                    save_report(output, report)
                    completed = len(report["records"])
                    if completed % 10 == 0 or completed == report["planned_evaluations"]:
                        print(
                            json.dumps(
                                {
                                    "completed": completed,
                                    "planned": report["planned_evaluations"],
                                    "failed_calls": report["summary"]["failed_calls"],
                                    "rubric_mismatches": report["summary"]["rubric_mismatches"],
                                }
                            ),
                            flush=True,
                        )

            for round_number in range(1, args.repeats + 1):
                await asyncio.gather(
                    *(evaluate_case(round_number, case) for case in dataset["cases"])
                )
        report["run_status"] = "completed"
    except asyncio.CancelledError:
        report["run_status"] = "interrupted"
        raise
    except Exception:
        report["run_status"] = "infrastructure_failed"
        report["infrastructure_error_code"] = "mcp_session_failed"
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["total_seconds"] = round(time.perf_counter() - started, 3)
        report["records"].sort(key=lambda record: (record["round"], record["case_id"]))
        save_report(output, report)
    summary = report["summary"]
    print(
        json.dumps(
            {
                "run_status": report["run_status"],
                "evaluation_calls_recorded": summary["evaluation_calls_recorded"],
                "completed_decisions": summary["completed_decisions"],
                "uncached_decisions": summary["uncached_decisions"],
                "unique_decision_ids": summary["unique_decision_ids"],
                "failed_calls": summary["failed_calls"],
                "rubric_passes": summary["rubric_passes"],
                "rubric_mismatches": summary["rubric_mismatches"],
                "production_accuracy_claimed": False,
            }
        ),
        flush=True,
    )
    return int(
        report["run_status"] != "completed"
        or summary["completed_decisions"] != report["planned_evaluations"]
        or summary["failed_calls"] != 0
        or summary["rubric_mismatches"] != 0
        or summary["unique_decision_ids"] != report["planned_evaluations"]
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("jev", "laya"), required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals" / "acceptance.json")
    parser.add_argument("--repeats", type=int, choices=range(1, 21), default=5)
    parser.add_argument("--concurrency", type=int, choices=(1, 2), default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run", action="store_true", help="Authorize real provider evaluations")
    args = parser.parse_args(argv)
    try:
        dataset, digest = load_dataset(args.dataset)
    except Exception:
        print("Dataset validation failed; no provider was started.", file=sys.stderr)
        return 2
    if not args.run:
        print(
            json.dumps(
                {
                    "mode": "validation_only",
                    "provider_started": False,
                    "network_started": False,
                    "cases": len(dataset["cases"]),
                    "repeats": args.repeats,
                    "planned_evaluations": len(dataset["cases"]) * args.repeats,
                    "dataset_sha256": digest,
                    "requires_explicit_run_flag": True,
                }
            )
        )
        return 0
    if args.output is None:
        parser.error("--run requires --output for a durable report and sibling SQLite ledger")
    try:
        return asyncio.run(execute(args, dataset, digest))
    except KeyboardInterrupt:
        print("Interrupted; any completed results remain in the report.", file=sys.stderr)
        return 130
    except ValueError:
        print(
            "Invalid execution configuration or occupied output path; no run started.",
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            "Acceptance execution failed; inspect the sanitized report if created.", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
