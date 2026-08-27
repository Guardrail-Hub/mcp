# Configuration Reference

Every setting the GuardRail MCP Server reads is declared in the `app/core/config`
package (a `pydantic-settings` mixin package) and loaded from environment
variables / `.env`. This document is the complete, authoritative list. Copy
[`.env.example`](../.env.example) to `.env` and change only what you need.

- **Source of truth:** the config mixins (`_app`, `_auth`, `_database`,
  `_security`, `_server`, `tools/_zap`). Variable names are the upper-cased field
  names.
- **Access in code:** `from app.core.config import settings`.
- **Backward compatible:** all defaults below match the code; nothing is renamed.

## Fail-fast in production

When `APP_ENV=production`, the server validates required settings **at startup**
and refuses to boot (raising a clear error) if any are missing or left at an
insecure default. In `development`/`staging` these checks are skipped so local
defaults keep working. The production-required settings are:

| Condition | Required |
|---|---|
| always | `INTERNAL_TOKEN_SECRET` ≠ default and ≥ 32 chars (when `TOKEN_ALGORITHM=HS256`) |
| `TOKEN_ALGORITHM=RS256` | `TOKEN_PRIVATE_KEY_PATH`, `TOKEN_PUBLIC_KEY_PATH` |
| `MCP_MODE=CONNECTED` or `TOKEN_AUTHORITY=PLATFORM_ISSUED` | `PLATFORM_API_URL` |
| `DATABASE_PROVIDER=POSTGRES` | `POSTGRES_PASSWORD` or `DATABASE_URL` |
| `DATABASE_PROVIDER=DYNAMODB` | AWS creds or `DYNAMODB_ENDPOINT_URL` |
| always | `ZAP_API_KEY` ≠ default |

## App / Server

| Variable | Type | Default | Description |
|---|---|---|---|
| `APP_ENV` | enum | `development` | `development` \| `staging` \| `production`. Gates the fail-fast checks. |
| `APP_DEBUG` | bool | `true` | FastAPI debug mode. |
| `APP_NAME` | str | `GuardRail MCP Server` | Display name. |
| `APP_VERSION` | str | `0.1.0` | Reported version. |
| `MCP_SERVER_HOST` | str | `0.0.0.0` | Bind host. |
| `MCP_SERVER_PORT` | int | `8787` | Bind port. |
| `MCP_SERVER_PUBLIC_URL` | str | `http://localhost:8787` | Externally-visible base URL. |
| `MCP_MODE` | enum | `STANDALONE` | `STANDALONE` \| `CONNECTED`. |
| `MCP_DEPLOYMENT_MODE` | enum | `SELF_HOSTED_PRIVATE` | `HOSTED_PUBLIC` \| `SELF_HOSTED_PRIVATE`. |

## Auth / Tokens

| Variable | Type | Default | Description |
|---|---|---|---|
| `TOKEN_AUTHORITY` | enum | `SELF_MANAGED` | `SELF_MANAGED` \| `PLATFORM_ISSUED`. |
| `TOKEN_ALGORITHM` | enum | `HS256` | `HS256` (shared secret) \| `RS256` (key pair). |
| `INTERNAL_TOKEN_SECRET` | str | `change-this-secret` | HS256 signing secret. **Validation:** if not the default, must be ≥ 32 chars; **required (non-default, ≥32)** in production. |
| `TOKEN_ISSUER` | str | `guardrail-mcp-local` | JWT `iss`. |
| `TOKEN_AUDIENCE` | str | `guardrail-mcp` | JWT `aud`. |
| `TOKEN_EXPIRES_IN_DAYS` | int | `90` | Token lifetime. |
| `TOKEN_PRIVATE_KEY_PATH` | str? | — | RS256 private key path. Required in prod if `RS256`. |
| `TOKEN_PUBLIC_KEY_PATH` | str? | — | RS256 public key path. Required in prod if `RS256`. |
| `JWKS_URL` | str? | — | JWKS endpoint (optional). |
| `PLATFORM_API_URL` | str? | — | Platform base URL. Required in prod for connected mode. |
| `PLATFORM_API_TOKEN` | str? | — | Platform API token. |
| `PLATFORM_TOKEN_INTROSPECTION_PATH` | str | `/internal/tokens/introspect` | Introspection path. |
| `PLATFORM_AUDIT_PATH` | str | `/internal/audit/events` | Audit-forwarding path. |
| `PLATFORM_TIMEOUT_SECONDS` | int | `10` | Platform HTTP timeout. |

Empty strings for the optional (`str?`) auth fields are normalised to “unset”.

## Storage

| Variable | Type | Default | Description |
|---|---|---|---|
| `DATABASE_PROVIDER` | enum | `NONE` | `NONE` \| `POSTGRES` \| `DYNAMODB`. `NONE` = operations are not persisted. Selects the DAO via `get_operation_dao()`. |
| `DATABASE_URL` | str? | — | Explicit Postgres DSN; takes precedence when set. |
| `POSTGRES_HOST` | str | `postgres` | Postgres host. |
| `POSTGRES_PORT` | int | `5432` | Postgres port. |
| `POSTGRES_DB` | str | `guardrail_hub` | Database name. |
| `POSTGRES_USER` | str | `guardrail` | Username. |
| `POSTGRES_PASSWORD` | str? | — | Password. Required in prod for `POSTGRES` (unless `DATABASE_URL`). |
| `AWS_REGION` | str | `ap-southeast-1` | DynamoDB region. |
| `AWS_ACCESS_KEY_ID` | str? | — | AWS creds (DynamoDB). |
| `AWS_SECRET_ACCESS_KEY` | str? | — | AWS creds (DynamoDB). |
| `DYNAMODB_ENDPOINT_URL` | str? | — | Local DynamoDB endpoint; empty = real AWS. |
| `DYNAMODB_TABLE_PREFIX` | str | `guardrail_local` | Table-name prefix. |

Empty strings for `DATABASE_URL`, `POSTGRES_PASSWORD`, `DYNAMODB_ENDPOINT_URL`,
and the AWS creds are normalised to “unset”.

## Audit & Logging

| Variable | Type | Default | Description |
|---|---|---|---|
| `AUDIT_MODE` | enum | `LOCAL` | `LOCAL` \| `PLATFORM` \| `BOTH` \| `NONE`. |
| `AUDIT_LOG_PATH` | str | `./logs/audit.log` | Audit log file. |
| `AUDIT_LOG_FORMAT` | enum | `jsonl` | `jsonl` \| `json` (normalised to lowercase). |
| `AUDIT_INCLUDE_ARGUMENTS` | bool | `false` | Include tool arguments in audit entries. |
| `AUDIT_MASK_SECRETS` | bool | `true` | Redact secrets in audit entries. |
| `LOG_LEVEL` | enum | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL` (normalised uppercase). |
| `LOG_FORMAT` | enum | `json` | `text` \| `json` (normalised lowercase). |
| `LOG_FILE_PATH` | str | `./logs/server.log` | Server log file. |

## Rate limit & CORS

| Variable | Type | Default | Description |
|---|---|---|---|
| `RATE_LIMIT_ENABLED` | bool | `true` | Toggle rate limiting. |
| `RATE_LIMIT_REQUESTS` | int | `60` | Requests per window. |
| `RATE_LIMIT_WINDOW_SECONDS` | int | `60` | Window size. |
| `CORS_ALLOW_ORIGINS` | csv | — (empty) | Comma-separated origins. **Any non-empty value auto-enables CORS.** |
| `CORS_ALLOW_CREDENTIALS` | bool | `false` | Allow credentials. |

`CORS_ENABLED` is derived (true when origins are set); it is not set directly.

## Tool policy & Security guards

| Variable | Type | Default | Description |
|---|---|---|---|
| `TOOL_POLICY_PATH` | str | `./config/tool-policy.json` | Tool-policy file. |
| `ALLOWED_TOOLS_PATH` | str | `./config/allowed-tools.json` | Allowed-tools file. |
| `ALLOW_DANGEROUS_TOOLS` | bool | `false` | Enable dangerous tools. |
| `REQUIRE_APPROVAL_FOR_HIGH_RISK` | bool | `true` | Approval gate (high risk). |
| `REQUIRE_APPROVAL_FOR_CRITICAL_RISK` | bool | `true` | Approval gate (critical risk). |
| `WORKSPACE_DIR` | str | `./data/workspace` | Working directory. |
| `TEMP_DIR` | str | `./data/tmp` | Temp directory. |
| `MAX_FILE_SIZE_MB` | int | `10` | Max handled file size. |
| `ALLOW_PATH_TRAVERSAL` | bool | `false` | Allow `..` path traversal (keep false). |
| `BLOCKED_FILE_PATTERNS` | csv | `.env,*.pem,*.key,id_rsa,id_ed25519` | Blocked globs. |
| `ALLOWED_FILE_EXTENSIONS` | csv | `.txt,.md,.json,.yaml,.yml,.log` | Allowed extensions. |
| `ENABLE_SECRET_GUARD` | bool | `true` | Secret-scanning guard. |
| `ENABLE_COMMAND_GUARD` | bool | `true` | Command guard. |
| `ENABLE_PATH_GUARD` | bool | `true` | Path guard. |
| `DISABLE_SHELL_EXECUTION` | bool | `true` | Disable shell execution. |
| `COMMAND_TIMEOUT_SECONDS` | int | `30` | Command timeout. |
| `MAX_TOOL_EXECUTION_SECONDS` | int | `60` | Tool execution timeout. |

## Notifications — Slack (outbound)

Consumed by `app.bootstrap.build_notification_flow_from_settings`, which registers
the `SlackNotificationChannel` and subscribes operation events **only when
`SLACK_ENABLED=true`**. When disabled, no channel is registered and no
notifications are sent.

| Variable | Type | Default | Description |
|---|---|---|---|
| `SLACK_ENABLED` | bool | `false` | Master switch for Slack notifications. |
| `SLACK_BOT_TOKEN` | str? | — | Bot token (`xoxb-…`, scope `chat:write`). **Required** when `SLACK_ENABLED=true`. |
| `SLACK_DEFAULT_CHANNEL` | str? | — | Channel id to post to (bot must be invited). **Required** when `SLACK_ENABLED=true`. |

**Validation (fail fast, all environments):** if `SLACK_ENABLED=true` and either
`SLACK_BOT_TOKEN` or `SLACK_DEFAULT_CHANNEL` is missing, startup fails with a
clear error. Empty strings are treated as unset.

> Inbound Slack settings (e.g. a signing secret for slash-command verification)
> are intentionally **not** here. They will be added with the Slash Commands
> feature that actually reads them.

## OWASP ZAP

There is **no** container-name-prefix, worker-count, or hostname-array
configuration for ZAP workers — that generation scheme (`prefix-{index}`) has
been removed entirely. Workers self-register at runtime
(`POST /workers/register`) and send periodic heartbeats
(`POST /workers/heartbeat`); the Worker Registry
(`app/integrations/owasp_zap/registry.py`) is the single source of truth for
the pool. See [`architecture/zap-worker-pool/`](../../architecture/zap-worker-pool/README.md)
for the full design.

| Variable | Type | Default | Description |
|---|---|---|---|
| `ZAP_API_KEY` | str | `change-this-zap-key` | ZAP API key, shared by every worker. **Required (non-default)** in production. |
| `ZAP_READY_STABLE_SUCCESS_COUNT` | int | `3` | Consecutive health successes a worker requires (worker-side) before it registers itself. |
| `ZAP_SCAN_TIMEOUT_SECONDS` | int | `600` | Active-scan timeout. |
| `ZAP_SPIDER_TIMEOUT_SECONDS` | int | `300` | Spider timeout. |
| `ZAP_PASSIVE_SCAN_TIMEOUT_SECONDS` | int | `120` | Passive-scan timeout. |
| `ZAP_REQUEST_TIMEOUT_SECONDS` | float | `30.0` | Per proxied-request timeout. |

### Worker Registry / Pool Manager

| Variable | Type | Default | Description |
|---|---|---|---|
| `ZAP_WORKER_HEARTBEAT_TIMEOUT_SECONDS` | int | `60` | A worker silent for this long is marked Offline: it stops receiving new operations, and any operation it was running is marked FAILED (no auto-retry). |
| `ZAP_WORKER_HEARTBEAT_SWEEP_INTERVAL_SECONDS` | float | `10.0` | How often the server checks every worker's `last_heartbeat` against the timeout above. |
| `ZAP_QUEUE_POLL_INTERVAL_SECONDS` | float | `2.0` | How often the Pool Manager polls the Operations table for `QUEUED` work. |

### ZAP worker container

These are read by `docker/owasp_zap/register-worker.sh` — the worker-side
registration/heartbeat script baked into the ZAP image — **not** by the
mcp-server app. Set them on the `zap` service only.

| Variable | Type | Default | Description |
|---|---|---|---|
| `MCP_SERVER_INTERNAL_URL` | str | `http://mcp-server:8787` | Base URL of the mcp-server as seen from inside the ZAP container's network. |
| `ZAP_API_PORT` | int | `8080` | Port the worker's own ZAP REST API listens on; used to build its `endpoint`. |
| `ZAP_WORKER_HEARTBEAT_INTERVAL_SECONDS` | int | `20` | How often the worker sends a heartbeat. Should be well under `ZAP_WORKER_HEARTBEAT_TIMEOUT_SECONDS`. |

> There is **no** `ZAP_API_URL` variable on the server side — each worker
> reports its own `endpoint` at registration. (An older `.env.example` also
> listed `DYNAMODB_LOCAL_PORT`; it was never read by the code and has been
> removed.)

## Deliberately not configured yet

Per the "add only what the MVP needs, no speculative config" rule, the following
are **not** configured because no code reads them yet:

- **Slack inbound (slash commands)** — e.g. a signing secret. Deferred to the
  Slash Commands feature. (Slack *outbound* notifications are configured above.)
- **Discord and any other notification providers** — no adapter is wired.

These are added only alongside the code that consumes them.
