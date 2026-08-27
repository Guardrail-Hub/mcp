"""Builds a JMX test plan from a validated request.

One shape only: a duration-driven thread group addressing exactly one target,
which is what ``JMeterTestRequest`` describes. There is no plan builder DSL, no
template registry and no element plugin point — those would be abstractions over
a set of one (ADR-0009). When a second plan shape genuinely exists, extract then.

The generated plan contains **no scripting elements of any kind**. That is what
makes the generated path the safe one: the only thing it can do is issue HTTP
requests at the caller's authorized target.
"""

from xml.sax.saxutils import escape

from app.schemas.tools.jmeter.run_jmeter_test import JMeterTestRequest

# JMeter 5.x plan skeleton. Written as a template rather than assembled with
# ElementTree because a JMX is a fixed, deeply-nested document whose element
# names and `testname`/`guiclass` attributes must match exactly — a builder
# would be more code and easier to get subtly wrong.
_PLAN_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="{plan_name}">
      <boolProp name="TestPlan.functional_mode">false</boolProp>
      <boolProp name="TestPlan.serialize_threadgroups">false</boolProp>
      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments" \
guiclass="ArgumentsPanel" testclass="Arguments" testname="User Defined Variables">
        <collectionProp name="Arguments.arguments"/>
      </elementProp>
    </TestPlan>
    <hashTree>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Load">
        <stringProp name="ThreadGroup.num_threads">{thread_count}</stringProp>
        <stringProp name="ThreadGroup.ramp_time">{ramp_up_seconds}</stringProp>
        <boolProp name="ThreadGroup.scheduler">true</boolProp>
        <stringProp name="ThreadGroup.duration">{duration_seconds}</stringProp>
        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController" \
guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller">
          <boolProp name="LoopController.continue_forever">true</boolProp>
          <intProp name="LoopController.loops">-1</intProp>
        </elementProp>
      </ThreadGroup>
      <hashTree>
        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" \
testname="{sampler_name}">
          <stringProp name="HTTPSampler.domain">{host}</stringProp>
          <stringProp name="HTTPSampler.port">{port}</stringProp>
          <stringProp name="HTTPSampler.protocol">{scheme}</stringProp>
          <stringProp name="HTTPSampler.path">{path}</stringProp>
          <stringProp name="HTTPSampler.method">{method}</stringProp>
          <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
          <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments" \
guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables">
            <collectionProp name="Arguments.arguments"/>
          </elementProp>
        </HTTPSamplerProxy>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
"""


class JMeterPlanGenerator:
    """Turns a load-test request into a runnable JMX document."""

    def generate(self, operation_id: str, request: JMeterTestRequest) -> str:
        """Return the JMX XML for *request*.

        The thread group is duration-driven: *thread_count* threads start over
        *ramp_up_seconds* and the group runs for ramp-up plus *hold_seconds*,
        which is what "sustain the full thread count for hold_seconds" means in
        JMeter's scheduler terms.

        Every value interpolated below is XML-escaped. The request has already
        been validated, but a target URL is caller-controlled text going into a
        document that JMeter parses — escaping is not optional.
        """
        from app.services.tools.jmeter.plan.validator import (  # noqa: PLC0415
            split_target_url,
        )

        scheme, host, port, path = split_target_url(request.target_url)
        duration_seconds = request.ramp_up_seconds + request.hold_seconds

        return _PLAN_TEMPLATE.format(
            plan_name=escape(f"guardrail-hub {operation_id}"),
            sampler_name=escape(f"{request.method.value} {path}"),
            thread_count=request.thread_count,
            ramp_up_seconds=request.ramp_up_seconds,
            duration_seconds=duration_seconds,
            host=escape(host),
            port=escape(str(port)),
            scheme=escape(scheme),
            path=escape(path),
            method=escape(request.method.value),
        )
