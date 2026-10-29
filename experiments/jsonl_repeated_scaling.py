"""Synthetic repeated-analysis scaling; no provider or network execution.

Cases are TASKS:TRIALS:OUTPUT_BYTES:RESAMPLES. Each uses separate generation and
fresh legacy/JSONL workers. Default cases vary one factor from a shared control.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
from .jsonl_comparison_scaling import _result, _task, _write_documents

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ("100:4:128:333", "100:4:4096:333", "1000:4:4096:333",
                 "100:40:4096:333", "100:4:4096:10000")
META = ("schema_version", "suite_id", "suite_sha256", "baseline_batch_id", "candidate_batch_id", "analysis")


def _order(count, side):
    return range(count - 1, -1, -1) if side == "baseline" else chain(range(0, count, 2), range(1, count, 2))


def _generate(directory, case):
    tasks, trials, output_bytes, _ = case
    directory.mkdir(parents=True, exist_ok=False)
    suite = {"schema_version": 1, "suite_id": f"synthetic-repeated-{tasks}-{trials}-{output_bytes}"}
    _write_documents(directory, "suite", suite, "tasks", (_task(i) for i in range(tasks)))
    payload = "x" * output_bytes
    for side in ("baseline", "candidate"):
        header = {**suite, "batch_id": f"synthetic-{side}"}
        with (directory / f"{side}.json").open("xb") as document, (directory / f"{side}.jsonl").open("xb") as lines:
            document.write(_canonical(header)[:-1] + b',"trials":[')
            lines.write(_canonical({**header, "format": "weasley-agent-bench/batch-jsonl", "format_version": 1}) + b"\n")
            for position, trial in enumerate(_order(trials, side)):
                trial_id = ("a", "\ue000", "\U00010000", "\ud800")[trial] if trial < 4 else f"trial-{trial:09d}"
                run = {**suite, "run_id": f"synthetic-{side}-{trial}"}
                if position:
                    document.write(b",")
                document.write(b'{"trial_id":' + _canonical(trial_id) + b',"run":' + _canonical(run)[:-1] + b',"results":[')
                lines.write(_canonical({"trial_id": trial_id, "run": run}) + b"\n")
                for result_position, index in enumerate(_order(tasks, side)):
                    result = _result(index, side, payload)
                    threshold = 3 if side == "baseline" else 2 + trial % 4
                    result["final"] = index if (index + trial) % 7 < threshold else -1
                    encoded = _canonical(result)
                    if result_position:
                        document.write(b",")
                    document.write(encoded)
                    lines.write(encoded + b"\n")
                document.write(b"]}}")
                lines.write(_canonical({"end_trial": True, "count": tasks}) + b"\n")
            document.write(b"]}\n")
            lines.write(_canonical({"end": True, "count": trials}) + b"\n")
    return {p.name: p.stat().st_size for p in directory.iterdir()}


class _Digest:
    def __init__(self):
        self.rows, self.counts = hashlib.sha256(), {"trial": 0, "task": 0}

    def add(self, kind, row):
        self.rows.update(_canonical({kind: row}) + b"\n")
        self.counts[kind] += 1

    def finish(self, metadata, terminal):
        summary = terminal["summary"]
        if self.counts != {"trial": summary["trial_pairs"], "task": summary["tasks"]}:
            raise ValueError("incomplete repeated report")
        logical = {**metadata, "summary": summary, "interval": terminal["interval"],
                   "row_counts": self.counts, "ordered_rows_sha256": self.rows.hexdigest()}
        return {"logical_report_sha256": hashlib.sha256(_canonical(logical)).hexdigest(),
                "ordered_rows_sha256": self.rows.hexdigest(), "summary": summary, "interval": terminal["interval"]}


def _consume(mode, directory, scratch, seed, resamples):
    from weasley_agent_bench import compare_repeated, compare_repeated_jsonl, load_document
    digest = _Digest()
    if mode == "legacy":
        report = compare_repeated(*(load_document(directory / f"{name}.json") for name in ("suite", "baseline", "candidate")),
                                  seed=seed, resamples=resamples)
        for kind in ("trial", "task"):
            for row in report[kind + "s"]:
                digest.add(kind, row)
        return digest.finish({key: report[key] for key in META}, report)
    metadata = terminal = None
    for event in compare_repeated_jsonl(*(directory / f"{name}.jsonl" for name in ("suite", "baseline", "candidate")),
                                        seed=seed, resamples=resamples, temporary_directory=scratch):
        if terminal is not None:
            raise ValueError("record after completion")
        if metadata is None:
            if event.get("format") != "weasley-agent-bench/repeated-comparison-jsonl" or event.get("format_version") != 1:
                raise ValueError("unexpected report header")
            metadata = {key: event[key] for key in META}
        elif set(event) in ({"trial"}, {"task"}):
            kind = next(iter(event))
            digest.add(kind, event[kind])
        elif event.get("complete") is True:
            terminal = event
        else:
            raise ValueError("unexpected report record")
    if metadata is None or terminal is None:
        raise ValueError("missing completion")
    return digest.finish(metadata, terminal)


def _measure(mode, directory, seed, resamples):
    import weasley_agent_bench  # Resolve library imports before measurement.
    with tempfile.TemporaryDirectory(prefix="repeated-scratch-") as temporary:
        scratch, stop, disk = Path(temporary), threading.Event(), [0]
        def sample():
            while not stop.is_set():
                disk[0] = max(disk[0], _directory_bytes(scratch))
                stop.wait(SAMPLE_INTERVAL_SECONDS)
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        tracemalloc.start()
        started = time.perf_counter()
        try:
            report = _consume(mode, directory, scratch, seed, resamples)
            elapsed, peak = time.perf_counter() - started, tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
            stop.set()
            sampler.join()
    rss, unit = None, None
    if os.name == "posix":
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        unit = "bytes" if sys.platform == "darwin" else "KiB" if sys.platform.startswith("linux") else "platform-dependent"
    return {"mode": mode, **report, "elapsed_wall_seconds": elapsed, "tracemalloc_peak_bytes": peak,
            "process_ru_maxrss": rss, "process_ru_maxrss_unit": unit, "temporary_disk_peak_sampled_bytes": disk[0]}


def _child(action, directory, case, seed):
    result = subprocess.run([sys.executable, "-B", "-m", "experiments.jsonl_repeated_scaling", "--worker", action,
                             "--fixture-dir", str(directory), "--case", ":".join(map(str, case)), "--seed", str(seed)],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{action} worker failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="TASKS:TRIALS:OUTPUT_BYTES:RESAMPLES; repeat for multiple cases")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--report", type=Path, help="new evidence file; never overwrite")
    parser.add_argument("--worker", choices=("generate", "legacy", "jsonl"), help=argparse.SUPPRESS)
    parser.add_argument("--fixture-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        cases = [tuple(map(int, value.split(":"))) for value in (args.case or DEFAULT_CASES)]
        if not 0 <= args.seed < 2**64 or any(len(c) != 4 or c[0] < 1 or c[1] < 2 or c[2] < 0 or not 100 <= c[3] <= 100000 for c in cases):
            raise ValueError("invalid case or seed; require positive tasks, at least two trials, nonnegative bytes, 100..100000 resamples")
        if args.worker:
            if args.fixture_dir is None or len(cases) != 1:
                raise ValueError("worker requires one case and fixture-dir")
            report = (_generate(args.fixture_dir, cases[0]) if args.worker == "generate" else
                      _measure(args.worker, args.fixture_dir, args.seed, cases[0][3]))
        else:
            if args.report and args.report.exists():
                raise FileExistsError("evidence target already exists")
            rows, started = [], datetime.now(timezone.utc).isoformat()
            with tempfile.TemporaryDirectory(prefix="repeated-fixtures-") as temporary:
                for index, case in enumerate(cases):
                    directory = Path(temporary) / f"case-{index}"
                    sizes = _child("generate", directory, case, args.seed)
                    measured = [_child(mode, directory, case, args.seed) for mode in ("legacy", "jsonl")]
                    rows.append({**dict(zip(("tasks", "trials", "output_bytes_per_result", "resamples"), case)),
                                 "fixture_bytes": sizes, "measurements": measured,
                                 "report_parity": measured[0]["logical_report_sha256"] == measured[1]["logical_report_sha256"]})
            report = {"schema_version": 1, "synthetic": True, "started_at_utc": started, "seed": args.seed,
                      "environment": {"python": sys.version, "platform": platform.platform()},
                      "method": {"generation": "Incremental separate child; reversed baseline and even/odd candidate trial/result order; Unicode trial IDs",
                                 "workers": "Fresh processes, legacy then JSONL; one observation each; generation/import/startup excluded from elapsed time",
                                 "digest": "All metadata/analysis, ordered tagged trial/task rows, counts, summary and interval; streamed rows never materialized",
                                 "timing": "Input loading, validation, scoring, bootstrap and digest; includes tracing/sampling overhead",
                                 "memory": "tracemalloc excludes native allocations; ru_maxrss is whole worker peak with native units; null on Windows",
                                 "scratch": "10ms sampled logical scratch file bytes: lower bound on peak, not allocated blocks; fixtures excluded",
                                 "limits": "Default record/cache limits plus O(resamples) bootstrap totals; CPU O(resamples*trials) with disk-backed lookups",
                                 "claims": "Synthetic engineering only; named trials are not independent empirical observations; no provider, authenticity or adoption claim"},
                      "cases": rows}
        encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False)
        if args.report:
            with args.report.open("x", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
        print(encoded)
        return int(any(not row["report_parity"] for row in report.get("cases", [])))
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
