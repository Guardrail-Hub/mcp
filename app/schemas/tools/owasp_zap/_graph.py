"""Pure dependency-graph helpers for scenario workflows.

Operates on plain ``(name, depends_on)`` data — no ZAP, no schema imports — so it
can be shared by the request validators (fail fast, before execution) and the
execution-time ordering without creating an import cycle. Every error message is
written to be actionable for a human or an agent.
"""

from typing import Optional


def find_duplicate(names: list[str]) -> Optional[str]:
    """Return the first name that appears more than once, or ``None``."""
    seen: set[str] = set()
    for name in names:
        if name in seen:
            return name
        seen.add(name)
    return None


def find_unknown_dependency(
    name_to_deps: dict[str, list[str]],
) -> Optional[tuple[str, str]]:
    """Return the first ``(name, unknown_dep)`` reference, or ``None``."""
    known = set(name_to_deps)
    for name, deps in name_to_deps.items():
        for dep in deps:
            if dep not in known:
                return name, dep
    return None


def find_cycle(name_to_deps: dict[str, list[str]]) -> Optional[list[str]]:
    """Return one dependency cycle as an ordered path, or ``None`` if acyclic."""
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {name: WHITE for name in name_to_deps}
    stack: list[str] = []

    def visit(node: str) -> Optional[list[str]]:
        color[node] = GREY
        stack.append(node)
        for dep in name_to_deps.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GREY:
                # Found a back-edge: slice the current path from dep onward.
                idx = stack.index(dep)
                return stack[idx:] + [dep]
            if color[dep] == WHITE:
                found = visit(dep)
                if found is not None:
                    return found
        color[node] = BLACK
        stack.pop()
        return None

    for name in name_to_deps:
        if color[name] == WHITE:
            cycle = visit(name)
            if cycle is not None:
                return cycle
    return None


def topological_order(names: list[str], name_to_deps: dict[str, list[str]]) -> list[str]:
    """Return *names* ordered so dependencies precede dependents.

    Independent nodes keep their input order. Assumes the graph is already known
    to be valid (no unknown deps, no cycles) — callers validate first.
    """
    visited: dict[str, int] = {}
    ordered: list[str] = []

    def visit(name: str) -> None:
        state = visited.get(name)
        if state == 1:
            return
        visited[name] = 0
        for dep in name_to_deps.get(name, []):
            visit(dep)
        visited[name] = 1
        ordered.append(name)

    for name in names:
        visit(name)
    return ordered
