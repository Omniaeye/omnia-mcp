# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""Versioned public contracts, independent of MCP transport objects."""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

ProviderName = Literal["jev", "laya"]
Text = Annotated[str, Field(min_length=1, max_length=2000)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
QuestionId = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, strict=True)


class ChoiceQuestion(Contract):
    type: Literal["choice"]
    instructions: Text
    criteria: dict[Annotated[str, Field(min_length=1, max_length=100)], Text | None] = Field(
        min_length=2, max_length=64
    )


class ScoreQuestion(Contract):
    type: Literal["score"]
    instructions: Text
    criteria: list[Text] = Field(min_length=2, max_length=16)


class NoulQuestion(Contract):
    type: Literal["noul"]
    instructions: Text
    criteria: dict[Literal["true", "false"], Text] | None = None

    @field_validator("criteria")
    @classmethod
    def both_sides(cls, value):
        if value is not None and set(value) != {"true", "false"}:
            raise ValueError("noul criteria must describe both true and false")
        return value


Question = Annotated[ChoiceQuestion | ScoreQuestion | NoulQuestion, Field(discriminator="type")]


class DecisionPolicy(Contract):
    version: Literal["omnia.review.v1"] = "omnia.review.v1"
    min_probability: Probability = 0.8
    min_margin: Probability = 0.0


class EvaluationRequest(Contract):
    state: str | dict[str, JsonValue] | list[JsonValue]
    questions: dict[QuestionId, Question] = Field(min_length=1, max_length=16)
    provider: ProviderName | None = None
    evidence: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(
        default_factory=list, max_length=64
    )
    policy: DecisionPolicy = Field(default_factory=DecisionPolicy)

    @field_validator("state", mode="before")
    @classmethod
    def bounded_json(cls, value):
        def walk(item, depth=0):
            if depth > 24:
                raise ValueError("state exceeds nesting limit")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("state must contain finite JSON values")
            if isinstance(item, dict):
                for child in item.values():
                    walk(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    walk(child, depth + 1)

        if not isinstance(value, (str, dict, list)) or not value:
            raise ValueError("state must be nonempty text or JSON")
        walk(value)
        json.dumps(value, allow_nan=False)
        return value


class BatchRequest(Contract):
    items: list[EvaluationRequest] = Field(min_length=1, max_length=64)


class ChoiceAnswer(Contract):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, Probability]
    confidence: Probability | None = None
    answer_confidence: Probability | None = None


class ScoreAnswer(Contract):
    legend: dict[str, Text] | None = None
    type: Literal["score"]
    score: float = Field(allow_inf_nan=False)
    probabilities: dict[str, Probability]
    confidence: Probability | None = None
    answer_confidence: Probability | None = None


class NoulAnswer(Contract):
    type: Literal["noul"]
    noul: Probability
    confidence: Probability | None = None
    answer_confidence: Probability | None = None


Answer = Annotated[ChoiceAnswer | ScoreAnswer | NoulAnswer, Field(discriminator="type")]


class DecisionResult(Contract):
    schema_version: Literal["omnia.mcp.decision.v1"] = "omnia.mcp.decision.v1"
    decision_id: str
    input_hash: str
    provider: ProviderName
    model: str
    identity: dict[str, JsonValue]
    created_at: datetime = Field(strict=False)
    answers: dict[str, Answer]
    status: Literal["accepted", "needs_review"]
    review_reasons: list[str]
    policy: DecisionPolicy
    evidence: list[str]
    usage: dict[str, int] | None = None
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    cached: bool = False


class PublicError(Contract):
    code: str
    message: str
    retryable: bool = False


class BatchItemResult(Contract):
    index: int
    result: DecisionResult | None = None
    error: PublicError | None = None

    @model_validator(mode="after")
    def exclusive(self):
        if (self.result is None) == (self.error is None):
            raise ValueError("batch item requires exactly one result or error")
        return self


class BatchResult(Contract):
    schema_version: Literal["omnia.mcp.batch.v1"] = "omnia.mcp.batch.v1"
    items: list[BatchItemResult]
