# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
import pytest
from pydantic import ValidationError
from omnia_mcp.settings import Settings


def test_environment_is_explicit_and_secrets_are_redacted(monkeypatch):
    monkeypatch.setenv("OMNIA_ALLOWED_PROVIDERS", "jev,laya")
    monkeypatch.setenv("OMNIA_DEFAULT_PROVIDER", "jev")
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-value")
    settings = Settings.from_env()
    assert settings.allowed_providers == ("jev", "laya")
    assert "secret-value" not in repr(settings)
    assert "secret-value" not in str(settings.public())


@pytest.mark.parametrize("changes", [
    {"max_questions": 0}, {"allowed_providers": ()},
    {"allowed_providers": ("jev",)}, {"laya_revision": "main"},
    {"laya_max_len": 1024}, {"jev_model": "jev-latest"},
    {"request_timeout_seconds": float("nan")},
])
def test_configuration_fails_closed(changes):
    with pytest.raises(ValidationError):
        Settings(**changes)

