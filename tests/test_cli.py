# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""CLI diagnostics stay local, sanitized, and side-effect free."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from omnia_mcp import __version__
from omnia_mcp.cli import main

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_configuration(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("OMNIA_") or key == "TYPESAFE_API_KEY":
            monkeypatch.delenv(key)


def test_doctor_does_not_create_database_or_echo_secrets(tmp_path, monkeypatch, capsys):
    database = tmp_path / "not-created" / "decisions.sqlite3"
    monkeypatch.setenv("OMNIA_DATABASE", str(database))
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-diagnostic-token")
    assert main(["doctor"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["checks"] == {
        "model_loaded": False,
        "network_probed": False,
        "database_opened": False,
    }
    assert report["status"] == "configuration_valid"
    assert report["configuration"] == {
        "laya_revision_configured": False,
        "jev_api_key_configured": True,
    }
    assert set(report["packages"]) == {"mcp", "pydantic", "httpx", "laya"}
    assert "secret-diagnostic-token" not in captured.out + captured.err
    assert str(database) not in captured.out
    assert not database.parent.exists()


def test_invalid_settings_fail_with_sanitized_error(monkeypatch, capsys):
    monkeypatch.setenv("OMNIA_DEFAULT_PROVIDER", "secret-invalid-provider")
    assert main(["doctor"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["status"] == "invalid_configuration"
    assert "secret-invalid-provider" not in captured.err


def test_schemas_prints_public_contracts_without_configuration(monkeypatch, capsys):
    monkeypatch.setenv("OMNIA_DEFAULT_PROVIDER", "invalid")
    assert main(["schemas"]) == 0
    schemas = json.loads(capsys.readouterr().out)
    assert set(schemas) == {"EvaluationRequest", "BatchRequest", "DecisionResult", "BatchResult"}
    assert schemas["EvaluationRequest"]["additionalProperties"] is False


def test_version(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_default_command_starts_stdio(monkeypatch):
    from omnia_mcp import server

    calls = []

    class RecordingServer:
        def run(self, *, transport):
            calls.append(transport)

    monkeypatch.setattr(server, "create_server", lambda: RecordingServer())
    assert main([]) == 0
    assert calls == ["stdio"]


def test_server_start_error_is_sanitized(monkeypatch, capsys):
    from omnia_mcp import server

    def fail():
        raise RuntimeError("private-storage-path-and-secret")

    monkeypatch.setattr(server, "create_server", fail)
    assert main(["serve"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "private-storage-path-and-secret" not in captured.err
    assert json.loads(captured.err)["status"] == "server_failed"


def test_http_is_rejected():
    with pytest.raises(SystemExit) as raised:
        main(["serve", "--transport", "http"])
    assert raised.value.code == 2


def test_module_version_runs_in_real_process():
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "omnia_mcp", "version"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == __version__
    assert result.stderr == ""
