# Static tool-budget lint

`lint-suite` checks a validated suite before a runner is invoked. It is an
opt-in static gate, separate from evaluation, provenance, and repeated-run
analysis. No saved run, model, tool execution, or network is needed.

```bash
python -m weasley_agent_bench lint-suite examples/suite.json
python -m weasley_agent_bench lint-suite examples/impossible-tool-budget.json
```

The first command exits 0. The intentionally impossible synthetic fixture exits
1: it requires two distinct calls but permits at most one. Its issue identifies
both `suite.tasks[0].budget.max_tool_calls` and
`suite.tasks[0].expect.tools.steps`, with the required count and configured cap.
Reports use index-based paths and do not echo task IDs, prompts, tool names,
arguments or expected values. The suite digest still identifies the exact
supplied document, including its contents. Existing malformed-input error
messages may include paths or unknown field names; this is not a global
content-free error policy.

## Exact detection boundary

The versioned rule set `tool-budget-v1` detects only:

    number of required tool steps > max_tool_calls

Both exact and subsequence steps require distinct recorded calls. Repeated tool
names, required errors and `any` outcomes still each consume one call. For
subsequences, extra calls can increase cost but cannot reduce the required
minimum. The cap is inclusive; equality passes. An exact empty sequence and a
zero-call cap are compatible. No required tool contract means no positive
call-count lower bound; no call cap means this rule cannot find a conflict.

Token and duration caps do not declare positive minimum usage per call. This
linter therefore does not infer token cost, duration, billing, timeout or
provider feasibility. A clean report means **no contradiction found by this
rule**, not that the whole suite is satisfiable. Conflicting final assertions
or impossible tool argument predicates are outside this rule. Existing schema
validation still rejects invalid inputs, including required-and-forbidden tools.

## Intentional negative fixtures

```bash
python -m weasley_agent_bench lint-suite examples/impossible-tool-budget.json --allow-contradictions
```

This explicit mode exits 0 while retaining all issues and `passed: false`.
`accepted: true` means the caller explicitly allowed detected contradictions;
it must not be read as a passing lint result. The flag never bypasses schema
validation: malformed documents still exit 2. No suite field, budget, or
expectation is changed. Without the flag, `accepted` equals `passed`.

```python
from weasley_agent_bench import lint_suite, load_document

report = lint_suite(load_document("examples/impossible-tool-budget.json"))
assert not report["passed"] and not report["accepted"]
```

The keyword `allow_contradictions` accepts only a Python boolean. Reports are
deterministic, contain the canonical suite digest, list findings in suite order,
and identify the rule set so future additional rules can be distinguished.
Exit 0 means policy acceptance, 1 a detected unallowed contradiction, and 2
invalid input or command usage. This new command does not alter the existing
`evaluate`, `compare`, `compare-repeated`, or provenance commands. Those remain
usable for intentional negative contracts and deliberate rescoring.

## Evidence

Acceptance tests cover exact/subsequence cardinalities and boundary caps,
repeated names and error outcomes, all findings, deterministic reports, explicit
allow mode, schema failures, CLI exit behavior, input immutability and legacy
evaluation. An exhaustive small synthetic grid checks the lint rule against
the independently implemented evaluator over possible call counts. These are
contract-logic tests, not estimates of real-world issue-detection rates.
