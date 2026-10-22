"""Synthetic fresh-process resource measurements for paired JSONL comparison.

    python -m experiments.jsonl_comparison_scaling --tasks 500 5000 --output-bytes 128 4096
    python -m experiments.jsonl_comparison_scaling --tasks 500 --modes legacy

No providers or networks are used. These are evaluator engineering measurements,
not agent performance results or evidence of real workload demand.
"""

from __future__ import annotations

import argparse
import hashlib
from itertools import chain
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc

from .jsonl_scaling import SAMPLE_INTERVAL_SECONDS, _canonical, _directory_bytes


ROOT = Path(__file__).resolve().parents[1]
METADATA_FIELDS = ("schema_version", "suite_id", "suite_sha256", "baseline_run_id", "candidate_run_id")


def _write_documents(directory: Path, name: str, metadata: dict, field: str, records) -> None:
    """Write both transports while retaining just one generated record."""
    kind = "suite" if field == "tasks" else "run"
    with (directory / f"{name}.json").open("xb") as document, (directory / f"{name}.jsonl").open("xb") as lines:
        document.write(_canonical(metadata)[:-1] + b',"' + field.encode("ascii") + b'":[')
        lines.write(_canonical({"format": f"weasley-agent-bench/{kind}-jsonl",
                                "format_version": 1, **metadata}) + b"\n")
        count = 0
        for record in records:
            encoded = _canonical(record)
            if count:
                document.write(b",")
            document.write(encoded)
            lines.write(encoded + b"\n")
            count += 1
        document.write(b"]}\n")
        lines.write(_canonical({"end": True, "count": count}) + b"\n")


def _task(index: int) -> dict:
    return {"id": f"task-{index:09d}", "prompt": f"Synthetic lookup for record {index}.",
            "expect": {"final": [{"path": "", "equals": index}],
                       "tools": {"mode": "exact", "steps": [
                           {"name": "synthetic_lookup", "outcome": "success"}]}},
            "budget": {"max_tool_calls": 1}}


def _result(index: int, side: str, payload: str) -> dict:
    # Each block of four has one improvement, regression, unchanged pass and
    # unchanged failure. Shared telemetry includes exact fractional durations;
    # absent candidate output_tokens must not become a zero-cost observation.
    correct = index % 4 in ((1, 2) if side == "baseline" else (0, 2))
    usage = {"input_tokens": 100 + index, "output_tokens": 10 + index,
             "duration_ms": index + 0.5}
    if side == "candidate":
        usage = {"input_tokens": 90 + index, "duration_ms": index + 0.75}
        if index % 2 == 0:
            usage["output_tokens"] = 0 if index % 4 == 0 else 12 + index
    return {"task_id": f"task-{index:09d}", "status": "completed",
            "final": index if correct else -1,
            "tool_calls": [{"name": "synthetic_lookup", "arguments": {"id": index},
                            "outcome": "success", "output": payload}], "usage": usage}


def generate_fixture(directory: Path, tasks: int, output_bytes: int) -> dict:
    """Generate complete paired synthetic runs incrementally in a new directory.

    output_bytes is the ASCII tool-output length per task in EACH run.
    Existing directories are refused; an I/O error can leave a partial fixture.
    """
    if tasks < 1 or output_bytes < 0:
        raise ValueError("tasks must be positive and output_bytes non-negative")
    directory.mkdir(parents=True, exist_ok=False)
    suite_header = {"schema_version": 1, "suite_id": f"synthetic-comparison-{tasks}-{output_bytes}"}
    _write_documents(directory, "suite", suite_header, "tasks", (_task(index) for index in range(tasks)))
    payload = "x" * output_bytes
    orders = (("baseline", range(tasks - 1, -1, -1)),
              ("candidate", chain(range(0, tasks, 2), range(1, tasks, 2))))
    for side, order in orders:
        _write_documents(directory, side, {**suite_header, "run_id": f"synthetic-{side}"},
                         "results", (_result(index, side, payload) for index in order))
    return {f"{name}.{suffix}": (directory / f"{name}.{suffix}").stat().st_size
            for name in ("suite", "baseline", "candidate") for suffix in ("json", "jsonl")}


class _ComparisonDigest:
    def __init__(self):
        self.changes = hashlib.sha256()
        self.count = 0

    def add(self, change: dict) -> None:
        self.changes.update(_canonical(change) + b"\n")
        self.count += 1

    def finish(self, metadata: dict, terminal: dict) -> dict:
        summary = terminal["summary"]
        if self.count != summary["total"]:
            raise ValueError("incomplete comparison: change count does not match summary")
        flags = {key: terminal[key] for key in ("no_regressions", "candidate_passed")}
        logical = {**metadata, **flags, "summary": summary, "change_count": self.count,
                   "ordered_changes_sha256": self.changes.hexdigest()}
        return {"logical_report_sha256": hashlib.sha256(_canonical(logical)).hexdigest(),
                "ordered_changes_sha256": self.changes.hexdigest(),
                "suite_sha256": metadata["suite_sha256"], **flags, "summary": summary}


def _consume_legacy(directory: Path) -> dict:
    from weasley_agent_bench import compare, load_document
    report = compare(*(load_document(directory / f"{name}.json")
                       for name in ("suite", "baseline", "candidate")))
    digest = _ComparisonDigest()
    for change in report["changes"]:
        digest.add(change)
    return digest.finish({key: report[key] for key in METADATA_FIELDS}, report)


def _consume_jsonl(directory: Path, scratch: Path, max_record_bytes: int) -> dict:
    from weasley_agent_bench import compare_jsonl
    digest = _ComparisonDigest()
    metadata = terminal = None
    for event in compare_jsonl(*(directory / f"{name}.jsonl" for name in ("suite", "baseline", "candidate")),
                               max_record_bytes=max_record_bytes, temporary_directory=scratch):
        if terminal is not None:
            raise ValueError("comparison contains records after its terminal summary")
        if metadata is None:
            if event.get("format") != "weasley-agent-bench/comparison-jsonl" or event.get("format_version") != 1:
                raise ValueError("unexpected comparison report header")
            metadata = {key: event[key] for key in METADATA_FIELDS}
        elif "change" in event:
            digest.add(event["change"])
        elif event.get("complete") is True:
            terminal = event
        else:
            raise ValueError("unexpected comparison report record")
    if metadata is None or terminal is None:
        raise ValueError("comparison report did not complete")
    return digest.finish(metadata, terminal)


def _worker(mode: str, directory: Path, max_record_bytes: int) -> dict:
    # Both modes resolve evaluator imports before timing and allocation tracing.
    import weasley_agent_bench
    if mode == "jsonl" and not hasattr(weasley_agent_bench, "compare_jsonl"):
        raise ValueError("compare_jsonl is unavailable; use --modes legacy for baseline-only measurement")
    with tempfile.TemporaryDirectory(prefix="bench-comparison-scratch-") as temporary:
        scratch = Path(temporary)
        stop = threading.Event()
        peak_disk = [0]

        def sample():
            while not stop.is_set():
                peak_disk[0] = max(peak_disk[0], _directory_bytes(scratch))
                stop.wait(SAMPLE_INTERVAL_SECONDS)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        tracemalloc.start()
        start = time.perf_counter()
        try:
            comparison = (_consume_legacy(directory) if mode == "legacy" else
                          _consume_jsonl(directory, scratch, max_record_bytes))
            elapsed = time.perf_counter() - start
            _, peak_python = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            stop.set()
            sampler.join()
        peak_disk[0] = max(peak_disk[0], _directory_bytes(scratch))
    rss, rss_unit = None, None
    if os.name == "posix":
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_unit = "bytes" if sys.platform == "darwin" else "KiB" if sys.platform.startswith("linux") else "platform-dependent"
    return {"mode": mode, **comparison, "elapsed_wall_seconds": elapsed,
            "tracemalloc_peak_bytes": peak_python, "process_ru_maxrss": rss,
            "process_ru_maxrss_unit": rss_unit, "temporary_disk_peak_sampled_bytes": peak_disk[0]}


def _run_child(mode: str, directory: Path, max_record_bytes: int) -> dict:
    process = subprocess.run(
        [sys.executable, "-B", "-m", "experiments.jsonl_comparison_scaling", "--worker", mode,
         "--fixture-dir", str(directory.resolve()), "--max-record-bytes", str(max_record_bytes)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if process.returncode:
        raise RuntimeError(f"{mode} comparison measurement failed: {process.stderr.strip()}")
    return json.loads(process.stdout)


def _measure(directory: Path, task_counts: list[int], sizes: list[int], modes: list[str],
             max_record_bytes: int) -> dict:
    cases = []
    for tasks in task_counts:
        for output_bytes in sizes:
            fixture = directory / f"tasks-{tasks}-bytes-{output_bytes}"
            sizes_on_disk = generate_fixture(fixture, tasks, output_bytes)
            measurements = [_run_child(mode, fixture, max_record_bytes) for mode in modes]
            digests = {item["logical_report_sha256"] for item in measurements}
            cases.append({"tasks": tasks, "output_bytes_per_task_per_run": output_bytes,
                          "fixture_bytes": sizes_on_disk, "measurements": measurements,
                          "comparison_parity": len(digests) == 1 if len(measurements) > 1 else None})
    return {"schema_version": 1, "synthetic": True,
            "purpose": "Offline paired-comparison engineering measurements; not provider performance or real workload demand",
            "environment": {"python": sys.version, "platform": platform.platform()},
            "workload": {"suite_order": "ascending task index", "baseline_order": "descending task index",
                         "candidate_order": "even indices then odd indices",
                         "transitions": "index modulo 4: improvement, regression, unchanged pass, unchanged failure",
                         "usage": "shared input_tokens and fractional duration; candidate output_tokens absent at odd indices, zero at indices divisible by four"},
            "measurement": {
                "fresh_process_per_case_and_mode": True, "max_record_bytes": max_record_bytes,
                "includes": "input loading, validation, paired scoring and compact comparison digest consumption",
                "excludes": "fixture generation, child process startup and evaluator module import",
                "tracemalloc": "Python allocations during comparison, including sampler bookkeeping; excludes SQLite/native allocations",
                "ru_maxrss": "Whole child process lifetime peak; null on Windows; includes tracing overhead",
                "temporary_disk": "Sampled logical file bytes in isolated scratch directory; a lower bound on peak, not allocated disk blocks",
                "temporary_disk_sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
                "digest": "SHA-256 of canonical v1 metadata, both final flags, summary, change count and ordered canonical-change SHA-256",
                "working_set": "JSONL retains a task and two result records plus database/interpreter caches; record byte limits are not process-memory hard limits",
            }, "cases": cases}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", nargs="+", type=int, default=[500, 5000])
    parser.add_argument("--output-bytes", nargs="+", type=int, default=[128, 4096])
    parser.add_argument("--modes", nargs="+", choices=("legacy", "jsonl"), default=["legacy", "jsonl"])
    parser.add_argument("--max-record-bytes", type=int, default=1048576)
    parser.add_argument("--output-dir", type=Path, help="retain fixtures in a new directory; existing directories are refused")
    parser.add_argument("--worker", choices=("legacy", "jsonl"), help=argparse.SUPPRESS)
    parser.add_argument("--fixture-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if any(value < 1 for value in args.tasks) or any(value < 0 for value in args.output_bytes) or not 1 <= args.max_record_bytes < sys.maxsize:
        parser.error("tasks must be positive, output-bytes non-negative, and max-record-bytes positive and below sys.maxsize")
    if any(len(values) != len(set(values)) for values in (args.tasks, args.output_bytes, args.modes)):
        parser.error("duplicate task counts, output sizes or modes are not allowed")
    try:
        if args.worker:
            if args.fixture_dir is None:
                parser.error("worker requires fixture-dir")
            report = _worker(args.worker, args.fixture_dir, args.max_record_bytes)
        elif args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=False)
            report = _measure(args.output_dir, args.tasks, args.output_bytes, args.modes, args.max_record_bytes)
        else:
            with tempfile.TemporaryDirectory(prefix="bench-comparison-fixture-") as temporary:
                report = _measure(Path(temporary), args.tasks, args.output_bytes, args.modes, args.max_record_bytes)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 1 if any(case["comparison_parity"] is False for case in report.get("cases", [])) else 0


if __name__ == "__main__":
    raise SystemExit(main())
