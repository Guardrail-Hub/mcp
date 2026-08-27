"""Pure scenario-workflow logic: ordering, placeholders, and state propagation.

No OWASP ZAP or network dependency lives here on purpose — everything in this
module is deterministic and unit-testable:

* :func:`resolve_execution_order` — dependency (``depends_on``) resolution via a
  stable topological sort, falling back to authored order.
* :func:`dotted_get` — read a value from a JSON response by a dotted path such
  as ``data.access_token.token`` (supports list indices like ``items.0.id``).
* :class:`ScenarioContext` — the mutable state carried across steps: captured
  variables, accumulated cookies, and the current auth token. It resolves
  ``${var}`` placeholders and applies propagated auth/cookies to each step.
"""

import copy
import re
from typing import Any, Optional

from app.schemas.tools.owasp_zap._graph import (
    find_cycle,
    find_unknown_dependency,
    topological_order,
)
from app.schemas.tools.owasp_zap.common import ZapTokenType
from app.schemas.tools.owasp_zap.scan_api_scenario import ScenarioStep

# ${var} / ${step.var} placeholder. Names are dotted identifiers.
_PLACEHOLDER = re.compile(r"\$\{([a-zA-Z0-9_.\-]+)\}")


class ScenarioStepError(RuntimeError):
    """A scenario step failed. Carries the step name for failure reporting."""

    def __init__(self, step_name: str, message: str) -> None:
        super().__init__(f"Step '{step_name}': {message}")
        self.step_name = step_name


def resolve_execution_order(steps: list[ScenarioStep]) -> list[ScenarioStep]:
    """Order *steps* honouring ``depends_on``; ties keep authored order.

    Raises:
        ScenarioStepError: on an unknown dependency or a dependency cycle — both
            unrecoverable, so the operation is failed rather than guessed.
    """
    by_name: dict[str, ScenarioStep] = {}
    for step in steps:
        if step.name in by_name:
            raise ScenarioStepError(step.name, "duplicate step name")
        by_name[step.name] = step

    name_to_deps = {step.name: list(step.depends_on) for step in steps}

    unknown = find_unknown_dependency(name_to_deps)
    if unknown is not None:
        name, dep = unknown
        raise ScenarioStepError(name, f"depends on unknown step '{dep}'")

    cycle = find_cycle(name_to_deps)
    if cycle is not None:
        raise ScenarioStepError(cycle[0], f"dependency cycle detected: {' -> '.join(cycle)}")

    order = topological_order([s.name for s in steps], name_to_deps)
    return [by_name[name] for name in order]


def dotted_get(data: Any, path: str) -> Optional[Any]:
    """Return the value at dotted *path* in *data*, or ``None`` if absent.

    Supports mapping keys and list indices, e.g. ``data.items.0.id``.
    """
    current = data
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


class ScenarioContext:
    """Mutable authenticated state carried across scenario steps."""

    def __init__(self) -> None:
        self.variables: dict[str, Any] = {}
        self.cookies: dict[str, str] = {}
        self.token: Optional[str] = None
        self.token_type: ZapTokenType = ZapTokenType.BEARER
        self.token_header_name: str = "Authorization"
        self.token_prefix: Optional[str] = None

    # ── Placeholder resolution ───────────────────────────────────────────

    def _substitute(self, text: str) -> str:
        """Replace every ``${var}`` in *text* with its captured value."""

        def repl(match: "re.Match[str]") -> str:
            key = match.group(1)
            value = self.variables.get(key)
            return "" if value is None else str(value)

        return _PLACEHOLDER.sub(repl, text)

    def _resolve_value(self, value: Any) -> Any:
        """Recursively resolve placeholders in strings within *value*."""
        if isinstance(value, str):
            return self._substitute(value)
        if isinstance(value, dict):
            return {k: self._resolve_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_value(v) for v in value]
        return value

    def resolve_step(self, step: ScenarioStep) -> ScenarioStep:
        """Return a copy of *step* with placeholders resolved and context auth
        applied.

        Explicit values on the step always win over propagated context — a step
        may override the propagated token/cookie by setting its own.
        """
        resolved = copy.deepcopy(step)
        resolved.url = self._substitute(step.url)
        if step.headers:
            resolved.headers = {
                k: self._substitute(v) for k, v in step.headers.items()
            }
        resolved.body = self._resolve_value(step.body)

        # Propagate the accumulated auth token unless the step sets its own.
        if not step.token and self.token:
            resolved.token = self.token
            resolved.token_type = self.token_type
            resolved.token_header_name = self.token_header_name
            resolved.token_prefix = self.token_prefix
        elif step.token:
            resolved.token = self._substitute(step.token)

        # Propagate accumulated cookies, merged with any explicit step cookie.
        merged_cookie = self._merged_cookie(step)
        if merged_cookie:
            resolved.cookie = merged_cookie
        return resolved

    def _merged_cookie(self, step: ScenarioStep) -> Optional[str]:
        pairs: dict[str, str] = dict(self.cookies)
        if step.cookie:
            for chunk in step.cookie.split(";"):
                if "=" in chunk:
                    name, val = chunk.split("=", 1)
                    pairs[name.strip()] = val.strip()
        if not pairs:
            return None
        return "; ".join(f"{name}={value}" for name, value in pairs.items())

    # ── Capture (variable / cookie / JWT propagation) ────────────────────

    def capture(
        self,
        step: ScenarioStep,
        response_json: Any,
        set_cookies: Optional[dict[str, str]] = None,
    ) -> None:
        """Update context state from a step's response.

        * ``extract`` captures → :attr:`variables`
        * ``token_field`` → current auth token (JWT/bearer/access-token),
          propagated to subsequent steps using this step's token conventions
        * ``cookie_field`` and any ``Set-Cookie`` headers → :attr:`cookies`
        """
        for var_name, path in (step.extract or {}).items():
            self.variables[var_name] = dotted_get(response_json, path)

        if step.token_field:
            token_value = dotted_get(response_json, step.token_field)
            if token_value is not None:
                self.token = str(token_value)
                self.token_type = step.token_type
                self.token_header_name = step.token_header_name
                self.token_prefix = step.token_prefix
                # Also expose as ${step.token} for explicit downstream reference.
                self.variables[f"{step.name}.token"] = self.token

        if step.cookie_field:
            cookie_value = dotted_get(response_json, step.cookie_field)
            if cookie_value is not None:
                self._absorb_cookie_string(str(cookie_value))

        for name, value in (set_cookies or {}).items():
            self.cookies[name] = value

    def _absorb_cookie_string(self, cookie_string: str) -> None:
        for chunk in cookie_string.split(";"):
            if "=" in chunk:
                name, val = chunk.split("=", 1)
                self.cookies[name.strip()] = val.strip()
