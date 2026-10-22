"""Bounded saved-batch analysis; whole-trial assumptions match legacy analysis."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .contracts import ContractError, _object, _string, _validate_result
from .jsonl import DEFAULT_MAX_RECORD_BYTES, _canonical, _error, _ingest, _records, _storage
from .repeated import _analysis_metadata, _bootstrap_interval, _validate_analysis
from .scoring import _evaluate_task


def _one(db, sql, parameters=()):
    with closing(db.execute(sql, parameters)) as cursor:
        return cursor.fetchone()


def _batch_header(record: dict, suite_id: str) -> None:
    _object(record, "batch", {"format", "format_version", "schema_version", "suite_id", "batch_id"})
    if record["format"] != "weasley-agent-bench/batch-jsonl":
        raise ContractError("unsupported format")
    for field in ("format_version", "schema_version"):
        if type(record[field]) is not int or record[field] != 1:
            raise ContractError("unsupported version")
    if record["suite_id"] != suite_id:
        raise ContractError("suite mismatch")
    _string(record["batch_id"], "batch.batch_id")


def _trial_header(record: dict, suite_id: str) -> None:
    _object(record, "trial", {"trial_id", "run"})
    _string(record["trial_id"], "trial.trial_id")
    run = record["run"]
    _object(run, "run", {"schema_version", "suite_id", "run_id"})
    if type(run["schema_version"]) is not int or run["schema_version"] != 1:
        raise ContractError("unsupported version")
    if run["suite_id"] != suite_id:
        raise ContractError("suite mismatch")
    _string(run["run_id"], "run.run_id")


def _end(record: dict, field: str, count: int) -> bool:
    return (set(record) == {field, "count"} and record[field] is True
            and type(record["count"]) is int and record["count"] == count)


def _ingest_batch(db, path, source: str, suite_id: str, task_count: int, limit: int):
    stream = _records(path, source, limit)
    with closing(stream):
        first = next(stream, None)
        if first is None:
            raise _error(source, 1, "missing header")
        _, header = first
        try:
            _batch_header(header, suite_id)
        except ContractError as error:
            raise _error(source, 1, "invalid or unsupported batch header") from error
        active = None
        trials = observed = passed = 0
        line = 1
        for line, record in stream:
            if active is None:
                if "end" in record:
                    if not _end(record, "end", trials):
                        raise _error(source, line, "invalid end marker or trial count")
                    if trials < 2:
                        raise _error(source, line, "at least two complete trials are required")
                    trailing = next(stream, None)
                    if trailing is not None:
                        raise _error(source, trailing[0], "record after end marker")
                    return header, trials
                try:
                    _trial_header(record, suite_id)
                    # UTF-32BE byte order matches Python str code-point order,
                    # including lone surrogates. JSON spelling would not.
                    active = record["trial_id"].encode("utf-32-be", errors="surrogatepass")
                    db.execute("INSERT INTO trials VALUES (?, ?, ?, ?, 0)",
                               (source, active, _canonical(record["trial_id"]), _canonical(record["run"]["run_id"])))
                except sqlite3.IntegrityError as error:
                    raise _error(source, line, "duplicate trial ID or run ID within batch") from error
                except (ValueError, RecursionError) as error:
                    raise _error(source, line, "invalid trial header") from error
                observed = passed = 0
            elif "end_trial" in record:
                if not _end(record, "end_trial", observed):
                    raise _error(source, line, "invalid trial end marker or result count")
                if observed != task_count:
                    raise _error(source, line, "one result for every suite task is required")
                db.execute("UPDATE trials SET passed = ? WHERE source = ? AND key = ?", (passed, source, active))
                trials += 1
                active = None
            else:
                try:
                    _validate_result(record, "result")
                    task = _one(db, "SELECT ordinal, payload FROM tasks WHERE id = ?", (_canonical(record["task_id"]),))
                    if task is None:
                        raise ContractError("unknown task")
                    ordinal, payload = task
                    success = _evaluate_task(json.loads(payload), record)["passed"]
                    db.execute("INSERT INTO trial_scores VALUES (?, ?, ?, ?)", (source, active, ordinal, success))
                except sqlite3.IntegrityError as error:
                    raise _error(source, line, "duplicate task ID within trial") from error
                except (ValueError, RecursionError) as error:
                    raise _error(source, line, "invalid result contract or scoring limit") from error
                observed += 1
                passed += success
        reason = "missing trial end marker" if active is not None else "missing end marker"
        raise _error(source, line + 1, reason)


class _DiskDifferences:
    """Random indexed integer deltas without an input-sized Python list."""

    def __init__(self, db, count):
        self.db, self.count = db, count

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        return _one(self.db, "SELECT delta FROM trial_reports WHERE ordinal = ?", (index,))[0]


def compare_repeated_jsonl(suite_path: str | Path, baseline_path: str | Path, candidate_path: str | Path, *,
                           seed: int = 0, resamples: int = 10000,
                           max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
                           temporary_directory: str | Path | None = None) -> Iterator[dict]:
    """Yield legacy-equivalent repeated analysis after complete input validation.

    Independent representative trial pairs are an external assumption, not an
    inference from IDs or scores. Whole pairs are resampled with the legacy RNG.
    Records/cache/bootstrap totals are bounded; temporary disk grows with data.
    Consume or close the iterator; only the terminal complete event is final.
    """
    _validate_analysis(seed, resamples)
    with _storage(max_record_bytes, temporary_directory) as db:
        db.execute("CREATE TABLE trials (source TEXT, key BLOB, id TEXT, run_id TEXT, passed INTEGER, "
                   "PRIMARY KEY (source, key), UNIQUE (source, run_id))")
        db.execute("CREATE TABLE trial_scores (source TEXT, key BLOB, ordinal INTEGER, passed INTEGER, "
                   "PRIMARY KEY (source, ordinal, key))")
        db.execute("CREATE TABLE trial_reports (ordinal INTEGER PRIMARY KEY, delta INTEGER, payload TEXT)")
        db.execute("CREATE TABLE task_reports (ordinal INTEGER PRIMARY KEY, payload TEXT)")
        suite, task_count, digest = _ingest(db, suite_path, "suite", max_record_bytes)
        baseline, count = _ingest_batch(db, baseline_path, "baseline", suite["suite_id"], task_count, max_record_bytes)
        candidate, new_count = _ingest_batch(db, candidate_path, "candidate", suite["suite_id"], task_count, max_record_bytes)
        paired = _one(db, "SELECT COUNT(*) FROM trials AS old JOIN trials AS new ON new.key = old.key "
                      "AND new.source = 'candidate' WHERE old.source = 'baseline'")[0]
        if new_count != count or paired != count:
            raise ContractError("candidate trials must match baseline exactly; no unpaired trials are dropped")
        db.commit()

        old_total = new_total = 0
        with closing(db.execute(
            "SELECT old.id, old.run_id, new.run_id, old.passed, new.passed "
            "FROM trials AS old JOIN trials AS new ON new.key = old.key AND new.source = 'candidate' "
            "WHERE old.source = 'baseline' ORDER BY old.key"
        )) as rows:
            for ordinal, (trial_id, old_id, new_id, old, new) in enumerate(rows):
                row = {"trial_id": json.loads(trial_id), "baseline_run_id": json.loads(old_id),
                       "candidate_run_id": json.loads(new_id), "baseline_passed": old, "candidate_passed": new,
                       "pass_rate_delta": (new - old) / task_count}
                db.execute("INSERT INTO trial_reports VALUES (?, ?, ?)", (ordinal, new - old, _canonical(row)))
                old_total += old
                new_total += new
        with closing(db.execute("SELECT ordinal, id FROM tasks ORDER BY ordinal")) as rows:
            for ordinal, task_id in rows:
                old, new, improved, regressed = _one(db,
                    "SELECT SUM(old.passed), SUM(new.passed), "
                    "SUM(old.passed = 0 AND new.passed = 1), SUM(old.passed = 1 AND new.passed = 0) "
                    "FROM trial_scores AS old JOIN trial_scores AS new ON new.key = old.key "
                    "AND new.ordinal = old.ordinal AND new.source = 'candidate' "
                    "WHERE old.source = 'baseline' AND old.ordinal = ?", (ordinal,))
                row = {"task_id": json.loads(task_id), "baseline_passed": old, "candidate_passed": new,
                       "improvements": improved, "regressions": regressed, "pass_rate_delta": (new - old) / count}
                db.execute("INSERT INTO task_reports VALUES (?, ?)", (ordinal, _canonical(row)))
        db.commit()
        interval = _bootstrap_interval(_DiskDifferences(db, count), task_count, seed, resamples)
        denominator = task_count * count
        summary = {"tasks": task_count, "trial_pairs": count,
                   "baseline_pass_rate": old_total / denominator, "candidate_pass_rate": new_total / denominator,
                   "pass_rate_delta": (new_total - old_total) / denominator}
        yield {"format": "weasley-agent-bench/repeated-comparison-jsonl", "format_version": 1,
               "schema_version": 1, "suite_id": suite["suite_id"], "suite_sha256": digest,
               "baseline_batch_id": baseline["batch_id"], "candidate_batch_id": candidate["batch_id"],
               "analysis": _analysis_metadata(seed, resamples)}
        for field, table in (("trial", "trial_reports"), ("task", "task_reports")):
            # Table names are internal constants, never artifact content.
            with closing(db.execute(f"SELECT payload FROM {table} ORDER BY ordinal")) as rows:
                for (payload,) in rows:
                    yield {field: json.loads(payload)}
        yield {"complete": True, "summary": summary, "interval": interval}
