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


# Separate floating-point arithmetic noise from the provider's decimal rounding.
_NUMERIC_EPSILON = 1e-12


def _rounding_interval(value: float, half_unit: float, ceiling: float = 1) -> tuple[float, float]:
    # Closed endpoints tolerate either upstream tie-breaking convention.
    return max(0.0, value - half_unit), min(ceiling, value + half_unit)


def _normalized_feasible(
    bounds: list[tuple[float, float]], score: tuple[float, float] | None = None
) -> bool:
    """Can one normalized distribution in these bounds also have this expected index?"""
    if any(low > high + _NUMERIC_EPSILON for low, high in bounds):
        return False
    minimum_mass = math.fsum(low for low, _ in bounds)
    maximum_mass = math.fsum(high for _, high in bounds)
    if minimum_mass > 1 + _NUMERIC_EPSILON or maximum_mass < 1 - _NUMERIC_EPSILON:
        return False
    if score is None:
        return True

    # A linear expectation over a bounded simplex attains each extreme by
    # allocating the remaining mass to the lowest/highest levels first.
    def extreme(indices: range) -> float:
        remaining = max(0.0, 1 - minimum_mass)
        terms = [index * low for index, (low, _) in enumerate(bounds)]
        for index in indices:
            low, high = bounds[index]
            added = min(remaining, max(0.0, high - low))
            terms.append(index * added)
            remaining -= added
        return math.fsum(terms)

    lowest = extreme(range(len(bounds)))
    highest = extreme(range(len(bounds) - 1, -1, -1))
    # Every intermediate expectation is attainable: the feasible set is convex.
    return lowest <= score[1] + _NUMERIC_EPSILON and highest >= score[0] - _NUMERIC_EPSILON


def _consistent_distribution(
    bounds: list[tuple[float, float]],
    *,
    score: tuple[float, float] | None = None,
    peak: tuple[float, float] | None = None,
    chosen: int | None = None,
) -> bool:
    """Check all constraints against the same possible unrounded distribution."""
    if chosen is not None:
        # A Choice has no ordinal score. Set its selected probability to the
        # largest allowed value; other probabilities can then fit under it iff
        # any possible selected maximum could accommodate the required mass.
        others = [low for index, (low, _) in enumerate(bounds) if index != chosen]
        peak_low, peak_high = peak or (0.0, 1.0)
        top = min(bounds[chosen][1], peak_high, 1 - math.fsum(others))
        if top < max(bounds[chosen][0], peak_low, max(others)) - _NUMERIC_EPSILON:
            return False
        selected_bounds = [
            (top, top) if index == chosen else (low, min(high, top))
            for index, (low, high) in enumerate(bounds)
        ]
        return _normalized_feasible(selected_bounds)
    if peak is None:
        return _normalized_feasible(bounds, score)

    # max(p) in [a, b] means every p <= b and at least one p >= a.
    # Enumerating that one index gives a union of bounded simplexes; testing
    # score inside each one avoids accepting independently compatible fields
    # that cannot all arise from a single distribution.
    capped = [(low, min(high, peak[1])) for low, high in bounds]
    for index, (low, high) in enumerate(capped):
        candidate = capped.copy()
        candidate[index] = max(low, peak[0]), high
        if _normalized_feasible(candidate, score):
            return True
    return False


def validate_answers(raw: dict, questions: dict, *, decimal_places: int = 4) -> dict:
    """Preserve native values, accepting only jointly feasible rounding intervals.

    Laya 0.3.20 rounds to four decimal places. The pinned JEV adapter passes
    two for the observed jev-1.13.0 wire format; this is not a claim that the
    hosted API guarantees that precision across future model versions.
    """
    if type(decimal_places) is not int or not 1 <= decimal_places <= 9:
        raise ValueError("decimal_places must be an integer from 1 to 9")
    half_unit = 0.5 * 10 ** (-decimal_places)
    try:
        answers = raw["answers"]
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ValueError("question IDs differ")
        result = {}
        for key, question in questions.items():
            answer = answers[key]
            kind = question["type"]
            if (
                kind not in ("noul", "choice", "score")
                or not isinstance(answer, dict)
                or answer.get("type") != kind
            ):
                raise ValueError("answer type differs")
            validated = {"type": kind}
            for field in ("confidence", "answer_confidence"):
                if field in answer:
                    validated[field] = probability(answer[field])
            peak = (
                _rounding_interval(validated["answer_confidence"], half_unit)
                if "answer_confidence" in validated
                else None
            )
            score_interval = None
            chosen_index = None
            if kind == "noul":
                p = probability(answer["noul"])
                validated["noul"] = p
                low, high = _rounding_interval(p, half_unit)
                bounds = [(low, high), (1 - high, 1 - low)]
            else:
                criteria = question["criteria"]
                labels = (
                    list(criteria) if kind == "choice" else list(map(str, range(len(criteria))))
                )
                distribution = answer["probabilities"]
                if not isinstance(distribution, dict) or set(distribution) != set(labels):
                    raise ValueError("distribution labels differ")
                probabilities = {label: probability(distribution[label]) for label in labels}
                bounds = [_rounding_interval(value, half_unit) for value in probabilities.values()]
                validated["probabilities"] = probabilities
                if kind == "choice":
                    chosen = answer["choice"]
                    if chosen not in probabilities:
                        raise ValueError("choice label differs")
                    chosen_index = labels.index(chosen)
                    validated["choice"] = chosen
                else:
                    score = answer["score"]
                    if isinstance(score, bool) or not isinstance(score, (int, float)):
                        raise ValueError("score is not numeric")
                    if not math.isfinite(score) or not 0 <= score <= len(labels) - 1:
                        raise ValueError("score outside rubric")
                    score_interval = _rounding_interval(score, half_unit, len(labels) - 1)
                    validated["score"] = score
                    if "legend" in answer:
                        expected_legend = {str(i): level for i, level in enumerate(criteria)}
                        if answer["legend"] != expected_legend:
                            raise ValueError("score legend conflicts with rubric")
                        validated["legend"] = answer["legend"]
            if not _consistent_distribution(
                bounds, score=score_interval, peak=peak, chosen=chosen_index
            ):
                raise ValueError("answer fields have no consistent normalized distribution")
            result[key] = validated
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        raise OmniaError(
            "invalid_provider_response", "Provider returned an incompatible decision."
        ) from None


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
