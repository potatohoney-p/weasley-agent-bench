from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from weasley_agent_bench import ContractError, evaluate, evaluate_jsonl, load_document

ROOT = Path(__file__).resolve().parents[1]


def records(document, kind):
    field = "tasks" if kind == "suite" else "results"
    header = {key: value for key, value in document.items() if key != field}
    header.update(format=f"weasley-agent-bench/{kind}-jsonl", format_version=1)
    return [header, *document[field], {"end": True, "count": len(document[field])}]


def write_records(path, values):
    path.write_bytes(b"".join(
        (json.dumps(value, ensure_ascii=True) + "\n").encode("utf-8") for value in values
    ))


def materialize(events):
    header, *rest = events
    terminal = rest.pop()
    assert terminal["complete"] is True
    return {**{key: header[key] for key in ("schema_version", "suite_id", "suite_sha256", "run_id")},
            "passed": terminal["passed"], "summary": terminal["summary"],
            "results": [event["result"] for event in rest]}


class JsonlEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.suite_path = self.root / "suite.jsonl"
        self.run_path = self.root / "run.jsonl"
        self.suite = load_document(ROOT / "examples/suite.json")
        self.run = load_document(ROOT / "examples/candidate.json")

    def write_inputs(self):
        write_records(self.suite_path, records(self.suite, "suite"))
        write_records(self.run_path, records(self.run, "run"))

    def cli(self, *options):
        return subprocess.run(
            [sys.executable, "-m", "weasley_agent_bench", "evaluate-jsonl",
             str(self.suite_path), str(self.run_path), *options],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def test_cli_preserves_scores_digest_order_and_missing_coverage(self):
        self.run["results"].reverse()
        self.run["results"].pop()
        self.write_inputs()
        result = self.cli()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(materialize([json.loads(line) for line in result.stdout.splitlines()]),
                         evaluate(self.suite, self.run))

    def test_public_iterator_parity_for_scoring_features_and_empty_run(self):
        for name in ("baseline", "candidate"):
            self.run = load_document(ROOT / "examples" / f"{name}.json")
            self.run["results"].reverse()
            self.write_inputs()
            self.assertEqual(materialize(list(evaluate_jsonl(self.suite_path, self.run_path))),
                             evaluate(self.suite, self.run))
        self.run["results"] = []
        self.write_inputs()
        self.assertEqual(materialize(list(evaluate_jsonl(self.suite_path, self.run_path))),
                         evaluate(self.suite, self.run))

    def test_types_unicode_ids_and_cancelled_results_keep_legacy_meaning(self):
        self.suite["suite_id"] = self.run["suite_id"] = "synthetic-서울-\ud800"
        self.suite["tasks"][0]["id"] = self.run["results"][0]["task_id"] = "case-\ud800"
        for actual, expected in ((True, 1), ([1.0], [1]), ({"a/b": [None]}, {"a/b": [None]})):
            self.suite["tasks"][0]["expect"]["final"] = [{"path": "", "equals": expected}]
            self.run["results"][0]["final"] = actual
            self.run["results"][1]["status"] = "cancelled"
            self.write_inputs()
            self.assertEqual(materialize(list(evaluate_jsonl(self.suite_path, self.run_path))),
                             evaluate(self.suite, self.run))

    def test_passing_cli_has_terminal_summary_and_exit_zero(self):
        self.write_inputs()
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(materialize(events), evaluate(self.suite, self.run))
        self.assertEqual(events[-1]["complete"], True)

    def test_late_contract_errors_emit_nothing_and_identify_physical_line(self):
        for source in ("suite", "run"):
            document = self.suite if source == "suite" else self.run
            original = records(document, source)
            duplicate = copy.deepcopy(original[:-1]) + [copy.deepcopy(original[1]), {"end": True, "count": 5}]
            invalid = copy.deepcopy(original)
            invalid[-2]["private-payload-sentinel"] = "never echo this"
            bad_count = copy.deepcopy(original)
            bad_count[-1]["count"] = 999
            cases = [(original[:-1], 6), (duplicate, 6), (invalid, 5), (bad_count, 6),
                     (original + [{"end": True, "count": 4}], 7)]
            if source == "run":
                unknown = copy.deepcopy(original)
                unknown[-2]["task_id"] = "unknown"
                cases.append((unknown, 5))
            for values, line in cases:
                with self.subTest(source=source, values=values[-1]):
                    self.write_inputs()
                    path = self.suite_path if source == "suite" else self.run_path
                    write_records(path, values)
                    result = self.cli()
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(f"{source} JSONL line {line}:", json.loads(result.stderr)["error"])
                    self.assertNotIn("private-payload-sentinel", result.stderr)

    def test_strict_json_and_binary_decode_errors_before_any_report(self):
        for invalid in (b"\n", b"{\xff}\n", b'{"a":1,"a":2}\n', b'{"a":NaN}\n',
                        b"[]\n", b"{" * 2000 + b"\n"):
            with self.subTest(invalid=invalid[:30]):
                self.write_inputs()
                raw = self.run_path.read_bytes().splitlines(keepends=True)
                self.run_path.write_bytes(b"".join(raw[:-1]) + invalid)
                iterator = evaluate_jsonl(self.suite_path, self.run_path)
                with self.assertRaisesRegex(ContractError, "run JSONL line 6:"):
                    next(iterator)

    def test_headers_counts_and_nonempty_suite_are_strict(self):
        for source in ("suite", "run"):
            original = records(self.suite if source == "suite" else self.run, source)
            for field, value in (("format_version", True), ("schema_version", 2),
                                 ("extra", 1), ("suite_id", ""), ("format", "other")):
                self.write_inputs()
                values = copy.deepcopy(original)
                values[0][field] = value
                path = self.suite_path if source == "suite" else self.run_path
                write_records(path, values)
                with self.assertRaisesRegex(ContractError, f"{source} JSONL line 1:"):
                    next(evaluate_jsonl(self.suite_path, self.run_path))
        self.write_inputs()
        write_records(self.suite_path, [records(self.suite, "suite")[0], {"end": True, "count": 0}])
        with self.assertRaisesRegex(ContractError, "suite JSONL line 2: at least one"):
            next(evaluate_jsonl(self.suite_path, self.run_path))
        self.write_inputs()
        values = records(self.run, "run")
        values[0]["suite_id"] = "wrong-suite"
        write_records(self.run_path, values)
        with self.assertRaisesRegex(ContractError, "suite_id does not match"):
            next(evaluate_jsonl(self.suite_path, self.run_path))

    def test_record_byte_limit_includes_newline_and_cannot_be_disabled(self):
        self.run["results"][0]["final"] = "x" * 4096
        self.write_inputs()
        limit = max(map(len, self.run_path.read_bytes().splitlines(keepends=True)))
        self.assertEqual(materialize(list(evaluate_jsonl(self.suite_path, self.run_path,
                                                       max_record_bytes=limit))),
                         evaluate(self.suite, self.run))
        with self.assertRaisesRegex(ContractError, "run JSONL line 2: record exceeds"):
            next(evaluate_jsonl(self.suite_path, self.run_path, max_record_bytes=limit - 1))
        for bad in (0, -1, True, 1.5, sys.maxsize, 10**100):
            with self.assertRaisesRegex(ContractError, "positive integer"):
                next(evaluate_jsonl(self.suite_path, self.run_path, max_record_bytes=bad))
        result = self.cli("--max-record-bytes", str(sys.maxsize))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("positive integer", json.loads(result.stderr)["error"])

    def test_lf_crlf_and_final_record_without_newline_preserve_digest(self):
        self.write_inputs()
        for path in (self.suite_path, self.run_path):
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n").rstrip(b"\r\n"))
        self.assertEqual(materialize(list(evaluate_jsonl(self.suite_path, self.run_path))),
                         evaluate(self.suite, self.run))

    def test_temporary_storage_released_on_completion_error_and_early_close(self):
        scratch = self.root / "scratch"
        scratch.mkdir()
        self.write_inputs()
        list(evaluate_jsonl(self.suite_path, self.run_path, temporary_directory=scratch))
        self.assertEqual(list(scratch.iterdir()), [])
        iterator = evaluate_jsonl(self.suite_path, self.run_path, temporary_directory=scratch)
        next(iterator)
        self.assertTrue(list(scratch.iterdir()))
        iterator.close()
        self.assertEqual(list(scratch.iterdir()), [])
        self.run_path.write_bytes(b"not JSON\n")
        with self.assertRaises(ContractError):
            next(evaluate_jsonl(self.suite_path, self.run_path, temporary_directory=scratch))
        self.assertEqual(list(scratch.iterdir()), [])

    def test_missing_input_or_unavailable_temporary_directory_is_contract_error(self):
        self.write_inputs()
        with self.assertRaisesRegex(ContractError, "suite JSONL line 1: cannot read"):
            next(evaluate_jsonl(self.root / "absent", self.run_path))
        with self.assertRaisesRegex(ContractError, "temporary storage failure"):
            next(evaluate_jsonl(self.suite_path, self.run_path,
                                temporary_directory=self.root / "absent"))


if __name__ == "__main__":
    unittest.main()
