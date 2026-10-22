"""Bounded JSONL transport for offline v1 evaluation, with temporary disk indexes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from collections.abc import Iterator

from .contracts import (
    ContractError, _constant, _object, _pairs, _string, _validate_result, _validate_task,
)
from .scoring import _evaluate_task

DEFAULT_MAX_RECORD_BYTES = 1024 * 1024


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False)


def _error(source: str, line: int, reason: str) -> ContractError:
    return ContractError(f"{source} JSONL line {line}: {reason}")


def _records(path: str | Path, source: str, limit: int) -> Iterator[tuple[int, dict]]:
    line = 1
    try:
        with Path(path).open("rb") as stream:
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


def _header(record: dict, source: str) -> None:
    try:
        fields = {"format", "format_version", "schema_version", "suite_id"}
        if source == "run":
            fields.add("run_id")
        _object(record, "header", fields)
        if record["format"] != f"weasley-agent-bench/{source}-jsonl":
            raise ContractError("unsupported format")
        for field in ("format_version", "schema_version"):
            if type(record[field]) is not int or record[field] != 1:
                raise ContractError("unsupported version")
        _string(record["suite_id"], "header.suite_id")
        if source == "run":
            _string(record["run_id"], "header.run_id")
    except ContractError as error:
        raise _error(source, 1, "invalid or unsupported header") from error


def _ingest(db: sqlite3.Connection, path: str | Path, source: str, limit: int,
            suite_id: str | None = None) -> tuple[dict, int, str | None]:
    stream = _records(path, source, limit)
    try:
        first = next(stream, None)
        if first is None:
            raise _error(source, 1, "missing header")
        _, header = first
        _header(header, source)
        if source == "run" and header["suite_id"] != suite_id:
            raise _error(source, 1, "suite_id does not match the task suite")
        digest = hashlib.sha256()
        # Exact sorted-key v1 document representation, including original task order.
        digest.update(('{"schema_version":1,"suite_id":' + _canonical(header["suite_id"])
                       + ',"tasks":[').encode("ascii"))
        count = 0
        line = 1
        for line, record in stream:
            if "end" in record:
                if (set(record) != {"end", "count"} or record["end"] is not True
                        or type(record["count"]) is not int or record["count"] != count):
                    raise _error(source, line, "invalid end marker or record count")
                if source == "suite" and count == 0:
                    raise _error(source, line, "at least one task is required")
                trailing = next(stream, None)
                if trailing is not None:
                    raise _error(source, trailing[0], "record after end marker")
                digest.update(b"]}")
                return header, count, digest.hexdigest() if source == "suite" else None
            try:
                if source == "suite":
                    _validate_task(record, "task")
                    payload = _canonical(record)
                    db.execute("INSERT INTO tasks VALUES (?, ?, ?)",
                               (count, _canonical(record["id"]), payload))
                    if count:
                        digest.update(b",")
                    digest.update(payload.encode("ascii"))
                else:
                    _validate_result(record, "result")
                    key = _canonical(record["task_id"])
                    if db.execute("SELECT 1 FROM tasks WHERE id = ?", (key,)).fetchone() is None:
                        raise _error(source, line, "unknown task ID")
                    db.execute("INSERT INTO results VALUES (?, ?)", (key, _canonical(record)))
            except sqlite3.IntegrityError as error:
                raise _error(source, line, "duplicate task ID") from error
            except (ValueError, RecursionError) as error:
                # Do not echo record values, unknown field names, or saved payloads.
                raise _error(source, line, f"invalid {source} record contract") from error
            count += 1
        raise _error(source, line + 1, "missing end marker")
    finally:
        stream.close()


def evaluate_jsonl(suite_path: str | Path, run_path: str | Path, *,
                   max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
                   temporary_directory: str | Path | None = None) -> Iterator[dict]:
    """Yield a header, task rows and a completion summary after validating all input.

    Input records (including newline bytes) are bounded by max_record_bytes.
    Temporary disk grows with inputs and reports; SQLite's page cache is 2 MiB.
    Consume the iterator completely, or close it, to release temporary storage.
    Only the terminal complete=true event establishes a complete report.
    """
    if type(max_record_bytes) is not int or not 1 <= max_record_bytes < sys.maxsize:
        raise ContractError("max_record_bytes must be a positive integer below sys.maxsize")
    try:
        with tempfile.TemporaryDirectory(prefix="weasley-jsonl-", dir=temporary_directory) as directory:
            db = sqlite3.connect(Path(directory) / "evaluation.sqlite3")
            try:
                db.execute("PRAGMA cache_size = -2048")
                db.execute("PRAGMA temp_store = FILE")
                db.execute("PRAGMA mmap_size = 0")
                db.execute("CREATE TABLE tasks (ordinal INTEGER PRIMARY KEY, id TEXT UNIQUE, payload TEXT)")
                db.execute("CREATE TABLE results (id TEXT PRIMARY KEY, payload TEXT)")
                db.execute("CREATE TABLE reports (ordinal INTEGER PRIMARY KEY, payload TEXT)")
                suite, total, digest = _ingest(db, suite_path, "suite", max_record_bytes)
                run, observed, _ = _ingest(db, run_path, "run", max_record_bytes, suite["suite_id"])
                db.commit()
                passed = 0
                # The integer primary key supplies suite order without an in-memory sort.
                rows = db.execute(
                    "SELECT tasks.ordinal, tasks.payload, results.payload FROM tasks "
                    "LEFT JOIN results ON results.id = tasks.id ORDER BY tasks.ordinal"
                )
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
                for (payload,) in db.execute("SELECT payload FROM reports ORDER BY ordinal"):
                    yield {"result": json.loads(payload)}
                yield {"complete": True, "passed": passed == total,
                       "summary": {"total": total, "passed": passed, "failed": total - passed,
                                   "missing": total - observed}}
            finally:
                db.close()
    except (OSError, sqlite3.Error) as error:
        raise ContractError("JSONL evaluation temporary storage failure") from error
