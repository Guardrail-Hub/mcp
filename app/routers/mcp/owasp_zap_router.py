"""
OWASP ZAP MCP tools — exposed as FastAPI POST endpoints.

Each endpoint becomes an MCP tool via fastapi-mcp; the function docstring is the
tool description shown in an MCP client's catalog, and the request model (with
its field descriptions and example) is the tool's input schema. Endpoints are
thin submit adapters: they validate the request and enqueue a ``QUEUED``
operation, then the generic background Dispatcher resolves the right handler and
executes it. No endpoint runs a scan inline — all three return an
``operation_id`` to poll via ``GET /api/history/get-result``.
"""
from fastapi import APIRouter, HTTPException

from app.core.exception import InvalidScanRequestError
from app.schemas.tools.owasp_zap.api_scan import ZapApiScanLLMRequest
from app.schemas.tools.owasp_zap.scan_api_scenario import ZapApiScenarioScanRequest
from app.schemas.tools.owasp_zap.scan_api_suite import ZapApiSuiteScanRequest
from app.services.tools.owasp_zap.api_scanner import ZapApiScanService
from app.services.tools.owasp_zap.scan_api_scenario import ZapApiScenarioService
from app.services.tools.owasp_zap.scan_api_suite import ZapApiSuiteService

zap_router = APIRouter(prefix="/zap", tags=["OWASP ZAP"])


@zap_router.post(
    "/scan_api",
    operation_id="zap_scan_api",
    summary="Security-scan a single API endpoint",
)
def zap_scan_api(request: ZapApiScanLLMRequest):
    """Run an OWASP ZAP security scan against ONE API endpoint.

    Use this when you have a single URL + method to test (optionally with auth).
    For an authenticated multi-step workflow use `scan_api_scenario`; to scan a
    whole application of many services use `scan_api_suite`.

    Input: `target_url`, `method`, optional `headers`/`body`, optional auth
    (`token` + `token_type`, or `cookie`), and `scan_mode` (`quick` | `full`).
    Returns `{operation_id, status, message}`; poll the operation for the report
    (Markdown link + JSON export).
    """
    try:
        rs = ZapApiScanService().init_api_scan(request)
        return {"result": rs}
    except InvalidScanRequestError as e:
        # Domain validation error -> client error at the HTTP boundary.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error running Owasp Zap scan: \n{str(e)}"
        ) from e


@zap_router.post(
    "/scan_api_scenario",
    operation_id="zap_scan_api_scenario",
    summary="Security-scan an authenticated API workflow (ordered steps)",
)
def zap_scan_api_scenario(request: ZapApiScenarioScanRequest):
    """Run an OWASP ZAP scan over an ordered API workflow (a business scenario).

    Use this when the endpoints you want to test require an authenticated,
    stateful sequence — e.g. Login -> Get Profile -> Create Order -> Checkout.
    The tool replays the `steps` in dependency order, capturing variables,
    cookies and JWT/bearer tokens from each response and propagating them to
    later steps (reference them with `${variable_name}`), then active-scans the
    authenticated endpoints.

    Do NOT use it for a single endpoint (use `scan_api`) or to scan many
    independent services at once (use `scan_api_suite`). Loops, branches and
    retries are intentionally out of scope.

    Returns `{operation_id, status, message}`; poll the operation for the report.
    """
    try:
        rs = ZapApiScenarioService().init_scenario_scan(request)
        return {"result": rs}
    except InvalidScanRequestError as e:
        # Domain validation error -> client error at the HTTP boundary.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error running Owasp Zap scenario scan: \n{str(e)}",
        ) from e


@zap_router.post(
    "/scan_api_suite",
    operation_id="zap_scan_api_suite",
    summary="Security-scan a whole application of multiple API services",
)
def zap_scan_api_suite(request: ZapApiSuiteScanRequest):
    """Scan several API groups/services as ONE logical application (a suite).

    Use this when an application is composed of multiple services, each with its
    own OpenAPI spec (e.g. Authentication, Users, Orders, Payments). The suite is
    an orchestrator: it expands every category's spec into child `scan_api`
    scans, runs them in parallel across the ZAP worker pool, and aggregates the
    results into an application report with Executive/Severity/Category summaries,
    recommendations, and detailed findings (Markdown + JSON + SARIF).

    Do NOT use it for a single endpoint (`scan_api`) or one authenticated
    workflow (`scan_api_scenario`). The suite never scans itself and never
    occupies a worker.

    Returns `{operation_id, status, message}`; poll the operation for the
    aggregated application report.
    """
    try:
        rs = ZapApiSuiteService().init_suite_scan(request)
        return {"result": rs}
    except InvalidScanRequestError as e:
        # Domain validation error -> client error at the HTTP boundary.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error running Owasp Zap suite scan: \n{str(e)}",
        ) from e


@zap_router.post("/scan_web_application", operation_id="zap_scan_web_application")
def zap_scan_web_application(request: ZapApiScanLLMRequest):
    """
    Run OWASP ZAP scan for a web application.

    Intentionally not implemented — the browser-automation architecture is still
    under evaluation. The generic execution layer already supports adding it later
    purely by registering a new operation (no Dispatcher/WorkerService change).
    """
    try:
        return {"result": "in development"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error running Owasp Zap scan for web application: \n{str(e)}",
        ) from e


@zap_router.post(
    "/scan_interactive_web_session", operation_id="zap_scan_interactive_web_session"
)
def zap_scan_interactive_web_session(request: ZapApiScanLLMRequest):
    """
    Run OWASP ZAP scan for an interactive web session.

    Intentionally not implemented — see scan_web_application. Added later via
    normal operation registration.
    """
    try:
        return {"result": "in development"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error running Owasp Zap scan for interactive web session: \n{str(e)}",
        ) from e
