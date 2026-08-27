"""Domain layer — the canonical business concepts of Guardrail Hub.

This is the innermost layer. It defines what is *true* about security work,
independent of how that work is scheduled, stored, transported, or displayed.

Vocabulary is fixed by Decision 0008 (the ubiquitous language); the layer's
existence is justified by Decision 0007. Capabilities:

* :mod:`app.domain.findings` — what a security finding is, how serious it is,
  and how findings aggregate.
* :mod:`app.domain.lifecycle` — the lifecycle an operation moves through.

Dependency rule (Decision 0007 section 5): this package imports **nothing**
from the rest of ``app`` — no services, routers, core, schemas, dao, or
integrations, and no framework (FastAPI, Pydantic) or transport (Slack) types.
Application and infrastructure layers may depend on domain; never the reverse.
"""
