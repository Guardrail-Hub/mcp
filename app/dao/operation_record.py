from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.domain.lifecycle import OperationPhase


class OperationRecord(BaseModel):
    """Persistent record of a single MCP operation (scan job, batch, etc.)."""

    operation_id: str = Field(
        ...,
        description=(
            "Unique identifier for this operation. "
            "Used as the primary key in both PostgreSQL and DynamoDB."
        ),
        examples=["zap_api_scan:abc123"],
    )
    status: OperationPhase = Field(
        ...,
        description="Current lifecycle status of the operation.",
    )

    batch_type: str = Field(
        ...,
        description="Category / tool type that produced this operation.",
        examples=["api_scan", "web_scan", "interactive_scan", "batch_scan"],
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value context attached at creation time (targets, config, etc.).",
    )

    result: Optional[Any] = Field(
        None,
        description="Final result payload once the operation completes successfully.",
    )

    error: Optional[str] = Field(
        None,
        description="Human-readable error message when status is FAILED.",
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the operation was first created.",
    )

    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the most recent status change.",
    )

    log_path: Optional[str] = Field(
        None,
        description="Filesystem path to the log file produced by this operation.",
        examples=["logs/ZapApiScanService_20260615.log"],
    )
