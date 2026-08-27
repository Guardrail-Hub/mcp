"""
Central registry for all MCP tool routers.

Add new tool routers here so main.py stays unchanged as the project grows.
Each router is also responsible for declaring its own operation IDs, which
are collected below for use with FastApiMCP(include_operations=...).
"""
from fastapi import APIRouter

# MCP routers
from app.routers.mcp.jmeter_router import jmeter_router
from app.routers.mcp.owasp_zap_router import zap_router

# Public routers
from app.routers.public.history_router import history_router

# ---------------------------------------------------------------------------
# Master router — mount all tool sub-routers here
# ---------------------------------------------------------------------------

tools_router = APIRouter(prefix="/tools")
tools_router.include_router(zap_router, prefix="")
tools_router.include_router(jmeter_router, prefix="")
tools_router.include_router(history_router, prefix="")

# ---------------------------------------------------------------------------
# All operation IDs exposed as MCP tools
# Keep in sync with the operation_id values in each tool router.
# ---------------------------------------------------------------------------

MCP_OPERATIONS: list[str] = [
    # OWASP ZAP
    "zap_scan_api",
    "zap_scan_api_scenario",
    "zap_scan_api_suite",

    # JMeter
    "run_jmeter",
    "analyze_jmeter_result",

    # History
    "get_result",
    "list_ids_by_type",
    "list_operations",
]
