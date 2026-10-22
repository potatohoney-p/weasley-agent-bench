"""Synthetic offline provenance measurements; generation is a separate process.

python -m experiments.jsonl_provenance_scaling --tasks 500 5000 20000 --output-bytes 4096
No provider/network calls. Measurements do not establish real workload demand.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
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

from .jsonl_scaling import SAMPLE_INTERVAL_SECONDS, _directory_bytes, generate_fixture

ROOT = Path(__file__).resolve().parents[1]
REVISION = "synthetic-provenance-scaling-v1"


def _generate(directory: Path, tasks: int, output_bytes: int) -> dict:
    sizes = generate_fixture(directory, tasks, output_bytes)
    suite, run = [json.loads((directory / name).read_text(encoding="utf-8"))
                  for name in ("suite.json", "run.json")]
    # Independent whole-document oracle: no library digest or provenance helper.
    def digest(document):
        canonical = json.dumps(document, sort_keys=True, ensure_ascii=True,
                               separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    sidecar = {"schema_version": 1, "suite_id": suite["suite_id"], "run_id": run["run_id"],
               "suite_sha256": digest(suite), "run_sha256": digest(run), "runner_revision": REVISION}
    with (directory / "provenance.json").open("x", encoding="utf-8") as stream:
        json.dump(sidecar, stream, sort_keys=True, indent=2)
    sizes["provenance.json"] = (directory / "provenance.json").stat().st_size
    return sizes


def _measure(mode: str, directory: Path) -> dict:
    from weasley_agent_bench import load_document, verify_provenance, verify_provenance_jsonl
    with tempfile.TemporaryDirectory(prefix="provenance-scratch-") as temporary:
        scratch, stop, peak_disk = Path(temporary), threading.Event(), [0]
        def sample():
            while not stop.is_set():
                peak_disk[0] = max(peak_disk[0], _directory_bytes(scratch))
                stop.wait(SAMPLE_INTERVAL_SECONDS)
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        tracemalloc.start()
        started = time.perf_counter()
        try:
            if mode == "legacy":
                report = verify_provenance(
                    *(load_document(directory / name) for name in ("suite.json", "run.json", "provenance.json")),
                    expected_runner_revision=REVISION)
            else:
                report = verify_provenance_jsonl(
                    directory / "suite.jsonl", directory / "run.jsonl", directory / "provenance.json",
                    expected_runner_revision=REVISION, temporary_directory=scratch)
            elapsed = time.perf_counter() - started
            _, peak_python = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            stop.set()
            sampler.join()
    rss, unit = None, None
    if os.name == "posix":
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        unit = "bytes" if sys.platform == "darwin" else "KiB" if sys.platform.startswith("linux") else "platform-dependent"
    return {"mode": mode, "report": report, "elapsed_wall_seconds": elapsed,
            "tracemalloc_peak_bytes": peak_python, "process_ru_maxrss": rss,
            "process_ru_maxrss_unit": unit, "temporary_disk_peak_sampled_bytes": peak_disk[0]}


def _child(action: str, directory: Path, tasks: int, output_bytes: int) -> dict:
    result = subprocess.run(
        [sys.executable, "-B", "-m", "experiments.jsonl_provenance_scaling", "--worker", action,
         "--fixture-dir", str(directory), "--tasks", str(tasks), "--output-bytes", str(output_bytes)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{action} child failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", nargs="+", type=int, default=[500, 5000, 20000])
    parser.add_argument("--output-bytes", type=int, default=4096)
    parser.add_argument("--report", type=Path, help="save evidence to a new file; existing files are refused")
    parser.add_argument("--worker", choices=("generate", "legacy", "jsonl"), help=argparse.SUPPRESS)
    parser.add_argument("--fixture-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if any(n < 1 for n in args.tasks) or args.output_bytes < 0 or len(set(args.tasks)) != len(args.tasks):
        parser.error("task counts must be positive and unique; output-bytes must be non-negative")
    try:
        if args.worker:
            if args.fixture_dir is None:
                parser.error("worker requires fixture-dir")
            report = (_generate(args.fixture_dir, args.tasks[0], args.output_bytes) if args.worker == "generate"
                      else _measure(args.worker, args.fixture_dir))
        else:
            if args.report and args.report.exists():
                raise FileExistsError("evidence target already exists")
            started = datetime.now(timezone.utc).isoformat()
            cases = []
            with tempfile.TemporaryDirectory(prefix="provenance-fixtures-") as temporary:
                for tasks in args.tasks:
                    directory = Path(temporary) / f"tasks-{tasks}"
                    sizes = _child("generate", directory, tasks, args.output_bytes)
                    measured = [_child(mode, directory, tasks, args.output_bytes) for mode in ("legacy", "jsonl")]
                    cases.append({"tasks": tasks, "output_bytes_per_task": args.output_bytes, "fixture_bytes": sizes,
                                  "report_equality": measured[0]["report"] == measured[1]["report"], "measurements": measured})
            report = {"schema_version": 1, "synthetic": True, "started_at_utc": started,
                      "environment": {"python": sys.version, "platform": platform.platform()},
                      "command_arguments": sys.argv[1:] if argv is None else argv,
                      "method": {
                          "generation": "Separate child; full-document json.dumps plus hashlib oracle; excluded from worker RSS",
                          "workers": "Fresh process per mode and task count; legacy then JSONL; one observation per case",
                          "timing": "Includes file loading, validation and hashing; excludes imports, generation and process startup; tracing and disk-sampler overhead included",
                          "tracemalloc": "Python peak allocations; excludes SQLite/native memory and interpreter baseline",
                          "ru_maxrss": "Whole worker lifetime peak including imports and tracing; native units recorded; null on Windows",
                          "scratch": "10ms sampled logical scratch file bytes; lower bound on peak, not allocated blocks; fixtures excluded",
                          "limits": "JSONL API default record/sidecar limits; no process-memory cap; one record plus database/interpreter caches",
                          "claims": "Synthetic engineering evidence only; no provider performance, execution authenticity or real workload demand claim",
                      }, "cases": cases}
        encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False)
        if args.report:
            with args.report.open("x", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
        print(encoded)
        return 1 if any(not case["report_equality"] for case in report.get("cases", [])) else 0
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
