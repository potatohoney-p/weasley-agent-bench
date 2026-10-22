"""Deterministic scoring and paired comparison of saved agent results."""

from __future__ import annotations

from typing import Any

from .contracts import ContractError, USAGE_FIELDS, _document_sha256, validate_run, validate_suite

_MISSING = object()


def _at_pointer(document: Any, pointer: str) -> Any:
    if not pointer:
        return document
    current = document
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(key, _MISSING)
        elif isinstance(current, list) and (key == "0" or (key.isascii() and key.isdigit() and not key.startswith("0"))):
            # Compare lengths before conversion; gigantic indices cannot hit a list.
            if len(key) > len(str(len(current))) or int(key) >= len(current):
                return _MISSING
            current = current[int(key)]
        else:
            return _MISSING
        if current is _MISSING:
            return _MISSING
    return current


def _equal(actual: Any, expected: Any) -> bool:
    """JSON equality: object order is irrelevant, but true is not the number 1."""
    if actual is _MISSING:
        return False
    if type(actual) in (int, float) and type(expected) in (int, float):
        return actual == expected
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            _equal(actual[key], expected[key]) for key in actual
        )
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            _equal(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _check(code: str, passed: bool, detail: str) -> dict:
    return {"code": code, "passed": passed, "detail": detail}


def _assertion_checks(value: Any, assertions: list, prefix: str) -> list[dict]:
    return [
        _check(f"{prefix}[{index}]", _equal(_at_pointer(value, assertion["path"]), assertion["equals"]),
               f"value at JSON Pointer {assertion['path']!r} matches")
        for index, assertion in enumerate(assertions)
    ]


def _step_matches(call: dict, step: dict) -> bool:
    return call["name"] == step["name"] and (
        step["outcome"] == "any" or call["outcome"] == step["outcome"]
    ) and all(
        check["passed"]
        for field in ("arguments", "output")
        for check in _assertion_checks(call[field], step.get(field, []), field)
    )


def _tool_checks(calls: list, contract: dict) -> list[dict]:
    steps = contract["steps"]
    checks: list[dict] = []
    if contract["mode"] == "exact":
        checks.append(_check("tools.count", len(calls) == len(steps), "tool count matches the exact sequence"))
        for index, step in enumerate(steps):
            checks.append(_check(f"tools.step[{index}]", index < len(calls) and _step_matches(calls[index], step),
                                 f"tool step {index} matches name, outcome and assertions"))
    else:
        position = 0
        for index, step in enumerate(steps):
            while position < len(calls) and not _step_matches(calls[position], step):
                position += 1
            matched = position < len(calls)
            checks.append(_check(f"tools.step[{index}]", matched, f"ordered tool step {index} is present"))
            if matched:
                position += 1
    return checks


def _evaluate_task(task: dict, result: dict | None) -> dict:
    """Score one validated task/result pair; shared by both input transports."""
    checks = [_check("result.present", result is not None, "a result was supplied for this task")]
    if result is not None:
        checks.append(_check("result.completed", result["status"] == "completed", "agent completed the task"))
        expected = task["expect"]
        checks.extend(_assertion_checks(result["final"], expected.get("final", []), "final"))
        calls = result["tool_calls"]
        if "tools" in expected:
            checks.extend(_tool_checks(calls, expected["tools"]))
        forbidden = set(expected.get("forbidden_tools", []))
        if forbidden:
            checks.append(_check("tools.forbidden", not any(call["name"] in forbidden for call in calls),
                                 "no forbidden tool was called"))
        for limit_name, limit in sorted(task.get("budget", {}).items()):
            metric = limit_name.removeprefix("max_")
            observed = len(calls) if metric == "tool_calls" else result.get("usage", {}).get(metric)
            checks.append(_check(f"budget.{metric}", observed is not None and observed <= limit,
                                 "usage is present and within the configured cap"))
    return {
        "task_id": task["id"],
        "status": result["status"] if result else "missing",
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def evaluate(suite: dict, run: dict) -> dict:
    """Score every suite task in suite order, including absent results as failures."""
    validate_suite(suite)
    validate_run(run, suite)
    by_id = {result["task_id"]: result for result in run["results"]}
    rows = [_evaluate_task(task, by_id.get(task["id"])) for task in suite["tasks"]]
    passed = sum(row["passed"] for row in rows)
    return {
        "schema_version": 1,
        "suite_id": suite["suite_id"],
        "suite_sha256": _document_sha256(suite),
        "run_id": run["run_id"],
        "passed": passed == len(rows),
        "summary": {"total": len(rows), "passed": passed, "failed": len(rows) - passed,
                    "missing": len(rows) - len(by_id)},
        "results": rows,
    }


def compare(suite: dict, baseline: dict, candidate: dict) -> dict:
    """Compare paired tasks, rejecting incomplete coverage rather than dropping it."""
    before = evaluate(suite, baseline)
    after = evaluate(suite, candidate)
    if before["summary"]["missing"] or after["summary"]["missing"]:
        raise ContractError("comparison requires one result for every task in both runs")
    old_results = {item["task_id"]: item for item in baseline["results"]}
    new_results = {item["task_id"]: item for item in candidate["results"]}
    changes: list[dict] = []
    for old, new in zip(before["results"], after["results"]):
        task_id = old["task_id"]
        change = "unchanged"
        if old["passed"] and not new["passed"]:
            change = "regression"
        elif not old["passed"] and new["passed"]:
            change = "improvement"
        old_usage = old_results[task_id].get("usage", {})
        new_usage = new_results[task_id].get("usage", {})
        changes.append({
            "task_id": task_id,
            "change": change,
            "baseline_passed": old["passed"],
            "candidate_passed": new["passed"],
            "usage_delta": {key: new_usage[key] - old_usage[key] for key in USAGE_FIELDS
                            if key in old_usage and key in new_usage},
            "failed_checks": [check["code"] for check in new["checks"] if not check["passed"]],
        })
    regressions = sum(item["change"] == "regression" for item in changes)
    return {
        "schema_version": 1,
        "suite_id": suite["suite_id"],
        "suite_sha256": before["suite_sha256"],
        "baseline_run_id": baseline["run_id"],
        "candidate_run_id": candidate["run_id"],
        "no_regressions": regressions == 0,
        "candidate_passed": after["passed"],
        "summary": {"total": len(changes), "regressions": regressions,
                    "improvements": sum(item["change"] == "improvement" for item in changes),
                    "baseline_passed": before["summary"]["passed"],
                    "candidate_passed": after["summary"]["passed"]},
        "changes": changes,
    }
