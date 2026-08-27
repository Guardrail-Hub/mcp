"""Pure OpenAPI specification parsing + endpoint expansion.

Lives in the schema layer (no ZAP / service dependency) so it can be used both by
the ``scan_api_suite`` request validator (fail fast on a malformed spec) and by
the suite orchestrator at execution time — one source of truth, no import cycle.
Deliberately small: it reads ``paths`` and ``servers`` only.
"""

import json
from dataclasses import dataclass
from typing import Any, Optional, Union

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


class OpenApiSpecError(ValueError):
    """Raised when an OpenAPI specification cannot be parsed or has no operations.

    Subclasses ``ValueError`` so pydantic surfaces it as a request validation
    error (HTTP 422) with the human-readable message intact.
    """


@dataclass(frozen=True)
class Endpoint:
    """One scannable operation expanded from a spec."""

    method: str  # upper-case HTTP method
    path: str
    url: str


def load_spec(spec: Union[dict, str]) -> dict:
    """Return *spec* as a dict, parsing a JSON or YAML string when needed."""
    if isinstance(spec, dict):
        return spec
    if not isinstance(spec, str):
        raise OpenApiSpecError(
            "openapi_spec must be an object or a JSON/YAML string."
        )
    text = spec.strip()
    if not text:
        raise OpenApiSpecError("openapi_spec is empty.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # noqa: PLC0415 - optional; only needed for YAML specs
        except ImportError as exc:  # pragma: no cover - yaml ships with the app
            raise OpenApiSpecError(
                "openapi_spec is not valid JSON and a YAML parser is unavailable."
            ) from exc
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise OpenApiSpecError(f"openapi_spec is not valid JSON or YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise OpenApiSpecError("openapi_spec did not parse to an object.")
    return parsed


def _resolve_base_url(spec: dict, base_url: Optional[str]) -> str:
    if base_url:
        return base_url.rstrip("/")
    servers = spec.get("servers")
    if isinstance(servers, list) and servers:
        url = servers[0].get("url") if isinstance(servers[0], dict) else None
        if url:
            return str(url).rstrip("/")
    return ""


def extract_endpoints(
    spec: Union[dict, str],
    base_url: Optional[str] = None,
    methods: Optional[list[str]] = None,
) -> list[Endpoint]:
    """Expand *spec* into a list of :class:`Endpoint` records.

    Raises:
        OpenApiSpecError: if the spec cannot be parsed or defines no ``paths``.
    """
    document = load_spec(spec)
    paths: Any = document.get("paths")
    if paths is None:
        raise OpenApiSpecError(
            "openapi_spec has no 'paths' section — nothing to scan."
        )
    if not isinstance(paths, dict):
        raise OpenApiSpecError("openapi_spec 'paths' must be an object.")

    resolved_base = _resolve_base_url(document, base_url)
    allow = {m.upper() for m in methods} if methods else None

    endpoints: list[Endpoint] = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method_name, operation in item.items():
            if method_name.lower() not in HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                continue
            method = method_name.upper()
            if allow is not None and method not in allow:
                continue
            url = f"{resolved_base}{path}" if resolved_base else path
            endpoints.append(Endpoint(method=method, path=path, url=url))
    return endpoints
