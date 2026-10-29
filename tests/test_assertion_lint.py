import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from weasley_agent_bench import ContractError, evaluate, lint_suite


def suite_for(parent, child, parent_path="", child_path="/a"):
    return {"schema_version": 1, "suite_id": "synthetic", "tasks": [{
        "id": "task", "prompt": "Synthetic consistency probe.", "expect": {"final": [
            {"path": parent_path, "equals": parent}, {"path": child_path, "equals": child},
        ]}}]}


class AssertionLintTests(unittest.TestCase):
    def test_conflict_is_opt_in_and_legacy_report_unchanged(self):
        suite = suite_for({"a": 1}, 2)
        legacy = lint_suite(suite)
        self.assertTrue(legacy["passed"])
        self.assertEqual(legacy["rule_set"], "tool-budget-v1")
        report = lint_suite(suite, check_assertions=True)
        self.assertFalse(report["passed"])
        self.assertEqual(report["rule_set"], "tool-budget-and-assertions-v1")
        self.assertEqual(report["issues"][0]["path"], "suite.tasks[0].expect.final[1]")
        self.assertEqual(report["issues"][0]["related_path"], "suite.tasks[0].expect.final[0]")
        self.assertEqual(lint_suite(suite, check_assertions=False), legacy)

    def test_same_values_and_numeric_equality(self):
        for parent, child in [({"a": 1}, 1.0), ({"a": None}, None), ({"a": True}, True),
                              ({"a": {"b": [1, 2]}}, {"b": [1.0, 2]})]:
            self.assertTrue(lint_suite(suite_for(parent, child), check_assertions=True)["passed"])
        self.assertFalse(lint_suite(suite_for({"a": 1}, True), check_assertions=True)["passed"])

    def test_missing_null_scalar_and_array_indices(self):
        for parent, path in [({}, "/a"), (None, "/a"), ([], "/0"), ([1], "/01"),
                             ([1], "/-"), ([1], "/1"), ([1], "/" + "9" * 5000)]:
            self.assertFalse(lint_suite(suite_for(parent, None, child_path=path), check_assertions=True)["passed"])
        self.assertTrue(lint_suite(suite_for([None], None, child_path="/0"), check_assertions=True)["passed"])
        self.assertTrue(lint_suite(suite_for({"01": None}, None, child_path="/01"), check_assertions=True)["passed"])

    def test_escaped_and_empty_tokens_do_not_alias(self):
        for parent_path, child_path, parent in [("/a~1b", "/a~1b/~0", {"~": 1}),
                                                 ("/", "//", {"": 1}),
                                                 ("/a~01", "/a~01/b", {"b": 1})]:
            self.assertFalse(lint_suite(suite_for(parent, 2, parent_path, child_path), check_assertions=True)["passed"])
        for p, c in [("/a", "/ab/x"), ("/a~1b", "/a/b/x"), ("/0", "/00/x")]:
            self.assertTrue(lint_suite(suite_for({}, 2, p, c), check_assertions=True)["passed"])

    def test_reordered_and_transitive_conflicts(self):
        suite = suite_for({"a": {"b": 1}}, {"b": 2})
        suite["tasks"][0]["expect"]["final"].append({"path": "/a/b", "equals": 3})
        self.assertEqual(lint_suite(suite, check_assertions=True)["summary"]["contradictions"], 3)
        suite["tasks"][0]["expect"]["final"].reverse()
        self.assertEqual(lint_suite(suite, check_assertions=True)["summary"]["contradictions"], 3)

    def test_tool_assertions_checked_within_each_field_only(self):
        for mode in ("exact", "subsequence"):
            suite = suite_for({"a": 1}, 2)
            final = suite["tasks"][0]["expect"].pop("final")
            suite["tasks"][0]["expect"]["tools"] = {"mode": mode, "steps": [
                {"name": "lookup", "outcome": "any", "arguments": final, "output": final}]}
            report = lint_suite(suite, check_assertions=True)
            self.assertEqual(report["summary"]["contradictions"], 2)
            self.assertIn(".arguments[", report["issues"][0]["path"])
            self.assertIn(".output[", report["issues"][1]["path"])
            for step in suite["tasks"][0]["expect"]["tools"]["steps"]:
                step["arguments"] = final[:1]
                step["output"] = final[1:]
            self.assertTrue(lint_suite(suite, check_assertions=True)["passed"])

    def test_allow_mode_and_strict_option_and_no_content_leak(self):
        suite = suite_for({"secret": "SENTINEL"}, "other", child_path="/secret")
        original = copy.deepcopy(suite)
        report = lint_suite(suite, check_assertions=True, allow_contradictions=True)
        self.assertFalse(report["passed"])
        self.assertTrue(report["accepted"])
        self.assertNotIn("secret", json.dumps(report))
        self.assertNotIn("SENTINEL", json.dumps(report))
        self.assertEqual(suite, original)
        for value in (None, 0, 1, "true", [], {}):
            with self.assertRaises(ContractError):
                lint_suite(suite, check_assertions=value)
        suite["tasks"][0]["expect"]["final"].append({"path": "/secret", "equals": "x"})
        with self.assertRaises(ContractError):
            lint_suite(suite, check_assertions=True, allow_contradictions=True)

    def test_finite_root_contract_grid_matches_evaluator(self):
        # Root equality determines the entire candidate: evaluating that candidate
        # is an independent scoring oracle for this restricted two-assertion grid.
        values = [None, True, 0, 1, "s", {}, {"a": None}, {"a": 1},
                  {"a": [True]}, [], [None], [1], {"0": 1}, {"": 0}, {"a/b": 1}]
        for root in values:
            for child in values:
                for pointer in ("/a", "/a/0", "/0", "/01", "/", "/a~1b", "/-"):
                    suite = suite_for(root, child, child_path=pointer)
                    run = {"schema_version": 1, "suite_id": "synthetic", "run_id": "r", "results": [{
                        "task_id": "task", "status": "completed", "final": root, "tool_calls": []}]}
                    self.assertEqual(lint_suite(suite, check_assertions=True)["passed"], evaluate(suite, run)["passed"])

    def test_budget_and_assertion_findings_preserve_order(self):
        suite = suite_for({"a": 1}, 2)
        suite["tasks"][0]["expect"]["tools"] = {"mode": "exact", "steps": [
            {"name": "lookup", "outcome": "error"}]}
        suite["tasks"][0]["budget"] = {"max_tool_calls": 0}
        second = copy.deepcopy(suite["tasks"][0])
        second["id"] = "second"
        suite["tasks"].append(second)
        report = lint_suite(suite, check_assertions=True)
        self.assertEqual([i["code"] for i in report["issues"]], [
            "budget.required_tool_calls_exceed_cap", "assertion.ancestor_conflict"] * 2)
        self.assertEqual([i["task_index"] for i in report["issues"]], [0, 0, 1, 1])
        self.assertEqual(report, lint_suite(suite, check_assertions=True))

    def test_distinct_steps_do_not_share_assertion_values(self):
        suite = suite_for({"a": 1}, 2)
        final = suite["tasks"][0]["expect"].pop("final")
        suite["tasks"][0]["expect"]["tools"] = {"mode": "subsequence", "steps": [
            {"name": "lookup", "outcome": "success", "output": [final[0]]},
            {"name": "lookup", "outcome": "error", "output": [final[1]]}]}
        self.assertTrue(lint_suite(suite, check_assertions=True)["passed"])

    def test_cli_opt_in_allow_and_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "suite.json"
            path.write_text(json.dumps(suite_for({"a": 1}, 2)), encoding="utf-8")
            cmd = [sys.executable, "-m", "weasley_agent_bench", "lint-suite", str(path)]
            for flags, code in [([], 0), (["--check-assertions"], 1),
                                (["--check-assertions", "--allow-contradictions"], 0)]:
                result = subprocess.run(cmd + flags, capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(result.stderr, "")
            path.write_text('{}', encoding="utf-8")
            result = subprocess.run(cmd + ["--check-assertions", "--allow-contradictions"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
