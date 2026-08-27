# scan_api

Security-scan a **single API endpoint** with OWASP ZAP.

## Purpose

**Use `scan_api` when** you want to test one URL + HTTP method — optionally
authenticated — and get a vulnerability report for that endpoint. It is the
building block the other two tools reuse.

**Do NOT use it when:**

- The endpoints require an ordered, authenticated workflow (log in, then call
  other endpoints in sequence) → use [`scan_api_scenario`](scan_api_scenario.md).
- You want to scan many services/specs as one application → use
  [`scan_api_suite`](scan_api_suite.md).

## Workflow

```
submit scan_api ──► operation QUEUED ──► Dispatcher assigns a ZAP worker
      │                                        │
      └─ returns {operation_id, queued}        ▼
                                     route request through ZAP proxy (passive scan)
                                                │
                                quick: spider ──┤── full: context-scoped active scan
                                                ▼
                                 active scan ► collect alerts ► save reports ► COMPLETED
```

You poll `GET /api/history/get-result?operation_id=<id>` until `status` is
`completed` or `failed`.

## Input

| Field | Required | Default | Description |
|---|---|---|---|
| `report_group` | yes | — | Groups reports on disk/S3. |
| `target_url` | yes | — | Absolute http(s) URL to scan. |
| `method` | yes | — | `GET`/`POST`/`PUT`/`PATCH`/`DELETE`/`HEAD`/`OPTIONS`. |
| `scan_mode` | no | `full` | `quick` (spider + fast param scan) or `full` (body fuzzing). |
| `headers` | no | — | Extra request headers. |
| `body` | no | — | JSON object/array, raw string, or form payload. |
| `token` | no | — | Auth token value (see Authentication). |
| `token_type` | no | `bearer` | `bearer` \| `jwt` \| `access_token` \| `custom`. |
| `token_header_name` | no | `Authorization` | Header the token is sent under. |
| `token_prefix` | no | — | Prefix before the token; `Bearer` assumed for bearer/jwt/access_token. |
| `cookie` | no | — | Raw `Cookie` header value. |
| `bucket_name`, `file_name`, `prompt` | no | — | Optional S3 export + LLM tuning hint. |

### Example

```json
{
  "report_group": "storefront",
  "target_url": "https://api.example.com/v1/orders",
  "method": "POST",
  "scan_mode": "full",
  "headers": {"Content-Type": "application/json"},
  "body": {"sku": "ABC-123", "qty": 1},
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "jwt"
}
```

## Output

Returns `{ "operation_id": "...", "status": "queued", "message": "..." }`. Once
complete, the operation's `result` is a `ZapScanResult`:

```json
{
  "operation_id": "3d65f41b-...",
  "report_group": "storefront",
  "status": "completed",
  "target_url": "https://api.example.com/v1/orders",
  "duration_seconds": 42.1,
  "summary": {"total": 3, "high": 1, "medium": 1, "low": 1, "informational": 0},
  "alerts": [
    {"name": "SQL Injection", "risk": "High", "url": "https://api.example.com/v1/orders",
     "solution": "Use parameterized queries.", "cwe_id": 89}
  ],
  "tls_result": {"TLSv1.2": "enabled", "TLSv1.0": "disabled"},
  "reports": {"view_report": ".../<id>.html", "export_data": ".../<id>.json"}
}
```

- **`view_report`** — ZAP's HTML report (open in a browser).
- **`export_data`** — ZAP's JSON report (import into other tools).
- SARIF is produced at the **application** level by `scan_api_suite`; a single
  `scan_api` returns ZAP HTML + JSON.

## Authentication

| Method | Fields | Result header |
|---|---|---|
| Bearer | `token`, `token_type: bearer` | `Authorization: Bearer <token>` |
| JWT | `token`, `token_type: jwt` | `Authorization: Bearer <token>` |
| Access token | `token`, `token_type: access_token` | `Authorization: Bearer <token>` |
| API key (custom header) | `token`, `token_type: custom`, `token_header_name: X-Api-Key` | `X-Api-Key: <token>` |
| Cookie / session | `cookie: "session=...; csrf=..."` | `Cookie: session=...; csrf=...` |

Auth is applied both to the recorded request and to every request ZAP issues
during the active scan (via replacer rules). Omit auth for public endpoints.

## Target reachability (Docker)

The target must be reachable **from the assigned ZAP worker** — scans run from
the worker container, not from the API caller. Before scanning, a lightweight
pre-flight check probes the target *through the worker's proxy*; if the worker
cannot reach it, the scan fails fast with an actionable message instead of a
late, cryptic `url_not_found`. The check never rewrites your URL, substitutes
hosts, or guesses ports — it only explains the likely cause.

When the worker runs in Docker:

- `localhost` / `127.0.0.1` inside the worker refer to the **worker container
  itself**, not your application.
- Use the application's **service/container hostname** and its **internal
  container port** — not the published host port.
- The worker and the target must **share a Docker network**; services on
  different networks cannot reach each other unless a network is shared.

## Best practices

- Use `quick` in CI gates for speed; use `full` for pre-release depth.
- Scan realistic bodies — active scanning fuzzes the fields you send.
- Give each project a stable `report_group` so reports stay grouped.
- Prefer short-lived tokens; a scan can take a while, so ensure the token stays
  valid for the run.

## Common mistakes

- **Relative `target_url`** (`/v1/orders`) — must be absolute with scheme; the
  request is rejected up front: *"target_url … is not a valid absolute URL."*
- **`token_prefix` without a `token`** — rejected: *"token_prefix is set but no
  'token' or 'token_field' was provided."*
- **Expecting the report inline** — the call returns an `operation_id`; poll for
  the report.
- **Wrong `token_type` for an API key** — use `custom` + `token_header_name`,
  not `bearer`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `status` stays `queued` | No idle ZAP worker registered | Start/scale ZAP workers; check the worker registry. |
| `failed`: "ZAP is not accessible" | Worker's ZAP process down | Verify the ZAP container is up and healthy. |
| Auth-required endpoint returns only info findings | Token/cookie not applied | Check `token_type`/`token_header_name`; confirm the token is valid. |
| Scan runs very long | `full` mode on a large surface | Use `quick`, or scope the URL more tightly. |
| 422 on submit | Request validation failed | Read the message — it names the exact field and fix. |
| Fails: "Pre-flight reachability check failed …" | The worker cannot resolve/connect to the target | Use a host+port reachable from the worker; see **Target reachability (Docker)** above. |
| Fails: "could not start the active scan … url_not_found" | Target not reachable from the ZAP worker, so ZAP recorded no node | Use a host reachable from the worker container; `localhost`/`127.0.0.1` point at the worker itself, not your app. |
