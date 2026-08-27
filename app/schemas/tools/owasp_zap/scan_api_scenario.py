"""Request models for the ``scan_api_scenario`` operation.

A scenario replays an ordered sequence of API requests (a business workflow such
as Login -> Get Profile -> Create Order -> Checkout -> Logout) through the ZAP
proxy, carrying authenticated state (variables, cookies, JWT/bearer token)
forward from one step to the next, and then scans the authenticated endpoints
with the standard OWASP ZAP primitives.

Each step reuses :class:`ZapApiTarget` — so a step already has ``url``,
``method``, ``headers``, ``body``, the token fields and the response-extraction
fields ``token_field`` / ``cookie_field``. The scenario schema adds only workflow
concerns: a step ``name``, generic ``extract`` captures, ``depends_on`` ordering,
and whether the step is an active-scan target.
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.tools.owasp_zap._graph import (
    find_cycle,
    find_duplicate,
    find_unknown_dependency,
)
from app.schemas.tools.owasp_zap.common import ZapApiTarget, ZapScanMode


class ScenarioStep(ZapApiTarget):
    """One request in a scenario workflow.

    Inherits every request/auth primitive from :class:`ZapApiTarget`; adds only
    the workflow metadata the scenario engine needs. Reference values captured by
    earlier steps with ``${variable_name}`` placeholders anywhere in this step's
    url, headers, or body.
    """

    name: str = Field(
        ...,
        description="Unique step name; referenced by depends_on and ${...} placeholders.",
        examples=["login", "get_profile", "create_order"],
    )

    extract: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Variables to capture from this step's JSON response, as "
            "{variable_name: dotted.response.path}. Captured values are available "
            "to later steps as ${variable_name}."
        ),
        examples=[{"order_id": "data.order.id"}],
    )

    depends_on: list[str] = Field(
        default_factory=list,
        description="Names of steps that must run before this one (dependency ordering).",
        examples=[["login"]],
    )

    scan: bool = Field(
        False,
        description=(
            "Whether this step's endpoint is an active-scan target after replay. "
            "If no step sets this, every step is scanned by default."
        ),
    )


class ZapApiScenarioScanRequest(BaseModel):
    """An ordered API workflow to replay and then scan authenticated."""

    report_group: str = Field(
        ...,
        description="Label used to organize the report folder.",
        examples=["my-project"],
    )

    scan_mode: ZapScanMode = Field(
        ZapScanMode.FULL,
        description="Scan mode applied to the active-scan phase (quick or full).",
    )

    steps: list[ScenarioStep] = Field(
        ...,
        min_length=1,
        description="Workflow steps. Authored order is the default execution order.",
    )

    bucket_name: Optional[str] = Field(
        None, description="Optional AWS S3 bucket name for report export."
    )
    file_name: Optional[str] = Field(
        None, description="Optional AWS S3 file name for report export."
    )

    @model_validator(mode="after")
    def _validate_workflow(self) -> "ZapApiScenarioScanRequest":
        """Fail fast on structural workflow errors, with actionable messages."""
        names = [step.name for step in self.steps]

        duplicate = find_duplicate(names)
        if duplicate is not None:
            raise ValueError(
                f"Duplicate step name '{duplicate}'. Each step must have a unique name."
            )

        name_to_deps = {step.name: list(step.depends_on) for step in self.steps}
        unknown = find_unknown_dependency(name_to_deps)
        if unknown is not None:
            step_name, dep = unknown
            raise ValueError(
                f"Step '{step_name}' depends on unknown step '{dep}'. "
                f"Add a step named '{dep}', or fix the depends_on reference."
            )

        cycle = find_cycle(name_to_deps)
        if cycle is not None:
            raise ValueError(
                "Circular dependency between steps: "
                f"{' -> '.join(cycle)}. Remove one of the depends_on links."
            )

        # Duplicate capture variables across steps make ${var} ambiguous.
        seen: dict[str, str] = {}
        for step in self.steps:
            for var_name in step.extract:
                if var_name in seen:
                    raise ValueError(
                        f"Variable '{var_name}' is captured by more than one step "
                        f"('{seen[var_name]}' and '{step.name}'). Use unique variable names."
                    )
                seen[var_name] = step.name
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "report_group": "storefront",
                "scan_mode": "full",
                "steps": [
                    {
                        "name": "login",
                        "target_url": "https://api.example.com/v1/auth/login",
                        "method": "POST",
                        "body": {"username": "alice", "password": "s3cret"},
                        "token_field": "data.access_token",
                        "token_type": "jwt",
                    },
                    {
                        "name": "get_profile",
                        "target_url": "https://api.example.com/v1/me",
                        "method": "GET",
                        "depends_on": ["login"],
                        "extract": {"user_id": "data.id"},
                        "scan": True,
                    },
                    {
                        "name": "create_order",
                        "target_url": "https://api.example.com/v1/users/${user_id}/orders",
                        "method": "POST",
                        "depends_on": ["get_profile"],
                        "body": {"sku": "ABC-123", "qty": 1},
                        "scan": True,
                    },
                ],
            }
        }
    }
