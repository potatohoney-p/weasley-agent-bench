# Offline provenance verification for JSONL artifacts

`verify-provenance-jsonl` checks the existing unsigned v1 sidecar against a
supplied suite JSONL file and saved run JSONL file. It recomputes both digests
from the files. Matching IDs alone, or a caller's assertion about a file hash,
cannot satisfy verification. The result establishes consistency with the
sidecar, not authenticity, model execution, or task success.

## Reproduce a synthetic check

The existing JSON and JSONL examples represent the same documents. Record a
sidecar from the JSON examples, then check the JSONL representation:

```bash
python -m weasley_agent_bench create-provenance examples/suite.json examples/candidate.json --runner-revision synthetic-example-r1 > candidate.provenance.json
python -m weasley_agent_bench verify-provenance-jsonl examples/jsonl/suite.jsonl examples/jsonl/candidate.jsonl candidate.provenance.json --expected-runner-revision synthetic-example-r1
```

This uses hand-authored synthetic data and no provider, network, or artifact
execution. It does not recover historical provenance: a sidecar created today
only binds the inputs supplied today. Existing exporters can retain their v1
sidecars when converting saved JSON documents to the equivalent JSONL transport.
There is no new manifest schema or JSONL sidecar creation command.

On success the command exits **0** and emits one ordinary JSON object with
exactly the same fields as `verify-provenance`: the six sidecar fields,
`consistent: true`, and `runner_revision_check` (`matched` or `not_requested`).
This is not a report-event stream. Require a complete JSON object and successful
exit. Invalid artifacts, mismatches, read failures, and exhausted temporary
storage exit **2**, emit a JSON error on stderr, and emit no stdout report.
Success is returned only after input validation and temporary storage cleanup.

## Exactly what is checked

Both files must satisfy the existing [JSONL protocol](jsonl-evaluation.md):
strict versioned headers, existing v1 task/result contracts, bounded UTF-8 JSON
records, unique IDs, known result task IDs, an exact end count, and no trailing
records. The suite cannot be empty. A run can contain failed/cancelled results,
omit tasks, or be empty, as in legacy provenance verification. Such a run may be
consistent with its sidecar while failing evaluation or being unsuitable for
comparison. A missing file, missing digest, or truncated stream is never treated
as partial successful verification.

The sidecar must be the exact six-field [v1 object](provenance.md), including
both lowercase SHA-256 strings. Its suite and run IDs must match the files.
`--expected-runner-revision`, when supplied, must match the declared label.
That label is never resolved to a checkout or independently authenticated.

The digests cover the equivalent canonical **v1 JSON documents**, using Python
`json.dumps(sort_keys=True, ensure_ascii=True, separators=(",", ":"),
allow_nan=False)` encoded as UTF-8. This is not RFC 8785 and not raw file hashing.
The exact reconstructed root members in sorted order are:

- Suite: `schema_version`, `suite_id`, `tasks` in original input order.
- Run: `results` in original input order, `run_id`, `schema_version`, `suite_id`.

All task/result content participates, including prompts, expectations, tags,
budgets, statuses, calls, outputs, final values and recorded usage. JSONL
`format`, `format_version`, and end markers are validated transport framing,
not members of these v1 documents. LF/CRLF, trailing newline presence, JSON
whitespace, object-key order and Unicode escape spelling do not affect digests.
Array order, parsed `1` versus `1.0`, and Unicode normalization differences do.
In particular, reordering run records invalidates an old sidecar even when
scores remain identical. SQLite's index order never determines either digest.

Checking an original retained sidecar can detect a changed historical suite
even if its `suite_id` is reused. The verifier cannot establish that the sidecar
was recorded at execution time. Anyone who can replace the artifacts and
sidecar can produce a new consistent set. Retain trusted originals externally.

## Filesystem and resource boundaries

Only the three explicit caller paths are input files. Sidecars contain no path
or URL fields; extra fields are rejected before suite/run files are read.
Strings inside tasks/results are data and are never interpreted as paths,
commands, or URLs. No manifest path resolution, remote fetching, or artifact
execution occurs. Caller-selected paths use ordinary local filesystem semantics
(including symlinks); this API is not a filesystem containment layer. Callers
must choose the files and temporary directory they intend to access.

`--max-record-bytes` bounds each physical JSONL line including newline bytes;
`--max-sidecar-bytes` separately bounds the entire sidecar document, including
whitespace. Both default to **1,048,576 bytes** and must be positive integers
below `sys.maxsize`. Pretty-printed sidecars are supported. Strict UTF-8, duplicate
JSON key, non-finite number and nesting/parser validation apply to both inputs.
These new file-boundary errors do not echo input paths, payloads or unknown keys.
Successful reports intentionally contain IDs and the declared revision label.

Temporary SQLite indexes retain suite/result records and validate identities.
Temporary disk grows with input size; sufficient writable space is required.
`--temporary-directory` chooses its parent. The page cache target is 2 MiB;
working memory scales with the largest permitted record, sidecar and cache,
not the number of tasks. Parser copies, Python/SQLite overhead and the OS file
cache mean these are not a fixed process-RSS ceiling. Verification hashes and
validates but does not score records. It does not impose a total input/disk/time
quota. Resource exhaustion remains an error, not partial success.

Files are read sequentially, not as an atomic snapshot. Keep inputs stable
during verification and any later scoring. The verifier does not lock files or
guarantee that a later command reopening the same path reads the same bytes.

```python
from weasley_agent_bench import verify_provenance_jsonl

report = verify_provenance_jsonl(
    "suite.jsonl", "run.jsonl", "run.provenance.json",
    expected_runner_revision="synthetic-example-r1",
    max_record_bytes=1024 * 1024, max_sidecar_bytes=1024 * 1024,
    temporary_directory="scratch",
)
assert report["consistent"]
```

Legacy `verify-provenance` continues accepting whole JSON documents. Evaluation
and comparison still permit intentional rescoring and do not automatically
verify sidecars. Before paired JSONL comparison, verify each run separately
against the same intended suite and its own retained sidecar. Repeated-run
JSONL analysis and independent execution attestation remain outside this feature.

## Reproducible synthetic resource observations

```bash
python -m experiments.jsonl_provenance_scaling --tasks 500 5000 20000 --output-bytes 4096
```

The harness generates equivalent saved formats and an independent whole-document
`json.dumps`/`hashlib` sidecar in a separate process. Each verification mode then
runs in a fresh child; complete reports must be exactly equal. Generation and
process startup are excluded from timings, and generation is excluded from
worker RSS. Timings include input loading, validation, hashing, Python tracing
and a scratch sampler. Each case is one observation, legacy first.

[Recorded Linux/WSL Python 3.12.3 results](measurements/jsonl-provenance-linux.json)
include commands, platform, byte counts, digests, reports and method details:

| Tasks | Output bytes/task | Process peak MiB legacy / JSONL | Seconds legacy / JSONL | JSONL Python peak KiB |
| ---: | ---: | ---: | ---: | ---: |
| 500 | 4,096 | 28.1 / 23.5 | 0.073 / 0.211 | 81.0 |
| 5,000 | 4,096 | 110.4 / 22.5 | 0.784 / 1.573 | 79.1 |
| 20,000 | 4,096 | 393.1 / 22.2 | 3.443 / 5.844 | 77.7 |

All three report comparisons matched. The largest JSONL scratch observation was
92.4 MiB. Scratch is sampled every 10 ms and measures logical file sizes, so it
is a lower bound on peak space and excludes fixtures. `tracemalloc` excludes
native SQLite allocations; process RSS includes them, imports and tracing.
Process RSS is unavailable in this harness on Windows. These selected synthetic
cases demonstrate a memory/disk/time tradeoff, not a universal memory ceiling,
speed guarantee, model-performance result, or observed real-workload demand.
