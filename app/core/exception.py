"""Application-level (transport-agnostic) exceptions.

Services raise these domain errors instead of transport exceptions
(``fastapi.HTTPException``); HTTP routers translate them into status codes at
the boundary. Keeping the service layer free of transport concerns preserves
the ``routers -> services`` dependency direction
(see ``.ai/standards/architecture/backend-layering``).
"""


class InvalidScanRequestError(ValueError):
    """A scan submission failed request-level validation.

    Routers map this to HTTP 400. The message is user-facing and actionable.
    """


class ScanSubmissionError(RuntimeError):
    """A scan submission failed unexpectedly during initialization.

    Routers map this to HTTP 500. Wraps the original cause via ``raise ... from``.
    """
