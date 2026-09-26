# Validation

Application behavior, protocol behavior, and model behavior are different validation layers. Passing one does not establish the others.

## Automated checks

CI installs from source on Python 3.11, 3.12, and 3.13. It runs Ruff and pytest, builds wheel/sdist artifacts, checks metadata with Twine, and installs the wheel outside the checkout for CLI smoke checks.

Provider tests use explicit test doubles and controlled HTTP responses. They establish translation, validation, policy, cache, and error behavior. They do not establish live availability, billing, accuracy, or latency.

Stdio tests launch the actual MCP process. They establish protocol communication, not behavior inside every desktop client.

```sh
python -m pip install -e . --group dev
python -m ruff check .
python -m ruff format --check .
python -m pytest
python -m build
python -m twine check dist/*
```

Use a recent pip supporting dependency groups. The default suite needs no provider credentials. Real calls and model downloads remain explicit opt-in work.

## Real providers

For live JEV evidence, record the server commit/version, provider/model, sanitized input, UTC timestamp, outcome, and usage metadata actually returned. Use a small, non-sensitive input and an operator-authorized credential.

For LAYA evidence, record its package version, checkpoint ID, full revision, OS, Python/PyTorch versions, device, dtype, and input limits. Test the installed wheel rather than imports that depend on a checkout.

For offline inference, prepare the environment and snapshot first, disable outbound network access, and run a real inference. An import-only check or test double cannot establish it. Cached operation does not prove first-time offline installation.

## Release evidence

Attach actual CI run links and provider/client records to release notes. State skipped checks plainly. This project makes no fixed accuracy, latency, profit, or universal-compatibility claim.

## Recorded local checks — 2026-09-26

A real local inference completed using public `laya==0.3.20`, the English checkpoint in `convaiinnovations/laya`, revision `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`, CPU, two threads, and offline mode with prepared assets. The supplied state contained one choice question and one binary question. [Raw smoke record](evidence/laya-offline-smoke-2026-09-26.json).

The complete cold smoke took 16.327 seconds on that machine. This single run includes startup and model loading; it is not a steady-state latency or accuracy benchmark. Prepared checkpoint files occupied approximately 0.84 GB; this is disk storage, not a RAM requirement or an installation-size guarantee.

A second real check exercised the MCP stdio process with the same public LAYA runtime and pinned checkpoint in CPU offline mode. It evaluated choice, binary, and score questions, validated structured output, retrieved the stored decision, and replayed it from cache with the same decision ID. The result remained `needs_review`; one decision was persisted and the client context closed. The first evaluation took 6.009 seconds, the cache replay 0.002 seconds, and the complete check 6.773 seconds on that machine. These are observations from one example, not a performance guarantee. [Stdio smoke record](evidence/laya-stdio-smoke-2026-09-26.json).

The source Docker image was built locally and checked with networking disabled: version and diagnostics completed, the process ran as UID 10001, and stdio discovery plus `omnia_capabilities` worked with both legacy and 2026-07-28 protocol clients. This base image does not include the LAYA runtime; those Docker checks did not perform inference.

The README evaluation example passed the source `EvaluationRequest` schema. Client JSON, workflow YAML, relative documentation links, SVG XML, and the identity-only `server.json` were validated. A schema-valid manifest with no package or remote entry is still not an installable Registry listing.
## JEV acceptance run — 2026-09-26

The hosted `jev-1.13.0` model was exercised through the actual MCP stdio server with 20 authored cases, five times each: **100 uncached evaluations**. Automatic retries were disabled; concurrency was two. The cases cover English, Portuguese, Chinese, and Japanese, with published releases, documentation-only commits, quoted authorship, missing snapshot fields, and explicit liquidity/volume/age/risk rules. These are controlled acceptance inputs, not production news or trading records.

The first run produced **87 valid decisions and 13 rejected responses**. All 201 question checks in the 87 valid decisions agreed with the declared rubric. The rejected responses were recorded as `invalid_provider_response` in the ledger; none became a successful decision. The initial runner displayed the less specific `tool_error`, which was separately corrected. The original report is retained unchanged.

A diagnostic request reproduced the integration defect: the provider returned score `1.99` alongside a displayed distribution whose expected score was `2.00`. The original validator assumed four-decimal precision. JEV returned two-decimal values in this run. The correction validates feasible rounded distributions and preserves the provider's original values; it does not change the rubric or recompute an answer. Two-decimal precision is an observed compatibility envelope for this pinned provider, not a provider SLA. [TypeSafe response contract](https://docs.typesafe.ai/api).

Evidence:

- [Acceptance cases](../evals/acceptance.json), including expectations fixed before execution.
- [Initial 100-evaluation report](evidence/jev-acceptance-initial-100-2026-09-26.json).
- [Read-only ledger reconciliation](evidence/jev-initial-ledger-check-2026-09-26.json).
- [Diagnostic provider response](evidence/jev-rounding-diagnostic-2026-09-26.json).
- [JEV evaluation, lookup, cache, and batch smoke](evidence/jev-stdio-smoke-2026-09-26.json).

The runner validates the dataset without inference by default:

```sh
python scripts/evaluate_suite.py --provider jev --repeats 5
```

With an authorized `TYPESAFE_API_KEY` in the process environment, start real evaluations explicitly. Choose a new output path; existing reports and ledgers are never overwritten:

```sh
python scripts/evaluate_suite.py --provider jev --repeats 5 --concurrency 2 --output var/acceptance.json --run
```

Passing this small rubric establishes agreement only for these cases. Repetition measures consistency, not additional independent coverage. The policy status `accepted` is reported separately from rubric agreement. It does not establish authorization to publish news or execute trades.

### Verification after the correction

The unchanged 20-case dataset was run once more against the real JEV API: **20/20 evaluations completed, 48/48 rubric checks passed, zero transport/validation failures, and 20 unique uncached decisions**. Observed median end-to-end call latency was 272 ms and p95 was 379 ms with concurrency two. These timings exclude installation and are not a throughput or production-capacity guarantee. [Post-correction report](evidence/jev-acceptance-postfix-20-2026-09-26.json).

The full controlled suite passed **175 tests**. The numerical tests exercise feasible rounded distributions and reject contradictions; service regression tests verify that JEV's two-decimal envelope does not weaken LAYA's four-decimal validation. A fresh real local LAYA stdio evaluation, lookup, and cache replay also passed after the correction. [Final LAYA smoke](evidence/laya-stdio-final-2026-09-26.json).

Across the acceptance work, 120 real JEV evaluations were attempted: the initial 100 plus 20 post-correction checks. One additional diagnostic call inspected the rounding defect; the separate JEV stdio/cache smoke performed one provider evaluation. Cache lookups and replays did not add inference calls. No request contained production records, customer accounts, wallet credentials, or private code.

The repeated initial sample remains useful failure evidence. The successful follow-up does not retroactively turn its 13 rejected responses into successful decisions, and one post-correction round does not establish five-round repeatability.

The final Docker build was rechecked after the source correction. All 15 installed Python files matched the working source by SHA-256. Both protocol modes passed with networking disabled and a non-root process; no test container remained. [Final Docker record](evidence/docker-final-2026-09-26.json).
