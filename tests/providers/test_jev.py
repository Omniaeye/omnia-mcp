# Copyright 2026 OMNIA EYE Corporation
# SPDX-License-Identifier: Apache-2.0
"""Transport doubles only: no request reaches TypeSafe."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from omnia_mcp.errors import OmniaError
from omnia_mcp.providers.jev import ENDPOINT, JevProvider, _retry_delay

QUESTIONS = {"q": {"type": "noul", "instructions": "Is there evidence?"}}
RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {"q": {"type": "noul", "noul": 0.2}},
    "usage": {"input_tokens": 10, "output_tokens": 4},
}


def settings(**changes):
    value = {
        "jev_model": "jev-1.13.0",
        "typesafe_api_key": SecretStr("dummy-test-key"),
        "request_timeout_seconds": 2.0,
        "max_retries": 2,
        "max_response_bytes": 1024,
    }
    return SimpleNamespace(**(value | changes))


async def test_native_response_and_fixed_endpoint():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=RESPONSE)

    provider = JevProvider(settings(), transport=httpx.MockTransport(handle))
    try:
        assert await provider.evaluate({"evidence": "reported"}, QUESTIONS) == RESPONSE
        assert str(requests[0].url) == ENDPOINT
        assert requests[0].method == "POST"
        assert requests[0].headers["authorization"] == "Bearer dummy-test-key"
        assert json.loads(requests[0].content)["model"] == "jev-1.13.0"
        public = json.dumps([provider.identity(), provider.capabilities()])
        assert "dummy-test-key" not in public
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "provider_authentication"),
        (403, "provider_authentication"),
        (422, "provider_rejected"),
        (302, "provider_rejected"),
    ],
)
async def test_rejections_are_sanitized_and_redirects_not_followed(status, code):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"location": "https://untrusted.invalid/"},
            text="private input and dummy-test-key",
        )

    provider = JevProvider(settings(), transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate("evidence", QUESTIONS)
        assert caught.value.code == code
        assert len(calls) == 1
        assert "private input" not in str(caught.value)
        assert "dummy-test-key" not in str(caught.value)
    finally:
        await provider.aclose()


@pytest.mark.parametrize("status", [429, 529, 503])
async def test_only_bounded_retries_for_overload(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"retry-after": "0"})

    provider = JevProvider(settings(max_retries=1), transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate("evidence", QUESTIONS)
        assert caught.value.retryable
        assert len(calls) == 2
    finally:
        await provider.aclose()


async def test_retry_can_succeed_without_altering_answer():
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(529) if calls == 1 else httpx.Response(200, json=RESPONSE)

    provider = JevProvider(settings(), transport=httpx.MockTransport(handle))
    try:
        assert await provider.evaluate("evidence", QUESTIONS) == RESPONSE
        assert calls == 2
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "body,code",
    [
        (b"x" * 1025, "response_too_large"),
        (b"{invalid", "invalid_provider_response"),
        (b'{"model":"jev-1.13.0","answers":{"q":NaN}}', "invalid_provider_response"),
        (b'{"model":"different","answers":{}}', "provider_model_mismatch"),
    ],
)
async def test_bounded_response_and_model_identity(body, code):
    provider = JevProvider(
        settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    try:
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate("evidence", QUESTIONS)
        assert caught.value.code == code
    finally:
        await provider.aclose()


async def test_total_deadline_and_transport_errors_are_safe():
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=RESPONSE)

    provider = JevProvider(
        settings(request_timeout_seconds=0.01), transport=httpx.MockTransport(slow)
    )
    try:
        with pytest.raises(OmniaError, match="deadline") as caught:
            await provider.evaluate("evidence", QUESTIONS)
        assert caught.value.code == "provider_timeout"
    finally:
        await provider.aclose()

    def broken(request):
        raise httpx.ConnectError("sensitive transport details", request=request)

    provider = JevProvider(settings(), transport=httpx.MockTransport(broken))
    try:
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate("evidence", QUESTIONS)
        assert caught.value.code == "provider_unavailable"
        assert "sensitive" not in str(caught.value)
    finally:
        await provider.aclose()


async def test_unconfigured_and_nonfinite_requests_never_send():
    def forbidden(request):
        pytest.fail("request should not be sent")

    provider = JevProvider(
        settings(typesafe_api_key=None), transport=httpx.MockTransport(forbidden)
    )
    try:
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate("evidence", QUESTIONS)
        assert caught.value.code == "provider_not_configured"
    finally:
        await provider.aclose()
    provider = JevProvider(settings(), transport=httpx.MockTransport(forbidden))
    try:
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate({"number": float("nan")}, QUESTIONS)
        assert caught.value.code == "invalid_request"
    finally:
        await provider.aclose()


def test_retry_after_is_finite_and_respects_server_wait():
    for header in ("nan", "inf", "-100", "bad date", "Thu, 01 Jan 1970 00:00:00 GMT"):
        assert 0 <= _retry_delay(2, header) <= 2
    assert _retry_delay(0, "60") == 60


async def test_retry_after_beyond_deadline_never_retries_early():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(429, headers={"retry-after": "60"})

    provider = JevProvider(settings(), transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate("evidence", QUESTIONS)
        assert caught.value.code == "provider_rate_limited"
        assert len(requests) == 1
    finally:
        await provider.aclose()


def test_empty_key_is_not_advertised_as_configured():
    assert not JevProvider(settings(typesafe_api_key=SecretStr(""))).capabilities()["configured"]


async def test_compressed_response_is_refused_even_when_small():
    # Use an identity body with the header injected after HTTPX construction to
    # test our boundary without testing HTTPX's decompressor.
    response = httpx.Response(200, json=RESPONSE)
    response.headers["content-encoding"] = "gzip"
    provider = JevProvider(settings(), transport=httpx.MockTransport(lambda request: response))
    try:
        with pytest.raises(OmniaError) as caught:
            await provider.evaluate("evidence", QUESTIONS)
        assert caught.value.code == "invalid_provider_response"
    finally:
        await provider.aclose()
