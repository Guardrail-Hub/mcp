from typing import Optional

from pydantic import BaseModel, Field


class ZapAlertInstance(BaseModel):
    uri: Optional[str] = None
    method: Optional[str] = None
    param: Optional[str] = None
    attack: Optional[str] = None
    evidence: Optional[str] = None
    other_info: Optional[str] = None


class ZapAlert(BaseModel):
    alert_ref: str = Field(..., description="ZAP alert reference ID")
    name: str = Field(..., description="Vulnerability name")
    risk: str = Field(..., description="Risk level: Informational, Low, Medium, High")
    confidence: str = Field(..., description="Confidence: Low, Medium, High, Confirmed")
    description: str = Field(..., description="Detailed vulnerability description")
    url: str = Field(..., description="Affected URL")
    solution: str = Field(..., description="Recommended remediation steps")
    reference: str = Field(..., description="References and CWE/WASC links")
    cwe_id: Optional[int] = Field(None, description="CWE identifier")
    wasc_id: Optional[int] = Field(None, description="WASC identifier")
    instances: list[ZapAlertInstance] = Field(default_factory=list)
    tags: dict[str, str] = Field(default_factory=dict)


class ZapScanSummary(BaseModel):
    total: int = Field(0, description="Total number of alerts")
    high: int = Field(0, description="High-risk findings")
    medium: int = Field(0, description="Medium-risk findings")
    low: int = Field(0, description="Low-risk findings")
    informational: int = Field(0, description="Informational findings")


class ZapTlsResult(BaseModel):
    SSLv2: str = Field("disabled", description="SSLv2 status: enabled or disabled")
    SSLv3: str = Field("disabled", description="SSLv3 status: enabled or disabled")
    TLSv1_0: str = Field("disabled", alias="TLSv1.0", description="TLS 1.0 status: enabled or disabled")
    TLSv1_1: str = Field("disabled", alias="TLSv1.1", description="TLS 1.1 status: enabled or disabled")
    TLSv1_2: str = Field("disabled", alias="TLSv1.2", description="TLS 1.2 status: enabled or disabled")
    TLSv1_3: str = Field("disabled", alias="TLSv1.3", description="TLS 1.3 status: enabled or disabled")

    model_config = {"populate_by_name": True}


class ZapScanReports(BaseModel):
    view_report: Optional[str] = Field(
        None,
        description="Open this link in your browser to view the full security report",
    )
    export_data: Optional[str] = Field(
        None,
        description="Download the complete scan data for import into other tools or further analysis",
    )


class ZapScanResult(BaseModel):
    operation_id: str = Field(..., description="Operation ID — matches the operation_id returned by the scan tool")
    report_group: str = Field(..., description="Label used to organize the report folder")
    status: str = Field(..., description="Scan status: completed or failed")
    target_url: str = Field(..., description="Primary target URL that was scanned")
    duration_seconds: float = Field(..., description="Total scan duration in seconds")
    alerts: list[ZapAlert] = Field(default_factory=list, description="Vulnerabilities found")
    summary: ZapScanSummary = Field(default_factory=ZapScanSummary, description="Alert count by risk level")
    tls_result: Optional[ZapTlsResult] = Field(None, description="TLS/SSL protocol support check result")
    reports: Optional[ZapScanReports] = Field(None, description="Links to access the full scan reports")
    error: Optional[str] = Field(None, description="Error message if scan failed")


class ZapInteractiveStartResult(BaseModel):
    operation_id: str = Field(..., description="Operation ID — pass this to zap_interactive_scan")
    proxy_host: str = Field(..., description="ZAP proxy host to configure in your browser")
    proxy_port: int = Field(..., description="ZAP proxy port to configure in your browser")
    session_token: str = Field(..., description="Session token — pass this to zap_interactive_scan")
    viewer_url: str = Field(..., description="ZAP web UI URL for monitoring recorded traffic")
    instructions: str = Field(
        ...,
        description=(
            "Step-by-step instructions: configure your browser to use the proxy, "
            "browse the target application to record traffic, "
            "then call zap_interactive_scan with the operation_id and session_token."
        ),
    )
