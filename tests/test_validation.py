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
