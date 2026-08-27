"""JMeter infrastructure adapters — the subprocess and its output.

Contains the pieces that touch the outside world: the worker that owns the
JMeter process, and the parser that reads what it wrote. No orchestration lives
here; that is ``services/tools/jmeter/execution/runtime.py``.

Not created yet, deliberately: ``registry.py``, ``scheduler.py``, ``pool.py``
and the process-singleton ``runtime.py``. They are the worker-pool tier and have
nothing to hold until the worker actually runs a process — creating them empty
now would be the scaffold pattern ADR-0005 removed twice from this exact path.
"""
