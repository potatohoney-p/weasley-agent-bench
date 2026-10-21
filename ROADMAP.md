# Roadmap and acceptance criteria

## Implemented capabilities

- **Task-contract evaluation:** structured final values, exact/subsequence tool
  outcomes, forbidden attempts, and resource caps are scored from strict versioned
  saved inputs. Missing tasks remain failures. Acceptance: the four synthetic
  examples and adversarial contract/CLI tests pass without a provider or network.
- **Paired regression comparison:** complete baseline/candidate runs are paired
  by ID, with explicit improvements/regressions and only jointly observed usage
  deltas. Acceptance: swapped failures with equal aggregate pass counts still
  fail the gate; missing cases cannot be compared; repeat reports are identical.
- **Cross-session memory benchmark:** export a synthetic event/question suite and
  three deterministic baselines with correction, retraction, restoration and
  provenance checks. Acceptance: no future-label leakage, 48 reproducible held-out
  questions, baseline scores 19/24/48 of 48, exported runs reproduce scores through
  the public CLI, and optional measured lookup timing stays separate from scoring.
- **Repeated-run analysis:** complete runs pair by trial/task ID; bootstrap
  resampling retains the whole paired suite trial and uses a fixed seed.
  Acceptance: the synthetic fixture's counts/interval match an exhaustive
  reference distribution, reordering does not change the report, duplicated
  correlated tasks do not narrow the interval, and missing/duplicated trials
  or incomplete runs fail. Intervals are conditional on the fixed suite and
  independent representative trial pairs, not provider rankings.
- **Optional artifact provenance:** separate versioned sidecars bind one suite
  and saved run to canonical digests and a declared runner revision. Acceptance:
  swapped/changed inputs and explicit revision mismatches fail verification;
  synthetic exporter sidecars roundtrip; legacy scoring and intentional
  rescoring remain unchanged. Hashes establish consistency, not authenticity
  or proof of actual execution.

- **Static tool-budget lint:** a separate opt-in gate detects required call
  counts exceeding inclusive caps for exact and subsequence contracts.
  Acceptance: structured paths identify both sides, intentional negative
  fixtures require explicit allowance without hiding findings, malformed
  schemas still fail, and legacy scoring outputs remain unchanged. This is
  not a general satisfiability or resource-cost model.

Tests, examples, documentation, and packaging support those six outcomes. They
are not additional work units.

## Next, in priority order

1. **Recorded real-run validation of the exporter.** The opt-in weasley-subagents
   foreground adapter now has a pinned source contract and a synthetic fixture
   generated through its actual writer. Its handwritten equivalent run produces
   identical evaluation reports; missing results fail export. This is format
   parity, not a recorded real run. Remaining acceptance: a consented sanitized
   real-run artifact must validate without guessing omitted fields. No live
   provider execution or private session ingestion is enabled by this adapter.
2. **Scalable suite ingestion.** Add bounded JSONL input and incremental reporting
   if real suite sizes justify it. Acceptance: a deterministic large fixture
   matches the current in-memory score with documented memory bounds and line
   numbers for malformed records.

## Publication status

The implementation was merged through [PR #1](https://github.com/potatohoney-p/weasley-agent-bench/pull/1)
and the repository was made public on 2026-10-21 under the [MIT License](LICENSE).
The capabilities above are available on the default branch. Included examples
remain synthetic; publication does not establish real-model performance.
