from typing import Literal

from pydantic import field_validator


class DatabaseMixin:
    # =====================================================
    # Persistence
    # =====================================================
    # NONE | POSTGRES | DYNAMODB
    database_provider: Literal["NONE", "POSTGRES", "DYNAMODB"] = "NONE"
    database_url: str | None = None

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "guardrail_hub"
    postgres_user: str = "guardrail"
    postgres_password: str | None = None

    dynamodb_endpoint_url: str | None = None
    dynamodb_table_prefix: str = "guardrail_local"
    aws_region: str = "ap-southeast-1"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None

    @field_validator(
        "database_url",
        "postgres_password",
        "dynamodb_endpoint_url",
        "aws_access_key_id",
        "aws_secret_access_key",
        mode="before",
    )
    @classmethod
    def _db_empty_string_to_none(cls, value):
        if value == "":
            return None
        return value
