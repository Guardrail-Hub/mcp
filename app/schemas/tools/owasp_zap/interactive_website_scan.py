from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.tools.owasp_zap.common import HttpMethod, ZapScanMode


class ZapInteractiveWebsiteStartRequest(BaseModel):
    report_group: str = Field(
        ...,
        description="Label used to organize the report folder on S3.",
        examples=["my-project"],
    )

    base_url: str = Field(
        ...,
        description="Starting URL for the interactive browser.",
        examples=["https://example.com/"],
    )

    include_in_context: Optional[str] = Field(
        None,
        description=(
            "Optional. Overrides ZAP context base. "
            "Defaults to scheme + host of base_url."
        ),
        examples=["https://example.com"],
    )

    prompt: Optional[str] = Field(
        None,
        description=(
            "Natural-language tuning for ZAP passive scanners. "
            "The backend can use LLM to map this to scanner threshold overrides."
        ),
        examples=["disable CSP and HSTS checks"],
    )


class ZapInteractiveScanTarget(BaseModel):
    url: str = Field(
        ...,
        description="Absolute URL to scan.",
        examples=["https://example.com/api/me"],
    )

    method: HttpMethod = Field(
        HttpMethod.GET,
        description="HTTP method as recorded by ZAP.",
    )

    postdata: Optional[str] = Field(
        None,
        description="Raw request body for non-GET methods captured by ZAP.",
    )


class ZapInteractiveScanRequest(BaseModel):
    operation_id: str = Field(
        ...,
        description="Operation ID from setup response.",
    )

    session_token: str = Field(
        ...,
        description="Session token from viewer URL query token.",
    )

    selected_targets: Optional[list[ZapInteractiveScanTarget]] = Field(
        None,
        description=(
            "If set and non-empty, scan only these selected endpoints. "
            "If omitted or empty, backend can run context-wide scan depending on current behavior."
        ),
    )

    scan_mode: ZapScanMode = Field(
        ZapScanMode.FULL,
        description="Scan mode for selected targets: quick or full.",
    )