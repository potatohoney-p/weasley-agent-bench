from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from weasley_agent_bench import ContractError, compare, evaluate, load_document, validate_run, validate_suite

ROOT = Path(__file__).resolve().parents[1]


def fixture(name):
    return load_document(ROOT / "examples" / f"{name}.json")


def single(final=None, expected=None):
    suite = {"schema_version": 1, "suite_id": "one", "tasks": [
        {"id": "case", "prompt": "synthetic", "expect": {"final": [{"path": "", "equals": expected}]}}
    ]}
    run = {"schema_version": 1, "suite_id": "one", "run_id": "run", "results": [
        {"task_id": "case", "status": "completed", "final": final, "tool_calls": []}
    ]}
    return suite, run


def failed_codes(report):
    return [check["code"] for result in report["results"] for check in result["checks"] if not check["passed"]]


class ScoringTests(unittest.TestCase):
    def test_demo_exposes_stale_memory_and_lost_handoff(self):
        result = evaluate(fixture("suite"), fixture("baseline"))
        self.assertEqual(result["summary"], {"total": 4, "passed": 2, "failed": 2, "missing": 0})
        self.assertEqual([row["task_id"] for row in result["results"] if not row["passed"]],
                         ["corrected-memory", "review-handoff"])
        self.assertTrue(evaluate(fixture("suite"), fixture("candidate"))["passed"])

    def test_missing_results_are_failures_in_denominator(self):
        run = fixture("candidate")
        run["results"].pop()
        report = evaluate(fixture("suite"), run)
        self.assertEqual(report["summary"], {"total": 4, "passed": 3, "failed": 1, "missing": 1})
        self.assertEqual(report["results"][-1]["status"], "missing")
        self.assertEqual(failed_codes(report), ["result.present"])

    def test_cancelled_or_error_cannot_pass_with_correct_output(self):
        for status in ("error", "cancelled"):
            with self.subTest(status=status):
                suite, run = single(True, True)
                run["results"][0]["status"] = status
                self.assertEqual(failed_codes(evaluate(suite, run)), ["result.completed"])

    def test_pointer_handles_arrays_escaped_keys_unicode_and_null(self):
        value = {"a/b": {"~서울": [None, {"": "ok"}]}}
        suite, run = single(value)
        suite["tasks"][0]["expect"]["final"] = [
            {"path": "/a~1b/~0서울/0", "equals": None},
            {"path": "/a~1b/~0서울/1/", "equals": "ok"},
        ]
        self.assertTrue(evaluate(suite, run)["passed"])

    def test_missing_is_not_null_and_invalid_indices_do_not_alias(self):
        for pointer in ("/missing", "/a/01", "/a/-1", "/a/+0", "/a/٠", "/a/-", "/a/" + "9" * 5000):
            with self.subTest(pointer=pointer[:50]):
                suite, run = single({"a": [None]})
                suite["tasks"][0]["expect"]["final"] = [{"path": pointer, "equals": None}]
                self.assertFalse(evaluate(suite, run)["passed"])

    def test_equality_preserves_json_types_and_nested_structure(self):
        cases = [(True, 1, False), ({"x": False}, {"x": 0}, False), ([1], [1.0], True),
                 ({"a": 1, "b": 2}, {"b": 2, "a": 1}, True), ([1, 2], [2, 1], False),
                 ({"extra": 1}, {}, False), ("서울", "서울", True)]
        for actual, expected, passed in cases:
            with self.subTest(actual=actual, expected=expected):
                self.assertEqual(evaluate(*single(actual, expected))["passed"], passed)

    def test_exact_sequence_rejects_extra_missing_reordered_or_failed_calls(self):
        suite = fixture("suite")
        mutations = [lambda calls: calls.append(copy.deepcopy(calls[-1])),
                     lambda calls: calls.pop(), lambda calls: calls.reverse(),
                     lambda calls: calls[-1].update(outcome="error")]
        for mutation in mutations:
            run = fixture("candidate")
            mutation(run["results"][1]["tool_calls"])
            self.assertFalse(evaluate(suite, run)["results"][1]["passed"])

    def test_subsequence_matches_complete_step_and_preserves_retry_order(self):
        suite, run = single("ok", "ok")
        suite["tasks"][0]["expect"]["tools"] = {"mode": "subsequence", "steps": [
            {"name": "lookup", "outcome": "error"},
            {"name": "lookup", "outcome": "success", "arguments": [{"path": "/id", "equals": 2}]},
        ]}
        run["results"][0]["tool_calls"] = [
            {"name": "lookup", "outcome": "success", "arguments": {"id": 2}, "output": None},
            {"name": "lookup", "outcome": "error", "arguments": {}, "output": None},
            {"name": "lookup", "outcome": "success", "arguments": {"id": 1}, "output": None},
            {"name": "lookup", "outcome": "success", "arguments": {"id": 2}, "output": None},
        ]
        self.assertTrue(evaluate(suite, run)["passed"])
        run["results"][0]["tool_calls"].pop()
        self.assertFalse(evaluate(suite, run)["passed"])

    def test_one_call_cannot_satisfy_two_required_steps(self):
        suite, run = single()
        suite["tasks"][0]["expect"]["tools"] = {"mode": "subsequence", "steps": [
            {"name": "read", "outcome": "any"}, {"name": "read", "outcome": "success"}
        ]}
        run["results"][0]["tool_calls"] = [{"name": "read", "outcome": "success", "arguments": {}, "output": None}]
        self.assertEqual(failed_codes(evaluate(suite, run)), ["tools.step[1]"])

    def test_forbidden_tool_fails_even_when_it_reports_error(self):
        suite, run = single()
        suite["tasks"][0]["expect"]["forbidden_tools"] = ["send"]
        run["results"][0]["tool_calls"] = [{"name": "send", "arguments": {}, "outcome": "error", "output": None}]
        self.assertEqual(failed_codes(evaluate(suite, run)), ["tools.forbidden"])

    def test_budget_missing_over_and_boundary(self):
        suite, run = single()
        suite["tasks"][0]["budget"] = {"max_tool_calls": 0, "max_input_tokens": 0, "max_output_tokens": 2, "max_duration_ms": 1.5}
        self.assertEqual(len(failed_codes(evaluate(suite, run))), 3)
        run["results"][0]["usage"] = {"input_tokens": 0, "output_tokens": 2, "duration_ms": 1.5}
        self.assertTrue(evaluate(suite, run)["passed"])
        run["results"][0]["usage"]["duration_ms"] = 1.5001
        self.assertEqual(failed_codes(evaluate(suite, run)), ["budget.duration_ms"])

    def test_report_is_repeatable_without_mutating_input(self):
        suite, run = fixture("suite"), fixture("candidate")
        original = copy.deepcopy((suite, run))
        first = evaluate(suite, run)
        run["results"].reverse()
        self.assertEqual(first, evaluate(suite, run))
        run["results"].reverse()
        self.assertEqual((suite, run), original)
        self.assertNotIn("현재 사무실", json.dumps(first, ensure_ascii=False))
        suite["tasks"][0]["budget"]["max_input_tokens"] += 1
        self.assertNotEqual(first["suite_sha256"], evaluate(suite, run)["suite_sha256"])


class ComparisonTests(unittest.TestCase):
    def test_improvement_and_regression_are_paired_by_id(self):
        suite, baseline, candidate = fixture("suite"), fixture("baseline"), fixture("candidate")
        candidate["results"].reverse()
        report = compare(suite, baseline, candidate)
        self.assertEqual(report["summary"]["improvements"], 2)
        self.assertTrue(report["no_regressions"])
        self.assertEqual(report["changes"][0]["usage_delta"]["input_tokens"], -50)
        reverse = compare(suite, candidate, baseline)
        self.assertEqual(reverse["summary"]["regressions"], 2)
        self.assertFalse(reverse["no_regressions"])

    def test_unchanged_average_does_not_hide_regression(self):
        suite, baseline = fixture("suite"), fixture("candidate")
        baseline["results"][0]["final"]["city"] = "Tokyo"
        candidate = fixture("candidate")
        candidate["results"][3]["final"]["status"] = "sent"
        report = compare(suite, baseline, candidate)
        self.assertEqual(report["summary"]["baseline_passed"], 3)
        self.assertEqual(report["summary"]["candidate_passed"], 3)
        self.assertEqual(report["summary"]["regressions"], 1)
        self.assertEqual(report["summary"]["improvements"], 1)
        self.assertFalse(report["no_regressions"])

    def test_incomplete_baseline_or_candidate_cannot_be_compared(self):
        for missing_side in (0, 1):
            runs = [fixture("baseline"), fixture("candidate")]
            runs[missing_side]["results"].pop()
            with self.assertRaisesRegex(ContractError, "every task"):
                compare(fixture("suite"), *runs)

    def test_missing_usage_is_not_a_zero_cost_improvement(self):
        suite, baseline = single()
        baseline["results"][0]["usage"] = {"input_tokens": 20}
        candidate = copy.deepcopy(baseline)
        candidate["results"][0].pop("usage")
        self.assertEqual(compare(suite, baseline, candidate)["changes"][0]["usage_delta"], {})

    def test_no_regression_does_not_claim_all_cases_passed(self):
        report = compare(fixture("suite"), fixture("baseline"), fixture("baseline"))
        self.assertTrue(report["no_regressions"])
        self.assertFalse(report["candidate_passed"])


class ValidationTests(unittest.TestCase):
    def test_unknown_duplicate_missing_and_mismatched_result_ids(self):
        for change in ("duplicate", "unknown", "different_suite", "version", "missing", "unknown_field"):
            with self.subTest(change=change):
                suite, run = single()
                if change == "duplicate":
                    run["results"].append(copy.deepcopy(run["results"][0]))
                elif change == "unknown":
                    run["results"][0]["task_id"] = "unknown"
                elif change == "different_suite":
                    run["suite_id"] = "wrong"
                elif change == "version":
                    run["schema_version"] = True
                elif change == "missing":
                    run["results"][0].pop("status")
                else:
                    run["results"][0]["cost"] = 1
                with self.assertRaises(ContractError):
                    evaluate(suite, run)

    def test_invalid_numeric_usage_and_limits_rejected(self):
        for value in (-1, True, "3", float("nan"), float("inf"), 1.5):
            with self.subTest(value=value):
                suite, run = single()
                run["results"][0]["usage"] = {"input_tokens": value}
                with self.assertRaises(ContractError):
                    evaluate(suite, run)
                run["results"][0].pop("usage")
                suite["tasks"][0]["budget"] = {"max_input_tokens": value}
                with self.assertRaises(ContractError):
                    evaluate(suite, run)

    def test_huge_integer_is_valid_and_does_not_overflow_float_conversion(self):
        suite, run = single(10**1000, 10**1000)
        run["results"][0]["usage"] = {"input_tokens": 10**1000}
        suite["tasks"][0]["budget"] = {"max_input_tokens": 10**1000}
        self.assertTrue(evaluate(suite, run)["passed"])

    def test_escaped_json_surrogates_do_not_crash_report_hashing(self):
        suite, run = single("\ud800", "\ud800")
        suite["tasks"][0]["prompt"] = "JSON escape: \ud800"
        report = evaluate(suite, run)
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["suite_sha256"]), 64)

    def test_invalid_suite_contracts_fail_closed(self):
        mutations = [
            lambda s: s.update(schema_version=2),
            lambda s: s.update(tasks=[]),
            lambda s: s["tasks"].append(copy.deepcopy(s["tasks"][0])),
            lambda s: s["tasks"][0].update(expect={}),
            lambda s: s["tasks"][0].update(expect={"final": []}),
            lambda s: s["tasks"][0].update(expect={"tools": {"mode": "subsequence", "steps": []}}),
            lambda s: s["tasks"][0].update(budget={"max_tokens": 10}),
            lambda s: s["tasks"][0].update(budget={}),
            lambda s: s["tasks"][0].update(tags=["a", "a"]),
            lambda s: s["tasks"][0]["expect"]["final"][0].update(path="/x~2y"),
            lambda s: s["tasks"][0]["expect"]["final"][0].update(path="x"),
            lambda s: s["tasks"][0]["expect"]["final"].append({"path": "", "equals": None}),
            lambda s: s["tasks"][0]["expect"]["final"][0].update(equals=float("nan")),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                suite, _ = single()
                mutation(suite)
                with self.assertRaises(ContractError):
                    validate_suite(suite)

    def test_contradictory_tool_contract_rejected(self):
        suite, _ = single()
        suite["tasks"][0]["expect"].update(forbidden_tools=["send"], tools={"mode": "exact", "steps": [{"name": "send", "outcome": "any"}]})
        with self.assertRaisesRegex(ContractError, "also forbidden"):
            validate_suite(suite)

    def test_malformed_run_types_and_non_json_values(self):
        mutations = [
            lambda r: r.update(results={}),
            lambda r: r["results"][0].update(status="pending"),
            lambda r: r["results"][0].update(final=float("inf")),
            lambda r: r["results"][0].update(usage={"tokens": 5}),
            lambda r: r["results"][0].update(final={1: "nonstring"}),
            lambda r: r["results"][0].update(final=set()),
            lambda r: r["results"][0].update(tool_calls=[{"name": "read", "arguments": "{}", "outcome": "success", "output": None}]),
            lambda r: r["results"][0].update(tool_calls=[{"name": "read", "arguments": {}, "outcome": "unknown", "output": None}]),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                suite, run = single()
                mutation(run)
                with self.assertRaises(ContractError):
                    validate_run(run, suite)

    def test_json_reader_rejects_duplicate_keys_nonfinite_and_bad_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for value in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":', b'\xff'):
                path.write_bytes(value)
                with self.assertRaises(ContractError):
                    load_document(path)
            with self.assertRaises(ContractError):
                load_document(Path(directory) / "absent.json")


class CliTests(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "weasley_agent_bench", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")

    def test_demo_commands_and_exit_codes(self):
        commands = [
            (("evaluate", "examples/suite.json", "examples/candidate.json"), 0),
            (("evaluate", "examples/suite.json", "examples/baseline.json"), 1),
            (("compare", "examples/suite.json", "examples/baseline.json", "examples/candidate.json"), 0),
            (("compare", "examples/suite.json", "examples/candidate.json", "examples/baseline.json"), 1),
        ]
        for args, code in commands:
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(json.loads(result.stdout)["schema_version"], 1)
                self.assertEqual(result.stderr, "")

    def test_invalid_input_returns_json_error_without_score(self):
        result = self.cli("evaluate", "missing.json", "examples/candidate.json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("error", json.loads(result.stderr))
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_report_is_byte_identical_on_repeated_runs(self):
        first = self.cli("evaluate", "examples/suite.json", "examples/candidate.json")
        second = self.cli("evaluate", "examples/suite.json", "examples/candidate.json")
        self.assertEqual(first.stdout, second.stdout)


if __name__ == "__main__":
    unittest.main()
