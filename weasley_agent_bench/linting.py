"""Opt-in static contradictions; not a general contract satisfiability solver."""

from __future__ import annotations

from .contracts import ContractError, _document_sha256, validate_suite
from .scoring import _at_pointer, _equal


def _assertion_conflicts(assertions: list, path: str, task_index: int) -> list[dict]:
    """Prove conflicts only when one equality fixes an ancestor's full value."""
    issues = []
    tokens = [a["path"][1:].split("/") if a["path"] else [] for a in assertions]
    for child_index, child in enumerate(assertions):
        for parent_index, parent in enumerate(assertions):
            prefix = tokens[parent_index]
            suffix = tokens[child_index]
            if len(prefix) >= len(suffix) or suffix[:len(prefix)] != prefix:
                continue
            relative = "/" + "/".join(suffix[len(prefix):])
            if not _equal(_at_pointer(parent["equals"], relative), child["equals"]):
                issues.append({
                    "code": "assertion.ancestor_conflict",
                    "task_index": task_index,
                    "path": f"{path}[{child_index}]",
                    "related_path": f"{path}[{parent_index}]",
                    "detail": "ancestor equality cannot satisfy the descendant equality",
                })
    return issues


def lint_suite(suite: dict, *, allow_contradictions: bool = False,
               check_assertions: bool = False) -> dict:
    """Check required tool counts and optional equality conflicts without a run.

    Each exact or subsequence step needs a distinct call, including error/any
    outcomes and repeated tool names. Other resource limits have no declared
    positive lower bounds, so they cannot establish this contradiction.
    """
    if type(allow_contradictions) is not bool:
        raise ContractError("allow_contradictions: expected a boolean")
    if type(check_assertions) is not bool:
        raise ContractError("check_assertions: expected a boolean")
    validate_suite(suite)
    issues = []
    for index, task in enumerate(suite["tasks"]):
        steps = task["expect"].get("tools", {}).get("steps", [])
        cap = task.get("budget", {}).get("max_tool_calls")
        if cap is not None and len(steps) > cap:
            prefix = f"suite.tasks[{index}]"
            issues.append({
                "code": "budget.required_tool_calls_exceed_cap",
                "task_index": index,
                "path": f"{prefix}.budget.max_tool_calls",
                "related_path": f"{prefix}.expect.tools.steps",
                "required_calls": len(steps),
                "max_tool_calls": cap,
                "detail": "each required step needs a distinct call; the cap is too small",
            })
        if check_assertions:
            prefix = f"suite.tasks[{index}].expect"
            issues.extend(_assertion_conflicts(task["expect"].get("final", []),
                                               f"{prefix}.final", index))
            for position, step in enumerate(steps):
                for field in ("arguments", "output"):
                    issues.extend(_assertion_conflicts(
                        step.get(field, []), f"{prefix}.tools.steps[{position}].{field}", index))
    return {
        "schema_version": 1,
        "suite_sha256": _document_sha256(suite),
        "rule_set": "tool-budget-and-assertions-v1" if check_assertions else "tool-budget-v1",
        "allow_contradictions": allow_contradictions,
        "passed": not issues,
        "accepted": not issues or allow_contradictions,
        "summary": {"tasks": len(suite["tasks"]), "contradictions": len(issues)},
        "issues": issues,
    }
