# Repeated runs on a fixed suite

`compare-repeated` describes variation across saved, paired whole-suite trials.
It does not execute a runner or rank providers. The bundled three-task,
four-trial fixture is hand-authored and synthetic, not an experiment with a
model. It exists to check the calculation and its failure modes.

The separate [`compare-repeated-jsonl`](jsonl-repeated.md) command accepts bounded
suite/batch records and emits equivalent trial/task rows plus a required terminal
summary. It uses the same statistics and assumptions, with disk-backed identities
and observations. The legacy JSON format and report remain unchanged.

```bash
python -m weasley_agent_bench compare-repeated examples/repeated/suite.json examples/repeated/baseline.json examples/repeated/candidate.json --seed 17
```

The fixture has baseline pass rate `7/12`, candidate pass rate `8/12`, and a
candidate-minus-baseline difference of `1/12` (about 8.33 percentage points).
The 95% paired bootstrap interval is `[-0.25, 0.5]`. Task 1 still has three
paired regressions and one improvement. A positive aggregate difference does
not erase those regressions or establish superiority.

## What is estimated?

The estimand is the mean candidate-minus-baseline pass-rate change **on the
supplied, fixed task suite**, over repeated paired trials of that suite.
Every task has equal weight; every trial has equal weight. Let `T` be the number
of tasks and `N` the number of trial pairs. If `d_i` is candidate passes minus
baseline passes for trial `i`, the observed difference is:

`sum(d_i) / (N * T)`

Each explicit `trial_id` pairs one baseline run and one candidate run. The
runner must give those IDs a meaningful pairing, for example shared external
conditions and a prespecified trial plan. IDs alone do not prove matched
conditions. **The resampling unit is one whole trial pair**, including both
conditions and every task. Tasks are not resampled and the `N * T` individual
results are not treated as independent samples.

The interpretation assumes independent, representative trial pairs and
consistent execution conditions. Correlations between tasks and between the
two conditions within each pair are retained. Dependence between separate
trials, selection of favorable runs, or invented trial IDs invalidate that
interpretation; the evaluator cannot detect these from results alone.

## Algorithm and reproducibility

1. Validate both batches and rescore every run against the same supplied suite.
2. Sort trial IDs lexicographically. Pair tasks by their IDs through the normal
   evaluator, which reports them in suite order.
3. Initialize a local `random.Random(seed)`. For each of `B` resamples, draw
   `N` trial indices with replacement using `randrange(N)` and sum their `d_i`.
4. Sort the integer bootstrap totals. For probability `p`, use index
   `ceil(p * B) - 1`, with no interpolation. Divide the 2.5th and 97.5th
   percentile totals by `N * T` to report the interval.

The default seed is `0`; the default resample count is `10000`. The seed must
be an integer in `[0, 2^64 - 1]`; resamples must be an integer in
`[100, 100000]`. Booleans, fractions, and non-finite numbers are invalid.
The lower resample limit is an input guard, not a guarantee of stable quantiles.
More resamples reduce Monte Carlo error, not sampling bias or the need for more
independent trials. Runtime is `O(B * N)` in addition to scoring; bootstrap
storage is `O(B)`.

Fixed inputs, options, and Python RNG behavior give identical reports with no
timestamps. Permuting batch trials or run result order does not change the
report. Tests pin the fixture's output in the supported CI Python versions.
The algorithm records its RNG name and method version; Python does not promise
that every `randrange` implementation will remain identical across all future
versions. Preserve inputs, options, and Python version for archival replay.
Global random state and input documents are not changed.

## Interpretation limits

- This is a percentile bootstrap interval, not a p-value, significance test,
  automatic regression gate, or provider ranking. The nominal 95% level does
  not guarantee 95% repeated-sampling coverage, especially with few trials.
- It describes repeated-trial uncertainty conditional on this fixed suite.
  It says nothing about generalization to new tasks or a wider user population.
- At least two complete trial pairs are required. Even then, few trials and
  discrete pass/fail outcomes can give unstable or degenerate intervals. Two
  trials are a technical minimum, not a recommendation for inference.
- A zero-width interval can occur for identical observed differences. It does
  not establish zero real uncertainty or that a candidate will always pass.
- All results are **rescored** under the supplied suite. Matching `suite_id`
  values and the report's suite digest do not prove which contract the original
  runner executed. Optional single-run sidecars can check artifact consistency;
  [provenance verification](provenance.md) remains separate from this analysis
  and does not authenticate execution.
- Saved outcomes are trusted declarations. No model calls, measured provider
  performance, causality claims, or real user/company data are involved.

For a strict single-run regression gate, use `compare`. For this descriptive
command, exit `0` means valid input and successful analysis even if the
observed change or the entire interval is negative. Exit `2` means invalid
input. The report intentionally has no `passed` or `no_regressions` field.
