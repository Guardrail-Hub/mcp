# scan_api_scenario

Security-scan an **authenticated API workflow** — an ordered sequence of requests
that build state (log in, get a token, use it) before scanning.

## Purpose

**Use `scan_api_scenario` when** the endpoints you want to test only work inside
a stateful, authenticated sequence — for example:

```
Login → Get Profile → Create Order → Checkout → Logout
```

The tool replays the steps in dependency order, captures variables / cookies /
JWT tokens from each response, propagates them to later steps, and then
active-scans the authenticated endpoints using the standard scan pipeline.

**Do NOT use it when:**

- You only have one endpoint → [`scan_api`](scan_api.md).
- You want to scan many independent services/specs → [`scan_api_suite`](scan_api_suite.md).
- You need loops, conditional branches, retries, or data-driven runs — these are
  intentionally out of scope for the MVP.

## Workflow

```
submit scenario ─► QUEUED ─► Dispatcher assigns ONE worker
                                   │
              order steps (depends_on) ; for each step:
                 resolve ${vars} → send through ZAP proxy → capture token/cookie/vars
                                   │
                 (auth context now established for the whole session)
                                   ▼
              active-scan the scan-target steps (auth applied) → aggregate alerts
                                   ▼
                     save reports → COMPLETED
```

A scenario runs on **one** worker (a single coherent session). If any step
fails, the operation fails with a message naming the step.

## Input

Top level:

| Field | Required | Default | Description |
|---|---|---|---|
| `report_group` | yes | — | Groups reports. |
| `scan_mode` | no | `full` | Applied to the active-scan phase. |
| `steps` | yes | — | Ordered workflow steps (≥ 1). |
| `bucket_name`, `file_name` | no | — | Optional S3 export. |

Each **step** extends the standard request target and adds workflow fields:

| Step field | Required | Description |
|---|---|---|
| `name` | yes | Unique step name; referenced by `depends_on` and `${name.token}`. |
| `target_url`, `method` | yes | The request. `target_url` may contain `${var}` placeholders. |
| `headers`, `body` | no | May contain `${var}` placeholders. |
| `token`/`token_type`/`cookie` | no | Explicit per-step auth (overrides propagated context). |
| `token_field` | no | Dotted path to a token in the response to capture + propagate (e.g. `data.access_token`). |
| `cookie_field` | no | Dotted path to a cookie value to capture. |
| `extract` | no | `{var_name: dotted.path}` — capture arbitrary values for later `${var_name}` use. |
| `depends_on` | no | Names of steps that must run first. |
| `scan` | no | `true` marks this endpoint as an active-scan target. If no step sets it, all steps are scanned. |

### Example

```json
{
  "report_group": "storefront",
  "scan_mode": "full",
  "steps": [
    {
      "name": "login",
      "target_url": "https://api.example.com/v1/auth/login",
      "method": "POST",
      "body": {"username": "alice", "password": "s3cret"},
      "token_field": "data.access_token",
      "token_type": "jwt"
    },
    {
      "name": "get_profile",
      "target_url": "https://api.example.com/v1/me",
      "method": "GET",
      "depends_on": ["login"],
      "extract": {"user_id": "data.id"},
      "scan": true
    },
    {
      "name": "create_order",
      "target_url": "https://api.example.com/v1/users/${user_id}/orders",
      "method": "POST",
      "depends_on": ["get_profile"],
      "body": {"sku": "ABC-123", "qty": 1},
      "scan": true
    }
  ]
}
```

Here `login` captures a JWT (`token_field`), which is automatically attached to
`get_profile` and `create_order`; `get_profile` captures `user_id`, used in the
`create_order` URL via `${user_id}`.

## Output

Same shape as `scan_api` — a `ZapScanResult` with `summary`, `alerts`,
`tls_result`, and `reports` (ZAP HTML + JSON). Findings cover the scan-target
steps, scanned authenticated.

## Authentication

Two complementary mechanisms:

1. **Explicit** — set `token`/`token_type`/`cookie` on any step (same as
   `scan_api`).
2. **Captured & propagated** — set `token_field` (and/or `cookie_field`) on the
   login step; the captured JWT/bearer token and cookies are carried to every
   later step automatically. Reference a captured token explicitly as
   `${<step>.token>}` if needed.

| Scenario auth need | How |
|---|---|
| Log in, reuse JWT | `token_field: "data.access_token"`, `token_type: jwt` on login |
| Session cookie | `cookie_field` on login, or ZAP auto-captures `Set-Cookie` |
| Static API key for all steps | put `token`/`token_header_name` on each step, or on the first + let propagation carry it |

## Target reachability (Docker)

Every step's target must be reachable **from the assigned ZAP worker** — a
scenario runs entirely from the worker container, not from the API caller. Before
each step is replayed, a lightweight pre-flight check probes that step's target
*through the worker's proxy*; if the worker cannot reach it, the scenario fails
fast with an actionable message instead of a late `url_not_found` from the
active-scan phase. The check never rewrites your URL or guesses ports.

When the worker runs in Docker:

- `localhost` / `127.0.0.1` inside the worker refer to the **worker container
  itself**, not your application.
- Use the application's **service/container hostname** and its **internal
  container port** — not the published host port.
- The worker and the target must **share a Docker network**; services on
  different networks cannot reach each other unless a network is shared.

## Best practices

- Order matters — declare `depends_on` so the engine can resolve a correct order
  even if the authored order changes.
- Capture only what you reuse; keep `extract` variable names unique and
  descriptive.
- Mark the endpoints you actually want fuzzed with `scan: true`; leave `login`
  and `logout` unmarked if you only want to scan business endpoints.
- Keep credentials in the login step; let propagation handle the rest.

## Common mistakes

- **Circular `depends_on`** (`a → b → a`) — rejected up front: *"Circular
  dependency between steps: a -> b -> a."*
- **`depends_on` a step that doesn't exist** — *"Step 'x' depends on unknown step
  'y'."*
- **Duplicate step names / duplicate `extract` variables** — rejected with a
  message naming the collision.
- **Referencing `${var}` before it's captured** — the placeholder resolves to
  empty; make the capturing step a dependency.
- **Templated host** (`https://${host}/x`) — allowed, but ensure the variable is
  captured before the step runs.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Fails: "Pre-flight reachability check failed …" | The worker cannot resolve/connect to that step's target | Use a host+port reachable from the worker; see **Target reachability (Docker)** above. |
| Fails at step 'login' | Login endpoint unreachable / bad creds | Verify URL, body, and credentials. |
| Later steps unauthorized (401) | Token not captured | Check `token_field` path matches the real response JSON. |
| `${user_id}` appears literally in a URL | Variable not captured or wrong path | Fix the `extract` dotted path; ensure `depends_on`. |
| Only login gets scanned | No `scan: true` and login is first | Mark business steps with `scan: true`. |
| 422 on submit | Structural workflow error | Read the message — it names the exact problem. |
| Fails: "could not start the active scan … url_not_found" | The target was never reached during replay, so ZAP has no node to scan | Ensure the target is reachable from the ZAP **worker** container. `localhost`/`127.0.0.1` refer to the worker itself, not your app — use the app's reachable host/IP or Docker service name. |
