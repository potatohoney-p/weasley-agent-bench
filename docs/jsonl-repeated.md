# Bounded repeated-run JSONL analysis

`compare-repeated-jsonl` describes paired repeated runs on a supplied fixed task
suite. It uses bounded physical records, local temporary SQLite indexes, and the
same whole-trial bootstrap as [`compare-repeated`](repeated-runs.md). It does not
run a model, rank providers, prove independent repetitions, or provide an
automatic regression/significance gate. This is an opt-in research capability;
real-workload demand and adoption remain unvalidated.

```bash
python -m weasley_agent_bench compare-repeated-jsonl examples/repeated/jsonl/suite.jsonl examples/repeated/jsonl/baseline.jsonl examples/repeated/jsonl/candidate.jsonl --seed 17
```

These examples are equivalent transports of the hand-authored synthetic legacy
fixture. The baseline/candidate pass rates are `7/12` and `8/12`, the difference
is `1/12`, and the nominal 95% percentile interval is `[-0.25, 0.5]`. Three paired
regressions on task 1 remain visible. These numbers check the algorithm and do
not describe provider performance.

## Input protocol

The suite uses the existing [suite JSONL format](jsonl-evaluation.md), including
its exact end marker and canonical v1 suite digest. Baseline and candidate each
use a new `batch-jsonl` format version 1. Every physical line is one JSON object;
blank lines and duplicate JSON keys are invalid. The first line has exactly:

```json
{"format":"weasley-agent-bench/batch-jsonl","format_version":1,"schema_version":1,"suite_id":"synthetic-suite","batch_id":"synthetic-baseline"}
```

Each trial is a contiguous block, beginning with its ID and run metadata. The
nested `run` object has exactly three metadata fields and no results array:

```json
{"trial_id":"trial-0","run":{"schema_version":1,"suite_id":"synthetic-suite","run_id":"baseline-0"}}
{"task_id":"task-0","status":"completed","final":true,"tool_calls":[]}
{"task_id":"task-1","status":"completed","final":false,"tool_calls":[]}
{"end_trial":true,"count":2}
```

Bare result records follow the existing v1 run-result contract. `end_trial` is
required, with exactly the observed result count. It is followed by another
trial header or the final batch marker:

```json
{"end":true,"count":4}
```

The batch marker's count is the number of **completed trial blocks**, not the
number of task results. No trailing record is allowed. The versions and counts
are integers, not booleans; marker flags are literal `true`. Unknown fields,
malformed UTF-8/JSON, unsupported versions and non-finite numbers fail. LF/CRLF
and a final record without a newline are supported. No file references or URLs
are interpreted. Existing v1 JSON batch documents remain unchanged.

Both batch IDs and all trial/run IDs must be non-empty strings. Every batch and
nested run must name the supplied suite. A batch needs at least two complete
trials; every trial must have exactly one result for every task. Unknown task
IDs, duplicate result IDs within a trial, duplicate trial IDs, and reused run
IDs within one batch fail. Error/cancelled results are observed failures, not
missing observations. No task/trial intersection is silently selected.

Baseline and candidate must have exactly the same trial-ID set. The same run ID
may occur on opposite sides, including self-comparison. Trial blocks and results
within each block may appear in any order; interleaved trial blocks are invalid.
The protocol bounds individual results rather than putting an entire run on one
line. A single oversized result must be rejected or explicitly accommodated by
the caller's record-size option, not silently truncated.

## Output and completion

All three inputs, every score, task/trial aggregation and the bootstrap finish
before the first output event. Output is JSONL in this order:

1. Header: `format: "weasley-agent-bench/repeated-comparison-jsonl"`,
   `format_version: 1`, `schema_version: 1`, suite ID/digest, baseline/candidate
   batch IDs, and the complete legacy `analysis` metadata.
2. One `{"trial": {...}}` event for each trial pair, in Python string sort order.
   Fields match legacy trial rows exactly, retaining both run IDs and pass counts.
3. One `{"task": {...}}` event for each task, in suite order. Fields match legacy
   task rows, including paired improvements/regressions and pass-rate differences.
4. One terminal `{"complete":true,"summary":{...},"interval":{...}}` event,
   with the exact legacy summary and interval.

Materializing those fields and row arrays gives the same logical report as
`compare_repeated` on equivalent v1 documents. Unicode trial IDs, including
escaped control characters and lone surrogates, retain Python code-point order;
JSON escape spelling is not the sort key. Report output intentionally includes
IDs, but does not reproduce saved prompts/tool output.

Consumers must require a valid header, the stated trial/task row counts from
the final summary, and the terminal `complete` event. Partial output without a
terminal event is incomplete. The iterator must be exhausted or closed to release
scratch storage. CLI exit **0** means valid descriptive analysis, including a
negative difference or interval. There is no `passed` or `no_regressions` flag.
Invalid input/storage failure exits **2** with a JSON stderr error. Input,
validation and analysis failures before emission produce no stdout. An I/O
failure while reading an already spooled report can leave an incomplete stream;
the terminal event is the completion requirement.

## Statistics and what the data cannot establish

For `N` paired trials and `T` tasks, let `d_i` be candidate passes minus baseline
passes in trial `i`. The observed change is `sum(d_i)/(N*T)`. Each bootstrap
resample draws **N whole trial pairs** with replacement. Tasks and both conditions
within a pair stay together. The `N*T` results are not counted as independent
samples. Uniformly duplicating every task as a perfectly correlated copy leaves
the interval unchanged. Selectively duplicating tasks changes their suite weight
and can change the interval, without adding independent trial evidence.

The implementation uses the exact legacy local `random.Random(seed)` sequence:
`B` resamples, each making `N` calls to `randrange(N)` in sorted-trial order.
Integer totals are sorted and inverse-CDF indices `ceil(p*B)-1` at 2.5% and
97.5% are divided by `N*T`. The same shared calculation serves both transports.
The default seed is 0 and default `B` is 10,000; the existing bounds remain seed
`0..2^64-1` and resamples `100..100000`, integer-only. More resamples address
Monte Carlo error, not bias, dependence, or lack of independent trials.

The interpretation is **conditional on the fixed suite and independent,
representative trial pairs supplied by the runner**. Trial IDs identify declared
pairings; they do not establish matched conditions or independent execution.
Identical outcomes with distinct valid IDs are allowed, but copying/relabeling
a saved trial supplies no additional experimental evidence. Duplicate identity
checks detect malformed records, not undisclosed dependence or duplicated
executions under new IDs. The evaluator cannot certify effective sample size.

Two trials are a technical minimum, not a recommendation for inference. The
nominal 95% interval does not guarantee 95% repeated-sampling coverage; few
trials/discrete outcomes can produce unstable or zero-width intervals. Zero
width does not establish zero real uncertainty. No p-value, causal claim,
unseen-task generalization, or provider-superiority conclusion is supplied.
Preserve the Python version/options for replay; future Python RNG behavior is
not guaranteed. Global random state and input artifacts are unchanged.

The canonical suite digest identifies the full supplied scoring contract,
including prompts/expectations/budgets; it does not authenticate the historical
suite used by the runner. Intentional rescoring remains supported. The separate
single-run [provenance verifier](jsonl-provenance.md) can check retained sidecars
against separate suite/run artifacts, but this batch command does not verify or
create batch provenance, signatures, or independent execution evidence.

## Resource boundaries and Python API

```python
from contextlib import closing
from weasley_agent_bench import compare_repeated_jsonl

with closing(compare_repeated_jsonl("suite.jsonl", "baseline.jsonl", "candidate.jsonl",
                                    seed=17, resamples=10000,
                                    max_record_bytes=1024 * 1024,
                                    temporary_directory="scratch")) as events:
    for event in events:
        consume(event)  # retain/process bounded rows, and require complete=true
```

`--max-record-bytes` defaults to **1,048,576 bytes**, including newline bytes,
and must be a positive integer below `sys.maxsize`. It applies to suite records,
batch/trial headers, results and markers. Output rows can combine IDs from two
headers, so the input limit is not an output-line bound. A caller who collects
all output rows forfeits bounded output-consumption memory.

The implementation stores the suite, validated pass observations, unique IDs and
report rows on temporary disk. Raw result payloads are scored and discarded one
record at a time. Identity indexes and pass observations grow with `N*T`; suite
payloads and report rows add disk space. SQLite uses a 2 MiB page-cache target,
file-backed temporary operations and no mmap. The bootstrap uses indexed disk
delta lookups and retains at most `B <= 100000` integer totals in Python memory.
Working memory is bounded by record size, caches and `O(B)` totals rather than
by all trial/task payloads. Parser copies, integer sizes, native allocations and
OS caches mean this is not a fixed RSS ceiling.

Bootstrap work is `O(B*N)` indexed SQL lookups, in addition to input validation,
scoring and aggregation. Large trial counts can therefore be slow even when
memory is low. More resamples cost time/memory but do not increase observed
sample size. No total disk/time quota or resumability is supplied. Storage must
be writable and large enough; failure cannot become a successful partial report.
`--temporary-directory` selects its parent. Inputs should remain stable during
the sequential reads; no atomic snapshot or file-lock guarantee is provided.

## Reproducible synthetic resource observations

```bash
python -m experiments.jsonl_repeated_scaling
python -m experiments.jsonl_repeated_scaling --case 100:40:4096:333 --seed 17
```

Each case is `tasks:trial_pairs:output_bytes_per_result:resamples`. The defaults
vary one factor at a time around `100:4:4096:333`. Generation is incremental in a
separate child, with shuffled trials/results and Unicode IDs. Fresh legacy and
JSONL workers hash all report metadata/analysis, every ordered trial/task row,
counts, summary and interval; the JSONL consumer never materializes the whole
report. All five complete logical-report comparisons matched.

[Recorded Linux/WSL Python 3.12.3 evidence](evidence/jsonl-repeated-scaling.json)
contains raw bytes, hashes, summaries, intervals and measurement methods:

| Tasks | Trial pairs | Output bytes/result | Resamples | RSS MiB legacy / JSONL | Seconds legacy / JSONL |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 4 | 128 | 333 | 24.8 / 20.5 | 0.145 / 0.196 |
| 100 | 4 | 4,096 | 333 | 28.3 / 19.9 | 0.193 / 0.200 |
| 1,000 | 4 | 4,096 | 333 | 110.4 / 21.4 | 1.411 / 1.480 |
| 100 | 40 | 4,096 | 333 | 110.6 / 21.8 | 1.347 / 1.279 |
| 100 | 4 | 4,096 | 10,000 | 29.3 / 20.0 | 0.191 / 0.439 |

These are single synthetic observations, legacy then JSONL, not universal memory
ceilings or speed guarantees. Timings include loading, validation, scoring,
bootstrap and report hashing plus tracing/sampling overhead; they exclude
generation/imports/process startup. Process RSS covers the whole worker lifetime
and native allocations; it is unavailable from this harness on Windows.
`tracemalloc` excludes native memory. JSONL traced Python peaks ranged from about
72–221 KiB, increasing with the bootstrap resample count as expected. Sampled
scratch peaked at about 1.35 MiB; 10 ms sampling gives a lower bound on logical
file sizes and excludes fixture files. The synthetic named trials are not
independent empirical observations; no provider-performance or adoption claim
follows from this engineering experiment.
