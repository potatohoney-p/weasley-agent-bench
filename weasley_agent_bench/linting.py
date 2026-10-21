"""Opt-in static contradictions; not a general contract satisfiability solver."""

from __future__ import annotations

from .contracts import ContractError, _document_sha256, validate_suite


def lint_suite(suite: dict, *, allow_contradictions: bool = False) -> dict:
    """Check required tool counts against caps without executing a saved run.

    Each exact or subsequence step needs a distinct call, including error/any
    outcomes and repeated tool names. Other resource limits have no declared
    positive lower bounds, so they cannot establish this contradiction.
    """
    if type(allow_contradictions) is not bool:
        raise ContractError("allow_contradictions: expected a boolean")
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
    return {
        "schema_version": 1,
        "suite_sha256": _document_sha256(suite),
        "rule_set": "tool-budget-v1",
        "allow_contradictions": allow_contradictions,
        "passed": not issues,
        "accepted": not issues or allow_contradictions,
        "summary": {"tasks": len(suite["tasks"]), "contradictions": len(issues)},
        "issues": issues,
    }
