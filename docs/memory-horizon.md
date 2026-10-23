# Synthetic cross-session memory benchmark

The benchmark asks whether a saved answer reflects the latest available fact
and cites its supporting event after session boundaries and corrections.

## Workload

`experiments.memory_horizon` deterministically generates eight synthetic teams
across six sessions. The first session introduces eight offices. Each later
session corrects two records; session 3 also retracts team-0's office, and
session 4 restores it with a new value. There are **19 fact events** and **48
held-out questions**, one for each team at each session cutoff.

Every question requires both `office` and `evidence_ids`. A retracted office
requires `null` and the retraction's source ID. An unknown office with no source
cannot impersonate a known retraction. A correct value with stale provenance
fails. The source stream contains facts only; question answers are not fed back
into a baseline. Earlier labels cannot change when a later event changes.

The workload is small and deliberately structured so failures are inspectable.
Repeated entity questions are correlated; the 48 observations are not presented
as independent samples for statistical inference.

## Baselines

| Baseline | State retained | Context selected for a query | Correct |
| --- | --- | --- | --- |
| Session-only | Latest records in the current session, cleared at every boundary | Current session state | 19/48 (39.58%) |
| Rolling-state-4 | Four most recently updated distinct records, including retraction markers | All retained records | 24/48 (50%) |
| Latest-record | Latest event per exact key across sessions | The requested key's one record | 48/48 (100%) |

Session-only represents no cross-session persistence, not an empty context.
Rolling state is a bounded structured summary, not a model-generated summary.
Latest-record is a dictionary baseline, not semantic search, durable storage,
or an agent. Exact keys and explicit corrections make that baseline's perfect
score expected. These policies demonstrate the evaluation surface and memory
tradeoffs; they do not establish a novel retrieval algorithm or model advantage.

For this workload, mean context proxies are approximately **59.83**, **75.33**,
and **19.13** respectively; final retained records are **2**, **4**, and **8**.
The proxy is `ceil(serialized context Unicode character count / 4)`, computed
per question. It excludes the prompt, is language-dependent, and is neither a
tokenizer count nor billing usage. It is kept out of the saved run's `usage`
fields. Reported storage characters are serialized logical state size, not Python
heap memory or on-disk size.

## Reproduce and inspect

```bash
python -m experiments.memory_horizon --output-dir reports/memory-demo
python -m weasley_agent_bench evaluate reports/memory-demo/suite.json reports/memory-demo/latest-record.json
python -m weasley_agent_bench compare reports/memory-demo/suite.json reports/memory-demo/session-only.json reports/memory-demo/latest-record.json
```

The export contains `episodes.json` for runner inputs, `suite.json` with held-out
evaluation labels, one saved run per baseline, and `report.json`. An external
runner must consume episodes in order and answer each session's questions
before receiving future events. Keep the suite's `expect` values away from the
runner. Store the suite digest with experiment results.

Export refuses to overwrite any target artifact. It checks the whole set before
writing, then creates each file exclusively so a competing writer that appears
after that check is also preserved. This applies to optional provenance sidecars.
An existing empty directory is allowed. A collision or other I/O failure stops
the export; the CLI exits 2 without a success report. Earlier files may remain:
this is not an atomic bundle transaction or a lock against later external edits.
Do not treat a failed export's partial directory as complete evidence; retain
the original artifacts and retry in a fresh directory.

## Optional measured timing

```bash
python -m experiments.memory_horizon --timing-trials 100 --output-dir reports/memory-timed
```

This measures 4,800 in-process lookups per baseline using `perf_counter_ns` and
reports median/p95 microseconds plus Python/platform/clock resolution. It times
answer creation and context selection; it excludes event ingestion, models,
network, databases, serialization, and evaluator scoring. Timing varies with
machine load, warm caches, interpreter, and timer overhead. Functional scores
and context counts are reproducible; timings are observations, not fixed test
expectations. A run on Windows/Python 3.14.5 on 2026-10-20 measured medians around
2–3 microseconds. This does not predict agent latency.

## Limits and next experiment

No model or paid API was called, and no real personal memory or production trace
was used. Event IDs are trusted synthetic provenance, not independently verified
citations. The baseline reads explicit keys and already-structured records; it
does not solve extraction, ambiguous corrections, adversarial documents, fuzzy
retrieval, or storage recovery.

A useful next increment is a reviewed exporter from a real agent runtime using
the same held-out contracts and cutoff discipline, then a larger suite with
ambiguous aliases, retrieval distractors, and repeated trials. That work should
report both errors and resource measurements before making model comparisons.

### Export artifact namespace

The Python `export` helper accepts run labels as ASCII filename stems: an initial
letter or digit followed by letters, digits, `.`, `_`, or `-`. Labels are not
paths. All generated filenames, including `report.json`, `suite.json`,
`episodes.json`, runs and optional provenance sidecars, must be unique ignoring
case. This rule also applies without provenance, and prevents an export prepared
on a case-sensitive host from aliasing its own evidence on another host. Invalid
names and collisions raise `ContractError` before creating the output directory
or writing any artifact. This preflight does not promise transactional rollback
on later I/O failure; exclusive file creation still protects existing evidence.
