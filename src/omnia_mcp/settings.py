# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""Validated process configuration; credentials never enter public manifests."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ProviderName = Literal["jev", "laya"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OMNIA_", extra="ignore", env_file=None, hide_input_in_errors=True
    )
    default_provider: ProviderName = "laya"
    allowed_providers: Annotated[tuple[ProviderName, ...], NoDecode] = ("laya",)
    database: Path = Field(default_factory=lambda: Path.home() / ".omnia-mcp" / "decisions.sqlite3")
    cache_ttl_seconds: int = Field(default=3600, ge=0, le=604800)
    retention_days: int = Field(default=30, ge=1, le=3650)
    max_input_bytes: int = Field(default=65536, ge=1024, le=1048576)
    max_questions: int = Field(default=8, ge=1, le=16)
    max_options: int = Field(default=16, ge=2, le=64)
    max_batch_items: int = Field(default=16, ge=1, le=64)
    max_concurrent_requests: int = Field(default=4, ge=1, le=32)
    max_queue_size: int = Field(default=32, ge=0, le=1024)
    max_requests_per_minute: int = Field(default=120, ge=1, le=100000)
    max_requests_per_day: int = Field(default=10000, ge=1, le=10000000)
    request_timeout_seconds: float = Field(default=30.0, ge=1, le=600, allow_inf_nan=False)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_response_bytes: int = Field(default=1048576, ge=1024, le=8388608)
    typesafe_api_key: SecretStr | None = Field(default=None, validation_alias="TYPESAFE_API_KEY")
    jev_model: str = Field(default="jev-1.13.0", pattern=r"^jev-[0-9]+\.[0-9]+\.[0-9]+$")
    laya_model: Literal["english", "typed-decisions", "multilingual"] = "english"
    laya_revision: str = ""
    laya_device: Literal["cpu", "cuda", "mps"] = "cpu"
    laya_threads: int = Field(default=2, ge=1, le=64)
    laya_max_len: int = Field(default=512, ge=128, le=8192)
    laya_head_max_len: int = Field(default=192, ge=32, le=8191)
    laya_offline: bool = False
    log_level: Literal["ERROR", "WARNING", "INFO", "DEBUG"] = "WARNING"

    @field_validator("allowed_providers", mode="before")
    @classmethod
    def split_providers(cls, value):
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("laya_revision")
    @classmethod
    def revision_format(cls, value: str) -> str:
        import re
        if value and not re.fullmatch("[0-9a-f]{40}", value):
            raise ValueError("LAYA revision must be a complete lowercase commit SHA")
        return value

    @model_validator(mode="after")
    def coherent(self):
        if not self.allowed_providers or len(set(self.allowed_providers)) != len(self.allowed_providers):
            raise ValueError("allowed providers must be nonempty and unique")
        if self.default_provider not in self.allowed_providers:
            raise ValueError("default provider must be enabled")
        if self.laya_head_max_len >= self.laya_max_len:
            raise ValueError("LAYA head budget must be smaller than context")
        limits = {"english": 512, "typed-decisions": 1024, "multilingual": 8192}
        if self.laya_max_len > limits[self.laya_model]:
            raise ValueError("LAYA context exceeds selected checkpoint capacity")
        return self

    @classmethod
    def from_env(cls) -> Settings:
        return cls()

    def public(self) -> dict:
        return {
            "default_provider": self.default_provider,
            "allowed_providers": list(self.allowed_providers),
            "limits": {
                name: getattr(self, name) for name in (
                    "max_input_bytes", "max_questions", "max_options", "max_batch_items",
                    "max_concurrent_requests", "max_queue_size", "request_timeout_seconds",
                    "max_requests_per_minute", "max_requests_per_day",
                )
            },
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "retention_days": self.retention_days,
        }

