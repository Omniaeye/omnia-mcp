# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""Transport-independent evaluation with durable records and bounded admission."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from datetime import UTC, datetime

from omnia_mcp import __version__
from omnia_mcp.decision.validation import encode, fingerprint, review, validate_answers
from omnia_mcp.errors import OmniaError
from omnia_mcp.schemas import (
    BatchItemResult,
    BatchRequest,
    BatchResult,
    DecisionResult,
    EvaluationRequest,
    PublicError,
)
from omnia_mcp.settings import Settings
from omnia_mcp.storage import Ledger, read_decision

logger = logging.getLogger(__name__)


class DecisionService:
    def __init__(self, settings: Settings, providers: dict | None = None):
        self.settings = settings
        if providers is None:
            providers = {}
            if "jev" in settings.allowed_providers:
                from omnia_mcp.providers.jev import JevProvider

                providers["jev"] = JevProvider(settings)
            if "laya" in settings.allowed_providers:
                from omnia_mcp.providers.laya import LayaProvider

                providers["laya"] = LayaProvider(settings)
        self.providers = providers
        self._ledger = None
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        self._provider_semaphores = {
            name: asyncio.Semaphore(1 if name == "laya" else settings.max_concurrent_requests)
            for name in providers
        }
        self._inflight: dict[str, asyncio.Task] = {}
        self._callers = 0
        self._waiters: dict[str, int] = {}
        self._closed = False
        self._last_prune = 0.0

    def _db(self) -> Ledger:
        if self._ledger is None:
            self._ledger = Ledger(self.settings.database)
        if time.monotonic() - self._last_prune > 3600:
            self._ledger.prune(self.settings.retention_days)
            self._last_prune = time.monotonic()
        return self._ledger

    def capabilities(self) -> dict:
        return {
            "server": "omnia-mcp",
            "version": __version__,
            "transport": "stdio",
            "question_types": ["choice", "score", "noul"],
            **self.settings.public(),
            "providers": {
                name: provider.capabilities() for name, provider in self.providers.items()
            },
            "execution_authorized": False,
        }

    def get_decision(self, decision_id: str) -> DecisionResult | None:
        if not isinstance(decision_id, str) or not 1 <= len(decision_id) <= 128:
            raise OmniaError("invalid_decision_id", "A bounded decision identifier is required.")
        try:
            return read_decision(self.settings.database, decision_id)
        except Exception:
            raise OmniaError(
                "storage_unavailable", "Decision storage is unavailable.", True
            ) from None

    def _prepare(self, request: EvaluationRequest):
        name = request.provider or self.settings.default_provider
        if name not in self.settings.allowed_providers or name not in self.providers:
            raise OmniaError("provider_disabled", "Requested provider is not enabled.")
        payload = request.model_dump(mode="json")
        if len(encode(payload)) > self.settings.max_input_bytes:
            raise OmniaError("input_limit", "Request exceeds the configured byte limit.")
        if len(request.questions) > self.settings.max_questions:
            raise OmniaError("question_limit", "Request exceeds the configured question limit.")
        questions = {
            key: question.model_dump(mode="json", exclude_none=True)
            for key, question in request.questions.items()
        }
        for question in questions.values():
            criteria = question.get("criteria", {})
            if len(criteria) > self.settings.max_options:
                raise OmniaError("option_limit", "Question exceeds the configured option limit.")
            if name == "jev" and question["type"] == "score" and len(criteria) > 10:
                raise OmniaError("option_limit", "JEV score supports at most ten rubric levels.")
        provider = self.providers[name]
        identity = provider.identity()
        key = fingerprint(
            {
                "schema": "omnia.mcp.decision.v1",
                "server": __version__,
                "provider": name,
                "identity": identity,
                "request": payload,
            }
        )
        return name, provider, identity, questions, key

    async def evaluate(self, request: EvaluationRequest) -> DecisionResult:
        if self._closed:
            raise OmniaError("server_closed", "Decision service is shutting down.", True)
        if self._callers >= self.settings.max_concurrent_requests + self.settings.max_queue_size:
            raise OmniaError("queue_full", "Decision queue is full; retry later.", True)
        self._callers += 1
        key = None
        task = None
        waiting = False
        try:
            name, provider, identity, questions, key = self._prepare(request)
            existing = self._db().cached(key, self.settings.cache_ttl_seconds)
            if existing:
                return existing.model_copy(update={"cached": True})
            task = self._inflight.get(key)
            if task is not None and (task.done() or task.cancelling()):
                self._inflight.pop(key, None)
                task = None
            shared = task is not None
            if task is None:
                if (
                    len(self._inflight)
                    >= self.settings.max_concurrent_requests + self.settings.max_queue_size
                ):
                    raise OmniaError("queue_full", "Decision queue is full; retry later.", True)
                task = asyncio.create_task(
                    self._run(request, name, provider, identity, questions, key)
                )
                self._inflight[key] = task
                task.add_done_callback(lambda done: self._finished(key, done))
            self._waiters[key] = self._waiters.get(key, 0) + 1
            waiting = True
            result = await asyncio.shield(task)
            return result.model_copy(update={"cached": True}) if shared else result
        except OmniaError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("decision_failed code=internal_error")
            raise OmniaError("internal_error", "Evaluation could not be completed.", True) from None
        finally:
            if waiting and key is not None:
                remaining = self._waiters.get(key, 1) - 1
                if remaining:
                    self._waiters[key] = remaining
                else:
                    self._waiters.pop(key, None)
                    if task is not None and not task.done():
                        task.cancel()
            self._callers -= 1

    def _finished(self, key: str, task: asyncio.Task) -> None:
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)
        if not task.cancelled():
            task.exception()

    async def _run(self, request, name, provider, identity, questions, key) -> DecisionResult:
        owner = None
        start = time.perf_counter()
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                async with self._provider_semaphores[name], self._semaphore:
                    ledger = self._db()
                    while owner is None:
                        cached = ledger.cached(key, self.settings.cache_ttl_seconds)
                        if cached:
                            return cached.model_copy(update={"cached": True})
                        owner = ledger.claim(key, self.settings.request_timeout_seconds + 30)
                        if owner is None:
                            await asyncio.sleep(0.05)
                    # A different process may have completed between cache lookup and ownership.
                    cached = ledger.cached(key, self.settings.cache_ttl_seconds)
                    if cached:
                        return cached.model_copy(update={"cached": True})
                    ledger.reserve_usage(
                        self.settings.max_requests_per_minute, self.settings.max_requests_per_day
                    )
                    raw = await provider.evaluate(request.state, questions)
                    answers = validate_answers(
                        raw, questions, decimal_places=2 if name == "jev" else 4
                    )
                    model = raw.get("model")
                    if not isinstance(model, str) or not 1 <= len(model) <= 200:
                        raise OmniaError(
                            "invalid_provider_response", "Provider model identity is missing."
                        )
                    usage = raw.get("usage")
                    if usage is not None:
                        if not isinstance(usage, dict) or any(
                            type(value) is not int or value < 0 for value in usage.values()
                        ):
                            raise OmniaError(
                                "invalid_provider_response", "Provider usage is invalid."
                            )
                        usage = {
                            key: value
                            for key, value in usage.items()
                            if key in {"input_tokens", "output_tokens", "total_tokens"}
                        }
                    reasons = review(answers, request.policy)
                    result = DecisionResult(
                        decision_id=uuid.uuid4().hex,
                        input_hash=key,
                        provider=name,
                        model=model,
                        identity=identity,
                        created_at=datetime.now(UTC),
                        answers=answers,
                        status="needs_review" if reasons else "accepted",
                        review_reasons=reasons,
                        policy=request.policy,
                        evidence=request.evidence,
                        usage=usage,
                        duration_ms=round((time.perf_counter() - start) * 1000, 3),
                    )
                    ledger.complete(key, owner, result)
                    return result
        except TimeoutError:
            self._record_failure(key, "evaluation_timeout")
            raise OmniaError("evaluation_timeout", "Evaluation deadline exceeded.", True) from None
        except OmniaError as error:
            self._record_failure(key, error.code)
            raise
        except asyncio.CancelledError:
            self._record_failure(key, "cancelled")
            raise
        except Exception:
            self._record_failure(key, "provider_failure")
            raise OmniaError(
                "provider_failure", "Provider could not complete the evaluation.", True
            ) from None
        finally:
            if owner and self._ledger:
                with contextlib.suppress(Exception):
                    self._ledger.release(key, owner)

    def _record_failure(self, key, code):
        if self._ledger:
            with contextlib.suppress(Exception):
                self._ledger.failure(key, code)

    async def evaluate_batch(self, request: BatchRequest) -> BatchResult:
        if len(request.items) > self.settings.max_batch_items:
            raise OmniaError("batch_limit", "Batch exceeds the configured item limit.")
        if len(encode(request.model_dump(mode="json"))) > self.settings.max_input_bytes:
            raise OmniaError("input_limit", "Batch exceeds the configured byte limit.")

        async def one(index, item):
            try:
                return BatchItemResult(index=index, result=await self.evaluate(item))
            except OmniaError as error:
                return BatchItemResult(index=index, error=PublicError(**error.as_dict()))

        items = await asyncio.gather(
            *(one(index, item) for index, item in enumerate(request.items))
        )
        return BatchResult(items=items)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(
            *(provider.aclose() for provider in self.providers.values()), return_exceptions=True
        )
        if self._ledger:
            self._ledger.close()
