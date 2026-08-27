"""Shared request/result primitives for the OWASP ZAP tools.

These enums and the :class:`ZapApiTarget` base are reused across ``scan_api``,
``scan_api_scenario`` and ``scan_api_suite`` so authentication, targeting and
validation behave identically everywhere. Field names/aliases are stable — this
module adds validation and documentation, never renames.
"""

from enum import Enum
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


# Placeholder marker used by scenarios (e.g. ``https://api/users/${user_id}``).
# A templated URL is only fully known after step replay, so URL structure checks
# are skipped for it at request-validation time.
_TEMPLATE_MARKER = "${"


class ZapScanMode(str, Enum):
    """How thoroughly to scan a single endpoint.

    * ``quick`` — spider + a fast active scan of URL parameters only. Best for
      smoke tests and CI gates where speed matters.
    * ``full`` — context-scoped active scan including request-body fuzzing. Best
      for thorough, pre-release security testing.
    """

    QUICK = "quick"
    FULL = "full"


class HttpMethod(str, Enum):
    """HTTP method used to issue the request."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class ZapTokenType(str, Enum):
    """How the auth token is presented in the request header.

    ``bearer``/``jwt``/``access_token`` all default to an ``Authorization:
    Bearer <token>`` header; ``custom`` sends the raw token (optionally with
    ``token_prefix``) under ``token_header_name``.
    """

    BEARER = "bearer"
    JWT = "jwt"
    ACCESS_TOKEN = "access_token"
    CUSTOM = "custom"


class ScannerThreshold(str, Enum):
    DISABLE = "disable"
    DEFAULT = "default"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ZapApiTarget(BaseModel):
    """A single HTTP request target plus its authentication.

    Shared base for every ZAP API request. Authentication is optional: provide a
    ``token`` (Bearer/JWT/access-token/custom) and/or a ``cookie`` when the
    endpoint requires it; omit both for public endpoints.
    """

    url: str = Field(
        ...,
        description="Absolute target API URL, including scheme (http/https).",
        examples=["https://api.example.com/v1/login"],
        alias="target_url",
    )

    method: HttpMethod = Field(
        ...,
        description="HTTP method for the request.",
        examples=[HttpMethod.POST],
    )

    headers: Optional[dict[str, str]] = Field(
        None,
        description="Additional HTTP headers sent with the request.",
        examples=[{"Content-Type": "application/json", "X-Api-Key": "abc123"}],
    )

    body: Optional[Any] = Field(
        None,
        description="Request body: JSON object/array, a raw string, or a form payload.",
        examples=[{"username": "alice", "password": "s3cret"}],
    )

    token: Optional[str] = Field(
        None,
        description=(
            "Auth token value (Bearer, JWT, access token, or a custom token). "
            "Combined with token_type/token_header_name/token_prefix to build the "
            "auth header. Omit for unauthenticated endpoints."
        ),
    )

    token_type: ZapTokenType = Field(
        ZapTokenType.BEARER,
        description="How the token is presented (bearer/jwt/access_token/custom).",
    )

    token_header_name: str = Field(
        "Authorization",
        description="Header name the token is sent under (default: Authorization).",
    )

    token_prefix: Optional[str] = Field(
        None,
        description=(
            "Optional token prefix. For bearer/jwt/access_token the backend uses "
            "'Bearer' when this is empty; for a custom token this prefix (if any) "
            "is prepended to the token value."
        ),
        examples=["Bearer", "Token"],
    )

    cookie: Optional[str] = Field(
        None,
        description="Raw Cookie header value used to maintain a session.",
        examples=["session=abc123; csrftoken=xyz"],
    )

    token_field: Optional[str] = Field(
        None,
        description=(
            "Dotted path to a token in a JSON response, e.g. 'data.access_token'. "
            "Used by scenarios to capture a token from one step and reuse it later."
        ),
        examples=["data.access_token.token"],
    )

    cookie_field: Optional[str] = Field(
        None,
        description="Dotted path to a cookie value in a JSON response.",
        examples=["data.cookie"],
    )

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        """Fail fast on an obviously-invalid URL, with an actionable message.

        Templated scenario URLs (containing ``${...}``) are only fully known
        after replay, so their structure is checked after resolution, not here.
        """
        if value is None or not value.strip():
            raise ValueError(
                "target_url is required and must be an absolute http(s) URL, "
                "e.g. 'https://api.example.com/v1/login'."
            )
        candidate = value.strip()
        if _TEMPLATE_MARKER in candidate:
            return value  # templated — validated after step resolution
        parsed = urlparse(candidate)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                f"target_url '{candidate}' is not a valid absolute URL. It must "
                "start with http:// or https:// and include a host, e.g. "
                "'https://api.example.com/v1/login'."
            )
        return value

    @model_validator(mode="after")
    def _validate_auth(self) -> "ZapApiTarget":
        """Catch inconsistent auth configuration before a scan is queued."""
        if self.token_prefix and not self.token and not self.token_field:
            raise ValueError(
                "token_prefix is set but no 'token' or 'token_field' was provided. "
                "Add a token, or remove token_prefix."
            )
        return self


class ScannerThresholdOverride(BaseModel):
    rule_id: int = Field(
        ...,
        description="ZAP passive scanner rule ID. (e.g. 10038 for CSP violation)",
        examples=[10038],
    )
    threshold: ScannerThreshold = Field(
        ...,
        description="Alert threshold: disable, default, low, medium or high (default: default)",
        examples=[ScannerThreshold.DEFAULT],
    )
