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

### Optional pre-run lint

Schema validity does not guarantee satisfiability. The opt-in `lint-suite`
command detects required tool-step counts exceeding `max_tool_calls`, without
changing this schema or scoring. Exact and subsequence steps both require
distinct calls, including repeated names and error outcomes. Intentional
negative fixtures use `--allow-contradictions`; findings remain visible and
malformed inputs still fail. See [the detection and policy boundary](contract-lint.md).

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

Single-run `compare` emits the same ordered IDs, transitions, candidate failed-check
codes, and candidate-minus-baseline usage deltas for shared metrics. No
averaging, confidence interval, provider ranking, or inference of causation is
performed. Store both input run files and the exact suite with reports when
using the outputs as evidence.

### Optional provenance sidecar

Saved runs remain version 1 and do not require provenance fields. The separate
`create-provenance` / `verify-provenance` workflow binds one suite and run by
canonical digest plus an externally declared runner revision. Verification
rejects mismatched artifacts and, when requested, a mismatched expected revision.
It does not change scoring or prevent intentional rescoring. See the strict
[sidecar schema, canonicalization, and compatibility policy](provenance.md).
Unsigned hashes establish consistency, not actual execution or authenticity.

## Repeated-run batches

`compare-repeated` accepts the same suite plus two strict version 1 batch
documents, one for each condition:

```json
{
  "schema_version": 1,
  "suite_id": "lookup-v1",
  "batch_id": "baseline-batch",
  "trials": [
    {"trial_id": "trial-0", "run": {}},
    {"trial_id": "trial-1", "run": {}}
  ]
}
```

Each `run` placeholder above must be replaced with a complete saved-run object
in the format documented earlier. Batch and nested run `suite_id` values must
match the supplied suite. Batch IDs and trial IDs are non-empty strings. Unknown
fields, duplicate trial IDs, and duplicate run IDs within a batch are errors.
Both batches must contain at least two trials and exactly the same trial IDs.
Every nested run must contain exactly one result per suite task: missing,
duplicate, or extra task IDs fail validation. Error and cancelled task results
are present failures, not missing data. No partial trial or task intersection
is silently selected. The same run ID on opposite sides is allowed, for example
when comparing a batch with itself.

Reports include the suite digest, batch IDs, method/options, overall pass rates,
the paired pass-rate difference and its 95% bootstrap interval. Trial rows are
sorted by trial ID and retain both run IDs. Task rows follow suite order and
retain paired improvement/regression counts. These counts are out of the number
of trial pairs, not the number of tasks. Rates and interval endpoints are finite
fractions in `[-1, 1]` (pass rates in `[0, 1]`), not percentages.

The bootstrap resamples complete paired suite trials, conditional on the fixed
suite and independent representative trial-pair assumptions. It does not
resample individual tasks or produce an automatic performance verdict.
The [method and limitations](repeated-runs.md) specify the estimand, percentile
definition, deterministic seed policy, CLI option bounds, and synthetic example.

## Optional saved-harness adapter

`export-subagents` is separate from scoring and accepts only the explicitly
supported foreground artifact subset documented in [subagents-export.md](subagents-export.md).
It preserves saved text rather than parsing JSON-looking output, rejects
incomplete recorded tool lifecycles, and omits uncertain usage. The output is an
ordinary version-1 run, so all existing validation and missing-task rules apply.

## Bounded JSONL transport

The opt-in `evaluate-jsonl` command accepts separately versioned suite/run JSONL
files with strict metadata headers, existing v1 task/result objects, and required
end markers with exact record counts. Records are bounded (default 1 MiB including
newline bytes); errors identify source and physical line. Both inputs and scores
are validated before any report is emitted. Disk-backed indexes preserve arbitrary
run order, duplicate/unknown-ID rejection, suite-order rows, explicit missing
failures, and the exact existing canonical suite digest. Output consists of a
header, one result event per task, and a required completion summary. Consumers
must reject partial output without that summary. Legacy JSON contracts and
scoring are unchanged. See [the protocol and resource bounds](jsonl-evaluation.md).

`compare-jsonl` accepts the same suite/run transports for a shared suite and two
complete runs. It rejects missing coverage on either side, while retaining
arbitrary order and allowing identical run IDs across sides. Change rows, jointly
observed usage deltas, flags, and summary exactly match legacy `compare`. Its
`weasley-agent-bench/comparison-jsonl` output has one header, ordered change
events, and a required completion summary. Exit 0 means no regressions, not that
all candidate tasks passed. Both input streams and all scores are validated before
output. Matching suite IDs do not attest to historical suite contents; the supplied
suite defines intentional rescoring. See [paired comparison](jsonl-comparison.md).

`verify-provenance-jsonl` optionally checks the existing six-field v1 sidecar
against explicitly supplied JSONL suite/run files. It recomputes both canonical
v1 document digests in original record order after full contract/framing checks.
The entire sidecar has a separate byte bound; no manifest paths or URLs are
supported. Success emits the legacy verification JSON object, after all checks;
errors emit no report. Valid incomplete runs may be consistent but do not gain
coverage or successful scores. See [verification boundaries](jsonl-provenance.md).
