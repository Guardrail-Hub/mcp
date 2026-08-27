from typing import Literal

from pydantic import field_validator


class AuthMixin:
    # =====================================================
    # Token / Auth
    # =====================================================
    token_authority: Literal["SELF_MANAGED", "PLATFORM_ISSUED"] = "SELF_MANAGED"
    token_algorithm: Literal["HS256", "RS256"] = "HS256"

    internal_token_secret: str = "change-this-secret"
    token_issuer: str = "guardrail-mcp-local"
    token_audience: str = "guardrail-mcp"
    token_expires_in_days: int = 90

    token_private_key_path: str | None = None
    token_public_key_path: str | None = None
    jwks_url: str | None = None

    # =====================================================
    # Platform Connection
    # =====================================================
    platform_api_url: str | None = None
    platform_api_token: str | None = None
    platform_token_introspection_path: str = "/internal/tokens/introspect"
    platform_audit_path: str = "/internal/audit/events"
    platform_timeout_seconds: int = 10

    @field_validator(
        "token_private_key_path",
        "token_public_key_path",
        "jwks_url",
        "platform_api_url",
        "platform_api_token",
        mode="before",
    )
    @classmethod
    def _auth_empty_string_to_none(cls, value):
        if value == "":
            return None
        return value

    @field_validator("internal_token_secret")
    @classmethod
    def _validate_internal_token_secret(cls, value: str):
        if value == "change-this-secret":
            # Allow in development, but must be changed in production.
            return value
        if len(value) < 32:
            raise ValueError("INTERNAL_TOKEN_SECRET must be at least 32 characters")
        return value
