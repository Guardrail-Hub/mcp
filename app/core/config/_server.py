from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import NoDecode


class ServerMixin:
    # =====================================================
    # Audit Log
    # =====================================================
    # LOCAL | PLATFORM | BOTH | NONE
    audit_mode: Literal["LOCAL", "PLATFORM", "BOTH", "NONE"] = "LOCAL"
    audit_log_path: str = "./logs/audit.log"
    audit_log_format: Literal["jsonl", "json"] = "jsonl"
    audit_include_arguments: bool = False
    audit_mask_secrets: bool = True

    # =====================================================
    # Rate Limit
    # =====================================================
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # =====================================================
    # CORS
    # =====================================================
    cors_enabled: bool = False
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    cors_allow_credentials: bool = False

    # =====================================================
    # Logging
    # =====================================================
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["text", "json"] = "json"
    log_file_path: str = "./logs/server.log"

    @field_validator("audit_log_format", "log_format", mode="before")
    @classmethod
    def _normalize_lowercase_enum(cls, value):
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value):
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _enable_cors_when_origins_set(self):
        if self.cors_allow_origins:
            self.cors_enabled = True
        return self
