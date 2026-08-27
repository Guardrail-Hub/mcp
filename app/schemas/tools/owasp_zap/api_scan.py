from typing import Optional

from pydantic import Field

from app.schemas.tools.owasp_zap.common import ZapApiTarget, ZapScanMode


class ZapApiScanRequest(ZapApiTarget):
    report_group: str = Field(
        ...,
        description="Label used to organize the report folder on S3.",
        examples=["my-project"],
    )

    scan_mode: ZapScanMode = Field(
        ZapScanMode.FULL,
        description="Scan mode: quick or full.",
    )


class ZapApiScanLLMRequest(ZapApiScanRequest):
    bucket_name: Optional[str] = Field(
        None,
        description="AWS S3 bucket name. Default can use S3_BUCKET_NAME.",
        examples=["bucket-xxx"],
    )

    file_name: Optional[str] = Field(
        None,
        description="AWS S3 file name.",
        examples=["folder-x/file-y.json"],
    )

    prompt: Optional[str] = Field(
        None,
        description=(
            "Natural-language tuning for ZAP passive scanners. "
            "The backend can use LLM to map this to scanner threshold overrides. "
            "Example: 'disable CSP and HSTS checks'."
        ),
        examples=["disable CSP and HSTS checks"],
    )