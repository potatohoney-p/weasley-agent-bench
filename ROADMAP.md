# Roadmap and acceptance criteria

## Implemented locally

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
  reference distribution, reordering does not change the report, uniformly
  duplicating every task leaves the interval unchanged, and missing/duplicated trials
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

- **Bounded JSONL evaluation:** opt-in suite/run ingestion uses bounded physical
  records and temporary disk indexes, retaining arbitrary run order and missing
  coverage. Acceptance: equivalent artifacts reproduce every legacy score row
  and canonical suite digest; malformed late records produce no report; a
  deterministic synthetic scaling harness measures memory and disk growth in
  fresh processes. This is engineering evidence, not observed real-workload demand.

- **Bounded paired JSONL comparison:** the opt-in regression gate pairs two
  complete runs using temporary disk indexes and shared legacy comparison rules.
  Acceptance: exact ordered changes/digest/flags match legacy comparison despite
  shuffled inputs; equal aggregate scores cannot hide a regression; missing or
  malformed late input emits no report; fresh-process synthetic measurements
  expose memory/disk/time tradeoffs. This extends research capability without
  establishing real-workload adoption.

- **Bounded JSONL provenance verification:** recompute canonical suite/run hashes
  from fully validated JSONL files against the existing unsigned v1 sidecar.
  Acceptance: whole-document independent hash oracles and legacy reports agree;
  mutations, duplicate identities, malformed paths, incomplete evidence and late
  framing failures emit no success; failed or incomplete valid runs retain their
  original meaning. Explicit file/sidecar bounds support offline checks without
  claiming historical execution authenticity or real-workload adoption.

- **Bounded repeated JSONL analysis:** strict batch/trial framing and disk-backed
  pass observations preserve complete paired trials, canonical suite identity and
  the legacy whole-trial bootstrap. Acceptance: exact legacy reports and an
  exhaustive synthetic reference agree; malformed/missing/duplicate observations
  emit no report; trial ordering and RNG replay are preserved; resource evidence
  distinguishes bounded memory from bootstrap work. Distinct IDs do not establish
  independent repetitions or effective sample size; inference remains conditional.

Tests, examples, documentation, and packaging support those ten outcomes. They
are not additional work units.

## Next, in priority order

1. **Recorded real-run validation of the exporter.** The opt-in weasley-subagents
   foreground adapter now has a pinned source contract and a synthetic fixture
   generated through its actual writer. Its handwritten equivalent run produces
   identical evaluation reports; missing results fail export. This is format
   parity, not a recorded real run. Remaining acceptance: a consented sanitized
   real-run artifact must validate without guessing omitted fields. No live
   provider execution or private session ingestion is enabled by this adapter.
2. **Real-workload adoption of scalable ingestion.** The opt-in JSONL path has
   synthetic parity and scaling coverage with documented bounds and line-numbered
   errors. The original condition, that real suite sizes justify adoption, remains
   unvalidated. Paired comparison is available as an opt-in research extension
   with synthetic acceptance evidence. Use consented workload measurements before
   deciding on production adoption or changes to default ingestion. Repeated-run
   JSONL analysis is available as an opt-in research extension; it does not supply
   the missing consented workload or independent-trial evidence.

## Publication status

Private publication is intended, with a minimal README base and implementation
reviewed through a Draft PR. The coordinator clarified that minimal bootstrap of
this newly authorized private repository is allowed; existing repositories'
default branches remain protected. No public publication is authorized.

## JSONL report completion lifecycle

The evaluation, paired-comparison and repeated-analysis streams release scratch
before publishing their terminal completion event. Acceptance: stopping exactly
at completion leaves no open database or scratch directory; close/removal faults
cannot publish completion; early close and CLI output failures release resources
or preserve cleanup errors. Scores and successful streams remain unchanged.
This is local output/resource integrity, not real-run validation or adoption.
