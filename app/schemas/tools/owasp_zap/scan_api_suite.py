"""Request models for the ``scan_api_suite`` operation.

A suite scans several API groups/services as one logical application. Each
category carries an OpenAPI specification; the suite orchestrator expands every
category into child ``scan_api`` operations, runs them in parallel across the
worker pool, and aggregates their results into application-level reports.

The suite is an ORCHESTRATION request: it never scans anything itself.
"""

from typing import Any, Optional, Union

from pydantic import AliasChoices, BaseModel, Field, model_validator

from app.schemas.tools.owasp_zap._graph import find_duplicate
from app.schemas.tools.owasp_zap.common import HttpMethod, ZapScanMode, ZapTokenType
from app.schemas.tools.owasp_zap.openapi_spec import OpenApiSpecError, extract_endpoints


class ScanAuthDefaults(BaseModel):
    """Shared authentication/headers applied to every endpoint in a category.

    A minimal, auth-only subset of the standard request primitives (it does NOT
    require a url/method the way a full request target does), so a category can
    declare "use this token/cookie for all my endpoints".
    """

    headers: Optional[dict[str, str]] = Field(None, description="Extra HTTP headers.")
    token: Optional[str] = Field(None, description="Auth token value.")
    token_type: ZapTokenType = Field(
        ZapTokenType.BEARER, description="Token type for building the auth header."
    )
    token_header_name: str = Field(
        "Authorization", description="Header name used to send the token."
    )
    token_prefix: Optional[str] = Field(None, description="Optional token prefix.")
    cookie: Optional[str] = Field(None, description="Raw cookie string.")


class ApiCategory(BaseModel):
    """One API group/service within the application (e.g. Authentication, Orders)."""

    name: str = Field(
        ...,
        description="Category / service name, used in category-level reports.",
        examples=["Authentication", "Orders"],
    )

    openapi_spec: Union[dict, str] = Field(
        ...,
        description=(
            "OpenAPI 3.x specification for this category — an inline object, or a "
            "JSON/YAML string. Its paths are expanded into child scan_api scans."
        ),
    )

    base_url: Optional[str] = Field(
        None,
        description=(
            "Base URL prepended to each spec path. Falls back to the spec's "
            "servers[0].url when omitted."
        ),
        examples=["https://api.example.com"],
    )

    methods: Optional[list[HttpMethod]] = Field(
        None,
        description="Optional filter — only expand these HTTP methods from the spec.",
    )

    defaults: Optional[ScanAuthDefaults] = Field(
        None, description="Shared auth/headers applied to every endpoint here."
    )

    @model_validator(mode="after")
    def _validate_spec(self) -> "ApiCategory":
        """Fail fast if the category's OpenAPI spec is unusable."""
        try:
            endpoints = extract_endpoints(
                self.openapi_spec,
                self.base_url,
                [m.value for m in self.methods] if self.methods else None,
            )
        except OpenApiSpecError as exc:
            raise ValueError(f"Category '{self.name}': {exc}") from exc
        if not endpoints:
            raise ValueError(
                f"Category '{self.name}' expands to zero endpoints. Check the spec's "
                "'paths', the method filter, and that operations use standard HTTP methods."
            )
        return self


class ZapApiSuiteScanRequest(BaseModel):
    """Multiple API categories scanned together as one logical application."""

    # ``populate_by_name`` lets callers/tests construct with the canonical field
    # name (suite_name) while ``validation_alias`` also accepts the legacy
    # ``application_name`` on input and when replaying older persisted requests.
    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "report_group": "storefront",
                "suite_name": "Storefront release scan",
                "scan_mode": "full",
                "categories": [
                    {
                        "name": "Authentication",
                        "base_url": "https://api.example.com",
                        "openapi_spec": {"paths": {"/auth/login": {"post": {}}}},
                    },
                    {
                        "name": "Orders",
                        "base_url": "https://api.example.com",
                        "defaults": {"token": "eyJhbGci...", "token_type": "jwt"},
                        "openapi_spec": {"paths": {"/orders": {"get": {}, "post": {}}}},
                    },
                ],
            }
        },
    }

    report_group: str = Field(
        ..., description="Label used to organize the report folder.", examples=["my-project"]
    )
    suite_name: str = Field(
        ...,
        description=(
            "User-defined name for this suite scan run; shown as the report title. "
            "The legacy field name 'application_name' is still accepted as an alias."
        ),
        examples=["Storefront release scan"],
        validation_alias=AliasChoices("suite_name", "application_name"),
        serialization_alias="suite_name",
    )
    scan_mode: ZapScanMode = Field(
        ZapScanMode.FULL, description="Scan mode applied to every child scan_api operation."
    )
    categories: list[ApiCategory] = Field(
        ..., min_length=1, description="API categories that together make up the application."
    )
    max_parallelism: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Advisory hint only. Actual parallelism is bounded by the number of "
            "idle ZAP workers; the suite never occupies a worker itself."
        ),
    )
    metadata: Optional[dict[str, Any]] = Field(
        None, description="Optional caller metadata echoed into the suite report."
    )

    @model_validator(mode="after")
    def _validate_categories(self) -> "ZapApiSuiteScanRequest":
        duplicate = find_duplicate([c.name for c in self.categories])
        if duplicate is not None:
            raise ValueError(
                f"Duplicate category name '{duplicate}'. Category names must be unique."
            )
        return self
