"""Request/response wire formats for the ``run_jmeter`` MCP tool."""

from enum import Enum
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class JMeterHttpMethod(str, Enum):
    """HTTP method the generated sampler will use."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class JMeterTestRequest(BaseModel):
    """Input to ``run_jmeter``: what to load-test, and how hard.

    The load profile is duration-driven (a JMeter thread group with the
    scheduler enabled): *thread_count* threads are started over
    *ramp_up_seconds* and then sustained for *hold_seconds*. Loop-driven plans
    are not generated, and no field is reserved for them.
    """

    target_url: str = Field(
        ...,
        description="Absolute http(s) URL to load-test. Required and never inferred.",
        examples=["https://api.example.com/v1/orders"],
    )
    method: JMeterHttpMethod = Field(
        JMeterHttpMethod.GET, description="HTTP method for the request under test."
    )
    thread_count: int = Field(
        ...,
        ge=1,
        le=1000,
        description="Concurrent threads (virtual users) to run.",
        examples=[50],
    )
    ramp_up_seconds: int = Field(
        ...,
        ge=0,
        le=3600,
        description="Seconds over which all threads are started.",
        examples=[30],
    )
    hold_seconds: int = Field(
        ...,
        ge=1,
        le=3600,
        description="Seconds to sustain the full thread count once ramped up.",
        examples=[300],
    )
    jmx_plan: Optional[str] = Field(
        None,
        description=(
            "An existing JMeter test plan (raw .jmx XML) to run instead of a "
            "generated one. When given, the load-profile fields above are "
            "recorded but not applied — the supplied plan defines the load. "
            "Accepted only when the server enables it; the plan is still "
            "validated against target_url and is rejected if it addresses any "
            "other host."
        ),
    )

    @field_validator("target_url")
    @classmethod
    def _target_must_be_explicit_absolute_url(cls, value: str) -> str:
        """Enforce Guardrail G4 at the boundary: the target is named, not implied.

        A load engine's blast radius is larger than a scanner's — a wrong or
        unauthorized target is a denial-of-service, not an unwanted scan. So the
        caller must name one absolute http(s) target, and plan generation may
        never widen beyond it.
        """
        parsed = urlparse(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                "target_url must be an absolute http(s) URL naming exactly one target, "
                f"got: {value!r}"
            )
        return value.strip()


class JMeterTestSubmissionResponse(BaseModel):
    """What ``run_jmeter`` returns — a receipt, never a result.

    Load tests run for minutes, so submission is asynchronous exactly as every
    ZAP tool is. Metrics and artifact links come later from
    ``GET /api/history/get-result`` or ``analyze_jmeter_result``.
    """

    operation_id: str = Field(..., description="Poll this to retrieve the result.")
    status: str = Field(..., description="Lifecycle phase at submission (QUEUED).")
    message: str = Field(..., description="Human-readable next step.")


class JMeterResultAnalysisRequest(BaseModel):
    """Input to ``analyze_jmeter_result``."""

    operation_id: str = Field(
        ...,
        description="The operation_id returned by run_jmeter.",
        examples=["a3f1c2d4e5b60718293a4b5c6d7e8f90"],
    )


class JMeterResultAnalysisResponse(BaseModel):
    """Human-readable interpretation of a completed JMeter operation."""

    operation_id: str
    summary: str = Field(..., description="Plain-language reading of the run.")
    details: Optional[str] = Field(
        None, description="Supporting observations, when there are any."
    )
