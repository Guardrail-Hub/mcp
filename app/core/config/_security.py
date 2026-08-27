from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import NoDecode


class SecurityMixin:
    # =====================================================
    # Tool Policy
    # =====================================================
    tool_policy_path: str = "./config/tool-policy.json"
    allowed_tools_path: str = "./config/allowed-tools.json"

    allow_dangerous_tools: bool = False
    require_approval_for_high_risk: bool = True
    require_approval_for_critical_risk: bool = True

    # =====================================================
    # Workspace / Filesystem
    # =====================================================
    workspace_dir: str = "./data/workspace"
    temp_dir: str = "./data/tmp"
    max_file_size_mb: int = 10

    allow_path_traversal: bool = False
    blocked_file_patterns: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [".env", "*.pem", "*.key", "id_rsa", "id_ed25519"]
    )
    allowed_file_extensions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [".txt", ".md", ".json", ".yaml", ".yml", ".log"]
    )

    # =====================================================
    # Security Guard
    # =====================================================
    enable_secret_guard: bool = True
    enable_command_guard: bool = True
    enable_path_guard: bool = True

    disable_shell_execution: bool = True
    command_timeout_seconds: int = 30
    max_tool_execution_seconds: int = 60

    @field_validator("blocked_file_patterns", "allowed_file_extensions", mode="before")
    @classmethod
    def _parse_csv_list(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value
