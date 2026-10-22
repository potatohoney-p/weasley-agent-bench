# Optional artifact provenance sidecars

An evaluation report identifies the suite supplied **at scoring time**. It does
not establish which suite produced a saved run. Intentional rescoring against a
changed contract remains supported by `evaluate`, `compare`, and
`compare-repeated`, with their existing inputs, outputs, and exit codes.

Use a separate provenance sidecar when you need to detect a changed or swapped
suite/run artifact before scoring. Record the sidecar when exporting the run:

```bash
python -m weasley_agent_bench create-provenance examples/suite.json examples/candidate.json --runner-revision synthetic-runner-r1 > candidate.provenance.json
python -m weasley_agent_bench verify-provenance examples/suite.json examples/candidate.json candidate.provenance.json --expected-runner-revision synthetic-runner-r1
python -m weasley_agent_bench evaluate examples/suite.json examples/candidate.json
```

In an automated gate, run evaluation only if verification exits 0, for example
by joining the last two commands with `&&`. `create-provenance` emits the
sidecar to stdout; it does not edit a saved run or execute a runner. Creating a
sidecar later binds the inputs present then, not lost historical inputs.

## Version 1 schema and canonicalization

A sidecar is a strict JSON object with exactly these required fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1`, not a boolean. |
| `suite_id` | The supplied suite's non-empty ID. |
| `run_id` | The supplied run's non-empty ID. |
| `suite_sha256` | 64 lowercase hexadecimal characters identifying the canonical suite. |
| `run_sha256` | 64 lowercase hexadecimal characters identifying the canonical run. |
| `runner_revision` | A non-empty, case-sensitive, externally declared revision string. |

Both artifacts must first satisfy the existing version 1 input contracts.
The sidecar schema version is independent of the suite/run schema versions.
Unknown sidecar fields, missing fields, malformed digests, and unsupported
versions fail. The legacy CLI applies the same strict UTF-8/JSON loader to sidecars as
to suites and runs, including duplicate-key and non-finite-number rejection.
Sidecar field-validation and mismatch errors do not echo supplied field names
or values. Existing suite/run validation and file-read errors retain their
previous behavior and can include field names or file paths.

For bounded JSONL suite/run files, use the separate
[`verify-provenance-jsonl` command](jsonl-provenance.md). It checks the same
sidecar and canonical documents with a bounded sidecar loader and temporary
disk indexes, returning the same verification report. No schema migration is
required and no file paths are taken from sidecar content.

The digest algorithm is exactly the existing report's suite digest algorithm:

```python
canonical = json.dumps(document, sort_keys=True, ensure_ascii=True,
                       separators=(",", ":"), allow_nan=False)
digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

This is the project's version 1 Python JSON convention, not RFC 8785 or a digest
of raw file bytes. Whitespace, UTF-8 versus escaped Unicode representation,
and object-key order do not matter after parsing. Array order is retained,
including task and result order. JSON numbers parsed as `1` and `1.0` serialize
differently and therefore hash differently even though scoring considers them
equal. No Unicode normalization is applied. The suite digest includes prompts,
expectations, tags and budgets; the run digest includes IDs, status, final
values, calls and any usage. Reordering run results can leave scores unchanged
but intentionally fails verification against the previous artifact sidecar.

## Verification and compatibility policy

`verify-provenance` requires exact suite/run IDs and both matching digests. A
mismatch is an input error: exit **2**, a JSON error on stderr, and no stdout
report. A match exits **0** and emits the sidecar fields plus `consistent: true`
and `runner_revision_check`:

- `not_requested`: no expected revision was supplied; the revision is merely
  declared metadata and has not been compared to a trusted expectation.
- `matched`: `--expected-runner-revision` was supplied and exactly matched the
  declared string. A different string is rejected, even if the digests match.

The verification report is not itself a sidecar, because its two extra report
fields are outside the strict sidecar schema. Keep the `create-provenance`
output as the sidecar. A consistent failed or incomplete run can pass artifact
verification; only evaluation determines its task success and coverage.

Legacy artifacts without sidecars remain usable without migration. To rescore
intentionally under another suite, call the existing scoring commands directly
and retain the original artifacts and sidecar. Do not overwrite old provenance
to make historical evidence appear to match a new contract. For comparisons,
verify each run separately with the appropriate expected runner revision before
comparing; different runner revisions may be the intended experimental change.
For repeated batches, verify each nested run before analysis. The sidecar binds
one suite and one saved run, not a batch, score report, or event log.

## Python API and synthetic exporter

```python
from weasley_agent_bench import create_provenance, verify_provenance

sidecar = create_provenance(suite, run, runner_revision="synthetic-runner-r1")
check = verify_provenance(suite, run, sidecar,
                          expected_runner_revision="synthetic-runner-r1")
assert check["consistent"]
```

The memory experiment can emit one sidecar for each of its three synthetic runs:

```bash
python -m experiments.memory_horizon --output-dir reports/memory-bound --runner-revision synthetic-memory-r1
python -m weasley_agent_bench verify-provenance reports/memory-bound/suite.json reports/memory-bound/latest-record.json reports/memory-bound/latest-record.provenance.json --expected-runner-revision synthetic-memory-r1
```

`synthetic-memory-r1` is an example declaration, not an automatically discovered
source revision. Supply the version/commit appropriate to your exporter. The
flag requires `--output-dir`; omitting it preserves legacy export contents and
report output. Exports refuse to overwrite any target artifact, including a
sidecar. The Python `export` function accepts the same optional keyword
`runner_revision`; sidecar exports reject colliding generated filenames before
writing anything, including a run name that would occupy another run's sidecar
path. Exports are not atomic across files: an I/O error can leave partial
artifacts, so discard an incomplete bundle and retry in a fresh directory.
No provider call, paid service, or network access is used.

## Trust boundary

Hashes provide **artifact consistency, not authenticity or proof of execution**.
An actor able to rewrite the inputs can also regenerate the unsigned sidecar.
Even a matched expected revision does not prove that revision actually ran.
The verifier does not inspect a repository, authenticate the exporter, observe
tools, or certify a score. Preserve trusted original sidecars separately when
using them as evidence; signatures and execution attestation are not provided.
