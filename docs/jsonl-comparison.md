# Bounded paired comparison

`compare-jsonl` applies the existing paired regression gate to two complete saved
runs without retaining either whole run or evaluation report in memory. It is an
opt-in research extension of bounded JSONL evaluation, sharing its reader,
validation, canonical suite hash, temporary storage, and per-task scorer. The
change-row comparison is shared with legacy `compare`.

This capability builds on Draft PR #7. Its input formats are unchanged. Existing
JSON evaluation/comparison and single-run JSONL output remain compatible.

```bash
python -m weasley_agent_bench compare-jsonl examples/jsonl/suite.jsonl examples/jsonl/baseline.jsonl examples/jsonl/candidate.jsonl
```

These are the same four hand-authored synthetic tasks and two saved runs used by
the README demo. No model, tool, runtime, or network is executed. Reverse the two
run paths to demonstrate two regressions and exit 1.

## Inputs and pairing

Supply one suite JSONL file and two run JSONL files in the existing
[version-1 transport](jsonl-evaluation.md#input-protocol-version-1). Each run uses
`weasley-agent-bench/run-jsonl`, not a new baseline/candidate input format. Strict
headers, versions, end markers/counts, UTF-8, duplicate-key rejection, and the
physical record byte limit apply to all three files. Diagnostics distinguish
`suite`, `baseline`, and `candidate` and identify the one-based physical line.

Both runs must have the supplied suite's `suite_id` and exactly one result for
each task. Run order is arbitrary. Duplicate/unknown IDs are errors even if a run
has the expected number of records. Missing results on either side are errors;
there is no intersection-based comparison. `error` and `cancelled` results are
present failures, not missing cases. The same `run_id` on both sides is allowed,
including comparison of a run with itself. IDs retain their v1 JSON string meaning.

The **supplied suite defines the comparison**. Its exact canonical digest is
reported, and its task order defines change order. Like legacy `compare`, this
supports intentional rescoring. Matching run `suite_id` values do not prove the
historical suite contents used to generate either run. No provenance sidecar,
prompt binding, authenticity, or actual execution is inferred from matching IDs.

## Comparison semantics and output

Each task is scored on both sides under the shared suite. A pass-to-fail change
is a regression; fail-to-pass is an improvement; the other transitions are
unchanged. Equal aggregate pass totals can still contain regressions. Usage
deltas include only metrics actually present in both results. Absent telemetry
does not become zero; observed zero is retained. Candidate failed-check codes
remain visible, including unchanged failures.

Both complete input streams are validated before coverage is checked. Every
change is then scored and spooled before any output is emitted. Malformed late
records, missing coverage, scoring-depth failure, or a storage-write failure
produce no report. The shared storage closes active cursors before cleanup so a
mid-suite scoring exception does not pin scratch files on Windows.

Output is JSONL with this independently identified report format:

1. A header with `format: "weasley-agent-bench/comparison-jsonl"`,
   `format_version: 1`, `schema_version: 1`, `suite_id`, `suite_sha256`,
   `baseline_run_id`, and `candidate_run_id`.
2. One `{"change": ...}` per task, containing exactly the corresponding legacy
   comparison row: task ID, transition, both pass flags, jointly observed usage
   deltas, and candidate failed-check codes.
3. A terminal object with `complete: true`, `no_regressions`, `candidate_passed`,
   and `summary`. The summary contains `total`, `regressions`, `improvements`,
   `baseline_passed`, and `candidate_passed` counts, exactly as legacy `compare`.

Consumers must require the terminal completion record. A broken output pipe,
process interruption, or spool-read failure can leave incomplete output. The
CLI exits **0 when there are no regressions**, **1 when a regression is present**,
and **2 for invalid/incomplete inputs or evaluation errors**. Exit 0 does not mean
the candidate passed every task: unchanged failures pass the regression gate.
Argument/syntax errors follow argparse. The completion flags have separate meanings.

The API is an iterator with the same completion/close obligations as evaluation:

```python
from contextlib import closing
from weasley_agent_bench import compare_jsonl

with closing(compare_jsonl("suite.jsonl", "baseline.jsonl", "candidate.jsonl")) as events:
    for event in events:
        consume(event)
```

## Resources and scope

`--max-record-bytes N` and `--temporary-directory DIR` have the same defaults and
validation as `evaluate-jsonl`; API keyword names are `max_record_bytes` and
`temporary_directory`. The default limit is 1 MiB per physical record including
newline bytes, not a total-file cap. A positive limit below `sys.maxsize` is required.

The database indexes results by side and task ID. A suite-order scan uses indexed
left joins to both sides; no Python set/list of all IDs or changes is retained.
Working memory includes one task, two saved results, their score/change rows,
decoding/serialization copies, and fixed interpreter/database caches. The shared
connection retains the 2 MiB suggested SQLite page-cache budget and disables
memory mapping. These settings are not a hard process-memory cap. Raising the
record limit raises the working-memory budget.

Temporary disk grows with the suite, both runs, indexes, journals, and change
rows. It may exceed input bytes. First output waits for full validation/scoring.
Consume or close the iterator to release temporary storage. Forced termination
may leave scratch files. This is not a CPU timeout, filesystem sandbox, resumable
database, or live-run pipeline. Standard-library SQLite availability is required.

Paired repeated-run JSONL analysis and provenance-sidecar file verification are
outside this capability. Existing commands keep their original input formats.
Real-workload adoption remains an open roadmap decision: the measurements below
are synthetic engineering evidence, not evidence of actual deployment demand or
any provider/model's performance.

## Reproduce resource and parity measurements

```bash
python -m experiments.jsonl_comparison_scaling --tasks 500 5000 --output-bytes 128 4096
python -m experiments.jsonl_comparison_scaling --tasks 20000 --output-bytes 4096
```

The experiment incrementally generates equivalent suite/baseline/candidate
artifacts. Each group of four tasks includes one improvement, one regression,
one unchanged pass, and one unchanged failure. Baseline order is reversed and
candidate order groups even then odd task indices. Joint, absent, zero, integer,
and fractional usage values exercise delta semantics. Output size varies
independently of task count and is retained in both runs.

Fresh child processes run legacy and JSONL comparison. A digest binds all ordered
changes plus all logical report metadata, both terminal flags, and summary.
The experiment reports Python allocation peak, POSIX process peak RSS (null on
Windows), elapsed time, and sampled scratch logical bytes. Tracing affects time
and RSS; tracemalloc excludes SQLite/native allocations. Sampling is a lower
bound on peak logical file size, not physical disk allocation. Fixture creation
and process/module startup are outside the timed/traced interval; RSS covers the
whole process. Default fixtures are temporary. `--output-dir NEW_DIRECTORY`
retains them and refuses an existing directory; `--modes legacy` runs the baseline
alone. Reported values are observations, not universal performance guarantees.

## Recorded synthetic measurements

One run on October 23, 2026 KST (October 22 UTC), WSL2 Linux/Python 3.12.3.
Each case/mode used a fresh process, tracing enabled, the default 1 MiB
record limit, and the 2 MiB suggested SQLite page cache. All five complete
logical report digests matched. [Raw measurements and environment](evidence/jsonl-comparison-scaling.json)
contain exact bytes, flags, summaries, hashes, and methodology limits.

| Tasks | Output bytes/task/run | Python peak MiB legacy / JSONL | Process peak MiB legacy / JSONL | JSONL scratch MiB (sampled) | Seconds legacy / JSONL |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 500 | 128 | 3.94 / 0.06 | 28.26 / 21.00 | 0.68 | 0.18 / 0.34 |
| 500 | 4,096 | 7.81 / 0.08 | 32.47 / 24.10 | 4.69 | 0.19 / 0.36 |
| 5,000 | 128 | 39.83 / 0.06 | 124.87 / 22.45 | 6.75 | 1.97 / 2.94 |
| 5,000 | 4,096 | 78.61 / 0.08 | 167.16 / 22.64 | 46.80 | 1.84 / 3.16 |
| 20,000 | 4,096 | 314.66 / 0.08 | 594.20 / 22.29 | 187.34 | 8.34 / 12.31 |

The largest pair has roughly 165 MiB of saved run JSON. It contains 5,000
improvements and 5,000 regressions despite equal 10,000/20,000 pass counts.
The bounded path trades scratch space and elapsed time for a nearly flat
measured working set across these cases. This is evidence for this
implementation's behavior on synthetic data, not a deployment recommendation
or a guarantee for every workload/platform.
