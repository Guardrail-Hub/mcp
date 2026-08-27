"""JMeter execution internals — dispatcher and per-operation runtime.

Internal subpackage: nothing outside ``services/tools/jmeter/`` imports from
here. Mirrors ``services/tools/owasp_zap/execution/`` in shape only; no ZAP
module is imported.

Note there is deliberately no operation-type enum, handler protocol, or
operation registry here. ZAP needs those because it has five operation types
routed to different handlers; JMeter has exactly one. A registry over a set of
one resolves nothing (ADR-0009).
"""
