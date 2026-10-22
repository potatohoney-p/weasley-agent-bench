# Weasley Agent Bench

Evaluate saved agent results against explicit task contracts, without calling a
model, executing a tool, or depending on a particular agent runtime.

This offline prototype separates four questions:

1. Did the final structured answer satisfy the task?
2. Did the recorded tool sequence, outcomes, and resource usage satisfy its contract?
3. Which individual tasks improved or regressed between two runs?
4. How variable is a paired pass-rate change across repeated trials on a fixed suite?

The library and CLI use only Python's standard library. Python 3.11+ is required.
No accounts, API keys, external database, network service, or package installation are
needed to run from source.

## Reproduce the demo

From the repository root:

```bash
python -m unittest discover -s tests -v
python -m weasley_agent_bench evaluate examples/suite.json examples/candidate.json
python -m weasley_agent_bench compare examples/suite.json examples/baseline.json examples/candidate.json
```

All data is hand-authored and synthetic. The baseline fails two of four tasks:
it recalls a superseded fact and loses a child's review finding. The candidate
passes all four. This proves the evaluator's behavior; it measures no real
provider or model capability.

For suites and saved outputs that should not be retained entirely in memory,
an opt-in JSONL evaluator uses bounded records and temporary local SQLite storage:

```bash
python -m weasley_agent_bench evaluate-jsonl examples/jsonl/suite.jsonl examples/jsonl/candidate.jsonl
```

It preserves the same scores, suite digest, and missing-task failures. A terminal
summary marks a complete report. See [JSONL input, output, resource bounds, and
synthetic scaling measurements](docs/jsonl-evaluation.md).

| Task | Baseline | Candidate | Contract exercised |
| --- | --- | --- | --- |
| corrected-memory | Fail | Pass | Current Korean fact, evidence ID, recall result, token cap |
| retry-lookup | Pass | Pass | Failed lookup followed by successful retry, ordered output checks |
| review-handoff | Fail | Pass | Revision-bound delegation and retained child finding |
| draft-only | Pass | Pass | Draft result without tool side effects |

To demonstrate a failed gate, reverse the run order:

```bash
python -m weasley_agent_bench compare examples/suite.json examples/candidate.json examples/baseline.json
```

It exits **1** and identifies the two regressions. Equal aggregate pass counts do
not hide a regression: each task is paired by ID. Missing cases cannot disappear
from an evaluation denominator, and incomplete runs cannot be compared.

Run the reproducible fault-injection experiment:

```bash
python -m experiments.fault_matrix
```

It evaluates 12 deliberate mutations and one valid control. The complete
contract rejects all 12 faults; removing tool/budget expectations detects five.
Both versions accept the valid control. This small, selected fault matrix
explains what each contract adds; it is not an estimate of real-world detection
rates. See [the experiment design](docs/experiment.md).

### Memory across sessions

```bash
python -m experiments.memory_horizon --output-dir reports/memory-demo
python -m weasley_agent_bench evaluate reports/memory-demo/suite.json reports/memory-demo/latest-record.json
```

This exports a reusable 48-question suite, six synthetic episodes, three saved
baseline runs, and a report. Corrections, a retraction, restoration, session
resets, Korean values, and source IDs are scored against the state at each
question's historical cutoff. Questions and answer labels are kept out of the
stored fact stream.

The deterministic toy baselines score **19/48** with session-only state,
**24/48** with a four-record rolling state, and **48/48** with exact-key latest
record retrieval. These are reference implementations, not language models.
The experiment reports storage/context size and an explicitly labeled
character-based token proxy. Optional `--timing-trials 100` measures only local
lookup/context-selection time. [Memory experiment design](docs/memory-horizon.md)
explains the workload and limitations. Exports refuse to overwrite existing
artifacts; choose a fresh output directory on a later run.

Optional installation exposes the same CLI as `weasley-agent-bench`:

```bash
python -m pip install .
```

Installation needs setuptools for building; the installed package has no runtime
dependencies. Source execution is the reproducible offline path.

### Paired repeated runs

```bash
python -m weasley_agent_bench compare-repeated examples/repeated/suite.json examples/repeated/baseline.json examples/repeated/candidate.json --seed 17
```

This separate, hand-authored three-task/four-trial fixture exercises paired
analysis. Baseline and candidate pass rates are `7/12` and `8/12`; the difference
is `1/12`, with a 95% paired-trial bootstrap interval of `[-0.25, 0.5]`. The
report still exposes three paired regressions on one task. These synthetic
numbers validate the method, not any provider's performance.

Whole trial pairs are resampled with a fixed seed, preserving within-trial task
correlation and baseline/candidate pairing. Missing or duplicate trials,
unmatched trial IDs, and incomplete runs are rejected. The interval is
conditional on the fixed suite and independent representative trial pairs;
tasks are not independent bootstrap samples. This command is descriptive,
not a significance or regression gate. See the [method and input format](docs/repeated-runs.md).

## Use your own runner

Before spending a run, optionally check for impossible required-call budgets:

```bash
python -m weasley_agent_bench lint-suite examples/suite.json
```

The separate [static lint](docs/contract-lint.md) reports when required tool
steps exceed the call cap, with paths to both sides. It is not a general
satisfiability solver. Intentional negative fixtures can use the explicit
`--allow-contradictions` mode, which retains findings and `passed: false` while
accepting the fixture. Existing scoring and rescoring are unchanged.

1. Write a suite using the [version 1 contract](docs/contracts.md). Give every
   task a stable ID, prompt, and explicit expectations.
2. Run the task in your own harness and export its final JSON value, completed
   tool calls, status, and any measured usage into the result format. A bounded saved-text adapter is available for
   weasley-subagents foreground transcripts; see [its contract](docs/subagents-export.md).
3. Evaluate the saved result. Compare two complete result sets against the same
   suite to gate per-task regressions.

For optional export-time suite/run binding, use `create-provenance` with a
declared runner revision and check the sidecar with `verify-provenance` before
scoring. Verification detects changed/swapped artifacts and can require an
expected revision label. Existing saved runs and intentional rescoring remain
unchanged. See [provenance sidecars](docs/provenance.md) for CLI/API examples,
memory-export support, canonicalization, and the trust boundary. Unsigned
digests establish consistency, not authenticity or proof of execution.

```python
from weasley_agent_bench import evaluate, load_document

report = evaluate(load_document("examples/suite.json"),
                  load_document("examples/candidate.json"))
assert report["passed"]
```

The evaluator treats a failed/cancelled run, missing task, failed required tool,
unexpected exact-sequence call, forbidden tool, missing capped usage metric, or
exceeded cap as failure. It rejects malformed input, duplicate result IDs,
unknown tasks, ambiguous JSON keys, unsupported versions, and non-finite values.

## Reports and exit codes

Commands emit deterministic JSON to stdout, without timestamps. Reports include
the suite's canonical SHA-256 digest and stable per-task/check identifiers. They
do not reproduce full prompts, tool arguments, or tool outputs. Task IDs and
assertion paths are present, so use synthetic or appropriately reviewed inputs.

| Exit | Meaning |
| --- | --- |
| `0` | `evaluate`: every task passed. `compare`: no baseline pass became a failure. `compare-repeated`: valid descriptive analysis, even for negative changes. |
| `1` | A task failed evaluation or a comparison found a regression. |
| `2` | Invalid input or command usage; input errors are JSON on stderr. |

`create-provenance` and `verify-provenance` exit 0 on successful creation or
consistency verification, and 2 on invalid input or a mismatch. Verification
does not grade task success; a consistent failed run still needs evaluation.

`lint-suite` exits 0 for a clean or explicitly allowed report, 1 for an unallowed
static contradiction, and 2 for invalid input. Check `passed`, not just the exit
code, when using `--allow-contradictions`; `accepted` reflects that explicit policy.

`compare` is a regression gate. Its `candidate_passed` field separately states
whether **all** candidate tasks passed. Unchanged failures are still failures,
even when the regression gate exits 0. Usage deltas appear only for metrics
reported in both paired results; absent telemetry never counts as zero cost.

## Limits

This checks **declared results**, not independently observed execution. A runner
must record calls and telemetry honestly. The suite digest identifies the
contract used to score; it is not a signature or proof of how a run was produced.
This release has no provider execution, model judge, fuzzy language scoring,
trace replay, live side effects, or statistical significance claims. Repeated-run
intervals describe only trial variation on a fixed suite. JSON equality
does not imply factual correctness beyond the expectations supplied.

The [audit](docs/overlap-audit.md) explains why this is separate from the existing
Weasley runtimes. [ROADMAP.md](ROADMAP.md) lists acceptance criteria for the next
increments.

### Saved foreground transcript export

An opt-in `export-subagents` command converts a bounded, validated subset of
weasley-subagents version-1 foreground artifacts to a single task result.
See [the pinned source contract, synthetic fixture and limitations](docs/subagents-export.md).
This does not run a model or certify execution; missing telemetry stays missing.

## License

Licensed under the [MIT License](LICENSE).
