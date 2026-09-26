# Contributing

Read [architecture](docs/architecture.md), [configuration](docs/configuration.md), and relevant existing tests. Keep provider inference, OMNIA policy, and transport separate.

## Development

Use Python 3.11+ in a virtual environment and a recent pip:

```sh
python -m pip install --upgrade pip
python -m pip install -e . --group dev
python -m ruff check .
python -m ruff format --check .
python -m pytest
python -m build
python -m twine check dist/*
```

The default tests must run without credentials, paid requests, or model downloads. Keep real-provider checks explicitly opt-in.

## Changes

- Make focused pull requests describing the problem, resulting behavior, and validation.
- Update schemas, examples, and docs when public contracts change.
- Preserve revision pins and the shared policy/ledger path when adding providers.
- Cover rejection paths, timeouts, malformed provider responses, and affected concurrency behavior.
- Keep credentials, databases, model caches, distributions, and private inputs out of Git.
- Preserve attribution when moving or adapting code. Original files use `Copyright 2026 OMNIA EYE Corporation` and `SPDX-License-Identifier: Apache-2.0` where comments are supported.

A successful test double does not establish provider availability or model quality. Follow [validation](docs/validation.md) when reporting evidence.

Contributions are licensed under the repository's Apache License 2.0.
