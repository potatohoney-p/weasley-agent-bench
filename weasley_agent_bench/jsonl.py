"""Bounded JSONL transport for offline v1 evaluation, with temporary disk indexes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager

from .contracts import (
    ContractError, _constant, _object, _pairs, _string, _validate_result, _validate_task,
)
from .scoring import _compare_rows, _evaluate_task, _comparison_gate, _validate_check_policy
from .provenance import _validate_provenance, _verification_report

DEFAULT_MAX_RECORD_BYTES = 1024 * 1024
DEFAULT_MAX_SIDECAR_BYTES = 1024 * 1024


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False)


def _error(source: str, line: int, reason: str) -> ContractError:
    return ContractError(f"{source} JSONL line {line}: {reason}")


def _records(path: str | Path, source: str, limit: int) -> Iterator[tuple[int, dict]]:
    line = 1
    try:
        opened = Path(path).open("rb")
    except (OSError, ValueError) as error:
        raise _error(source, line, "cannot read input") from error
    try:
        with opened as stream:
            while True:
                raw = stream.readline(limit + 1)
                if not raw:
                    return
                if len(raw) > limit:
                    raise _error(source, line, "record exceeds max_record_bytes")
                try:
                    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                                       parse_constant=_constant)
                except (ValueError, UnicodeError, RecursionError) as error:
                    raise _error(source, line, "invalid UTF-8 JSON record") from error
                if not isinstance(value, dict):
                    raise _error(source, line, "record must be an object")
                yield line, value
                line += 1
    except OSError as error:
        raise _error(source, line, "cannot read input") from error


def _header(record: dict, kind: str, source: str) -> None:
    try:
        fields = {"format", "format_version", "schema_version", "suite_id"}
        if kind == "run":
            fields.add("run_id")
        _object(record, "header", fields)
        if record["format"] != f"weasley-agent-bench/{kind}-jsonl":
            raise ContractError("unsupported format")
        for field in ("format_version", "schema_version"):
            if type(record[field]) is not int or record[field] != 1:
                raise ContractError("unsupported version")
        _string(record["suite_id"], "header.suite_id")
        if kind == "run":
            _string(record["run_id"], "header.run_id")
    except ContractError as error:
        raise _error(source, 1, "invalid or unsupported header") from error


def _ingest(db: sqlite3.Connection, path: str | Path, kind: str, limit: int,
            suite_id: str | None = None, *, source: str | None = None) -> tuple[dict, int, str]:
    source = source or kind
    stream = _records(path, source, limit)
    try:
        first = next(stream, None)
        if first is None:
            raise _error(source, 1, "missing header")
        _, header = first
        _header(header, kind, source)
        if kind == "run" and header["suite_id"] != suite_id:
            raise _error(source, 1, "suite_id does not match the task suite")
        digest = hashlib.sha256()
        # Exact sorted-key v1 documents; retain original task AND result order.
        if kind == "suite":
            prefix = '{"schema_version":1,"suite_id":' + _canonical(header["suite_id"]) + ',"tasks":['
            suffix = "]}"
        else:
            prefix = '{"results":['
            suffix = ('],"run_id":' + _canonical(header["run_id"])
                      + ',"schema_version":1,"suite_id":' + _canonical(header["suite_id"]) + '}')
        digest.update(prefix.encode("ascii"))
        count = 0
        line = 1
        for line, record in stream:
            if "end" in record:
                if (set(record) != {"end", "count"} or record["end"] is not True
                        or type(record["count"]) is not int or record["count"] != count):
                    raise _error(source, line, "invalid end marker or record count")
                if kind == "suite" and count == 0:
                    raise _error(source, line, "at least one task is required")
                trailing = next(stream, None)
                if trailing is not None:
                    raise _error(source, trailing[0], "record after end marker")
                digest.update(suffix.encode("ascii"))
                return header, count, digest.hexdigest()
            try:
                if kind == "suite":
                    _validate_task(record, "task")
                    payload = _canonical(record)
                    db.execute("INSERT INTO tasks VALUES (?, ?, ?)",
                               (count, _canonical(record["id"]), payload))
                else:
                    _validate_result(record, "result")
                    key = _canonical(record["task_id"])
                    if db.execute("SELECT 1 FROM tasks WHERE id = ?", (key,)).fetchone() is None:
                        raise _error(source, line, "unknown task ID")
                    payload = _canonical(record)
                    db.execute("INSERT INTO results VALUES (?, ?, ?)", (source, key, payload))
                if count:
                    digest.update(b",")
                digest.update(payload.encode("ascii"))
            except sqlite3.IntegrityError as error:
                raise _error(source, line, "duplicate task ID") from error
            except (ValueError, RecursionError) as error:
                # Do not echo record values, unknown field names, or saved payloads.
                raise _error(source, line, f"invalid {kind} record contract") from error
            count += 1
        raise _error(source, line + 1, "missing end marker")
    finally:
        stream.close()


@contextmanager
def _storage(max_record_bytes: int, temporary_directory: str | Path | None) -> Iterator[sqlite3.Connection]:
    _byte_limit(max_record_bytes, "max_record_bytes")
    try:
        with tempfile.TemporaryDirectory(prefix="weasley-jsonl-", dir=temporary_directory) as directory:
            db = sqlite3.connect(Path(directory) / "evaluation.sqlite3")
            try:
                db.execute("PRAGMA cache_size = -2048")
                db.execute("PRAGMA temp_store = FILE")
                db.execute("PRAGMA mmap_size = 0")
                db.execute("CREATE TABLE tasks (ordinal INTEGER PRIMARY KEY, id TEXT UNIQUE, payload TEXT)")
                db.execute("CREATE TABLE results (source TEXT, id TEXT, payload TEXT, PRIMARY KEY (source, id))")
                db.execute("CREATE TABLE reports (ordinal INTEGER PRIMARY KEY, payload TEXT)")
                yield db
            finally:
                db.close()
    except (OSError, sqlite3.Error) as error:
        raise ContractError("JSONL evaluation temporary storage failure") from error


def _byte_limit(value: int, name: str) -> None:
    if type(value) is not int or not 1 <= value < sys.maxsize:
        raise ContractError(f"{name} must be a positive integer below sys.maxsize")


def _load_sidecar(path: str | Path, limit: int) -> dict:
    _byte_limit(limit, "max_sidecar_bytes")
    try:
        with Path(path).open("rb") as stream:
            # A permissive configured limit must not allocate that limit up front.
            raw = bytearray()
            while len(raw) <= limit:
                chunk = stream.read(min(64 * 1024, limit + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
    except (OSError, ValueError) as error:
        raise ContractError("provenance: cannot read input") from error
    if len(raw) > limit:
        raise ContractError("provenance: document exceeds max_sidecar_bytes")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ContractError("provenance: invalid UTF-8 JSON document") from error


def verify_provenance_jsonl(suite_path: str | Path, run_path: str | Path,
                            provenance_path: str | Path, *,
                            expected_runner_revision: str | None = None,
                            max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
                            max_sidecar_bytes: int = DEFAULT_MAX_SIDECAR_BYTES,
                            temporary_directory: str | Path | None = None) -> dict:
    """Verify an unsigned v1 sidecar against fully validated JSONL artifact files.

    Hash the equivalent canonical v1 documents, not raw transport bytes. Only
    explicit caller paths are opened; sidecars contain no file/URL references.
    Memory is bounded by record/sidecar limits and the SQLite cache; disk grows
    with input size. A consistent partial or failed run is not a successful run.
    """
    _byte_limit(max_record_bytes, "max_record_bytes")
    provenance = _load_sidecar(provenance_path, max_sidecar_bytes)
    _validate_provenance(provenance, expected_runner_revision)
    with _storage(max_record_bytes, temporary_directory) as db:
        suite, _, suite_digest = _ingest(db, suite_path, "suite", max_record_bytes)
        run, _, run_digest = _ingest(db, run_path, "run", max_record_bytes, suite["suite_id"])
        report = _verification_report(provenance, suite["suite_id"], run["run_id"],
                                      suite_digest, run_digest, expected_runner_revision)
    return report


def _report_events(db: sqlite3.Connection, field: str) -> Iterator[dict]:
    with closing(db.execute("SELECT payload FROM reports ORDER BY ordinal")) as rows:
        for (payload,) in rows:
            yield {field: json.loads(payload)}


def evaluate_jsonl(suite_path: str | Path, run_path: str | Path, *,
                   max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
                   temporary_directory: str | Path | None = None) -> Iterator[dict]:
    """Yield a header, task rows and a completion summary after validating all input.

    Input records (including newline bytes) are bounded by max_record_bytes.
    Temporary disk grows with inputs and reports; SQLite's page cache is 2 MiB.
    Consume the iterator completely, or close it, to release temporary storage.
    Only the terminal complete=true event, emitted after scratch cleanup,
    establishes a complete report.
    """
    with _storage(max_record_bytes, temporary_directory) as db:
        suite, total, digest = _ingest(db, suite_path, "suite", max_record_bytes)
        run, observed, _ = _ingest(db, run_path, "run", max_record_bytes, suite["suite_id"])
        db.commit()
        passed = 0
        # The integer primary key supplies suite order without an in-memory sort.
        with closing(db.execute(
            "SELECT tasks.ordinal, tasks.payload, results.payload FROM tasks "
            "LEFT JOIN results ON results.id = tasks.id AND results.source = 'run' ORDER BY tasks.ordinal"
        )) as rows:
            for ordinal, task, result in rows:
                try:
                    row = _evaluate_task(json.loads(task), json.loads(result) if result else None)
                    payload = _canonical(row)
                except (ValueError, RecursionError) as error:
                    raise _error("suite", ordinal + 2, "record exceeds scoring limits") from error
                passed += row["passed"]
                db.execute("INSERT INTO reports VALUES (?, ?)", (ordinal, payload))
        db.commit()
        yield {"format": "weasley-agent-bench/report-jsonl", "format_version": 1,
               "schema_version": 1, "suite_id": suite["suite_id"],
               "suite_sha256": digest, "run_id": run["run_id"]}
        yield from _report_events(db, "result")
    yield {"complete": True, "passed": passed == total,
           "summary": {"total": total, "passed": passed, "failed": total - passed,
                       "missing": total - observed}}


def compare_jsonl(suite_path: str | Path, baseline_path: str | Path, candidate_path: str | Path, *,
                  max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
                  temporary_directory: str | Path | None = None,
                  check_regressions: bool = False) -> Iterator[dict]:
    """Compare two complete saved runs with bounded records and temporary indexes.

    The inputs use the existing suite/run JSONL formats. Output changes follow
    suite order, with the same semantics as compare(). Only the terminal complete
    summary, emitted after scratch cleanup, establishes a complete report.
    Consume or close the iterator before that event.
    """
    _validate_check_policy(check_regressions)
    with _storage(max_record_bytes, temporary_directory) as db:
        suite, total, digest = _ingest(db, suite_path, "suite", max_record_bytes)
        baseline, old_count, _ = _ingest(db, baseline_path, "run", max_record_bytes,
                                       suite["suite_id"], source="baseline")
        candidate, new_count, _ = _ingest(db, candidate_path, "run", max_record_bytes,
                                        suite["suite_id"], source="candidate")
        # Counts establish complete coverage only after unique/known-ID validation.
        if old_count != total or new_count != total:
            raise ContractError("comparison requires one result for every task in both runs")
        db.commit()
        summary = {"total": total, "regressions": 0, "improvements": 0,
                   "baseline_passed": 0, "candidate_passed": 0}
        if check_regressions:
            summary.update(check_regressions=0, check_improvements=0, tasks_with_check_regressions=0)
        with closing(db.execute(
            "SELECT tasks.ordinal, tasks.payload, old.payload, new.payload FROM tasks "
            "LEFT JOIN results AS old ON old.id = tasks.id AND old.source = 'baseline' "
            "LEFT JOIN results AS new ON new.id = tasks.id AND new.source = 'candidate' ORDER BY tasks.ordinal"
        )) as rows:
            for ordinal, task, old, new in rows:
                try:
                    task, old, new = json.loads(task), json.loads(old), json.loads(new)
                    change = _compare_rows(_evaluate_task(task, old), _evaluate_task(task, new),
                                           old.get("usage", {}), new.get("usage", {}),
                                           check_regressions=check_regressions)
                    payload = _canonical(change)
                except (ValueError, RecursionError) as error:
                    raise _error("suite", ordinal + 2, "record exceeds scoring limits") from error
                summary["regressions"] += change["change"] == "regression"
                summary["improvements"] += change["change"] == "improvement"
                summary["baseline_passed"] += change["baseline_passed"]
                summary["candidate_passed"] += change["candidate_passed"]
                if check_regressions:
                    summary["check_regressions"] += len(change["check_regressions"])
                    summary["check_improvements"] += len(change["check_improvements"])
                    summary["tasks_with_check_regressions"] += bool(change["check_regressions"])
                db.execute("INSERT INTO reports VALUES (?, ?)", (ordinal, payload))
        db.commit()
        yield {"format": "weasley-agent-bench/comparison-jsonl", "format_version": 1,
               "schema_version": 1, "suite_id": suite["suite_id"], "suite_sha256": digest,
               "baseline_run_id": baseline["run_id"], "candidate_run_id": candidate["run_id"],
               **({"regression_policy": "checks-v1"} if check_regressions else {})}
        yield from _report_events(db, "change")
    yield {"complete": True, **_comparison_gate(summary, check_regressions),
           "candidate_passed": summary["candidate_passed"] == total, "summary": summary}
