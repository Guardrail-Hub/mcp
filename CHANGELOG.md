# MCP Server Changelog

All notable changes to the Guardrail Hub MCP Server are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

This file is the canonical project history. Detailed release notes live in
[`changelog/`](changelog/), one document per release. The initial v0.1.0 entry is
kept comprehensive to preserve the project's founding context; from v0.2.0 onward,
each release gets a concise summary here with a link to its full notes in
`changelog/vX.Y.Z.md`.

## [0.1.0] — 2026-07-19

First release of the Guardrail Hub MCP Server: a Model Context Protocol (MCP) server built on FastAPI that provides a reusable execution platform — operation orchestration, worker management, persistence, notification infrastructure, and an extensible tool architecture. OWASP ZAP security scanning is the first supported capability, exposed as a suite of MCP tools.

Detailed release notes: [`changelog/v0.1.0.md`](changelog/v0.1.0.md)

### 🚀 Added

**Execution platform**

- **Operation orchestration.** Long-running work is modeled as asynchronous operations with explicit status transitions (including a QUEUED state). Submitting a scan returns an operation ID; a history API lists operations and fetches past results.
- **Worker management.** A self-registering worker pool: worker containers announce themselves at startup via a registration endpoint and send periodic heartbeats, so `docker compose up --scale zap=N` scales the pool with zero server-side configuration. Management endpoints support listing, draining, and removing workers.
- **Background queue dispatcher.** Queued operations are persisted in the operations store and assigned to idle workers by a background dispatcher; operations orphaned by a dead worker's heartbeat timeout are failed automatically.
- **Pluggable persistence.** PostgreSQL and DynamoDB DAO backends behind a common interface, plus a SQL migration runner for the operations schema.
- **Notification infrastructure.** An in-process event bus (hexagonal ports plus dispatcher) routes platform and operation lifecycle events to notification channels. The Slack adapter supports connection checks, channel resolution, and message delivery; a platform lifecycle reporter announces startup, shutdown, and fatal errors.
- **Capability-aware progress notifications.** Operation lifecycle and progress events are rendered into professional status cards, throttled, and delivered as either in-place message updates or new messages depending on each channel's declared capabilities. Slack messages update in place as a scan progresses.
- **Extensible tool architecture.** A strongly typed operation registry with handler and execution-strategy abstractions lets new MCP tools plug into the shared dispatch path with a single registration.
- **Health endpoints** (`health`, liveness, readiness) backed by a dedicated health service.

**First supported capability: OWASP ZAP security scanning**

Exposed as MCP tools via `fastapi-mcp`:

- `scan_api` — security-scan a single API endpoint (quick or full mode) with bearer/JWT token and cookie authentication support.
- `scan_api_scenario` — replay an authenticated, ordered API workflow: steps run in dependency order, capturing and propagating variables, cookies, and tokens between steps before scanning the authenticated endpoints.
- `scan_api_suite` — scan a whole application composed of multiple API services: expands each service's OpenAPI spec into child `scan_api` operations, fans them out in parallel across the worker pool, and aggregates results into a single application report.
- `scan_website_application` and `scan_interactive_web_session` — website scanning with ZAP context creation, form-based authentication, and user management.
- **Aggregated multi-format suite reporting** in Markdown, JSON, and SARIF 2.1.0, including executive summary, scan/severity/category summaries, recommendations, and deduplicated, deterministically ordered findings.

### ✨ Improved

- Request-time validation now fails fast with actionable HTTP 422 messages: invalid or relative URLs, inconsistent auth configuration, duplicate/unknown/circular scenario step dependencies, duplicate suite categories, empty category expansions, and malformed OpenAPI specs are rejected at submit instead of failing mid-scan.
- Request schemas carry field descriptions, enum documentation, and per-tool examples, surfaced directly in the MCP tool catalog and OpenAPI schema.
- Configuration was reorganized into typed, domain-scoped settings modules (app, auth, database, security, server, Slack, ZAP) with fail-fast validation of required secrets, database, and ZAP settings in production.
- Operation progress reporting was extended through the ZAP client, dispatcher, and API scanner so notifications reflect real scan phases rather than opaque long-running jobs.

### ♻️ Refactored

- **Generic execution layer.** Replaced bespoke per-tool plumbing with a strongly typed operation registry, handler strategy, and execution-strategy abstraction (worker-bound vs. orchestration). The background dispatcher is fully tool-agnostic: adding a tool requires no dispatcher or worker-service changes.
- **Enforced architectural layering.** Routers are thin HTTP adapters delegating to services; worker and health logic was extracted into dedicated services. External routes, operation IDs, and payload shapes were preserved.
- Worker orchestration (queue dispatch, registration, heartbeat, removal) was grouped under a dedicated `worker/` subpackage, keeping scan execution code focused on scanning.
- The test suite was restructured to mirror the source layout (services, integrations, flows), with each test module named after the module under test.

### 🐛 Fixed

- ZAP container entrypoint no longer double-invokes the ZAP binary (the compose command already included it), which previously caused ZAP to attempt to open its own launcher script as a target file.
- Container shutdown now delivers SIGTERM directly to the application server, so lifespan shutdown hooks — including the shutdown notification — actually run.

### 🔒 Security

- Bearer/JWT token and cookie-based authentication support across all scanning tools, with token prefix handling and per-step token propagation in scenario scans.
- Fail-fast production configuration validation prevents the server from starting with missing secrets or incomplete database/ZAP configuration.
- Scan reports include TLS protocol status (TLS 1.0–1.3) alongside vulnerability findings.

### ⚡ Performance

- Suite scans fan out child endpoint scans in parallel across the worker pool instead of running sequentially.
- Progress notifications are throttled to avoid flooding channels, and Slack updates reuse a single message instead of posting repeatedly.
- Horizontal scan throughput scales linearly with worker count via the self-registering pool and queue dispatcher.

### 🛠 Developer Experience

- Docker Compose stack with FastAPI and scalable ZAP worker services, healthchecks, and hot-reload (opt-in via environment flag) for local development.
- Comprehensive `.env.example` documenting all application, database, Slack, and ZAP settings.
- Domain-mirrored test tree covering validation, ordering, variable propagation, OpenAPI expansion, orchestration, aggregation, reporting determinism, and notification flows.
- Editor and MCP client configuration checked in for consistent local tooling.

### 📚 Documentation

- Full OWASP ZAP module documentation: overview with a tool decision tree, per-tool references for `scan_api`, `scan_api_scenario`, and `scan_api_suite`, migration notes, and module release notes.
- Server configuration reference documenting every settings group and environment variable.

### ⚠ Breaking Changes

- The request field `project_name` was renamed to `report_group` across all ZAP tools, schemas, and reports; the old name is no longer accepted. "Project" implied a whole system, which did not fit a single scenario replay or endpoint scan.
- In `scan_api_suite`, the request field `application_name` was renamed to `suite_name`. The old name is still accepted on input as an alias, but the aggregated report key changed from `application_name` to `suite_name` — report consumers must update.
- Some previously invalid requests (e.g., relative target URLs) that used to fail during execution now fail immediately at submit with HTTP 422. Valid requests are unaffected.

---

## Technical Highlights

The MCP Server evolved from a single hard-wired scanner integration into a layered, event-driven execution platform in four architectural phases:

**Foundation.** A FastAPI application with the core layout (config, routers, schemas, Docker packaging), the first OWASP ZAP integration, and dual-backend operation persistence (PostgreSQL/DynamoDB).

**Event-driven core.** Hexagonal ports (event publisher, notification channel) and an in-process event dispatcher became the core's event spine; services emit lifecycle events that subscribers route to notification channels, all assembled by a composition root from settings.

**Distributed execution.** A self-registering worker pool replaced fixed container addressing: workers register and heartbeat over HTTP, the persisted operations table acts as the queue, and a background dispatcher matches queued operations to idle workers and reaps orphans. Execution became fully asynchronous.

**Generic execution platform.** A strongly typed operation registry with handler and execution-strategy abstractions unified all operation types under one dispatch path, enabling scenario replays and OpenAPI-driven suite orchestration without dispatcher changes — the foundation for adding future MCP tool capabilities with a single registration.
