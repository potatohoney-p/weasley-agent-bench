# Version 1 input contracts

Only the fields documented here are accepted. Unknown fields fail validation,
including a misspelled budget or expectation. JSON must be UTF-8, without
duplicate object keys, NaN, or Infinity. IDs and names are case-sensitive.

## Suite

```json
{
  "schema_version": 1,
  "suite_id": "lookup-v1",
  "tasks": [{
    "id": "lookup-42",
    "prompt": "Return the synthetic lookup value for record 42.",
    "tags": ["lookup"],
    "expect": {
      "final": [{"path": "/value", "equals": "ready"}],
      "tools": {"mode": "exact", "steps": [{
        "name": "lookup",
        "outcome": "success",
        "arguments": [{"path": "/id", "equals": 42}],
        "output": [{"path": "/value", "equals": "ready"}]
      }]},
      "forbidden_tools": ["delete"]
    },
    "budget": {"max_tool_calls": 1, "max_input_tokens": 200, "max_output_tokens": 30, "max_duration_ms": 1000}
  }]
}
```

A suite has at least one task. Task IDs are unique. `tags` and `budget` are
optional. An expectation contains at least one final assertion, a forbidden
tool, or a meaningful tool contract; an exact empty sequence asserts no calls.

- `final`: assertions over the final JSON value. Each assertion has a `path` and
  an `equals` JSON value. Every assertion must pass.
- `tools.mode`: `exact` requires precisely the listed calls, in order.
  `subsequence` permits intervening calls, but required steps must occur in
  order. A call can match only one step. Each match includes name, outcome, and
  all argument/output assertions.
- `steps[].outcome`: `success`, `error`, or `any`. Both successful and failed
  recorded calls count toward exact sequences and tool-call budgets.
- `arguments` / `output`: optional assertion arrays for the corresponding
  call fields. Tool arguments themselves must be JSON objects; outputs may be
  any JSON value.
- `forbidden_tools`: any recorded attempt fails the contract, even if it
  returned an error. A tool cannot be both required and forbidden.
- `budget`: inclusive upper bounds. Tool calls and token limits are
  non-negative integers. Duration is non-negative finite milliseconds, at most
  **9,007,199,254,740,991** (`2^53 - 1`) for both `max_duration_ms` and recorded
  `duration_ms`. Fractional durations are allowed. This numeric ceiling avoids
  overflowing mixed integer/float arithmetic in comparison; it is not a runtime
  timeout. Very large durations can still lose sub-millisecond precision under
  ordinary floating-point arithmetic. A
  capped usage metric must be recorded; missing usage fails that cap. All
  usage is reported by the external runner. These are scoring gates, not
  runtime cancellation or billing controls.

### Assertion paths

Paths use JSON Pointer syntax. The empty string targets the entire value;
`/items/0/name` targets an array element's field. `~1` escapes a slash and `~0`
escapes a tilde in a field name. Array indices are non-negative ASCII integers
without leading zeroes. Object keys such as `"01"` remain valid object keys.

Missing values fail, including when `equals` is `null`. Object key order does
not matter; extra object fields matter when comparing that whole object.
Array order matters. Booleans and numbers differ (`true` is not `1`); JSON
numbers compare numerically (`1` equals `1.0`). Strings compare exactly, with no
case folding or Unicode normalization. Duplicate assertion paths are rejected.

## Saved run

```json
{
  "schema_version": 1,
  "suite_id": "lookup-v1",
  "run_id": "candidate-a",
  "results": [{
    "task_id": "lookup-42",
    "status": "completed",
    "final": {"value": "ready"},
    "tool_calls": [{
      "name": "lookup",
      "arguments": {"id": 42},
      "outcome": "success",
      "output": {"value": "ready"}
    }],
    "usage": {"input_tokens": 150, "output_tokens": 20, "duration_ms": 100}
  }]
}
```

Every result requires `task_id`, `status`, `final`, and `tool_calls`. An aborted
runner can supply `final: null` and its calls observed so far. Status is
`completed`, `error`, or `cancelled`; only completed tasks can pass. Calls have
`success` or `error` outcome. Pending calls have no completed outcome and must
be represented as a failed/cancelled run by the exporting runner.

`usage` is optional, and its individual fields are optional unless the task
caps them. `results` can be empty or omit tasks during evaluation; missing
tasks become explicit failures. Extra/duplicate task IDs are errors. Comparison
requires exactly one result for every suite task on both sides, regardless of
result ordering. Comparison does not pair partial subsets or invent missing
telemetry.

## Stable reports

Evaluation emits one result per task in **suite order**, with `passed`,
`status`, and named checks. A failed check's detail describes its assertion;
the associated boolean is authoritative. The summary denominator is the whole
suite. `suite_sha256` hashes the canonical JSON suite (keys sorted, compact
separators, non-ASCII characters escaped, UTF-8), including prompts and budgets.

Comparison emits the same ordered IDs, transitions, candidate failed-check
codes, and candidate-minus-baseline usage deltas for shared metrics. No
averaging, confidence interval, provider ranking, or inference of causation is
performed. Store both input run files and the exact suite with reports when
using the outputs as evidence.
