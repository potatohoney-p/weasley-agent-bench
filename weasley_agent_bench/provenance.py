"""Opt-in artifact consistency checks, not signatures or proof of execution."""

from __future__ import annotations

import re

from .contracts import (
    _document_sha256, _fail, _object, _string, validate_run, validate_suite,
)


def create_provenance(suite: dict, run: dict, *, runner_revision: str) -> dict:
    """Bind current suite/run contents to an externally declared runner revision.

    Record this alongside exports, not after the historical inputs are lost.
    Creating a sidecar cannot recover or authenticate historical provenance.
    A valid failed or incomplete run can have a consistent sidecar.
    """
    validate_suite(suite)
    validate_run(run, suite)
    _string(runner_revision, "provenance.runner_revision")
    return {
        "schema_version": 1,
        "suite_id": suite["suite_id"],
        "run_id": run["run_id"],
        "suite_sha256": _document_sha256(suite),
        "run_sha256": _document_sha256(run),
        "runner_revision": runner_revision,
    }


def verify_provenance(
    suite: dict, run: dict, provenance: dict, *, expected_runner_revision: str | None = None,
) -> dict:
    """Reject inconsistent artifacts; optionally match an expected revision label.

    A matching label is still a declaration, not evidence that this revision
    actually ran. No signature, checkout, provider, or network is consulted.
    """
    validate_suite(suite)
    validate_run(run, suite)
    _validate_provenance(provenance, expected_runner_revision)
    return _verification_report(
        provenance, suite["suite_id"], run["run_id"],
        _document_sha256(suite), _document_sha256(run), expected_runner_revision,
    )


def _validate_provenance(provenance: dict, expected_runner_revision: str | None) -> None:
    fields = {
        "schema_version", "suite_id", "run_id", "suite_sha256", "run_sha256", "runner_revision",
    }
    # A sidecar may contain an accidentally pasted payload as an unknown key.
    # Do not echo those user-controlled keys in this new validation boundary.
    if isinstance(provenance, dict) and provenance.keys() - fields:
        _fail("provenance", "unknown fields are not allowed")
    _object(provenance, "provenance", fields)
    if type(provenance["schema_version"]) is not int or provenance["schema_version"] != 1:
        _fail("provenance.schema_version", "only version 1 is supported")
    for field in ("suite_id", "run_id", "runner_revision"):
        _string(provenance[field], f"provenance.{field}")
    for field in ("suite_sha256", "run_sha256"):
        value = provenance[field]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            _fail(f"provenance.{field}", "expected 64 lowercase hexadecimal characters")
    if expected_runner_revision is not None:
        _string(expected_runner_revision, "expected_runner_revision")


def _verification_report(provenance: dict, suite_id: str, run_id: str,
                         suite_sha256: str, run_sha256: str,
                         expected_runner_revision: str | None) -> dict:
    # Callers validate the sidecar and compute these digests from validated inputs.
    for field, expected in (
        ("suite_id", suite_id), ("run_id", run_id),
        ("suite_sha256", suite_sha256), ("run_sha256", run_sha256),
    ):
        if provenance[field] != expected:
            _fail(f"provenance.{field}", "does not match the supplied artifact")
    if expected_runner_revision is not None and provenance["runner_revision"] != expected_runner_revision:
        _fail("provenance.runner_revision", "does not match the expected runner revision")
    return {
        **provenance,
        "consistent": True,
        "runner_revision_check": "matched" if expected_runner_revision is not None else "not_requested",
    }
