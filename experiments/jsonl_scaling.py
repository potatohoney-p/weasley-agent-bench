"""Synthetic, offline scaling measurements in fresh evaluator processes.

Examples:
    python -m experiments.jsonl_scaling --tasks 500 5000 --output-bytes 128 4096
    python -m experiments.jsonl_scaling --tasks 500 --modes legacy

This measures evaluator engineering behavior, not agent/provider performance or
real workload demand. Timing and process memory are observations, not constants.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
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


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_INTERVAL_SECONDS = 0.01


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def generate_fixture(directory: Path, tasks: int, output_bytes: int) -> dict:
    """Write equivalent formats one task/result at a time into a new directory.

    Existing directories are refused. A write error can leave a partial fixture.
    output_bytes is the exact ASCII byte length of each retained tool output.
    """
    if tasks < 1 or output_bytes < 0:
        raise ValueError("tasks must be positive and output_bytes non-negative")
    directory.mkdir(parents=True, exist_ok=False)
    suite_id = f"synthetic-scaling-{tasks}-{output_bytes}"
    suite_header = {"schema_version": 1, "suite_id": suite_id}
    run_header = {**suite_header, "run_id": "synthetic-scaling-run"}
    payload = "x" * output_bytes
    names = ("suite.json", "run.json", "suite.jsonl", "run.jsonl")
    with ExitStack() as stack:
        suite, run, suite_lines, run_lines = [
            stack.enter_context((directory / name).open("xb")) for name in names
        ]
        suite.write(_canonical(suite_header)[:-1] + b',"tasks":[')
        run.write(_canonical(run_header)[:-1] + b',"results":[')
        for stream, kind, header in ((suite_lines, "suite", suite_header),
                                     (run_lines, "run", run_header)):
            stream.write(_canonical({"format": f"weasley-agent-bench/{kind}-jsonl",
                                     "format_version": 1, **header}) + b"\n")
        for index in range(tasks):
            task_id = f"task-{index:09d}"
            task = {"id": task_id, "prompt": f"Synthetic lookup for record {index}.",
                    "expect": {"final": [{"path": "", "equals": index}],
                               "tools": {"mode": "exact", "steps": [
                                   {"name": "synthetic_lookup", "outcome": "success"}]}},
                    "budget": {"max_tool_calls": 1}}
            result = {"task_id": task_id, "status": "completed",
                      "final": index if index % 11 else -1,
                      "tool_calls": [{"name": "synthetic_lookup", "arguments": {"id": index},
                                      "outcome": "success", "output": payload}]}
            for document, stream, lines in ((task, suite, suite_lines), (result, run, run_lines)):
                encoded = _canonical(document)
                if index:
                    stream.write(b",")
                stream.write(encoded)
                lines.write(encoded + b"\n")
        suite.write(b"]}\n")
        run.write(b"]}\n")
        end = _canonical({"end": True, "count": tasks}) + b"\n"
        suite_lines.write(end)
        run_lines.write(end)
    return {name: (directory / name).stat().st_size for name in names}


class _ReportDigest:
    """Bind every ordered row plus all logical v1 report metadata and totals."""

    def __init__(self):
        self.rows = hashlib.sha256()
        self.count = 0

    def add(self, row: dict) -> None:
        self.rows.update(_canonical(row) + b"\n")
        self.count += 1

    def finish(self, metadata: dict, summary: dict, passed: bool) -> dict:
        if self.count != summary["total"]:
            raise ValueError("incomplete report: row count does not match summary")
        logical = {**metadata, "passed": passed, "summary": summary,
                   "ordered_results_sha256": self.rows.hexdigest(), "result_count": self.count}
        return {"logical_report_sha256": hashlib.sha256(_canonical(logical)).hexdigest(),
                "ordered_results_sha256": self.rows.hexdigest(),
                "suite_sha256": metadata["suite_sha256"], "summary": summary, "passed": passed}


def _consume_legacy(directory: Path) -> dict:
    from weasley_agent_bench import evaluate, load_document
    report = evaluate(load_document(directory / "suite.json"), load_document(directory / "run.json"))
    digest = _ReportDigest()
    for row in report["results"]:
        digest.add(row)
    metadata = {key: report[key] for key in ("schema_version", "suite_id", "suite_sha256", "run_id")}
    return digest.finish(metadata, report["summary"], report["passed"])


def _consume_jsonl(directory: Path, scratch: Path, max_record_bytes: int) -> dict:
    from weasley_agent_bench import evaluate_jsonl
    digest = _ReportDigest()
    metadata = None
    terminal = None
    for record in evaluate_jsonl(directory / "suite.jsonl", directory / "run.jsonl",
                                 max_record_bytes=max_record_bytes, temporary_directory=scratch):
        if terminal is not None:
            raise ValueError("report contains records after its terminal summary")
        if metadata is None:
            if record.get("format") != "weasley-agent-bench/report-jsonl" or record.get("format_version") != 1:
                raise ValueError("unexpected streaming report header")
            metadata = {key: record[key] for key in ("schema_version", "suite_id", "suite_sha256", "run_id")}
        elif "result" in record:
            digest.add(record["result"])
        elif record.get("complete") is True:
            terminal = record
        else:
            raise ValueError("unexpected streaming report record")
    if metadata is None or terminal is None:
        raise ValueError("streaming report did not complete")
    return digest.finish(metadata, terminal["summary"], terminal["passed"])


def _directory_bytes(directory: Path) -> int:
    total = 0
    for root, _, files in os.walk(directory):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except FileNotFoundError:
                pass  # Evaluator cleanup can race the sampler.
    return total


def _worker(mode: str, directory: Path, max_record_bytes: int) -> dict:
    # Resolve imports before the timed/traced evaluation in both modes.
    import weasley_agent_bench
    if mode == "jsonl" and not hasattr(weasley_agent_bench, "evaluate_jsonl"):
        raise ValueError("evaluate_jsonl is unavailable; use --modes legacy for baseline-only measurement")
    with tempfile.TemporaryDirectory(prefix="bench-scaling-scratch-") as temporary:
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
            score = (_consume_legacy(directory) if mode == "legacy" else
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
    return {"mode": mode, **score, "elapsed_wall_seconds": elapsed,
            "tracemalloc_peak_bytes": peak_python, "process_ru_maxrss": rss,
            "process_ru_maxrss_unit": rss_unit,
            "temporary_disk_peak_sampled_bytes": peak_disk[0]}


def _run_child(mode: str, directory: Path, max_record_bytes: int) -> dict:
    process = subprocess.run(
        [sys.executable, "-B", "-m", "experiments.jsonl_scaling", "--worker", mode,
         "--fixture-dir", str(directory.resolve()), "--max-record-bytes", str(max_record_bytes)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if process.returncode:
        raise RuntimeError(f"{mode} measurement failed: {process.stderr.strip()}")
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
            cases.append({"tasks": tasks, "output_bytes_per_task": output_bytes,
                          "fixture_bytes": sizes_on_disk, "measurements": measurements,
                          "score_parity": len(digests) == 1 if len(measurements) > 1 else None})
    return {"schema_version": 1, "synthetic": True,
            "purpose": "Offline evaluator scaling; not provider performance or evidence of real workload demand",
            "environment": {"python": sys.version, "platform": platform.platform()},
            "measurement": {
                "fresh_process_per_case_and_mode": True, "max_record_bytes": max_record_bytes,
                "includes": "input loading, validation, scoring and compact report digest consumption",
                "excludes": "fixture generation, child process startup and evaluator module import",
                "tracemalloc": "Python allocations during evaluation; excludes SQLite/native allocations",
                "ru_maxrss": "Whole child process lifetime peak; null on Windows; includes tracing overhead",
                "temporary_disk": "Sampled logical file bytes in isolated evaluator scratch directory; a lower bound on peak, not allocated disk blocks",
                "temporary_disk_sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
                "digest": "SHA-256 of canonical v1 metadata, passed, summary, row count and ordered canonical-row SHA-256",
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
    if any(value < 1 for value in args.tasks) or any(value < 0 for value in args.output_bytes) or args.max_record_bytes < 1:
        parser.error("tasks and max-record-bytes must be positive; output-bytes must be non-negative")
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
            with tempfile.TemporaryDirectory(prefix="bench-scaling-fixture-") as temporary:
                report = _measure(Path(temporary), args.tasks, args.output_bytes, args.modes, args.max_record_bytes)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 1 if any(case["score_parity"] is False for case in report.get("cases", [])) else 0


if __name__ == "__main__":
    raise SystemExit(main())
