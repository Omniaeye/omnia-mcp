# Copyright 2026 OMNIA EYE Corporation
# SPDX-License-Identifier: Apache-2.0
"""Bounded TypeSafe transport. Official wire contract: https://docs.typesafe.ai/api."""

from __future__ import annotations

import asyncio
import json
import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from ..errors import OmniaError
from .base import copy_request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def _invalid_constant(_value: str) -> None:
    raise ValueError("nonfinite JSON")


def _retry_delay(attempt: int, header: str | None) -> float:
    """Honor Retry-After; the caller refuses delays beyond its remaining deadline."""
    delay = min(0.25 * 2**attempt, 2.0)
    if header:
        try:
            supplied = float(header)
        except ValueError:
            try:
                supplied = (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                supplied = delay
        if math.isfinite(supplied):
            delay = max(delay, supplied)
    return max(0.0, delay)


class JevProvider:
    def __init__(self, settings: Any, *, transport: httpx.AsyncBaseTransport | None = None):
        self._model = settings.jev_model
        self._key = settings.typesafe_api_key
        self._timeout = float(settings.request_timeout_seconds)
        self._retries = int(settings.max_retries)
        self._max_bytes = int(settings.max_response_bytes)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._closed = False

    def identity(self) -> dict[str, Any]:
        return {"provider": "jev", "model": self._model, "revision": self._model}

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "jev",
            "configured": bool(self._key and self._key.get_secret_value().strip()),
            "remote": True,
            "question_types": ["choice", "score", "noul"],
            "score_semantics": "zero_based_expected_level",
            "confidence_semantics": "provider_native_distribution_confidence",
            "max_choice_options": 255,
            "max_score_levels": 10,
            "max_response_bytes": self._max_bytes,
            "timeout_seconds": self._timeout,
            "automatic_retries": self._retries,
        }

    async def evaluate(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        if self._closed:
            raise OmniaError("provider_closed", "The provider has been closed.")
        if self._key is None or not self._key.get_secret_value().strip():
            raise OmniaError("provider_not_configured", "A TypeSafe API key is required.")
        state, questions = copy_request(state, questions)
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
                headers={
                    "Authorization": "Bearer " + self._key.get_secret_value(),
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            )
        try:
            async with asyncio.timeout(self._timeout):
                return await self._evaluate(
                    {"model": self._model, "state": state, "questions": questions},
                    deadline=asyncio.get_running_loop().time() + self._timeout,
                )
        except (TimeoutError, httpx.TimeoutException):
            raise OmniaError(
                "provider_timeout", "The TypeSafe request exceeded its deadline.", retryable=True
            ) from None
        except httpx.RequestError:
            raise OmniaError(
                "provider_unavailable", "The TypeSafe transport failed.", retryable=True
            ) from None

    async def _evaluate(self, payload: dict[str, Any], *, deadline: float) -> dict[str, Any]:
        assert self._client is not None
        for attempt in range(self._retries + 1):
            async with self._client.stream("POST", ENDPOINT, json=payload) as response:
                status = response.status_code
                retryable = status in (429, 529) or 500 <= status < 600
                if status != 200:
                    if retryable and attempt < self._retries:
                        delay = _retry_delay(attempt, response.headers.get("retry-after"))
                        if delay >= deadline - asyncio.get_running_loop().time():
                            code = (
                                "provider_rate_limited" if status == 429 else "provider_unavailable"
                            )
                            raise OmniaError(
                                code,
                                "TypeSafe requested a retry after this request's deadline.",
                                retryable=True,
                            )
                    else:
                        code = (
                            "provider_authentication"
                            if status in (401, 403)
                            else "provider_rate_limited"
                            if status == 429
                            else "provider_unavailable"
                            if retryable
                            else "provider_rejected"
                        )
                        raise OmniaError(
                            code, "TypeSafe rejected the request.", retryable=retryable
                        )
                else:
                    return await self._read_result(response)
            # Close the response before waiting; error bodies never enter logs or caller errors.
            await asyncio.sleep(delay)
        raise OmniaError("provider_unavailable", "TypeSafe retries were exhausted.", retryable=True)

    async def _read_result(self, response: httpx.Response) -> dict[str, Any]:
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise OmniaError(
                "invalid_provider_response", "Compressed provider responses are not accepted."
            )
        try:
            if int(response.headers.get("content-length", "0")) > self._max_bytes:
                raise OmniaError("response_too_large", "TypeSafe response exceeds the byte budget.")
        except ValueError:
            pass
        raw = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=min(65536, self._max_bytes + 1)):
            if len(raw) + len(chunk) > self._max_bytes:
                raise OmniaError("response_too_large", "TypeSafe response exceeds the byte budget.")
            raw.extend(chunk)
        try:
            value = json.loads(raw, parse_constant=_invalid_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise OmniaError(
                "invalid_provider_response", "TypeSafe returned invalid JSON."
            ) from None
        if not isinstance(value, dict) or not isinstance(value.get("answers"), dict):
            raise OmniaError(
                "invalid_provider_response", "TypeSafe returned an invalid response envelope."
            )
        if value.get("model") != self._model:
            raise OmniaError(
                "provider_model_mismatch", "TypeSafe returned a different model version."
            )
        return value

    async def aclose(self) -> None:
        self._closed = True
        if self._client is not None:
            await self._client.aclose()
