from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.tools.owasp_zap.common import ZapScanMode


class ZapWebsiteScanRequest(BaseModel):
    report_group: str = Field(
        ...,
        description="Label used to organize the report folder on S3.",
        examples=["my-project"],
    )

    scan_mode: ZapScanMode = Field(
        ZapScanMode.QUICK,
        description="Scan mode: quick or full.",
    )

    login_url: str = Field(
        ...,
        description="Login URL for website scan.",
        examples=["https://example.com/login"],
    )

    username: str = Field(
        ...,
        description="Username for login.",
        examples=["username1"],
    )

    password: str = Field(
        ...,
        description="Password for login.",
        examples=["xxxx"],
    )

    token_field: Optional[str] = Field(
        None,
        description="Hidden input field for login form token or CSRF token.",
        examples=["_token"],
    )

    urls: list[str] = Field(
        ...,
        description="List of URLs to spider scan.",
        examples=[["https://example.com/page1", "https://example.com/page2"]],
    )

    login_indicator: Optional[str] = Field(
        None,
        description="Indicator text/pattern for successful login.",
        examples=["dashboard"],
    )

    logout_indicator: Optional[str] = Field(
        None,
        description="Indicator text/pattern for logout or expired session.",
        examples=["login"],
    )

    include_in_context: Optional[str] = Field(
        None,
        description=(
            "Quick scan only. Defines the ZAP context/scope URL prefix. "
            "Only URLs starting with this value are allowed to be spidered/scanned. "
            "Ignored when scan_mode='full'."
        ),
        examples=["https://example.com/app"],
    )

    @model_validator(mode="after")
    def validate_quick_scan_context(self):
        if self.scan_mode == ZapScanMode.QUICK and not self.include_in_context:
            raise ValueError(
                "include_in_context is required when scan_mode='quick'."
            )

        return self


class ZapWebsiteScanWithLLMRequest(ZapWebsiteScanRequest):
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