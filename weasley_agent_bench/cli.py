"""JSON-in/JSON-out command line interface; never invokes providers or tools."""

from __future__ import annotations

import argparse
import json
import sys

from .contracts import ContractError, load_document
from .scoring import compare, evaluate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    score = sub.add_parser("evaluate", help="score one saved run against a task suite")
    score.add_argument("suite")
    score.add_argument("run")
    diff = sub.add_parser("compare", help="compare two complete runs on the same tasks")
    diff.add_argument("suite")
    diff.add_argument("baseline")
    diff.add_argument("candidate")
    args = parser.parse_args(argv)
    try:
        suite = load_document(args.suite)
        if args.command == "evaluate":
            report = evaluate(suite, load_document(args.run))
            passed = report["passed"]
        else:
            report = compare(suite, load_document(args.baseline), load_document(args.candidate))
            # This is a regression gate, not a claim that every candidate task passed.
            passed = report["no_regressions"]
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False))
        return 0 if passed else 1
    except (ContractError, RecursionError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 2
