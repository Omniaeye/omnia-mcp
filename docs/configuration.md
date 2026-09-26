# Configuration

OMNIA MCP reads its process environment. Run `omnia-mcp doctor` to validate configuration and inspect the selected providers and installed packages before connecting a client. It does not certify provider readiness. The server does not automatically load `.env` files. [.env.example](../.env.example) is the editable configuration reference, not a credential store.

Set variables in the environment that actually starts the server. A desktop app may not inherit variables from a terminal opened afterward. Use the client's secret input facility when available. Do not commit keys to client JSON.

## Providers

| Variable | Purpose |
| --- | --- |
| `OMNIA_DEFAULT_PROVIDER` | Provider used when the request does not select one. |
| `OMNIA_ALLOWED_PROVIDERS` | Comma-separated allowlist, such as `jev` or `jev,laya`. |
| `TYPESAFE_API_KEY` | TypeSafe credential used by JEV. |
| `OMNIA_JEV_MODEL` | JEV model configured by the operator. |

Keep the allowlist as small as the installation needs. Inspect capabilities and request schemas through `omnia_capabilities` and `omnia-mcp schemas`.

## Local LAYA

Install `.[local-laya]` from a checkout. It selects the public `laya==0.3.20` runtime. The base installation does not install the local runtime.

| Variable | Purpose |
| --- | --- |
| `OMNIA_LAYA_MODEL` | `english`, `typed-decisions`, or `multilingual` within `convaiinnovations/laya`. |
| `OMNIA_LAYA_REVISION` | Full commit revision of that checkpoint. |
| `OMNIA_LAYA_DEVICE` | Device selection supported by the local provider. |
| `OMNIA_LAYA_THREADS` | CPU thread setting. |
| `OMNIA_LAYA_MAX_LEN` | Model input length limit. |
| `OMNIA_LAYA_HEAD_MAX_LEN` | Question/head length configuration. |
| `OMNIA_LAYA_OFFLINE` | Require local assets rather than fetching missing files. |

The library version and model revision are separate pins. Prepare the exact approved snapshot before offline operation. If files are absent, fix preparation; do not replace a missing revision with `main` or silently choose another checkpoint.

Local inference consumes memory and compute. Python support does not imply availability of every GPU build or accelerator on every operating system.

## Ledger and limits

| Variable | Purpose |
| --- | --- |
| `OMNIA_DATABASE` | SQLite ledger path in persistent application storage. |
| `OMNIA_CACHE_TTL_SECONDS` | Cache lifetime. |
| `OMNIA_RETENTION_DAYS` | Ledger retention setting. |
| `OMNIA_MAX_INPUT_BYTES` | Maximum serialized input size. |
| `OMNIA_MAX_QUESTIONS` | Question count bound per evaluation. |
| `OMNIA_MAX_OPTIONS` | Question option bound. |
| `OMNIA_MAX_BATCH_ITEMS` | Requests allowed in a batch. |
| `OMNIA_MAX_CONCURRENT_REQUESTS` | Concurrent evaluation bound. |
| `OMNIA_MAX_QUEUE_SIZE` | Pending work bound. |
| `OMNIA_MAX_REQUESTS_PER_MINUTE` | Ledger-backed evaluation allowance per minute. |
| `OMNIA_MAX_REQUESTS_PER_DAY` | Ledger-backed evaluation allowance per UTC day. |
| `OMNIA_REQUEST_TIMEOUT_SECONDS` | Cooperative timeout covering queued work and inference. |
| `OMNIA_MAX_RETRIES` | Provider retry bound. |
| `OMNIA_MAX_RESPONSE_BYTES` | Provider response size bound. |
| `OMNIA_LOG_LEVEL` | Diagnostic log level. |

See `.env.example` and the installed version's settings for the defaults. The default provider is LAYA, which requires the optional runtime and an explicit revision; JEV requires explicit provider selection and a credential. Changing input or model limits may affect results. Record configuration alongside deployment evidence.

Quotas count evaluations admitted by the service. JEV retries can make more than one HTTP attempt per evaluation, bounded by `OMNIA_MAX_RETRIES + 1`; these quotas are not an exact network-request rate limit. Timeouts include queueing and inference, but disk I/O and cleanup can add scheduling delay.

Retention removes expired decisions on the first evaluation and on later evaluations that trigger an hourly maintenance check. Records can include evidence references, labels, and score-rubric descriptions. Excluding the full state does not anonymize them. Local LAYA evaluations within a batch run sequentially; this preserves the local worker's single-inference boundary.

## Docker

Build from source:

```sh
docker build -t omnia-mcp:0.1.0 .
docker volume create omnia-mcp-data
docker run --rm -i --env TYPESAFE_API_KEY --env OMNIA_DEFAULT_PROVIDER=jev --env OMNIA_ALLOWED_PROVIDERS=jev --mount source=omnia-mcp-data,target=/data omnia-mcp:0.1.0
```

Do not detach the container: stdio must remain connected to the client. The image runs as a non-root user with its ledger under `/data`.

The default image contains the hosted-provider installation. LAYA requires its optional runtime, approved weights, and a separately validated image for the target hardware. No prebuilt registry image is claimed.
