# Historical tau2 observation fidelity

This offline adapter checks whether selected observations survive conversion.
It does **not** evaluate tau2 task success, policy compliance, model quality,
execution authenticity, billing, real-human transfer or recovery. The expected
answer is a frozen record of what was observed, not an answer judged correct.

The [TraceReplay adapter](https://github.com/potatohoney-p/weasley-trace-replay/pull/8)
at `a784f601aa0d994ab1904f215535eeac2416be69` preserves native call outcome and
output hashes but leaves argument-only and final-text-only drift invisible to
replay comparison. AgentBench's existing exact tool and final-value assertions
can check these observations. No evaluator schema or scoring rule is changed.

## Reviewed historical source

Only simulation `bfaceb85-d727-4de5-b37c-e31fdf90fb39`, task `46`, trial `0`, seed
`626729` is used. It is a recorded model interaction in a simulated benchmark,
not customer traffic and not a new model run. The
[original artifact](https://github.com/sierra-research/tau2-bench/blob/05736c27219b769d0be507574dd3135010916644/data/tau2/results/final/o4-mini-2025-04-16_airline_default_gpt-4.1-2025-04-14_4trials.json)
at revision `05736c27219b769d0be507574dd3135010916644` contains 7,599,444 bytes,
SHA-256 `6a3a354c9bce42b52905af096e9cea6f555548acbb17b567fd403c356df7b4ec`.
The [publication note](https://github.com/sierra-research/tau2-bench/blob/05736c27219b769d0be507574dd3135010916644/data/tau2/results/final/README.md)
identifies these as original June 2025 paper results. Airline tasks predate later
fixes; this is not a current leaderboard or task-correctness claim. The declared
original runner revision is unavailable for independent execution reproduction.

The inspected [minimized derivative](../examples/tau2/task46-trial0.json) is copied
from the pinned TraceReplay adapter with [Sierra Research's full MIT notice](../examples/tau2/LICENSE).
It retains six messages and one paired call/result. No customer identifiers or
credentials were observed in this selected derivative. The complete archive,
separate task object, other episodes, duplicated `raw_data`, reward explanations,
costs and timings are not vendored. This is reviewed minimization, not a generic
anonymization guarantee. The [source ledger](../examples/tau2/source.json) records
all 23 omitted subtrees and the distinct original-row hash protocols.

Source verification recomputed the pinned archive hash in memory, selected the
unique episode, checked both row hashes and reproduced the minimized fixture and
omission ledger. The archive and full row were not saved. The existing
[pinned source verifier](https://github.com/potatohoney-p/weasley-trace-replay/blob/a784f601aa0d994ab1904f215535eeac2416be69/demos/tau2_source.py)
can reproduce the source check with an explicit pinned fetch or local archive.
Default AgentBench execution never fetches URLs. Hash agreement establishes file
consistency, not that the reported model interaction occurred as claimed.
The [recorded source check](evidence/tau2-source-verification.json) retains only
identity/verification metadata; the [offline fidelity report](evidence/tau2-fidelity.json)
is reproduced by the default command below.

## Run the offline workflow

From this checkout with Python 3.11+:

```sh
python -m experiments.tau2_fidelity
python -m unittest discover -s tests -p test_tau2_fidelity.py -v
```

The experiment exports one ordinary v1 run, evaluates it through both legacy JSON
and bounded JSONL, requires the JSONL terminal summary, and compares the entire
report. Output includes the report, canonical input/suite/run hashes, conversion
losses, derived-field definitions and explicit interpretation flags. The eight
passing checks concern one recorded observation contract, not eight benchmark
tasks or eight independent measurements.

To examine a deliberately modified local derivative:

```sh
python -m experiments.tau2_fidelity --episode /path/to/candidate.json
```

Exit 0 means fidelity passed; exit 1 means a structurally supported candidate
differs from the frozen observations; exit 2 means malformed/unsupported input
or evaluation failure, with no stdout report. Candidate inputs never regenerate
expectations. The reviewed suite has its own pinned canonical digest. The default
fixture is also checked against its pinned canonical digest. An explicit candidate
may differ; `matches_reviewed_fixture` reports digest equality separately from
fidelity and does not authenticate its source.

```python
from weasley_agent_bench import evaluate, load_document
from weasley_agent_bench.tau2 import export_tau2_fidelity, load_tau2_episode

suite = load_document("examples/tau2/fidelity-suite.json")
bundle = export_tau2_fidelity(
    suite, load_tau2_episode("examples/tau2/task46-trial0.json"),
    task_id="historical-observations", run_id="recorded-reference",
)
report = evaluate(suite, bundle["run"])
# Preserve bundle["provenance"], ["losses"] and ["derived"] with the report.
```

This workflow runs from source; experiment fixtures are not installed package
data. The library adapter itself requires only the standard library.

## Supported minimized profile and observation mapping

The profile `tau2-minimized-single-call-transfer-fidelity-v1` accepts exactly the
roles assistant, user, assistant-with-call, tool, assistant, user, with ordered
integer turn indices 0–5. It accepts one assistant-owned call and matching result,
explicit boolean `error`, exact saved text output, and final user signal
`###TRANSFER###` with `termination_reason: user_stop`. Earlier stop/transfer/
out-of-scope signals, extra/missing/overlapping calls, mixed text/call turns,
unknown fields, unmatched IDs and absent/ambiguous outcomes fail. Full native
rows containing metadata are rejected: callers must supply the reviewed minimized
shape. This is one validated historical profile, not general tau2 coverage.

| Source observation | Run representation / fidelity rule |
| --- | --- |
| Native name and argument object | Ordinary tool fields; one exact tool step with root argument assertion |
| Explicit result `error` | `false` → `success`, `true` → `error`; never infer from output wording |
| Tool `content` | Exact saved string, including whitespace; never parse JSON-looking output |
| Final assistant text, turn 4 | `final.assistant_text`; exact string assertion |
| Native episode/task/trial/seed | `final.episode_identity`; exact identity assertion and strict integer validation |
| Paired native call ID | `final.call_id`; unmodified, exact assertion |
| Argument representation | `final.arguments_sha256`; recomputed canonical hash of candidate arguments |
| First assistant greeting and user request | Not scored; full input digest changes, and losses name both paths |
| Roles, indices, ownership, null call lists and terminal marker | Profile validation; no general conversational alignment or replay events |
| All participant usage | Omitted, even when present; observed/missing assistant turns are listed |

The `final` object is a **derived observation envelope**, not a native structured
answer. The suite prompt is an adapter-check instruction, not tau2's original
task prompt. Caller task/run labels identify the derived artifacts; native source
identity remains inside the envelope. `completed` means the supported recorded
conversation closed, including a well-formed native tool error. Existing report
wording about completion is interpreted within this fidelity task only.

Canonical hashes use Python JSON with sorted object keys, `ensure_ascii=True`,
compact separators, `allow_nan=False`, UTF-8 bytes and no newline. Array order
and parsed integer/float representations are retained. Object key order and JSON
serialization whitespace are ignored; string whitespace and Unicode are not
normalized. Existing JSON equality treats `1` and `1.0` as equal; the separately
asserted candidate argument digest distinguishes them without changing legacy
scoring. A hash only binds that representation. Tool outputs remain text, so
`"1"` and `"1.0"` already differ through ordinary string equality.

The loader bounds the whole derivative to 256 KiB and rejects duplicate JSON
keys, nonfinite values, invalid UTF-8 and malformed paths without echoing content.
Its optional lower byte limit cannot disable the ceiling. In-memory API callers
own allocation limits. Deep values exceeding Python conversion recursion limits
fail with a content-free contract error; the byte ceiling is not a nesting guarantee.
JSONL's existing record and temporary-disk rules still
apply; the tiny historical episode provides compatibility evidence, not evidence
that large real workloads justify scalable ingestion.

## Acceptance and limits

Original observations match an independent literal run oracle and both complete
transport reports. Deliberate argument/output/final-text changes export but fail
the relevant frozen assertions. A consistent call-ID rename fails the identity
assertion; a mismatched result ID is rejected before export. Malformed native
outcomes never become guessed success. Missing result evidence stays visible in
both evaluators. Tests also cover canonical reordering, integer/float argument
identity, exact byte bounds, strict terminals, immutable expectations, omitted
usage and content-free CLI errors. Mutated cases are synthetic fault injections
derived from this one historical record, not additional historical episodes.

Input/suite/run hashes and the source ledger are unsigned consistency evidence.
Initial dialogue can change while these selected fidelity checks still pass; its
input hash changes and the loss ledger explains the gap. The adapter does not
reconstruct the original task or runner, score reward/policy correctness, infer
missing usage as zero, or execute any recorded tool. The separate roadmap item
for a consented recorded weasley-subagents exporter remains open.
