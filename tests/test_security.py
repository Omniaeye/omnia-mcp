# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""Adversarial lifecycle checks using a controlled provider; no live inference."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing

import pytest

from omnia_mcp.errors import OmniaError
from omnia_mcp.schemas import EvaluationRequest
from omnia_mcp.service import DecisionService
from omnia_mcp.settings import Settings


class GatedProvider:
    def __init__(self):
        self.entered = asyncio.Queue()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.calls = []
        self.closed = False
        self.response = {
            "model": "fixture",
            "answers": {"review": {"type": "noul", "noul": 0.95}},
        }

    def identity(self):
        return {"provider": "laya", "model": "fixture", "revision": "fixture"}

    def capabilities(self):
        return {"fixture": True}

    async def evaluate(self, state, questions):
        self.calls.append(state)
        self.entered.put_nowait(state)
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return self.response

    async def aclose(self):
        self.closed = True


def request(state="fixture"):
    return EvaluationRequest(
        state=state,
        questions={"review": {"type": "noul", "instructions": "Does this need review?"}},
    )


def make_service(tmp_path, **overrides):
    provider = GatedProvider()
    settings = Settings(database=tmp_path / "decisions.sqlite3", **overrides)
    return DecisionService(settings, {"laya": provider}), provider, settings


def audit(settings):
    with closing(sqlite3.connect(settings.database)) as connection:
        return {
            "decisions": connection.execute("SELECT count(*) FROM decisions").fetchone()[0],
            "claims": connection.execute("SELECT count(*) FROM claims").fetchone()[0],
            "failures": connection.execute("SELECT code FROM failures ORDER BY rowid").fetchall(),
        }


async def test_cancelling_one_waiter_preserves_other_and_one_audit(tmp_path):
    service, provider, settings = make_service(tmp_path)
    try:
        async with asyncio.timeout(3):
            first = asyncio.create_task(service.evaluate(request()))
            await provider.entered.get()
            second = asyncio.create_task(service.evaluate(request()))
            await asyncio.sleep(0)  # Let the second caller join the blocked inference.
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not provider.cancelled.is_set()
            provider.release.set()
            result = await second
            assert result.cached
            assert provider.calls == ["fixture"]
            assert audit(settings) == {"decisions": 1, "claims": 0, "failures": []}
    finally:
        await service.aclose()


async def test_last_waiter_cancels_provider_and_records_only_failure(tmp_path):
    service, provider, settings = make_service(tmp_path)
    try:
        async with asyncio.timeout(3):
            caller = asyncio.create_task(service.evaluate(request()))
            await provider.entered.get()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            await provider.cancelled.wait()
            assert audit(settings) == {
                "decisions": 0,
                "claims": 0,
                "failures": [("cancelled",)],
            }
            provider.release.set()
            retry = await service.evaluate(request())
            assert retry.status == "accepted"
            assert provider.calls == ["fixture", "fixture"]
    finally:
        await service.aclose()


async def test_cancelled_queued_work_does_not_run_and_capacity_recovers(tmp_path):
    service, provider, settings = make_service(
        tmp_path, max_concurrent_requests=1, max_queue_size=1
    )
    try:
        async with asyncio.timeout(3):
            active = asyncio.create_task(service.evaluate(request("active")))
            await provider.entered.get()
            queued = asyncio.create_task(service.evaluate(request("cancelled-queue")))
            await asyncio.sleep(0)
            await asyncio.sleep(0)  # Start the queued operation at its semaphore wait.
            with pytest.raises(OmniaError) as rejection:
                await service.evaluate(request("overflow"))
            assert rejection.value.code == "queue_full"
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            await asyncio.sleep(0)
            replacement = asyncio.create_task(service.evaluate(request("replacement")))
            await asyncio.sleep(0)
            provider.release.set()
            results = await asyncio.gather(active, replacement)
            assert all(result.status == "accepted" for result in results)
            assert provider.calls == ["active", "replacement"]
            assert audit(settings) == {
                "decisions": 2,
                "claims": 0,
                "failures": [("cancelled",)],
            }
    finally:
        await service.aclose()


@pytest.mark.parametrize(
    "corruption",
    [
        {"model": ""},
        {"model": {"private-secret": "must-not-escape"}},
        {"usage": {"input_tokens": True}},
        {"usage": {"input_tokens": -1}},
        {"usage": {"input_tokens": "private-secret"}},
        {"answers": {"review": {"type": "noul", "noul": float("nan")}}},
        {"answers": {"review": {"type": "noul", "noul": True}}},
        {"answers": {"review": {"type": "choice", "choice": "private-secret"}}},
    ],
)
async def test_invalid_provider_metadata_and_answers_fail_closed(tmp_path, corruption, caplog):
    service, provider, settings = make_service(tmp_path)
    provider.response.update(corruption)
    provider.release.set()
    try:
        with pytest.raises(OmniaError) as failure:
            await service.evaluate(request())
        assert failure.value.code == "invalid_provider_response"
        assert "private-secret" not in str(failure.value)
        assert "private-secret" not in caplog.text
        assert audit(settings) == {
            "decisions": 0,
            "claims": 0,
            "failures": [("invalid_provider_response",)],
        }
        with closing(sqlite3.connect(settings.database)) as connection:
            assert "private-secret" not in "\n".join(connection.iterdump())
    finally:
        await service.aclose()
