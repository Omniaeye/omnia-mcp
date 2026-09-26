# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""Command-line entry points. Only serve starts storage and providers."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from . import __version__


def _installed_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def doctor() -> dict[str, Any]:
    """Inspect configuration and package metadata without model loading or DB writes."""
    from .settings import Settings

    settings = Settings.from_env()
    return {
        "status": "configuration_valid",
        "version": __version__,
        "transport": "stdio",
        "default_provider": settings.default_provider,
        "allowed_providers": list(settings.allowed_providers),
        "packages": {
            name: _installed_version(name) for name in ("mcp", "pydantic", "httpx", "laya")
        },
        "configuration": {
            "laya_revision_configured": bool(settings.laya_revision),
            "jev_api_key_configured": bool(
                settings.typesafe_api_key and settings.typesafe_api_key.get_secret_value()
            ),
        },
        "checks": {"model_loaded": False, "network_probed": False, "database_opened": False},
        "note": "Configuration and package presence do not prove provider readiness.",
    }


def _schemas() -> dict[str, Any]:
    from .schemas import BatchRequest, BatchResult, DecisionResult, EvaluationRequest

    return {
        model.__name__: model.model_json_schema()
        for model in (EvaluationRequest, BatchRequest, DecisionResult, BatchResult)
    }


def _print_json(value: Any, *, error: bool = False) -> None:
    print(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
        file=sys.stderr if error else sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="omnia-mcp", description="OMNIA local decision MCP server"
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")
    serve = subparsers.add_parser("serve", help="Run the local MCP server over stdio")
    serve.add_argument("--transport", choices=("stdio",), default="stdio")
    subparsers.add_parser(
        "doctor", help="Inspect configuration without loading models or opening storage"
    )
    subparsers.add_parser("schemas", help="Print public JSON Schemas")
    subparsers.add_parser("version", help="Print the package version")
    args = parser.parse_args(argv)
    command = args.command or "serve"

    if command == "version":
        print(__version__)
        return 0
    if command == "schemas":
        _print_json(_schemas())
        return 0
    if command == "doctor":
        try:
            _print_json(doctor())
            return 0
        except Exception:
            _print_json(
                {
                    "status": "invalid_configuration",
                    "error": "Check the documented OMNIA environment settings.",
                },
                error=True,
            )
            return 2

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        from .server import create_server

        create_server().run(transport="stdio")
    except KeyboardInterrupt:
        return 0
    except Exception:
        _print_json(
            {
                "status": "server_failed",
                "error": "The local server could not start or complete its lifecycle.",
            },
            error=True,
        )
        return 1
    return 0
