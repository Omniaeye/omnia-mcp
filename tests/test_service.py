# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""Lifecycle, concurrency and recovery checks with controlled provider boundaries."""

import asyncio
import sqlite3

import pytest

from omnia_mcp.errors import OmniaError
from omnia_mcp.schemas import BatchRequest, EvaluationRequest
from omnia_mcp.service import DecisionService
from omnia_mcp.settings import Settings


class CountingProvider:
    def __init__(self, delay=0):
        self.calls = 0
        self.delay = delay
        self.closed = False

    def identity(self):
        return {"provider": "laya", "revision": "a" * 40}

    def capabilities(self):
        return {"ready": True}

    async def evaluate(self, state, questions):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if state == "fail":
            raise RuntimeError("SECRET_TOKEN_MUST_NOT_ESCAPE")
        return {
            "model": "controlled-boundary",
            "answers": {key: {"type": "noul", "noul": 0.95} for key in questions},
        }

    async def aclose(self):
        self.closed = True


def request(state="Release published", **changes):
    return EvaluationRequest(
        state=state,
        questions={"release": {"type": "noul", "instructions": "Is this a release announcement?"}},
        **changes,
    )


def service(tmp_path, provider=None, **settings):
    backend = provider or CountingProvider()
    return DecisionService(
        Settings(database=tmp_path / "decisions.sqlite3", **settings), {"laya": backend}
    ), backend


async def test_concurrent_duplicates_share_one_inference_and_replay(tmp_path):
    engine, provider = service(tmp_path, CountingProvider(0.02))
    results = await asyncio.gather(*(engine.evaluate(request()) for _ in range(20)))
    assert provider.calls == 1
    assert len({result.decision_id for result in results}) == 1
    assert sum(result.cached for result in results) == 19
    saved = engine.get_decision(results[0].decision_id)
    await engine.aclose()
    reopened, other = service(tmp_path)
    replay = await reopened.evaluate(request())
    assert replay.cached and replay.decision_id == saved.decision_id
    assert other.calls == 0
    await reopened.aclose()


async def test_cancelled_caller_does_not_cancel_shared_work(tmp_path):
    engine, provider = service(tmp_path, CountingProvider(0.05))
    a = asyncio.create_task(engine.evaluate(request()))
    await asyncio.sleep(0.01)
    b_task = asyncio.create_task(engine.evaluate(request()))
    await asyncio.sleep(0.005)
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    b = await b_task
    assert b.status == "accepted" and provider.calls == 1
    await engine.aclose()


async def test_batch_keeps_good_items_and_sanitizes_failures(tmp_path):
    engine, provider = service(tmp_path)
    output = await engine.evaluate_batch(BatchRequest(items=[request(), request("fail")]))
    assert output.items[0].result.status == "accepted"
    assert output.items[1].error.code == "provider_failure"
    assert "SECRET" not in output.model_dump_json()
    assert engine._db().connection.execute("SELECT count(*) FROM failures").fetchone()[0] == 1
    await engine.aclose()


async def test_limits_are_applied_before_provider(tmp_path):
    engine, provider = service(tmp_path, max_input_bytes=1024, max_batch_items=1)
    with pytest.raises(OmniaError) as error:
        await engine.evaluate(request("x" * 2048))
    assert error.value.code == "input_limit"
    with pytest.raises(OmniaError):
        await engine.evaluate(request(provider="jev"))
    with pytest.raises(OmniaError):
        await engine.evaluate_batch(BatchRequest(items=[request(), request()]))
    assert provider.calls == 0
    await engine.aclose()


async def test_queue_rejects_excess_without_losing_accepted_work(tmp_path):
    engine, provider = service(
        tmp_path, CountingProvider(0.05), max_concurrent_requests=1, max_queue_size=0
    )
    task = asyncio.create_task(engine.evaluate(request()))
    await asyncio.sleep(0.01)
    with pytest.raises(OmniaError) as error:
        await engine.evaluate(request("second"))
    assert error.value.code == "queue_full"
    assert (await task).status == "accepted"
    await engine.aclose()


async def test_quota_survives_restart_and_cache_does_not_spend(tmp_path):
    engine, provider = service(tmp_path, max_requests_per_day=1)
    await engine.evaluate(request())
    assert (await engine.evaluate(request())).cached
    await engine.aclose()
    again, backend = service(tmp_path, max_requests_per_day=1)
    with pytest.raises(OmniaError) as error:
        await again.evaluate(request("another"))
    assert error.value.code == "rate_limited"
    assert backend.calls == 0
    await again.aclose()


async def test_read_only_lookup_never_initializes_database(tmp_path):
    engine, provider = service(tmp_path)
    assert engine.get_decision("missing") is None
    assert not (tmp_path / "decisions.sqlite3").exists()
    assert engine.capabilities()["transport"] == "stdio"
    await engine.aclose()


async def test_state_not_persisted(tmp_path):
    engine, provider = service(tmp_path)
    marker = "PRIVATE_INPUT_123456789"
    await engine.evaluate(request(marker))
    await engine.aclose()
    connection = sqlite3.connect(tmp_path / "decisions.sqlite3")
    assert marker not in "\n".join(connection.iterdump())
    connection.close()


async def test_shutdown_closes_provider_and_rejects_new_calls(tmp_path):
    engine, provider = service(tmp_path)
    await engine.aclose()
    assert provider.closed
    with pytest.raises(OmniaError) as error:
        await engine.evaluate(request())
    assert error.value.code == "server_closed"


async def test_local_batch_serializes_inference_instead_of_failing_busy(tmp_path):
    class ExclusiveProvider(CountingProvider):
        active = False

        async def evaluate(self, state, questions):
            assert not self.active
            self.active = True
            try:
                return await super().evaluate(state, questions)
            finally:
                self.active = False

    engine, backend = service(tmp_path, ExclusiveProvider(0.01))
    result = await engine.evaluate_batch(BatchRequest(items=[request(str(i)) for i in range(4)]))
    assert all(item.result is not None for item in result.items)
    assert backend.calls == 4
    await engine.aclose()


@pytest.mark.parametrize("provider_name", ["jev", "laya"])
async def test_service_uses_provider_wire_precision_for_rounded_score(tmp_path, provider_name):
    class RoundedScoreProvider(CountingProvider):
        def identity(self):
            return {"provider": provider_name, "revision": "wire-precision-fixture"}

        async def evaluate(self, state, questions):
            self.calls += 1
            return {
                "model": "wire-precision-fixture",
                "answers": {
                    "urgency": {
                        "type": "score",
                        "score": 1.99,
                        "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0},
                    }
                },
            }

    backend = RoundedScoreProvider()
    engine = DecisionService(
        Settings(
            database=tmp_path / f"{provider_name}.sqlite3",
            default_provider=provider_name,
            allowed_providers=(provider_name,),
        ),
        {provider_name: backend},
    )
    evaluation = EvaluationRequest(
        state="Authored fixture: a stable software release was published.",
        provider=provider_name,
        questions={
            "urgency": {
                "type": "score",
                "instructions": "Use the declared three-level fixture rubric.",
                "criteria": ["Documentation only", "Planned release", "Published release"],
            }
        },
    )
    try:
        if provider_name == "jev":
            result = await engine.evaluate(evaluation)
            assert result.answers["urgency"].score == 1.99
            assert result.answers["urgency"].probabilities == {"0": 0.0, "1": 0.0, "2": 1.0}
            assert result.policy.min_probability == 0.8
            assert result.status == "accepted"
            persisted = engine.get_decision(result.decision_id)
            assert persisted.answers == result.answers
            assert (await engine.evaluate(evaluation)).cached
        else:
            with pytest.raises(OmniaError) as rejected:
                await engine.evaluate(evaluation)
            assert rejected.value.code == "invalid_provider_response"
            ledger = engine._db().connection
            assert ledger.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
            assert ledger.execute("SELECT code FROM failures").fetchall() == [
                ("invalid_provider_response",)
            ]
        assert backend.calls == 1
    finally:
        await engine.aclose()
    assert backend.closed
