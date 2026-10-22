from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from weasley_agent_bench import ContractError, compare, compare_jsonl, evaluate_jsonl, load_document

ROOT = Path(__file__).resolve().parents[1]


def write_jsonl(path, document, kind):
    field = "tasks" if kind == "suite" else "results"
    header = {key: value for key, value in document.items() if key != field}
    header.update(format=f"weasley-agent-bench/{kind}-jsonl", format_version=1)
    values = [header, *document[field], {"end": True, "count": len(document[field])}]
    path.write_bytes(b"".join((json.dumps(value, ensure_ascii=True) + "\n").encode("utf-8")
                              for value in values))


def materialize(events):
    header, *remaining = events
    terminal = remaining.pop()
    assert header["format"] == "weasley-agent-bench/comparison-jsonl"
    assert terminal["complete"] is True
    fields = ("schema_version", "suite_id", "suite_sha256", "baseline_run_id", "candidate_run_id")
    return {**{key: header[key] for key in fields},
            **{key: terminal[key] for key in ("no_regressions", "candidate_passed", "summary")},
            "changes": [event["change"] for event in remaining]}


class JsonlComparisonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.paths = [self.root / f"{name}.jsonl" for name in ("suite", "baseline", "candidate")]
        self.suite = load_document(ROOT / "examples/suite.json")
        self.baseline = load_document(ROOT / "examples/candidate.json")
        self.candidate = load_document(ROOT / "examples/baseline.json")

    def write_inputs(self):
        for path, document, kind in zip(self.paths, (self.suite, self.baseline, self.candidate),
                                        ("suite", "run", "run")):
            write_jsonl(path, document, kind)

    def cli(self, *options):
        return subprocess.run(
            [sys.executable, "-m", "weasley_agent_bench", "compare-jsonl",
             *map(str, self.paths), *options], cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def test_cli_pairs_shuffled_runs_and_matches_legacy_regression_gate(self):
        self.baseline["results"].reverse()
        self.candidate["results"] = self.candidate["results"][2:] + self.candidate["results"][:2]
        self.write_inputs()
        result = self.cli()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, "")
        report = materialize([json.loads(line) for line in result.stdout.splitlines()])
        self.assertEqual(report, compare(self.suite, self.baseline, self.candidate))
        self.assertEqual(report["summary"]["regressions"], 2)

    def test_swapped_failures_cannot_hide_behind_equal_pass_totals(self):
        self.candidate = copy.deepcopy(self.baseline)
        self.baseline["results"][0]["status"] = "error"
        self.candidate["results"][1]["status"] = "cancelled"
        self.write_inputs()
        report = materialize(list(compare_jsonl(*self.paths)))
        self.assertEqual(report, compare(self.suite, self.baseline, self.candidate))
        self.assertEqual(report["summary"], {"total": 4, "regressions": 1, "improvements": 1,
                                           "baseline_passed": 3, "candidate_passed": 3})
        self.assertFalse(report["no_regressions"])
        self.assertFalse(report["candidate_passed"])

    def test_unchanged_failures_pass_gate_with_same_run_ids(self):
        self.baseline = copy.deepcopy(self.candidate)
        self.baseline["results"].reverse()
        self.write_inputs()
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = materialize([json.loads(line) for line in result.stdout.splitlines()])
        self.assertEqual(report, compare(self.suite, self.baseline, self.candidate))
        self.assertTrue(report["no_regressions"])
        self.assertFalse(report["candidate_passed"])
        self.assertEqual(report["summary"]["candidate_passed"], 2)

    def test_joint_telemetry_and_unicode_ids_preserve_comparison_meaning(self):
        self.suite["suite_id"] = self.baseline["suite_id"] = self.candidate["suite_id"] = "서울-\ud800"
        self.suite["tasks"][0]["id"] = "task-\ud800"
        self.baseline["results"][0]["task_id"] = self.candidate["results"][0]["task_id"] = "task-\ud800"
        self.baseline["results"][0]["usage"] = {"input_tokens": 0, "duration_ms": 2.5}
        self.candidate["results"][0]["usage"] = {"input_tokens": 5, "duration_ms": 1, "output_tokens": 9}
        self.baseline["results"][1].pop("usage", None)
        self.candidate["results"][1]["usage"] = {"output_tokens": 0}
        self.write_inputs()
        report = materialize(list(compare_jsonl(*self.paths)))
        self.assertEqual(report, compare(self.suite, self.baseline, self.candidate))
        self.assertEqual(report["changes"][0]["usage_delta"], {"input_tokens": 5, "duration_ms": -1.5})
        self.assertEqual(report["changes"][1]["usage_delta"], {})

    def test_missing_coverage_on_either_side_is_rejected_without_output(self):
        originals = (copy.deepcopy(self.baseline), copy.deepcopy(self.candidate))
        for side in ("baseline", "candidate"):
            for count in (0, 3):
                self.baseline, self.candidate = copy.deepcopy(originals)
                getattr(self, side)["results"] = getattr(self, side)["results"][:count]
                self.write_inputs()
                result = self.cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("one result for every task", json.loads(result.stderr)["error"])

    def test_both_streams_validate_before_missing_coverage_error(self):
        self.baseline["results"] = []
        self.write_inputs()
        with self.paths[2].open("ab") as stream:
            stream.write(b"private-invalid-payload\n")
        with self.assertRaisesRegex(ContractError, "candidate JSONL line 7:"):
            next(compare_jsonl(*self.paths))

    def test_late_invalid_run_records_are_source_labeled_and_emit_nothing(self):
        for side, index in (("baseline", 1), ("candidate", 2)):
            for mutation in ("duplicate", "unknown", "missing_end", "wrong_count", "trailing",
                             "invalid_utf8", "duplicate_key", "blank", "unknown_field"):
                with self.subTest(side=side, mutation=mutation):
                    self.write_inputs()
                    records = self.paths[index].read_bytes().splitlines(keepends=True)
                    line = 6
                    if mutation == "duplicate":
                        records.insert(-1, records[1])
                    elif mutation == "unknown":
                        item = json.loads(records[-2])
                        item["task_id"] = "unknown-task"
                        records[-2] = (json.dumps(item) + "\n").encode()
                        line = 5
                    elif mutation == "unknown_field":
                        item = json.loads(records[-2])
                        item["private-invalid-payload"] = True
                        records[-2] = (json.dumps(item) + "\n").encode()
                        line = 5
                    elif mutation == "missing_end":
                        records.pop()
                    elif mutation == "wrong_count":
                        records[-1] = b'{"end":true,"count":3}\n'
                    elif mutation == "trailing":
                        records.append(b'{}\n')
                        line = 7
                    else:
                        records[-1] = {"invalid_utf8": b"\xff\n", "duplicate_key": b'{"a":1,"a":2}\n',
                                       "blank": b"\n"}[mutation]
                    self.paths[index].write_bytes(b"".join(records))
                    result = self.cli()
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(f"{side} JSONL line {line}:", json.loads(result.stderr)["error"])
                    self.assertNotIn("private-invalid-payload", result.stderr)

    def test_incompatible_suite_ids_and_transport_headers_are_rejected(self):
        for index, side in ((1, "baseline"), (2, "candidate")):
            for field, value in (("suite_id", "other-suite"), ("format_version", True),
                                 ("format", "weasley-agent-bench/comparison-jsonl")):
                self.write_inputs()
                lines = self.paths[index].read_bytes().splitlines(keepends=True)
                header = json.loads(lines[0])
                header[field] = value
                lines[0] = (json.dumps(header) + "\n").encode()
                self.paths[index].write_bytes(b"".join(lines))
                with self.assertRaisesRegex(ContractError, f"{side} JSONL line 1:"):
                    next(compare_jsonl(*self.paths))

    def test_record_limits_apply_to_both_runs_and_invalid_limits_are_errors(self):
        for side, index in (("baseline", 1), ("candidate", 2)):
            old = getattr(self, side)["results"][0]["final"]
            getattr(self, side)["results"][0]["final"] = "x" * 4096
            self.write_inputs()
            limit = max(map(len, self.paths[index].read_bytes().splitlines(keepends=True)))
            self.assertEqual(materialize(list(compare_jsonl(*self.paths, max_record_bytes=limit))),
                             compare(self.suite, self.baseline, self.candidate))
            with self.assertRaisesRegex(ContractError, f"{side} JSONL line 2: record exceeds"):
                next(compare_jsonl(*self.paths, max_record_bytes=limit - 1))
            getattr(self, side)["results"][0]["final"] = old
        for limit in (0, -1, True, sys.maxsize):
            with self.assertRaisesRegex(ContractError, "positive integer"):
                next(compare_jsonl(*self.paths, max_record_bytes=limit))

    def test_iterator_cleans_scratch_on_completion_error_and_early_close(self):
        self.write_inputs()
        scratch = self.root / "scratch"
        scratch.mkdir()
        expected = materialize(list(compare_jsonl(*self.paths, temporary_directory=scratch)))
        self.assertEqual(expected, compare(self.suite, self.baseline, self.candidate))
        self.assertEqual(list(scratch.iterdir()), [])
        iterator = compare_jsonl(*self.paths, temporary_directory=scratch)
        self.assertEqual(next(iterator)["format"], "weasley-agent-bench/comparison-jsonl")
        iterator.close()
        self.assertEqual(list(scratch.iterdir()), [])
        self.paths[2].write_bytes(b"invalid\n")
        with self.assertRaises(ContractError):
            next(compare_jsonl(*self.paths, temporary_directory=scratch))
        self.assertEqual(list(scratch.iterdir()), [])

    def test_shared_suite_defines_rescoring_and_digest(self):
        self.write_inputs()
        before = materialize(list(compare_jsonl(*self.paths)))
        self.suite["tasks"].reverse()
        self.suite["tasks"][0]["prompt"] += " synthetic rescore"
        self.write_inputs()
        after = materialize(list(compare_jsonl(*self.paths)))
        self.assertEqual(after, compare(self.suite, self.baseline, self.candidate))
        self.assertNotEqual(before["suite_sha256"], after["suite_sha256"])
        self.assertEqual(after["changes"], list(reversed(before["changes"])))

    def test_late_scoring_depth_failure_releases_scratch_before_any_output(self):
        nested = None
        for _ in range(500):
            nested = [nested]
        self.suite["tasks"][-2]["expect"] = {"final": [{"path": "", "equals": nested}]}
        self.baseline["results"][-2]["final"] = self.candidate["results"][-2]["final"] = nested
        self.write_inputs()
        scratch = self.root / "scratch"
        scratch.mkdir()
        for operation, paths in ((compare_jsonl, self.paths), (evaluate_jsonl, self.paths[:2])):
            with self.assertRaisesRegex(ContractError, "suite JSONL line 4: record exceeds scoring limits"):
                next(operation(*paths, temporary_directory=scratch))
            self.assertEqual(list(scratch.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
