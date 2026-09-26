# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""Real subprocess transport checks; no provider inference is claimed or required."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_real_stdio_server_both_protocol_eras(tmp_path, mode):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OMNIA_") and key != "TYPESAFE_API_KEY"
    }
    environment.update(
        {
            "OMNIA_DATABASE": str(tmp_path / "stdio.sqlite3"),
            "OMNIA_DEFAULT_PROVIDER": "laya",
            "OMNIA_ALLOWED_PROVIDERS": "laya",
            "OMNIA_LAYA_OFFLINE": "true",
            "PYTHONPATH": str(ROOT / "src"),
        }
    )
    target = StdioServerParameters(
        command=sys.executable,
        args=["-m", "omnia_mcp", "serve"],
        env=environment,
        cwd=ROOT,
    )
    async with Client(target, mode=mode, read_timeout_seconds=15) as client:
        listed = await client.list_tools()
        assert {tool.name for tool in listed.tools} == {
            "omnia_evaluate",
            "omnia_evaluate_batch",
            "omnia_capabilities",
            "omnia_get_decision",
        }
        result = await client.call_tool("omnia_capabilities", {})
        assert not result.is_error
        assert isinstance(result.structured_content, dict)
        schemas = await client.read_resource("omnia://schemas")
        assert "DecisionResult" in json.loads(schemas.contents[0].text)
        missing = await client.call_tool("omnia_get_decision", {"decision_id": "not-present"})
        assert missing.is_error
        assert "decision_not_found" in missing.content[0].text
