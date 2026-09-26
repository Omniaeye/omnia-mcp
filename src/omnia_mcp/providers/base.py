# Copyright 2026 OMNIA EYE Corporation
# SPDX-License-Identifier: Apache-2.0
"""Provider contracts. Native answer semantics remain owned by each backend."""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from ..errors import OmniaError


@runtime_checkable
class DecisionProvider(Protocol):
    async def evaluate(
        self, state: Any, questions: dict[str, dict[str, Any]]
    ) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...

    def identity(self) -> dict[str, Any]: ...

    def capabilities(self) -> dict[str, Any]: ...


def copy_request(state: Any, questions: dict[str, dict[str, Any]]) -> tuple[Any, dict[str, Any]]:
    """Freeze caller-owned JSON before queued or remote execution; reject non-JSON numbers."""
    if not isinstance(state, (str, dict, list)) or not state:
        raise OmniaError("invalid_request", "State must be nonempty text, an object, or an array.")
    if not isinstance(questions, dict) or not questions:
        raise OmniaError("invalid_request", "Questions must be a nonempty object.")
    if any(
        not isinstance(key, str) or not key or not isinstance(value, dict)
        for key, value in questions.items()
    ):
        raise OmniaError("invalid_request", "Question identifiers and definitions are invalid.")

    def validate_json(value: Any, depth: int = 0) -> None:
        if depth > 24:
            raise OmniaError("invalid_request", "Request exceeds the JSON nesting limit.")
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise OmniaError("invalid_request", "JSON object keys must be strings.")
            for child in value.values():
                validate_json(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                validate_json(child, depth + 1)
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise OmniaError("invalid_request", "Request contains an unsupported JSON value.")

    validate_json(state)
    validate_json(questions)
    try:
        encoded = json.dumps([state, questions], ensure_ascii=False, allow_nan=False)
        # A second provider-level bound contains direct callers outside the service.
        if len(encoded.encode("utf-8")) > 1_048_576:
            raise OmniaError("request_too_large", "Provider input exceeds the byte budget.")
        value = json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        raise OmniaError("invalid_request", "Request must contain finite JSON values.") from None
    return value[0], value[1]
