# Copyright 2026 OMNIA EYE Corporation
# SPDX-License-Identifier: Apache-2.0
"""Provider boundaries reject state coercion before persistence or inference."""

import pytest

from omnia_mcp.errors import OmniaError
from omnia_mcp.providers.base import copy_request

QUESTIONS = {"q": {"type": "noul", "instructions": "Does this hold?"}}


@pytest.mark.parametrize(
    "state", [{1: "first", "1": "second"}, {"nested": ("a", "b")}, {"value": float("inf")}]
)
def test_rejects_silent_json_coercion_and_nonfinite_values(state):
    with pytest.raises(OmniaError) as caught:
        copy_request(state, QUESTIONS)
    assert caught.value.code == "invalid_request"


def test_caller_mutation_cannot_change_frozen_request():
    original = {"facts": ["one"]}
    state, questions = copy_request(original, QUESTIONS)
    original["facts"].append("two")
    assert state == {"facts": ["one"]}
    assert questions == QUESTIONS
    assert questions is not QUESTIONS
