"""Validate a small, versioned JSON contract before scoring anything."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


class ContractError(ValueError):
    """The input cannot be evaluated without guessing its meaning."""


USAGE_FIELDS = ("input_tokens", "output_tokens", "duration_ms")
BUDGET_FIELDS = ("max_tool_calls", *(f"max_{key}" for key in USAGE_FIELDS))
# Durations can be fractional, so comparison may promote integers to floats.
# Use the JSON/IEEE-754 safe-integer ceiling before that arithmetic occurs.
MAX_DURATION_MS = 2**53 - 1


def _fail(path: str, detail: str) -> None:
    raise ContractError(f"{path}: {detail}")


def _object(value: Any, path: str, required: set[str], optional: set[str] | None = None) -> None:
    if not isinstance(value, dict):
        _fail(path, "expected an object")
    if not all(isinstance(key, str) for key in value):
        _fail(path, "object keys must be strings")
    missing = required - value.keys()
    unknown = value.keys() - required - (optional or set())
    if missing:
        _fail(path, f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        _fail(path, f"unknown fields: {', '.join(sorted(unknown))}")


def _string(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "expected a non-empty string")


def _array(value: Any, path: str) -> None:
    if not isinstance(value, list):
        _fail(path, "expected an array")


def _number(value: Any, path: str, *, integer: bool = False, maximum: int | None = None) -> None:
    if type(value) not in (int, float) or value < 0 or (
        type(value) is float and not math.isfinite(value)
    ):
        _fail(path, "expected a finite non-negative number")
    if integer and type(value) is not int:
        _fail(path, "expected an integer")
    if maximum is not None and value > maximum:
        _fail(path, f"must not exceed {maximum}")


def _json_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            _fail(path, "non-finite numbers are not JSON values")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            _json_value(item, f"{path}.{key}")
        return
    _fail(path, "expected a JSON value")


def _unique_strings(value: Any, path: str) -> None:
    _array(value, path)
    for index, item in enumerate(value):
        _string(item, f"{path}[{index}]")
    if len(set(value)) != len(value):
        _fail(path, "duplicate entries")


def _assertions(value: Any, path: str) -> None:
    _array(value, path)
    seen: set[str] = set()
    for index, assertion in enumerate(value):
        location = f"{path}[{index}]"
        _object(assertion, location, {"path", "equals"})
        pointer = assertion["path"]
        if not isinstance(pointer, str) or (pointer != "" and not pointer.startswith("/")):
            _fail(location, "path must be a JSON Pointer (empty string for the root)")
        if re.search(r"~(?![01])", pointer):
            _fail(location, "invalid JSON Pointer escape")
        if pointer in seen:
            _fail(location, "duplicate assertion path")
        seen.add(pointer)
        _json_value(assertion["equals"], f"{location}.equals")


def validate_suite(suite: Any) -> None:
    """Raise ContractError for ambiguous, unsupported or vacuous task contracts."""
    _object(suite, "suite", {"schema_version", "suite_id", "tasks"})
    if type(suite["schema_version"]) is not int or suite["schema_version"] != 1:
        _fail("suite.schema_version", "only version 1 is supported")
    _string(suite["suite_id"], "suite.suite_id")
    _array(suite["tasks"], "suite.tasks")
    if not suite["tasks"]:
        _fail("suite.tasks", "at least one task is required")
    ids: set[str] = set()
    for index, task in enumerate(suite["tasks"]):
        path = f"suite.tasks[{index}]"
        _object(task, path, {"id", "prompt", "expect"}, {"tags", "budget"})
        _string(task["id"], f"{path}.id")
        _string(task["prompt"], f"{path}.prompt")
        if task["id"] in ids:
            _fail(f"{path}.id", "duplicate task ID")
        ids.add(task["id"])
        if "tags" in task:
            _unique_strings(task["tags"], f"{path}.tags")
        expect = task["expect"]
        _object(expect, f"{path}.expect", set(), {"final", "tools", "forbidden_tools"})
        if "final" in expect:
            _assertions(expect["final"], f"{path}.expect.final")
        if "forbidden_tools" in expect:
            _unique_strings(expect["forbidden_tools"], f"{path}.expect.forbidden_tools")
        if "tools" in expect:
            tools = expect["tools"]
            _object(tools, f"{path}.expect.tools", {"mode", "steps"})
            if tools["mode"] not in ("exact", "subsequence"):
                _fail(f"{path}.expect.tools.mode", "expected exact or subsequence")
            _array(tools["steps"], f"{path}.expect.tools.steps")
            for position, step in enumerate(tools["steps"]):
                location = f"{path}.expect.tools.steps[{position}]"
                _object(step, location, {"name", "outcome"}, {"arguments", "output"})
                _string(step["name"], f"{location}.name")
                if step["outcome"] not in ("success", "error", "any"):
                    _fail(f"{location}.outcome", "expected success, error or any")
                for field in ("arguments", "output"):
                    if field in step:
                        _assertions(step[field], f"{location}.{field}")
                if step["name"] in expect.get("forbidden_tools", []):
                    _fail(location, "a required tool is also forbidden")
        has_tool_contract = "tools" in expect and (
            expect["tools"]["mode"] == "exact" or bool(expect["tools"]["steps"])
        )
        if not (expect.get("final") or expect.get("forbidden_tools") or has_tool_contract):
            _fail(f"{path}.expect", "at least one meaningful expectation is required")
        if "budget" in task:
            _object(task["budget"], f"{path}.budget", set(), set(BUDGET_FIELDS))
            if not task["budget"]:
                _fail(f"{path}.budget", "must not be empty")
            for field, limit in task["budget"].items():
                _number(limit, f"{path}.budget.{field}", integer=field != "max_duration_ms",
                        maximum=MAX_DURATION_MS if field == "max_duration_ms" else None)


def validate_run(run: Any, suite: dict) -> None:
    """Allow missing tasks for visible failed coverage, but never extras/duplicates."""
    _object(run, "run", {"schema_version", "suite_id", "run_id", "results"})
    if type(run["schema_version"]) is not int or run["schema_version"] != 1:
        _fail("run.schema_version", "only version 1 is supported")
    if run["suite_id"] != suite["suite_id"]:
        _fail("run.suite_id", "does not match the task suite")
    _string(run["run_id"], "run.run_id")
    _array(run["results"], "run.results")
    known_ids = {task["id"] for task in suite["tasks"]}
    seen: set[str] = set()
    for index, result in enumerate(run["results"]):
        path = f"run.results[{index}]"
        _object(result, path, {"task_id", "status", "final", "tool_calls"}, {"usage"})
        _string(result["task_id"], f"{path}.task_id")
        if result["task_id"] not in known_ids:
            _fail(f"{path}.task_id", "unknown task ID")
        if result["task_id"] in seen:
            _fail(f"{path}.task_id", "duplicate result for task ID")
        seen.add(result["task_id"])
        if result["status"] not in ("completed", "error", "cancelled"):
            _fail(f"{path}.status", "expected completed, error or cancelled")
        _json_value(result["final"], f"{path}.final")
        _array(result["tool_calls"], f"{path}.tool_calls")
        for position, call in enumerate(result["tool_calls"]):
            location = f"{path}.tool_calls[{position}]"
            _object(call, location, {"name", "arguments", "outcome", "output"})
            _string(call["name"], f"{location}.name")
            if not isinstance(call["arguments"], dict):
                _fail(f"{location}.arguments", "expected an object")
            _json_value(call["arguments"], f"{location}.arguments")
            _json_value(call["output"], f"{location}.output")
            if call["outcome"] not in ("success", "error"):
                _fail(f"{location}.outcome", "expected success or error")
        if "usage" in result:
            _object(result["usage"], f"{path}.usage", set(), set(USAGE_FIELDS))
            for field, value in result["usage"].items():
                _number(value, f"{path}.usage.{field}", integer=field != "duration_ms",
                        maximum=MAX_DURATION_MS if field == "duration_ms" else None)


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("JSON object contains a duplicate key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ContractError(f"{value} is not a JSON number")


def load_document(path: str | Path) -> dict:
    """Read UTF-8 JSON without accepting duplicate keys or NaN/Infinity."""
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise ContractError(f"cannot read JSON document {path}: {error}") from error
