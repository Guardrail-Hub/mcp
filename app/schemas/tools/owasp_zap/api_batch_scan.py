from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.tools.owasp_zap.common import ZapApiTarget, ZapScanMode


class ZapApiBatchScanRequest(BaseModel):
    report_group: str = Field(
        ...,
        description="Label used to organize the report folder on S3.",
        examples=["my-project"],
    )

    scan_mode: ZapScanMode = Field(
        ZapScanMode.FULL,
        description="Scan mode applied to all APIs: quick or full.",
    )

    apis: list[ZapApiTarget] = Field(
        ...,
        description="List of APIs to scan.",
    )


class ZapApiBatchScanWithLLMRequest(ZapApiBatchScanRequest):
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
            "The backend can use LLM to map this to scanner threshold overrides."
        ),
        examples=["disable CSP and HSTS checks"],
    )