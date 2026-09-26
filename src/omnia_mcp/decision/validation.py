# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""Validate provider output against the exact submitted questions."""
from __future__ import annotations

import hashlib
import json
import math

from omnia_mcp.errors import OmniaError
from omnia_mcp.schemas import DecisionPolicy


def encode(value) -> bytes:
    # Choice order is model input; sorting mappings would create incorrect cache hits.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(value) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


def probability(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("probability must be numeric")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError("probability outside range")
    return number


def validate_answers(raw: dict, questions: dict) -> dict:
    try:
        answers = raw["answers"]
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ValueError("question IDs differ")
        result = {}
        for key, question in questions.items():
            answer = answers[key]
            kind = question["type"]
            if not isinstance(answer, dict) or answer.get("type") != kind:
                raise ValueError("answer type differs")
            validated = {"type": kind}
            if kind == "noul":
                p = probability(answer["noul"])
                validated["noul"] = p
                highest = max(p, 1 - p)
            else:
                criteria = question["criteria"]
                labels = list(criteria) if kind == "choice" else list(map(str, range(len(criteria))))
                distribution = answer["probabilities"]
                if not isinstance(distribution, dict) or set(distribution) != set(labels):
                    raise ValueError("distribution labels differ")
                probabilities = {label: probability(distribution[label]) for label in labels}
                if abs(sum(probabilities.values()) - 1) > len(labels) * 0.000051:
                    raise ValueError("distribution is not normalized")
                validated["probabilities"] = probabilities
                highest = max(probabilities.values())
                if kind == "choice":
                    chosen = answer["choice"]
                    if chosen not in probabilities or abs(probabilities[chosen] - highest) > .00011:
                        raise ValueError("choice conflicts with distribution")
                    validated["choice"] = chosen
                else:
                    score = answer["score"]
                    if isinstance(score, bool) or not isinstance(score, (int, float)):
                        raise ValueError("score is not numeric")
                    expected = sum(i * probabilities[str(i)] for i in range(len(labels)))
                    if not math.isfinite(score) or not 0 <= score <= len(labels) - 1:
                        raise ValueError("score outside rubric")
                    if abs(score - expected) > len(labels) ** 2 * .000051:
                        raise ValueError("score conflicts with distribution")
                    validated["score"] = score
            for field in ("confidence", "answer_confidence"):
                if field in answer:
                    validated[field] = probability(answer[field])
            if "answer_confidence" in validated and abs(validated["answer_confidence"] - highest) > .00011:
                raise ValueError("answer confidence conflicts with distribution")
            result[key] = validated
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        raise OmniaError("invalid_provider_response", "Provider returned an incompatible decision.") from None


def review(answers: dict, policy: DecisionPolicy) -> list[str]:
    reasons = []
    for key, answer in answers.items():
        if answer["type"] == "noul":
            p = answer["noul"]
            probabilities = sorted((p, 1 - p), reverse=True)
        else:
            probabilities = sorted(answer["probabilities"].values(), reverse=True)
        if probabilities[0] < policy.min_probability:
            reasons.append(f"{key}:below_probability_threshold")
        if probabilities[0] - probabilities[1] < policy.min_margin:
            reasons.append(f"{key}:below_margin_threshold")
    return reasons

