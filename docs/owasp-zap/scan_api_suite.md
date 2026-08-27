# scan_api_suite

Security-scan a **whole application** made of multiple API services, as one job,
with an aggregated application-level report.

## Purpose

**Use `scan_api_suite` when** your application is composed of several services,
each with its own OpenAPI specification, and you want to scan them together and
get one roll-up report:

```
Application "Storefront"
├─ Authentication   auth.yaml
├─ Users            users.yaml
├─ Orders           orders.yaml
└─ Payments         payments.yaml
```

The suite is an **orchestrator**: it expands each category's OpenAPI spec into
child `scan_api` operations, runs them in parallel across the ZAP worker pool,
waits for them, and aggregates the results. It never scans anything itself and
never occupies a worker.

**Do NOT use it when:**

- You only have one endpoint → [`scan_api`](scan_api.md).
- You need a single authenticated workflow → [`scan_api_scenario`](scan_api_scenario.md).

## Workflow

```
submit suite ─► QUEUED ─► Dispatcher runs the orchestrator (NO worker)
                                │
     parse each category's OpenAPI spec → expand endpoints
                                │
     create one child scan_api op per endpoint (QUEUED, parent=suite)
                                │
     Dispatcher schedules children across ALL idle workers (parallel)
                                │
     poll children until all terminal → aggregate → app report → COMPLETED
```

## Input

Top level:

| Field | Required | Default | Description |
|---|---|---|---|
| `report_group` | yes | — | Groups reports. |
| `suite_name` | yes | — | User-defined name for this scan run; shown as the report title. Accepts `application_name` as a legacy alias. |
| `scan_mode` | no | `full` | Applied to every child scan. |
| `categories` | yes | — | The API groups/services (≥ 1). |
| `max_parallelism` | no | — | Advisory only; real parallelism = idle workers. |
| `metadata` | no | — | Free-form, echoed into the report. |

Each **category**:

| Field | Required | Description |
|---|---|---|
| `name` | yes | Category/service name (used in category reports); must be unique. |
| `openapi_spec` | yes | OpenAPI 3.x as an inline object or a JSON/YAML string. |
| `base_url` | no | Prefix for spec paths; falls back to the spec's `servers[0].url`. |
| `methods` | no | Only expand these HTTP methods. |
| `defaults` | no | Shared auth/headers applied to every endpoint in this category. |

`defaults` (auth-only): `headers`, `token`, `token_type`, `token_header_name`,
`token_prefix`, `cookie`.

### Example

```json
{
  "report_group": "storefront",
  "suite_name": "Storefront release scan",
  "scan_mode": "full",
  "categories": [
    {
      "name": "Authentication",
      "base_url": "https://api.example.com",
      "openapi_spec": {"paths": {"/auth/login": {"post": {}}}}
    },
    {
      "name": "Orders",
      "base_url": "https://api.example.com",
      "defaults": {"token": "eyJhbGci...", "token_type": "jwt"},
      "openapi_spec": {"paths": {"/orders": {"get": {}, "post": {}}}}
    }
  ]
}
```

## Output

The suite operation completes with an aggregated report saved in three
structurally-consistent formats (same findings, different consumers):

- **Markdown** (`<id>-suite.md`) — human report.
- **JSON** (`<id>-suite.json`) — the canonical structure below.
- **SARIF 2.1.0** (`<id>-suite.sarif.json`) — for code-scanning tools/IDEs.

JSON structure:

```json
{
  "suite_name": "Storefront release scan",
  "generated_at": "2026-07-14T...Z",
  "executive_summary": {
    "overall_risk": "High",
    "endpoints_scanned": 3, "endpoints_failed": 0,
    "total_findings": 5, "distinct_findings": 3,
    "headline": "1 High / 2 Medium finding(s) across 3 endpoint(s) — overall risk: High."
  },
  "scan_summary": {"endpoints": 3, "completed": 3, "failed": 0},
  "severity_summary": {"total": 5, "high": 1, "medium": 2, "low": 2, "informational": 0},
  "category_summary": [{"name": "Authentication", "endpoints": 1, "summary": {"...": 0}}],
  "recommendations": ["Address High-risk findings before release ...", "Start with 'SQL Injection' ..."],
  "detailed_findings": [
    {"rule_id": "40018", "name": "SQL Injection", "risk": "High", "count": 1,
     "categories": ["Orders"], "example_url": "https://api.example.com/orders", "solution": "..."}
  ],
  "categories": [{"name": "Orders", "summary": {"...": 0}, "endpoints": [{"method": "GET", "url": "...", "status": "completed"}]}],
  "reports": {"view_report": ".../<id>-suite.md", "export_data": ".../<id>-suite.json", "sarif": ".../<id>-suite.sarif.json"}
}
```

Report sections (Markdown mirrors this order): **Executive Summary → Scan Summary
→ Severity Summary → Category Summary → Recommendations → Detailed Findings →
Endpoints by Category**.

## Authentication

Set auth per category via `defaults` — it is applied to every endpoint expanded
from that category's spec. Different services can use different tokens.

```json
{"name": "Orders", "defaults": {"token": "eyJ...", "token_type": "jwt"}}
{"name": "Legacy", "defaults": {"token": "k-123", "token_type": "custom", "token_header_name": "X-Api-Key"}}
```

If a category is public, omit `defaults`.

## Target reachability (Docker)

Every expanded endpoint must be reachable **from the assigned ZAP worker** —
scans run from the worker containers, not from the API caller. Each child scan
runs the same lightweight pre-flight check (through its worker's proxy) before
scanning, so an unreachable endpoint fails that child fast with an actionable
message rather than a late `url_not_found`; the other children continue. The
check never rewrites your URL or guesses ports.

When the workers run in Docker:

- `localhost` / `127.0.0.1` inside a worker refers to the **worker container
  itself**, not your application.
- Set each category's `base_url` to the application's **service/container
  hostname** and its **internal container port** — not the published host port.
- The workers and the targets must **share a Docker network**; services on
  different networks cannot reach each other unless a network is shared.

## Best practices

- One category per service/spec; keep names unique and human-friendly (they head
  the category reports).
- Set `base_url` explicitly if the spec's `servers` is missing or environment-
  specific.
- Use `methods` to scope large specs (e.g. skip `GET` read-only endpoints).
- Scale ZAP workers to increase real parallelism — the suite fans out as wide as
  the pool allows.
- Consume **SARIF** in CI to annotate findings inline; keep **JSON** for
  dashboards; share **Markdown** with stakeholders.

## Common mistakes

- **A category with empty/zero-endpoint `paths`** — rejected up front: *"Category
  'X' expands to zero endpoints."*
- **Malformed spec string** — rejected: *"Category 'X': openapi_spec is not valid
  JSON or YAML."*
- **Duplicate category names** — rejected: *"Duplicate category name 'X'."*
- **Missing `base_url` and no `servers` in the spec** — paths expand without a
  host; set `base_url`.
- **Expecting the suite to hold a worker** — it never does; only children do.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Suite stuck `running` a long time | Few workers, many endpoints | Scale ZAP workers; children run as workers free up. |
| Some endpoints `failed` in the report | Individual child scan failed | Open that child's `scan_api` result; the `error` explains why. |
| Endpoints `failed`: "Pre-flight reachability check failed …" | Worker cannot reach that endpoint | Fix the category `base_url` host+port; see **Target reachability (Docker)** above. |
| Endpoints scanned without auth | `defaults` missing/incorrect | Add `defaults.token`/`token_type` (or `token_header_name` for API keys). |
| Fewer endpoints than expected | `methods` filter or non-standard spec | Remove the filter; verify operations use standard HTTP methods. |
| 422 on submit | A category spec is invalid | The message names the category and the reason. |
