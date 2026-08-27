"""Synchronous OWASP ZAP scan runner over the existing ZapClient.

Executes one API scan (passive -> spider -> active -> collect alerts) using the
existing low-level ``ZapClient`` and returns a result dict. It contains **no**
operation/DAO bookkeeping (the ``EvaluationService`` owns the lifecycle) and uses
**no** thread pool — it runs synchronously on the calling thread.

The ``ZapClient`` is injectable so this runner is unit-testable without a live
ZAP. By default it constructs a ``ZapClient`` bound to settings, imported lazily
so importing this module does not require the zaproxy SDK.
"""

import re
import uuid
from app.integrations.owasp_zap.client import ZapClient
from typing import Any, Optional


class ZapScanRunner:
    """Runs one OWASP ZAP API scan synchronously via the existing ZapClient."""

    def __init__(self, client: Optional[Any] = None) -> None:
        """
        Args:
            client: An existing ``ZapClient`` (or compatible). Defaults to a new
                ``ZapClient`` bound to settings (lazily imported).
        """
        if client is None:
            client = ZapClient()
        self._client = client

    def __call__(self, request: Any) -> dict:
        """Run the scan for *request* and return a result dict.

        Raises:
            Exception: Any error from the ZAP client (e.g. ZAP unreachable). The
                caller (EvaluationService) turns this into a FAILED operation.
        """
        client = self._client
        url = request.url
        method = getattr(request.method, "value", request.method)
        name = f"eval-{uuid.uuid4().hex[:8]}"

        client.require_healthy()
        client.new_session(name)

        context_id = client.create_context(name)
        client.include_in_context(name, re.escape(url) + ".*")

        client.wait_for_passive_scan()

        spider_id = client.run_spider(url=url, context_name=name)
        client.wait_for_spider(spider_id)

        ascan_id = client.run_active_scan(
            url=url, context_id=context_id, recurse=False, method=method
        )
        client.wait_for_active_scan(ascan_id)

        raw_alerts = client.get_alerts(base_url=url)
        client.remove_context(name)

        return {
            "target_url": url,
            "alert_count": len(raw_alerts),
            "alerts": raw_alerts,
        }
