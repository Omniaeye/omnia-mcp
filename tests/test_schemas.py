# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
import pytest
from pydantic import ValidationError
from omnia_mcp.schemas import EvaluationRequest, BatchItemResult


def request(**extra):
    return EvaluationRequest(state="A new release", questions={
        "relevant": {"type": "noul", "instructions": "Does it announce a release?"}
    }, **extra)


def test_schema_rejects_unknown_controls():
    with pytest.raises(ValidationError):
        request(database="/tmp/other.sqlite")
    with pytest.raises(ValidationError):
        request(provider="arbitrary")


def test_nonfinite_nested_input_is_rejected():
    with pytest.raises(ValidationError):
        EvaluationRequest(state={"nested": [float("nan")]}, questions=request().questions)


def test_question_requires_both_noul_criteria():
    with pytest.raises(ValidationError):
        EvaluationRequest(state="x", questions={"q": {
            "type": "noul", "instructions": "x", "criteria": {"true": "yes"}
        }})


def test_batch_item_has_exactly_one_outcome():
    with pytest.raises(ValidationError):
        BatchItemResult(index=0)


def test_chinese_and_japanese_are_preserved():
    value = EvaluationRequest(state="新しいリリース。新版本已发布。", questions=request().questions)
    assert value.state.endswith("新版本已发布。")

