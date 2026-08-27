"""Validates a JMX plan before anything is executed.

This is the single enforcement point for Guardrail **G4** on the JMeter path,
and the reason it exists is blast radius: a scanner aimed at the wrong host is
an unwanted scan, while a load generator aimed at the wrong host is a denial of
service. So the rule from the accepted design holds for every plan, generated or
supplied — *the plan may address the caller's authorized target and nothing
else* (``architecture/jmeter-engine/runtime-responsibility.md`` §4).

Two checks, in order:

1. **Well-formed** — the document parses and actually is a ``jmeterTestPlan``.
2. **Within authorization** — every sampler host equals the target's host, and
   the plan carries no element that executes code.

The second check's script-element list is a **denylist**, and denylists over a
large format are weaker than what generation gives us. That is exactly why
supplied plans are refused unless an operator opts in
(``jmeter_allow_supplied_plan``): a generated plan cannot contain these elements
at all, so the denylist is defence for a path that is off by default rather than
the primary control.
"""

from typing import Tuple
from urllib.parse import urlparse
from xml.etree import ElementTree

from app.schemas.tools.jmeter.run_jmeter_test import JMeterTestRequest

_DEFAULT_PORTS = {"http": 80, "https": 443}

# Elements that execute caller-controlled code on the worker. A load test needs
# none of them; a plan containing one is rejected outright rather than sanitised,
# because partial removal of executable content is not a security boundary.
_CODE_EXECUTION_ELEMENTS = frozenset(
    {
        "JSR223Sampler",
        "JSR223PreProcessor",
        "JSR223PostProcessor",
        "JSR223Assertion",
        "JSR223Listener",
        "JSR223Timer",
        "BeanShellSampler",
        "BeanShellPreProcessor",
        "BeanShellPostProcessor",
        "BeanShellAssertion",
        "BeanShellListener",
        "BeanShellTimer",
        "BSFSampler",
        "BSFPreProcessor",
        "BSFPostProcessor",
        "BSFAssertion",
        "BSFListener",
        "BSFTimer",
        "SystemSampler",
        "TCPSampler",
        "JDBCSampler",
        "JDBCPreProcessor",
        "JDBCPostProcessor",
        "MailReaderSampler",
        "SmtpSampler",
        "FtpSampler",
        "OSProcessSampler",
        "IncludeController",
        "ModuleController",
    }
)

# Sampler elements whose host is checked against the authorized target.
_HTTP_SAMPLERS = frozenset({"HTTPSamplerProxy", "HTTPSampler", "AjpSampler"})


class JMeterPlanValidationError(ValueError):
    """The plan is malformed, or reaches beyond what the caller authorized."""


def split_target_url(target_url: str) -> Tuple[str, str, int, str]:
    """Split *target_url* into ``(scheme, host, port, path)``.

    Shared with the generator so the plan is built from, and validated against,
    exactly the same parse of the same string — two different parses would be a
    way for the two to disagree about what "the target" is.
    """
    parsed = urlparse(target_url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port or _DEFAULT_PORTS.get(scheme, 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return scheme, host, port, path


class JMeterPlanValidator:
    """Checks a JMX plan against the request that authorized it."""

    def validate(self, jmx_xml: str, request: JMeterTestRequest) -> None:
        """Raise :class:`JMeterPlanValidationError` unless *jmx_xml* is runnable
        and stays inside *request*'s authorized target.

        Returns nothing: validation is a gate, not a transformation. Nothing
        here rewrites the plan — a plan is either acceptable as written or it
        does not run.
        """
        root = self._parse(jmx_xml)
        _, authorized_host, _, _ = split_target_url(request.target_url)

        self._reject_code_execution(root)
        self._reject_foreign_hosts(root, authorized_host)

    # ── Checks ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse(jmx_xml: str) -> ElementTree.Element:
        if not (jmx_xml or "").strip():
            raise JMeterPlanValidationError("The test plan is empty.")
        try:
            root = ElementTree.fromstring(jmx_xml)
        except ElementTree.ParseError as exc:
            raise JMeterPlanValidationError(
                f"The test plan is not well-formed XML: {exc}"
            ) from exc
        if root.tag != "jmeterTestPlan":
            raise JMeterPlanValidationError(
                f"The test plan's root element is '{root.tag}', expected 'jmeterTestPlan'."
            )
        return root

    @staticmethod
    def _reject_code_execution(root: ElementTree.Element) -> None:
        found = sorted(
            {
                element.tag
                for element in root.iter()
                if element.tag in _CODE_EXECUTION_ELEMENTS
            }
        )
        if found:
            raise JMeterPlanValidationError(
                "The test plan contains elements that execute code or reach "
                f"non-HTTP services, which is not permitted: {', '.join(found)}."
            )

    @staticmethod
    def _reject_foreign_hosts(root: ElementTree.Element, authorized_host: str) -> None:
        samplers = [e for e in root.iter() if e.tag in _HTTP_SAMPLERS]
        if not samplers:
            raise JMeterPlanValidationError(
                "The test plan contains no HTTP sampler — there is nothing to run."
            )

        foreign = set()
        for sampler in samplers:
            domain = sampler.find("stringProp[@name='HTTPSampler.domain']")
            host = (domain.text or "").strip().lower() if domain is not None else ""
            if not host:
                # An empty domain inherits from an HTTP Request Defaults element,
                # which makes the effective target unknowable from the sampler
                # alone. Unknowable is not authorized.
                raise JMeterPlanValidationError(
                    "The test plan has a sampler with no explicit domain; every "
                    "sampler must name the authorized target directly."
                )
            if host != authorized_host:
                foreign.add(host)

        if foreign:
            raise JMeterPlanValidationError(
                f"The test plan addresses {', '.join(sorted(foreign))}, but only "
                f"'{authorized_host}' was authorized by target_url. A load test "
                "may never widen beyond the target the caller named."
            )
