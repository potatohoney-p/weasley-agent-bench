# Bounded JSONL evaluation

`evaluate-jsonl` evaluates saved synthetic or consented artifacts offline. It
indexes both inputs and report rows on temporary disk so Python does not retain
the whole suite, all saved outputs, all IDs, or all result rows. It calls no
provider, runtime, tool, or network. This is an opt-in transport for the same v1
task-contract evaluator; existing JSON commands and report schemas are unchanged.

```bash
python -m weasley_agent_bench evaluate-jsonl examples/jsonl/suite.jsonl examples/jsonl/candidate.jsonl
```

The example is the same four hand-authored synthetic tasks and passing candidate
as `examples/suite.json` and `examples/candidate.json`. It is not model evidence.

## Input protocol, version 1

Each file is UTF-8 JSON Lines. Every physical line contains one object; blank
lines, duplicate object keys, non-finite JSON numbers, invalid UTF-8, and unknown
fields are rejected. LF and CRLF are accepted. The last record may omit its
newline. A BOM is rejected. File types are explicit, never auto-detected.

The suite's first line is exactly:

```json
{"format":"weasley-agent-bench/suite-jsonl","format_version":1,"schema_version":1,"suite_id":"synthetic-example"}
```

Subsequent lines are ordinary v1 task objects (`id`, `prompt`, `expect`, and
optional `tags`/`budget`). At least one task is required. The final line is an
explicit end marker whose integer count matches the number of task records:

```json
{"end":true,"count":4}
```

The run header also requires `run_id`:

```json
{"format":"weasley-agent-bench/run-jsonl","format_version":1,"schema_version":1,"suite_id":"synthetic-example","run_id":"synthetic-candidate"}
```

It is followed by ordinary v1 result objects and the same end-marker shape,
counting results. Zero results is valid and scores every task as missing.
`suite_id` must match. Run order is arbitrary; suite order defines report order.
Duplicate task/result IDs and unknown result IDs are errors. Missing results
remain failures in the full suite denominator. No record may follow the end
marker. A missing marker is an error even if the last task/result was complete
JSON; line-aligned truncation cannot silently become a smaller valid input.

`format_version` versions this transport independently of the embedded v1 task
and run `schema_version`. Both currently accept only integer 1, not booleans.

## Report protocol and completion

All input is validated and all task rows are scored into temporary storage
before the first report event. This trades time to first output for a stronger
failure boundary: a malformed late input or scoring failure yields **no stdout**.

Output is JSONL:

1. One header with `format: "weasley-agent-bench/report-jsonl"`,
   `format_version: 1`, `schema_version: 1`, `suite_id`, `suite_sha256`, and `run_id`.
2. One `{"result": ...}` object per task in suite order. Its value is exactly
   the corresponding legacy evaluation row, including checks and missing status.
3. One terminal `{"complete": true, "passed": ..., "summary": ...}` object.
   The summary has the existing `total`, `passed`, `failed`, and `missing` counts.

Only a report with its terminal completion summary is complete evidence. A
broken output pipe, process interruption, or storage read failure may leave a
partial report; consumers must not treat the rows alone as a successful run.
CLI exit codes are 0 for a complete passing evaluation, 1 for a complete failing
evaluation, and 2 for input/evaluation errors. Input errors identify `suite` or
`run` and the one-based physical line without echoing saved payloads. Temporary
storage failures have a separate error. Syntax/argument errors follow argparse.

The suite hash is the SHA-256 of the **same canonical v1 suite document** used by
`evaluate`. It is computed incrementally, preserving task order and Python's
existing JSON number/string representation. JSONL framing, whitespace, and run
order do not change it. It establishes artifact consistency, not authenticity
or proof of real execution.

The public Python API returns an iterator:

```python
from contextlib import closing
from weasley_agent_bench import evaluate_jsonl

with closing(evaluate_jsonl("suite.jsonl", "run.jsonl")) as events:
    for event in events:
        consume(event)  # Persist/consume rows without accumulating a list.
```

Consume the iterator completely or close it when stopping early. Wrapping it in
`list(...)` retains the output and forfeits the caller's bounded-memory benefit.

## Resource bounds and limits

The default maximum physical record size is **1,048,576 bytes**, including LF
or CRLF bytes. The reader requests at most `limit + 1` bytes per physical line
before deciding whether to reject it. Headers and end markers share the limit.
`--max-record-bytes N` (API: `max_record_bytes=N`) accepts a positive integer
below the platform's `sys.maxsize` so the bounded read length is representable;
raising it explicitly raises the working-memory budget. This is not a file-size
cap, a process-memory hard limit, or a CPU timeout. Recursive JSON/scoring limits
still apply to individual deeply nested records.

SQLite stores unique ID indexes, payloads, and report rows in a private temporary
directory. IDs are encoded as canonical JSON strings so all v1 JSON string IDs,
including escaped surrogate code points, retain their meaning. The connection
uses a 2 MiB suggested page-cache budget, disables memory mapping, and requests
file-backed temporary work. Indexed lookups and integer primary-key ordering
avoid a whole-suite sort. Working memory scales with the largest permitted
decoded record/report plus fixed interpreter/database caches, **not task count**;
JSON objects and strings require more RAM than their wire bytes. SQLite/native
allocations and the interpreter baseline are not counted by `tracemalloc`.

Temporary disk scales with both inputs, indexes, journals, and report rows. It
may exceed input size. The location defaults to the platform temporary directory;
use `--temporary-directory DIR` (API: `temporary_directory=DIR`) to choose an
existing parent with enough space. Ordinary completion/errors clean up, while a
forced process termination can leave temporary files. This is ephemeral scratch
storage, not a durable resumable database. Saved input may be written to scratch;
use a suitable local filesystem for consented data. No sandbox or trust boundary
for hostile workloads is claimed.

This command evaluates one saved run. The separate opt-in
[`compare-jsonl` command](jsonl-comparison.md) compares two complete runs in the
same input formats. [`verify-provenance-jsonl`](jsonl-provenance.md) separately
checks retained sidecars against these files. Repeated trials use a separate
[batch transport and analysis](jsonl-repeated.md). Exporter output,
resumability, and live execution remain outside this transport's
scope. Existing JSON tools still consume their existing documents.

## Reproduce synthetic scaling evidence

```bash
python -m experiments.jsonl_scaling --tasks 500 5000 --output-bytes 128 4096
```

This generates equivalent legacy/JSONL inputs incrementally, evaluates each
task-count/output-size combination in a fresh subprocess, and compares a digest
binding every ordered report row, metadata, and totals. Every eleventh result
deliberately fails its final-value check. It reports Python allocation peak,
process peak RSS (POSIX only; unavailable on Windows), wall time, and sampled
temporary disk bytes. Tracing affects timing and RSS; scratch sampling is a
lower bound on peak logical file size. Inputs are temporary by default;
`--output-dir NEW_DIRECTORY` preserves them and refuses an existing directory.
`--modes legacy` runs only the existing evaluator as a baseline.

The fixture varies task count and retained tool-output size independently. This
tests evaluator scaling, not agent capability, provider performance, or observed
real workload demand. The documented 48-question memory workload does not itself
establish a need for large-scale ingestion. Real-workload adoption remains a
separate roadmap decision based on consented data.

## Recorded synthetic measurement

One run on 2026-10-22, WSL2 Linux with Python 3.12.3. Each row uses fresh
processes, tracing enabled, default 1 MiB record limit and 2 MiB suggested
SQLite page cache. All five cases matched the full logical report digest.
[Raw measurements and environment](evidence/jsonl-scaling.json) include
exact byte counts, hashes, summaries, RSS units, and methodology limits.

| Tasks | Output bytes/task | Python peak MiB legacy / JSONL | Process peak MiB legacy / JSONL | JSONL scratch MiB (sampled) | Seconds legacy / JSONL |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 500 | 128 | 2.31 / 0.05 | 24.23 / 20.94 | 0.65 | 0.12 / 0.37 |
| 500 | 4,096 | 5.17 / 0.07 | 26.30 / 23.04 | 2.71 | 0.14 / 0.40 |
| 5,000 | 128 | 23.31 / 0.06 | 75.98 / 22.04 | 6.38 | 1.38 / 2.52 |
| 5,000 | 4,096 | 51.96 / 0.07 | 97.03 / 22.30 | 26.76 | 1.25 / 2.71 |
| 20,000 | 4,096 | 207.97 / 0.07 | 319.61 / 22.04 | 107.10 | 5.22 / 11.63 |

For the largest case, the saved run alone is approximately 81 MiB. The bounded
path kept traced Python allocations nearly flat as task count grew, while
using temporary disk and more time. These observations support this
implementation's tradeoff; they are not universal memory ceilings or speed
guarantees. Process RSS includes native allocations and tracing overhead.
Reproduce the largest case with `--tasks 20000 --output-bytes 4096`.
