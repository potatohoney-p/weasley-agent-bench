# Offline weasley-subagents transcript adapter

This opt-in adapter converts a **saved text projection** from one successful
foreground child run into one benchmark result. It does not execute agents,
models or tools. It checks consistency, not authenticity. A forged but internally
consistent transcript can pass. Export success is not benchmark success.

## Pinned source contract

The inspected upstream revision is
[`72901e3f17eea4d56418b24e7a63f10c7ae22375`](https://github.com/potatohoney-p/weasley-subagents/commit/72901e3f17eea4d56418b24e7a63f10c7ae22375):

- [`src/shared/child-transcript.ts`](https://github.com/potatohoney-p/weasley-subagents/blob/72901e3f17eea4d56418b24e7a63f10c7ae22375/src/shared/child-transcript.ts), `createChildTranscriptWriter`, emits artifact version 1.
- [`src/runs/foreground/execution.ts`](https://github.com/potatohoney-p/weasley-subagents/blob/72901e3f17eea4d56418b24e7a63f10c7ae22375/src/runs/foreground/execution.ts), `persistSingleResultMetadata`, emits the companion metadata. The same file aggregates usage/duration with zero defaults.
- [`src/shared/utils.ts`](https://github.com/potatohoney-p/weasley-subagents/blob/72901e3f17eea4d56418b24e7a63f10c7ae22375/src/shared/utils.ts), `extractTextFromContent`, joins text blocks with newlines.

This is not the unversioned raw child stdout JSONL format. Version 1 alone does
not guarantee future upstream compatibility; review source changes before
expanding this supported subset.

## Mapping and completeness boundary

The caller explicitly supplies the benchmark task ID and run ID. The adapter
validates the task exists but does not claim that prompt equivalence was checked.
Other suite tasks remain missing under the existing evaluator semantics.

- Every record must have version 1, source `foreground`, the same runId/agent as
  metadata, and one consistent optional nonnegative childIndex.
- Metadata must explicitly report exitCode 0, one successful modelAttempts entry,
  toolCount, and no error, transcriptError or processSignal.
- Exactly one initial user prompt must be first. Assistant `toolUse` messages
  announce unique toolCall IDs, names and JSON object arguments. Each call must
  subsequently have one matching tool_start with complete JSON argsPayload,
  one tool_end and one toolResult message with explicit matching boolean isError.
  Result/end may arrive in either order, but only after start. All calls must
  complete before the terminal assistant message; toolCount must match.
- Calls are exported in **tool_start order**, never completion order. True
  isError becomes `error`, false becomes `success`. An overall completed run
  may still contain a failed tool call; the evaluator decides whether it passes.
- Final output is the sole terminal assistant `stop` message. Earlier assistant
  messages must use `toolUse`; multiple `stop` messages or any record after the
  terminal message fail instead of silently choosing an ambiguous last output.
  Only text blocks are accepted for final output and joined with newline.
- Tool output is the writer's saved text projection, including an explicitly
  empty content array as the empty string. The upstream writer can discard
  nontext content and whitespace-only output before serialization. The adapter
  cannot reconstruct or certify those original payloads; do not use this format
  for multimodal or whitespace-sensitive tool-output evaluation. Even nonempty
  saved text does not prove the original result had no other content.
- JSON-looking final/output text remains a string, not an invented parsed object.
  Use the empty JSON Pointer (`""`) to assert the whole text value.
- No usage fields are emitted. Upstream normalized usage and aggregated duration
  can substitute zero for missing measurements. Omitting them preserves unknown
  telemetry; a suite requiring usage budgets will fail visibly rather than pass
  on fabricated zero cost. No wall-clock duration is inferred from timestamps.

Unsupported or incomplete artifacts fail closed: async streams, multiple model
attempts/fallbacks, nonzero exits, errors/signals, unknown record types, raw stdout
or stderr records, explicit truncation or payload truncation markers, missing or
duplicate lifecycle records, ambiguous outcomes, unsupported content, or stop
reasons other than `toolUse` and terminal `stop`. Completion here is limited to
this recorded lifecycle and metadata consistency, not proof that original events
were never omitted or that a real model/tool ran.

JSONL must be UTF-8, newline-terminated, nonempty, with no blank lines, duplicate
JSON keys or nonfinite JSON constants. Input is capped at 50 MiB before parsing;
this is a bounded whole-document parser, not incremental ingestion. Parser errors
never echo transcript payloads. Existing scoring/provenance behavior is unchanged.

## Reproducible synthetic fixture

Everything in `examples/subagents/` is **synthetic**, not a captured real run,
performance result, or evidence of actual execution. The transcript is generated
through the actual pinned upstream writer from handwritten synthetic events.
Metadata is handwritten to its inspected emitted shape; it is not a metadata
capture. The suite and expected run are independently handwritten.

With an already available checkout of the upstream source and its normal build
requirements, regenerate without network or live provider calls. The generator
also asserts the pinned runtime finalOutput difference described below:

```sh
node --experimental-strip-types scripts/generate_subagents_fixture.mjs /path/to/weasley-subagents
```

The generator checks relevant tracked source against the exact revision before
importing the writer, uses a synthetic path and fixed zero timestamp, and runs no
model or tool. Node 24 was used for fixture generation. Do not point it at session
logs or private service data. Normal Python tests need neither Node nor upstream.

```sh
python -m weasley_agent_bench export-subagents examples/subagents/suite.json \
  examples/subagents/synthetic.transcript.jsonl examples/subagents/synthetic.metadata.json \
  --task-id read-note --run-id synthetic-export > /tmp/subagents-run.json
python -m weasley_agent_bench evaluate examples/subagents/suite.json /tmp/subagents-run.json
python -m unittest discover -s tests -q
```

The exported run must equal `expected-run.json`, and both evaluation reports must
match exactly. Missing lifecycle records, contradictory metadata, truncation,
unsupported final content and completion reordering are tested separately.
A genuinely recorded, consented, sanitized real-run validation remains future
work; this fixture establishes format parity only.

## Synthetic subprocess integration

A second opt-in test now drives the pinned upstream `runSync` entry point. A
small **synthetic replacement child**, not Pi or an LLM, runs as a real local
subprocess and reads a newly created synthetic note from a temporary workspace.
The harness itself parses its stdout and writes both transcript and metadata;
the test does not handwrite either artifact. The exporter and CLI then consume
those generated artifacts and compare against an independently specified result.

On POSIX with Node 24, Python 3.11+, and a clean upstream checkout at the exact
revision above, install that checkout's locked dependencies (`npm ci
--ignore-scripts --no-audit --no-fund`), then run:

```sh
WEASLEY_SUBAGENTS_TEST_SOURCE=/absolute/path/to/pinned/weasley-subagents \
  python -m unittest discover -s tests -p test_subagents_runtime.py -v
```

The dedicated Ubuntu CI job performs this test separately from the normal
Python-only tests. Dependency installation needs network access, but the test
uses no provider, credentials, model or private sessions. It isolates HOME and
agent settings, clears inherited environment variables except OS process-launch
paths, and points the runtime only at the generated child. This is test isolation,
not a security sandbox for arbitrary upstream code or dependencies. The checkout
and its dependencies must be trusted. The source pin/clean-source checks prevent
silently testing a modified runtime; they do not authenticate the dependency
installation. The existing Apache-2.0 provenance and NOTICE obligations apply.

Five assertion groups cover generated metadata/CLI parity, repeatable benchmark
projection despite varying raw timestamps/paths, a real child exit code 7, a
missing tool-result event despite exit code 0, and a mismatched generated run ID.
Unknown usage remains omitted, even though upstream metadata normalizes it to
zeros. Temporary raw artifacts are deleted after each capture, not committed.
The synthetic child emits a fixed protocol and performs a controlled file read;
this validates integration with the harness's persistence path, **not** real Pi
tool execution, provider behavior, untrusted-process containment, or real model
performance. Recorded, consented real-model validation remains future work.

### Deliberately distinct from runtime finalOutput

The final field here represents the terminal transcript message's joined text,
not the harness runtime `getFinalOutput`/`finalOutput` convenience value. That
runtime helper usually selects the last nonblank text block and has special
acceptance-report handling across messages. For the synthetic two-block fixture,
this adapter exports `synthetic\nnote`; the ordinary runtime helper would return
`note`. This documented raw-transcript mapping does not reproduce runtime
acceptance-report semantics. Use a different explicit adapter contract if runtime
finalOutput equivalence is required.

Additional metadata fields (task, usage, duration, transcriptPath, timestamps,
contracts, acceptance/effects and audit information) are not imported as benchmark
facts or checked for equivalence. Task assignment is explicit at the CLI; metadata
`task` can differ from the expanded initial prompt. Their presence does not add
trust. Only the lifecycle and identity fields described above are validated.

## Third-party provenance

The optional fixture generator imports an external checkout of
[weasley-subagents at 72901e3](https://github.com/potatohoney-p/weasley-subagents/tree/72901e3f17eea4d56418b24e7a63f10c7ae22375).
That upstream project retains its
[Apache-2.0 LICENSE](https://github.com/potatohoney-p/weasley-subagents/blob/72901e3f17eea4d56418b24e7a63f10c7ae22375/LICENSE)
and [NOTICE](https://github.com/potatohoney-p/weasley-subagents/blob/72901e3f17eea4d56418b24e7a63f10c7ae22375/NOTICE).
No upstream source is vendored here. This repository's MIT license does not
relicense that external source; preserve its applicable notices when using or
redistributing it. The checked-in fixture is synthetic generated output, not a
recorded model run.
