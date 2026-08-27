"""JMeter MCP tools — exposed as FastAPI POST endpoints.

Two tools, matching the accepted architecture:

* ``run_jmeter`` — validates a load-test request and enqueues a ``QUEUED``
  operation, returning ``{operation_id, status, message}``. Asynchronous, like
  every ZAP tool: load tests run for minutes, so nothing executes inline.
* ``analyze_jmeter_result`` — reads a completed operation's persisted result and
  interprets it.

Endpoints are thin submit adapters. They own the HTTP boundary — validation
errors become 4xx, everything else 5xx — and nothing else.

Registered in ``app/routers/endpoints.py`` now that the execution
infrastructure exists: the JMeter dispatcher polls the lane, so a submitted
operation is picked up and reaches a terminal state instead of sitting
``QUEUED`` forever.
"""

from fastapi import APIRouter, HTTPException

from app.core.exception import ScanSubmissionError
from app.schemas.tools.jmeter.run_jmeter_test import (
    JMeterResultAnalysisRequest,
    JMeterResultAnalysisResponse,
    JMeterTestRequest,
    JMeterTestSubmissionResponse,
)
from app.services.tools.jmeter import JMeterAnalyzer, JMeterTestService

jmeter_router = APIRouter(prefix="/jmeter", tags=["JMeter"])


@jmeter_router.post(
    "/run_test",
    operation_id="run_jmeter",
    summary="Load-test an HTTP endpoint with Apache JMeter",
    response_model=JMeterTestSubmissionResponse,
)
def run_jmeter(request: JMeterTestRequest) -> JMeterTestSubmissionResponse:
    """Run a JMeter load test against ONE HTTP endpoint.

    Use this to measure how an endpoint behaves under concurrent load — response
    times, throughput and error rate. This is a performance measurement, not a
    security scan; for security use the `zap_*` tools.

    Input: `target_url` (absolute http(s) URL, required — the test never infers
    a target), `method`, and the load profile `thread_count`, `ramp_up_seconds`
    and `hold_seconds`. Threads are started over the ramp-up window and then
    sustained for the hold window.

    Returns `{operation_id, status, message}` immediately; the run continues in
    the background. Poll `GET /api/history/get-result` for metrics and report
    links, or call `analyze_jmeter_result` for a reading of them.
    """
    try:
        return JMeterTestService().init_run_jmeter_test(request)
    except ScanSubmissionError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error submitting JMeter load test: \n{str(e)}"
        ) from e


@jmeter_router.post(
    "/analyze_result",
    operation_id="analyze_jmeter_result",
    summary="Interpret the result of a completed JMeter load test",
    response_model=JMeterResultAnalysisResponse,
)
def analyze_jmeter_result(
    request: JMeterResultAnalysisRequest,
) -> JMeterResultAnalysisResponse:
    """Explain what a finished JMeter load test's numbers mean.

    Reads the stored result for `operation_id` and describes it in plain
    language — latency distribution, throughput, error rate, and which sampler
    was slowest. It does not re-run anything and does not re-read the raw
    results file; if the operation is still running or failed, it says so.

    Use it after `run_jmeter` reports the operation is complete.
    """
    try:
        return JMeterAnalyzer().analyze_jmeter_result(request.operation_id)
    except LookupError as e:
        # Unknown operation, or one that does not belong to JMeter.
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error analyzing JMeter result: \n{str(e)}"
        ) from e
