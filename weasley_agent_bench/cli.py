"""JSON-in/JSON-out command line interface; never invokes providers or tools."""

from __future__ import annotations

import argparse
import json
import sys

from .contracts import ContractError, load_document
from .scoring import compare, evaluate
from .jsonl import DEFAULT_MAX_RECORD_BYTES, compare_jsonl, evaluate_jsonl
from .repeated import compare_repeated
from .provenance import create_provenance, verify_provenance
from .linting import lint_suite
from .subagents import export_subagents, load_subagents_transcript


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    lint = sub.add_parser("lint-suite", help="check static required-call budget contradictions")
    lint.add_argument("suite")
    lint.add_argument("--allow-contradictions", action="store_true",
                      help="accept intentional negative fixtures without hiding lint findings")
    score = sub.add_parser("evaluate", help="score one saved run against a task suite")
    score.add_argument("suite")
    score.add_argument("run")
    stream = sub.add_parser("evaluate-jsonl", help="score bounded JSONL files using temporary disk storage")
    stream.add_argument("suite")
    stream.add_argument("run")
    stream.add_argument("--max-record-bytes", type=int, default=DEFAULT_MAX_RECORD_BYTES)
    stream.add_argument("--temporary-directory", help="parent directory for temporary evaluation storage")
    diff = sub.add_parser("compare", help="compare two complete runs on the same tasks")
    diff.add_argument("suite")
    diff.add_argument("baseline")
    diff.add_argument("candidate")
    paired_stream = sub.add_parser("compare-jsonl", help="compare complete JSONL runs using temporary disk storage")
    paired_stream.add_argument("suite")
    paired_stream.add_argument("baseline")
    paired_stream.add_argument("candidate")
    paired_stream.add_argument("--max-record-bytes", type=int, default=DEFAULT_MAX_RECORD_BYTES)
    paired_stream.add_argument("--temporary-directory", help="parent directory for temporary comparison storage")
    repeated = sub.add_parser("compare-repeated", help="describe paired trials on a fixed task suite")
    repeated.add_argument("suite")
    repeated.add_argument("baseline")
    repeated.add_argument("candidate")
    repeated.add_argument("--seed", type=int, default=0)
    repeated.add_argument("--resamples", type=int, default=10000)
    record = sub.add_parser("create-provenance", help="emit an unsigned suite/run consistency sidecar")
    record.add_argument("suite")
    record.add_argument("run")
    record.add_argument("--runner-revision", required=True, help="declared external runner revision")
    verify = sub.add_parser("verify-provenance", help="check a sidecar against saved suite/run artifacts")
    verify.add_argument("suite")
    verify.add_argument("run")
    verify.add_argument("provenance")
    verify.add_argument("--expected-runner-revision", help="optionally require this exact declared revision")
    export = sub.add_parser("export-subagents", help="convert a supported saved foreground text projection")
    export.add_argument("suite")
    export.add_argument("transcript")
    export.add_argument("metadata")
    export.add_argument("--task-id", required=True)
    export.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "compare-jsonl":
            for event in compare_jsonl(args.suite, args.baseline, args.candidate,
                                       max_record_bytes=args.max_record_bytes,
                                       temporary_directory=args.temporary_directory):
                print(json.dumps(event, sort_keys=True, ensure_ascii=True, allow_nan=False))
            return 0 if event["no_regressions"] else 1
        if args.command == "evaluate-jsonl":
            for event in evaluate_jsonl(args.suite, args.run, max_record_bytes=args.max_record_bytes,
                                        temporary_directory=args.temporary_directory):
                print(json.dumps(event, sort_keys=True, ensure_ascii=True, allow_nan=False))
            return 0 if event["passed"] else 1
        suite = load_document(args.suite)
        if args.command == "export-subagents":
            report = export_subagents(suite, load_subagents_transcript(args.transcript),
                                      load_document(args.metadata), task_id=args.task_id, run_id=args.run_id)
            passed = True
        elif args.command == "lint-suite":
            report = lint_suite(suite, allow_contradictions=args.allow_contradictions)
            passed = report["accepted"]
        elif args.command == "evaluate":
            report = evaluate(suite, load_document(args.run))
            passed = report["passed"]
        elif args.command == "compare":
            report = compare(suite, load_document(args.baseline), load_document(args.candidate))
            # This is a regression gate, not a claim that every candidate task passed.
            passed = report["no_regressions"]
        elif args.command == "compare-repeated":
            report = compare_repeated(
                suite, load_document(args.baseline), load_document(args.candidate),
                seed=args.seed, resamples=args.resamples,
            )
            # Valid descriptive analysis, even when all observed changes are negative.
            passed = True
        elif args.command == "create-provenance":
            report = create_provenance(suite, load_document(args.run), runner_revision=args.runner_revision)
            passed = True
        else:
            report = verify_provenance(
                suite, load_document(args.run), load_document(args.provenance),
                expected_runner_revision=args.expected_runner_revision,
            )
            passed = True
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False))
        return 0 if passed else 1
    except (ContractError, RecursionError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 2
