"""
Settings assembly.

Each mixin file owns one category of settings + its validators.
Add a new tool by:
  1. Creating app/core/config/tools/_mytool.py with a MyToolMixin class.
  2. Adding MyToolMixin to the Settings base list below.

All code that currently does:
    from app.core.config import settings
continues to work unchanged.
"""
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config._app import AppMixin
from app.core.config._auth import AuthMixin
from app.core.config._database import DatabaseMixin
from app.core.config._security import SecurityMixin
from app.core.config._server import ServerMixin
from app.core.config._slack import SlackMixin
from app.core.config.tools._jmeter import JMeterMixin
from app.core.config.tools._zap import ZapMixin


class Settings(
    AppMixin,
    AuthMixin,
    DatabaseMixin,
    SecurityMixin,
    ServerMixin,
    ZapMixin,
    JMeterMixin,
    SlackMixin,
    BaseSettings,  # BaseSettings must be last in MRO
):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Cross-category validators (fields that span multiple mixins)
    # ------------------------------------------------------------------

    @field_validator(
        "mcp_mode",
        "mcp_deployment_mode",
        "token_authority",
        "token_algorithm",
        "database_provider",
        "audit_mode",
        "log_level",
        mode="before",
    )
    @classmethod
    def _normalize_uppercase_enum(cls, value):
        if isinstance(value, str):
            return value.strip().upper()
        return value

    # ------------------------------------------------------------------
    # Fail-fast: required production configuration
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _fail_fast_required_in_production(self):
        """Refuse to boot when required production config is missing or insecure.

        Only enforced when ``APP_ENV=production`` so local/dev defaults keep
        working unchanged (backward compatible with the existing package). In
        production the server fails fast at startup with a clear list of the
        missing / unsafe settings rather than failing later at request time.
        """
        if self.app_env != "production":
            return self

        problems: list[str] = []

        # Auth secret / keys
        if self.token_algorithm == "HS256":
            if (
                self.internal_token_secret == "change-this-secret"
                or len(self.internal_token_secret) < 32
            ):
                problems.append(
                    "INTERNAL_TOKEN_SECRET must be a strong value (>= 32 chars, not the default)"
                )
        elif self.token_algorithm == "RS256":
            if not self.token_private_key_path or not self.token_public_key_path:
                problems.append(
                    "TOKEN_PRIVATE_KEY_PATH and TOKEN_PUBLIC_KEY_PATH are required "
                    "when TOKEN_ALGORITHM=RS256"
                )

        # Connected-platform mode
        if self.mcp_mode == "CONNECTED" or self.token_authority == "PLATFORM_ISSUED":
            if not self.platform_api_url:
                problems.append(
                    "PLATFORM_API_URL is required when MCP_MODE=CONNECTED or "
                    "TOKEN_AUTHORITY=PLATFORM_ISSUED"
                )

        # Persistence
        if self.database_provider == "POSTGRES":
            if not self.database_url and not self.postgres_password:
                problems.append(
                    "POSTGRES_PASSWORD (or DATABASE_URL) is required when "
                    "DATABASE_PROVIDER=POSTGRES"
                )
        elif self.database_provider == "DYNAMODB":
            has_aws_creds = bool(self.aws_access_key_id and self.aws_secret_access_key)
            if not self.dynamodb_endpoint_url and not has_aws_creds:
                problems.append(
                    "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or DYNAMODB_ENDPOINT_URL "
                    "are required when DATABASE_PROVIDER=DYNAMODB"
                )

        # OWASP ZAP
        if self.zap_api_key == "change-this-zap-key":
            problems.append("ZAP_API_KEY must be changed from its default value")

        if problems:
            raise ValueError(
                "Invalid production configuration (APP_ENV=production):\n  - "
                + "\n  - ".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
