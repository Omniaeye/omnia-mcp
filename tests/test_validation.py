# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
import pytest

from omnia_mcp.decision.validation import fingerprint, review, validate_answers
from omnia_mcp.errors import OmniaError
from omnia_mcp.schemas import DecisionPolicy

QUESTIONS = {"q": {"type": "choice", "criteria": {"keep": "substantive", "skip": "routine"}}}


def test_choice_probability_and_confidence_are_distinct():
    result = validate_answers(
        {
            "answers": {
                "q": {
                    "type": "choice",
                    "choice": "keep",
                    "probabilities": {"keep": 0.8, "skip": 0.2},
                    "confidence": 0.278,
                }
            }
        },
        QUESTIONS,
    )
    assert result["q"]["confidence"] == 0.278
    assert review(result, DecisionPolicy()) == []


@pytest.mark.parametrize(
    "answer",
    [
        {"type": "noul", "noul": True},
        {"type": "noul", "noul": float("nan")},
        {"type": "noul", "noul": 1.1},
        {"type": "noul", "noul": 0.9, "answer_confidence": 0.5},
    ],
)
def test_invalid_answers_fail_closed(answer):
    with pytest.raises(OmniaError, match="incompatible"):
        validate_answers({"answers": {"q": answer}}, {"q": {"type": "noul"}})


def test_score_is_expected_ordinal_value():
    questions = {"q": {"type": "score", "criteria": ["low", "mid", "high"]}}
    raw = {
        "answers": {
            "q": {"type": "score", "score": 1.6, "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7}}
        }
    }
    assert validate_answers(raw, questions)["q"]["score"] == 1.6
    raw["answers"]["q"]["score"] = 90
    with pytest.raises(OmniaError):
        validate_answers(raw, questions)


def test_option_order_changes_cache_identity():
    assert fingerprint({"a": 1, "b": 2}) != fingerprint({"b": 2, "a": 1})


def test_review_uses_probability_and_margin():
    reasons = review(
        {"q": {"type": "noul", "noul": 0.55}}, DecisionPolicy(min_probability=0.8, min_margin=0.2)
    )
    assert len(reasons) == 2


def score_response(probabilities, score, **fields):
    questions = {
        "q": {"type": "score", "criteria": [f"level {i}" for i in range(len(probabilities))]}
    }
    answer = {
        "type": "score",
        "score": score,
        "probabilities": {str(i): value for i, value in enumerate(probabilities)},
        **fields,
    }
    return {"answers": {"q": answer}}, questions


def choice_response(probabilities, chosen=0, **fields):
    questions = {
        "q": {"type": "choice", "criteria": {str(i): None for i in range(len(probabilities))}}
    }
    answer = {
        "type": "choice",
        "choice": str(chosen),
        "probabilities": {str(i): value for i, value in enumerate(probabilities)},
        **fields,
    }
    return {"answers": {"q": answer}}, questions


def test_observed_jev_score_rounding_is_feasible_and_preserved():
    # Regression from a real jev-1.13.0 response, reduced to numerical fields.
    # This test replays a fixture and performs no provider call.
    raw, questions = score_response([0.0, 0.0, 1.0], 1.99, confidence=0.99)
    answer = validate_answers(raw, questions, decimal_places=2)["q"]
    assert answer == raw["answers"]["q"]
    # That discrepancy is not consistent with Laya's finer wire precision.
    with pytest.raises(OmniaError):
        validate_answers(raw, questions)


@pytest.mark.parametrize("probabilities", [[0.33, 0.33, 0.33], [0.34, 0.34, 0.33]])
def test_two_decimal_sums_can_differ_from_one(probabilities):
    raw, questions = choice_response(probabilities)
    assert validate_answers(raw, questions, decimal_places=2)["q"] == raw["answers"]["q"]


@pytest.mark.parametrize(
    "probabilities", [[0.33, 0.33, 0.32], [0.34, 0.34, 0.34], [0.0, 0.0, 0.0], [1.0, 1.0]]
)
def test_impossible_normalization_rejected_even_at_two_decimals(probabilities):
    raw, questions = choice_response(probabilities)
    with pytest.raises(OmniaError):
        validate_answers(raw, questions, decimal_places=2)


def test_score_bounds_account_for_normalization_and_clipped_endpoints():
    # At least .995 is on level 2. Assigning the remaining .005 entirely to
    # level 0 gives the lowest possible score, 1.99. A broad n^2 tolerance
    # would incorrectly accept this 1.98 response.
    raw, questions = score_response([0.0, 0.0, 1.0], 1.98)
    with pytest.raises(OmniaError):
        validate_answers(raw, questions, decimal_places=2)


def test_choice_must_be_maximum_in_a_normalized_distribution():
    # Option 1 can tie option 0 at .335 in isolation, but then the maximum
    # total mass would be .335 + .335 + .325 = .995, not one.
    raw, questions = choice_response([0.34, 0.33, 0.32], chosen=1)
    with pytest.raises(OmniaError):
        validate_answers(raw, questions, decimal_places=2)
    raw["answers"]["q"]["choice"] = "0"
    validate_answers(raw, questions, decimal_places=2)


def test_tied_rounded_choices_remain_possible():
    for chosen in range(3):
        raw, questions = choice_response([0.33, 0.33, 0.33], chosen=chosen)
        validate_answers(raw, questions, decimal_places=2)


def test_answer_confidence_and_score_must_be_jointly_feasible():
    raw, questions = score_response([0.6, 0.2, 0.2], 0.61)
    validate_answers(raw, questions, decimal_places=2)
    # This peak is feasible on its own, but requires p0 >= .605. With that
    # constraint the largest possible score is .595, below .61's interval.
    peak_raw, peak_questions = choice_response([0.6, 0.2, 0.2], answer_confidence=0.61)
    validate_answers(peak_raw, peak_questions, decimal_places=2)
    raw["answers"]["q"]["answer_confidence"] = 0.61
    with pytest.raises(OmniaError):
        validate_answers(raw, questions, decimal_places=2)


def test_answer_confidence_must_allow_normalization_too():
    raw, questions = choice_response([0.34, 0.33, 0.32], answer_confidence=0.33)
    with pytest.raises(OmniaError):
        validate_answers(raw, questions, decimal_places=2)


@pytest.mark.parametrize("p", [0.01, 0.99])
def test_noul_confidence_uses_both_complementary_probabilities(p):
    raw = {"answers": {"q": {"type": "noul", "noul": p, "answer_confidence": 0.99}}}
    questions = {"q": {"type": "noul"}}
    validate_answers(raw, questions, decimal_places=2)
    raw["answers"]["q"]["answer_confidence"] = 0.97
    with pytest.raises(OmniaError):
        validate_answers(raw, questions, decimal_places=2)


def test_entropy_confidence_is_not_treated_as_peak_probability():
    raw, questions = score_response([0.33, 0.33, 0.33], 1.0, confidence=0.0)
    assert validate_answers(raw, questions, decimal_places=2)["q"]["confidence"] == 0.0


@pytest.mark.parametrize("decimal_places", [2, 4])
def test_constructed_distributions_survive_native_rounding(decimal_places):
    # Deterministic mathematical fixtures, not evidence of model accuracy.
    import random

    rng = random.Random(4731)
    for count in range(2, 17):
        for _ in range(8):
            weights = [rng.random() ** 4 for _ in range(count)]
            total = sum(weights)
            probabilities = [value / total for value in weights]
            rounded = [round(value, decimal_places) for value in probabilities]
            peak = round(max(probabilities), decimal_places)
            chosen = max(range(count), key=probabilities.__getitem__)
            raw, questions = choice_response(rounded, chosen, answer_confidence=peak)
            validate_answers(raw, questions, decimal_places=decimal_places)
            score = round(sum(i * value for i, value in enumerate(probabilities)), decimal_places)
            raw, questions = score_response(rounded, score, answer_confidence=peak)
            validate_answers(raw, questions, decimal_places=decimal_places)


@pytest.mark.parametrize("decimal_places", [True, 0, -1, 10, 2.0, "2"])
def test_precision_configuration_is_explicit_and_bounded(decimal_places):
    with pytest.raises(ValueError, match="decimal_places"):
        validate_answers({}, {}, decimal_places=decimal_places)
