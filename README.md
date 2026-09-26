<p align="center"><img src="docs/assets/omnia-mcp.svg" alt="OMNIA MCP — JEV &amp; LAYA" width="760" /></p>

<p align="center">
  <a href="https://github.com/Omniaeye/omnia-mcp/actions/workflows/ci.yml"><img src="https://github.com/Omniaeye/omnia-mcp/actions/workflows/ci.yml/badge.svg" alt="CI status" /></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-3.11%2B-3776AB" alt="Python 3.11+" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="Apache 2.0 license" /></a>
</p>

**JEV and LAYA for MCP.**

Evaluate choice, score, and yes/no questions from your MCP client.

OMNIA MCP evaluates structured questions against supplied context. It applies a shared decision policy, records results in a local SQLite ledger, and returns a decision ID for later inspection. JEV uses the TypeSafe API; LAYA runs locally through an optional dependency.

The server exposes four tools over **stdio**. It does not execute the action suggested by a decision. Applications remain responsible for deciding how to use the result.

## What you get

- **Typed answers:** selected labels, rubric scores, binary probabilities, and provider metadata.
- **JEV and LAYA:** select hosted JEV or local LAYA through explicit configuration.
- **Decision records:** retrieve an earlier result by ID without another inference request.
- **Request limits:** configure input size, batches, concurrency, timeouts, and response size.

## Install

Requires Python **3.11+**. Source installation is the supported distribution path for this release; no PyPI package is required.

Install the tagged source with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install "git+https://github.com/Omniaeye/omnia-mcp.git@v0.1.0"
omnia-mcp version
omnia-mcp doctor
```

Or install from a checkout:

```sh
git clone https://github.com/Omniaeye/omnia-mcp.git
cd omnia-mcp
git checkout v0.1.0
python -m venv .venv
```

| Platform | Install | Inspect configuration |
| --- | --- | --- |
| macOS / Linux | `.venv/bin/python -m pip install .` | `.venv/bin/omnia-mcp doctor` |
| Windows PowerShell | `.\.venv\Scripts\python.exe -m pip install .` | `.\.venv\Scripts\omnia-mcp.exe doctor` |

`doctor` reports configuration without contacting a provider or loading model weights. `serve` starts the stdio server; running `omnia-mcp` with no subcommand does the same.

## Connect a client

Set `TYPESAFE_API_KEY` in the environment used to launch your MCP client and select JEV:

```text
OMNIA_DEFAULT_PROVIDER=jev
OMNIA_ALLOWED_PROVIDERS=jev
TYPESAFE_API_KEY=<your TypeSafe API key>
```

Use the installed executable as a local MCP server:

```json
{
  "mcpServers": {
    "omnia": {
      "command": "omnia-mcp",
      "args": ["serve"]
    }
  }
}
```

Use an absolute executable path if your desktop client cannot find `omnia-mcp`. Configuration formats and secret interpolation differ by client:

| Client | Example |
| --- | --- |
| Claude Code | [`.mcp.json`](examples/claude-code/.mcp.json) |
| Claude Desktop | [`claude_desktop_config.json`](examples/claude-desktop/claude_desktop_config.json) |
| Cursor | [`mcp.json`](examples/cursor/mcp.json) |
| VS Code | [`mcp.json`](examples/vscode/mcp.json) |

See [configuration](docs/configuration.md) and [compatibility](docs/compatibility.md). These are configuration recipes, not evidence of a completed client test.

## Evaluate a decision

Call `omnia_capabilities` first, then use `omnia_evaluate`. The MCP arguments contain a nested `request`:

```json
{
  "request": {
    "state": "A customer reports two charges for the same invoice.",
    "questions": {
      "queue": {
        "type": "choice",
        "instructions": "Choose the team that should inspect this request.",
        "criteria": {
          "billing": "Invoices, charges, payments, and refunds",
          "technical": "Software errors and access problems",
          "other": "Requests outside billing and technical support"
        }
      }
    }
  }
}
```

This is an input example, not a recorded provider response. Inspect the schemas shipped with your installation:

```sh
omnia-mcp schemas
```

| Tool | Arguments | Purpose |
| --- | --- | --- |
| `omnia_evaluate` | `request` | Evaluate one typed decision request. |
| `omnia_evaluate_batch` | `request.items` | Evaluate a bounded list of requests. |
| `omnia_capabilities` | None | Inspect the contract and configured capabilities. |
| `omnia_get_decision` | `decision_id` | Retrieve a decision from the local ledger. |

Question types have explicit meanings:

| Type | Result |
| --- | --- |
| `choice` | A selected label and its probability distribution. |
| `score` | An expected position on the supplied, zero-based rubric. |
| `noul` | A probability for the true/yes side of a binary question. |

The result includes `accepted` or `needs_review` according to the configured request policy. `accepted` means the model output passed those checks; it does not establish factual correctness or permission to act.

The server also provides `omnia://guide` and `omnia://schemas` resources. Optional fields and policy details are defined by the exported schemas.

## Run LAYA locally

From the checkout, install the local runtime extra with your virtual environment's Python:

```sh
python -m pip install ".[local-laya]"
```

The extra pins upstream `laya` to **0.3.20**. It does not start LAYA's native MCP server or require a separate inference service.

Select `OMNIA_DEFAULT_PROVIDER=laya` and `OMNIA_ALLOWED_PROVIDERS=laya`. Configure an approved model and its full commit revision through `OMNIA_LAYA_MODEL` and `OMNIA_LAYA_REVISION`; see [configuration](docs/configuration.md). Offline operation requires the exact model snapshot and dependencies to be present beforehand.

## Data and execution boundaries

JEV sends context and questions to TypeSafe. LAYA performs inference locally, although model preparation may require a download. The SQLite ledger stores answers, evidence references, labels, rubric descriptions, hashes, and model metadata. It does not store the full input state by default, but records are not anonymized. Retention automatically removes expired records; review storage and retention before using sensitive inputs.

Evaluation may incur provider usage and writes a decision record. OMNIA MCP is not a wallet, transaction signer, or trading executor. Scores are model outputs and must be validated for the intended domain.

## Develop

```sh
python -m pip install -e . --group dev
python -m ruff check .
python -m ruff format --check .
python -m pytest
python -m build
python -m twine check dist/*
```

CI runs without provider credentials. See [validation](docs/validation.md) for the distinction between contract tests, stdio tests, and real provider evaluation.

## Documentation

- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Compatibility](docs/compatibility.md)
- [Release process](docs/releasing.md)
- [Security](SECURITY.md) and [contributing](CONTRIBUTING.md)

## License and attribution

Apache License 2.0. OMNIA MCP is maintained by OMNIA. JEV is accessed through TypeSafe; the optional LAYA runtime is developed by Convai Innovations. These projects retain their own authorship, licenses, service terms, and model terms. See [NOTICE](NOTICE).

<!-- mcp-name: io.github.Omniaeye/omnia-mcp -->
