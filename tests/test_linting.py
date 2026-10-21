import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from weasley_agent_bench import ContractError, evaluate, lint_suite, validate_suite


def suite_for(count=2, cap=1, mode="exact"):
    return {"schema_version": 1, "suite_id": "negative-fixture", "tasks": [{
        "id": "task", "prompt": "Synthetic required-call budget check.",
        "expect": {"final": [{"path": "", "equals": True}], "tools": {
            "mode": mode, "steps": [{"name": "lookup", "outcome": "any"}
                                     for _ in range(count)]}},
        "budget": {"max_tool_calls": cap},
    }]}


class LintingTests(unittest.TestCase):
    def test_exact_and_subsequence_cardinality_boundaries(self):
        for mode in ("exact", "subsequence"):
            for count in range(5):
                for cap in range(5):
                    with self.subTest(mode=mode, count=count, cap=cap):
                        report = lint_suite(suite_for(count, cap, mode))
                        self.assertEqual(report["passed"], count <= cap)
                        self.assertEqual(report["accepted"], count <= cap)
                        self.assertEqual(report["summary"]["contradictions"], int(count > cap))

    def test_structured_paths_identify_both_sides(self):
        report = lint_suite(suite_for())
        self.assertEqual(report["issues"], [{
            "code": "budget.required_tool_calls_exceed_cap", "task_index": 0,
            "path": "suite.tasks[0].budget.max_tool_calls",
            "related_path": "suite.tasks[0].expect.tools.steps",
            "required_calls": 2, "max_tool_calls": 1,
            "detail": "each required step needs a distinct call; the cap is too small",
        }])

    def test_repeated_names_and_error_outcomes_need_distinct_calls(self):
        suite = suite_for(3, 2, "subsequence")
        for step, outcome in zip(suite["tasks"][0]["expect"]["tools"]["steps"],
                                 ("error", "success", "any")):
            step["outcome"] = outcome
        self.assertEqual(lint_suite(suite)["issues"][0]["required_calls"], 3)

    def test_multiple_issues_are_in_suite_order(self):
        suite = suite_for()
        second = copy.deepcopy(suite["tasks"][0])
        second["id"] = "other"
        suite["tasks"].append(second)
        report = lint_suite(suite)
        self.assertEqual(report["summary"], {"tasks": 2, "contradictions": 2})
        self.assertEqual([i["task_index"] for i in report["issues"]], [0, 1])

    def test_missing_tool_contract_or_cap_does_not_invent_lower_bounds(self):
        suite = suite_for()
        del suite["tasks"][0]["expect"]["tools"]
        self.assertTrue(lint_suite(suite)["passed"])
        suite = suite_for()
        del suite["tasks"][0]["budget"]
        self.assertTrue(lint_suite(suite)["passed"])

    def test_other_zero_budgets_do_not_imply_tool_cost(self):
        suite = suite_for()
        suite["tasks"][0]["budget"] = {
            "max_input_tokens": 0, "max_output_tokens": 0, "max_duration_ms": 0,
        }
        self.assertTrue(lint_suite(suite)["passed"])

    def test_allow_mode_preserves_issues_and_failure_truth(self):
        strict = lint_suite(suite_for())
        allowed = lint_suite(suite_for(), allow_contradictions=True)
        self.assertTrue(allowed["accepted"])
        self.assertFalse(allowed["passed"])
        self.assertEqual(strict["issues"], allowed["issues"])
        self.assertEqual(strict["suite_sha256"], allowed["suite_sha256"])

    def test_allow_mode_does_not_bypass_structural_errors(self):
        for value in (True, -1, 1.5, None, "1"):
            suite = suite_for(cap=value)
            with self.subTest(value=value), self.assertRaises(ContractError):
                lint_suite(suite, allow_contradictions=True)
        suite = suite_for()
        suite["tasks"][0]["expect"]["forbidden_tools"] = ["lookup"]
        with self.assertRaises(ContractError):
            lint_suite(suite, allow_contradictions=True)

    def test_option_is_strict_boolean(self):
        for value in (0, 1, "true", [], {}, None):
            with self.subTest(value=value), self.assertRaises(ContractError):
                lint_suite(suite_for(), allow_contradictions=value)

    def test_input_unchanged_and_digest_ignores_object_key_order(self):
        suite = suite_for()
        original = copy.deepcopy(suite)
        report = lint_suite(suite)
        self.assertEqual(suite, original)
        reordered = dict(reversed(list(suite.items())))
        self.assertEqual(lint_suite(reordered), report)
        suite["tasks"][0]["prompt"] += " revision"
        self.assertNotEqual(lint_suite(suite)["suite_sha256"], report["suite_sha256"])

    def test_report_does_not_echo_fixture_content(self):
        suite = suite_for()
        sentinel = "PRIVATE_FIXTURE_SENTINEL"
        suite["suite_id"] = sentinel
        suite["tasks"][0]["id"] = sentinel
        suite["tasks"][0]["prompt"] = sentinel
        for step in suite["tasks"][0]["expect"]["tools"]["steps"]:
            step["name"] = sentinel
        self.assertNotIn(sentinel, json.dumps(lint_suite(suite)))

    def test_existing_evaluator_still_accepts_intentional_negative_suites(self):
        suite = suite_for()
        validate_suite(suite)
        run = {"schema_version": 1, "suite_id": suite["suite_id"], "run_id": "run", "results": [{
            "task_id": "task", "status": "completed", "final": True,
            "tool_calls": [{"name": "lookup", "arguments": {}, "outcome": "success",
                            "output": None} for _ in range(2)],
        }]}
        before = evaluate(suite, run)
        lint_suite(suite, allow_contradictions=True)
        self.assertEqual(evaluate(suite, run), before)
        self.assertFalse(before["passed"])

    def test_exhaustive_small_tool_sequences_cannot_pass_flagged_contract(self):
        # Independent scoring oracle: every allowed call count fails the contract,
        # whereas a sufficient count satisfies every clean contract in this fixture.
        for mode in ("exact", "subsequence"):
            for required in range(4):
                for cap in range(4):
                    suite = suite_for(required, cap, mode)
                    passing = False
                    for actual in range(5):
                        run = {"schema_version": 1, "suite_id": suite["suite_id"], "run_id": "run",
                               "results": [{"task_id": "task", "status": "completed", "final": True,
                                            "tool_calls": [{"name": "lookup", "arguments": {},
                                                            "outcome": "success", "output": None}
                                                           for _ in range(actual)]}]}
                        passing |= evaluate(suite, run)["passed"]
                    self.assertEqual(lint_suite(suite)["passed"], passing)

    def test_cli_modes_and_invalid_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "suite.json"
            path.write_text(json.dumps(suite_for()), encoding="utf-8")
            command = [sys.executable, "-m", "weasley_agent_bench", "lint-suite", str(path)]
            strict = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(strict.returncode, 1)
            self.assertEqual(strict.stderr, "")
            self.assertFalse(json.loads(strict.stdout)["accepted"])
            allowed = subprocess.run([*command, "--allow-contradictions"], capture_output=True, text=True)
            self.assertEqual(allowed.returncode, 0)
            self.assertEqual(allowed.stderr, "")
            self.assertFalse(json.loads(allowed.stdout)["passed"])
            self.assertTrue(json.loads(allowed.stdout)["accepted"])
            path.write_text(json.dumps(suite_for(cap=2)), encoding="utf-8")
            clean = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(clean.returncode, 0)
            self.assertTrue(json.loads(clean.stdout)["passed"])
            path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            invalid = subprocess.run([*command, "--allow-contradictions"], capture_output=True, text=True)
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(invalid.stdout, "")
            self.assertIn("error", json.loads(invalid.stderr))


if __name__ == "__main__":
    unittest.main()
