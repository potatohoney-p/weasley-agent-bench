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

Tests, examples, documentation, and packaging support those three outcomes. They
are not additional work units.

## Next, in priority order

1. **One real exporter with fixture parity.** Add an opt-in Assistant JSONL export
   adapter after validating stable trace fields. Acceptance: a sanitized frozen
   trace and a hand-written equivalent run produce identical evaluation reports;
   missing tool results fail export. No live API call is required.
2. **Repeated-run analysis.** Preserve task/trial pairing and bootstrap intervals
   with a fixed seed. Acceptance: fixed datasets have reproducible intervals and
   incomplete or duplicated trials are rejected. Do not rank providers from the
   current four synthetic examples.
3. **Result provenance.** Bind exports to a suite digest and runner revision.
   Acceptance: mismatched contracts or revisions are visibly rejected or flagged
   under a documented compatibility policy. Do not describe unsigned metadata as
   proof of execution.
4. **Contract lint for impossible budgets.** Detect contradictions between required
   tool counts, exact sequences, and caps before a run. Acceptance: a suite
   explicitly requesting two required calls under a cap of one fails with a path
   to the contradiction; intentionally negative tests have an explicit mode.
5. **Scalable suite ingestion.** Add bounded JSONL input and incremental reporting
   if real suite sizes justify it. Acceptance: a deterministic large fixture
   matches the current in-memory score with documented memory bounds and line
   numbers for malformed records.

## Publication status

Private publication is intended, with a minimal README base and implementation
reviewed through a Draft PR. The coordinator clarified that minimal bootstrap of
this newly authorized private repository is allowed; existing repositories'
default branches remain protected. No public publication is authorized.
