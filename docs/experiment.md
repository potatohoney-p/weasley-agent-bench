# Fault-matrix experiment

**Question:** Which failures remain invisible if a benchmark validates only
structured final state, input shape, completion, and coverage?

**Method:** Start with the four passing, hand-authored synthetic results in
`examples/candidate.json`. Apply one deliberate mutation at a time. Evaluate
each mutated run under the full suite and under an ablation with every tool
and budget expectation removed. Retain the same strict input validation,
required completion status, final assertions, and missing-case accounting in
both arms. The unmodified run is a negative control.

Run from source, offline:

```bash
python -m experiments.fault_matrix
```

| Injected fault | Full contract rejects | Final-state ablation rejects |
| --- | --- | --- |
| Superseded final fact | Yes | Yes |
| Lost child finding | Yes | Yes |
| Failed recall with plausible final answer | Yes | No |
| Wrong recall arguments | Yes | No |
| Missing retry | Yes | No |
| Reversed retry | Yes | No |
| Forbidden send attempt returning error | Yes | No |
| Token cap exceeded | Yes | No |
| Capped usage omitted | Yes | No |
| Cancelled run with correct final value | Yes | Yes |
| Missing task result | Yes | Yes |
| Duplicate task result | Yes, invalid input | Yes, invalid input |
| Valid control | No | No |

Expected measured totals are **12/12** selected faults detected by the full
contract and **5/12** by the ablation; neither rejects the control. The experiment
has a deterministic test and exits nonzero if the complete contract disagrees
with the declared expected outcomes. Its JSON includes the suite digest.

## Interpretation and limits

Final-state checks alone cannot determine whether a plausible answer followed
the expected tool process or consumed an acceptable resource budget. The added
contract makes those requirements explicit and testable from a saved result.

These faults were selected to exercise known invariants, so the ratios are
coverage descriptions rather than statistical accuracy or benchmark scores.
All results and telemetry are synthetic. The benchmark trusts the exporting
runner and does not independently observe execution. No real model, DeepSeek
API, paid evaluation, or production trace was used.

## Design tradeoffs

- Exact JSON assertions are reproducible and explainable, but intentionally
  reject equivalent paraphrases. Tasks should ask for structured output.
- Exact tool sequences work for constrained workflows. Subsequence contracts
  permit extra reasoning/tool work while retaining required order and checks.
  Cap total calls or forbid tools when intervening calls matter.
- Paired gates prevent aggregate gains from hiding a specific regression.
  Unchanged failures do not count as new regressions and remain visible.
- Standard-library-only source execution avoids runtime package/provider drift;
  adapters and statistical repeated-run analysis are deferred until real inputs
  establish their requirements.
