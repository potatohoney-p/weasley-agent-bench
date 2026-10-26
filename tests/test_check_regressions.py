"""Opt-in check-level regression policy on synthetic saved observations."""
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from weasley_agent_bench import ContractError, compare, compare_jsonl, evaluate
from weasley_agent_bench.cli import main
from weasley_agent_bench.scoring import _evaluate_task
from test_jsonl import records, write_records


def suite():
    return {"schema_version": 1, "suite_id": "synthetic-checks", "tasks": [{
        "id": "case", "prompt": "Synthetic check policy fixture",
        "expect": {"final": [{"path": "/answer", "equals": True}, {"path": "/evidence", "equals": True}],
                   "tools": {"mode": "exact", "steps": [{"name": "lookup", "outcome": "success"}]},
                   "forbidden_tools": ["forbidden"]},
        "budget": {"max_input_tokens": 1},
    }]}


def run(name, flags=(False, True, True, True)):
    answer, evidence, tool, budget = flags
    return {"schema_version": 1, "suite_id": "synthetic-checks", "run_id": name, "results": [{
        "task_id": "case", "status": "completed", "final": {"answer": answer, "evidence": evidence},
        "tool_calls": [{"name": "lookup", "outcome": "success" if tool else "error", "arguments": {}, "output": None}],
        "usage": {"input_tokens": 1 if budget else 2},
    }]}


def materialize(events):
    header, *rest = events
    end = rest.pop()
    assert end.pop("complete") is True
    return {**{key: value for key, value in header.items() if key not in ("format", "format_version")},
            **end, "changes": [row["change"] for row in rest]}


class CheckRegressionTests(unittest.TestCase):
    def inputs(self, s, b, c):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for name, document in (("suite", s), ("baseline", b), ("candidate", c)):
            (root / f"{name}.json").write_text(json.dumps(document), encoding="utf-8")
            write_records(root / f"{name}.jsonl", records(document, "suite" if name == "suite" else "run"))
        return root

    def stream(self, root, **options):
        return compare_jsonl(*(root / f"{name}.jsonl" for name in ("suite", "baseline", "candidate")), **options)

    def test_already_failing_task_new_loss_is_visible_only_when_selected(self):
        s, b, c = suite(), run("baseline"), run("candidate", (False, False, True, True))
        legacy = compare(s, b, c)
        self.assertTrue(legacy["no_regressions"])
        self.assertEqual(legacy["changes"][0]["change"], "unchanged")
        report = compare(s, b, c, check_regressions=True)
        self.assertFalse(report["no_regressions"])
        self.assertTrue(report["task_no_regressions"])
        self.assertEqual(report["regression_policy"], "checks-v1")
        self.assertEqual(report["changes"][0]["check_regressions"], ["final[1]"])
        self.assertEqual(report["summary"]["regressions"], 0)
        self.assertEqual(report["summary"]["check_regressions"], 1)

    def test_improvements_cannot_cancel_new_losses(self):
        report = compare(suite(), run("b"), run("c", (True, False, True, True)), check_regressions=True)
        row = report["changes"][0]
        self.assertEqual(row["check_improvements"], ["final[0]"])
        self.assertEqual(row["check_regressions"], ["final[1]"])
        self.assertFalse(report["no_regressions"])
        self.assertEqual(report["summary"]["tasks_with_check_regressions"], 1)

    def test_exhaustive_independent_four_bit_oracle(self):
        codes = ["final[0]", "final[1]", "tools.step[0]", "budget.input_tokens"]
        vectors = list(itertools.product((False, True), repeat=4))
        for before, after in itertools.product(vectors, repeat=2):
            report = compare(suite(), run("b", before), run("c", after), check_regressions=True)
            lost = [code for code, a, b in zip(codes, before, after) if a and not b]
            gained = [code for code, a, b in zip(codes, before, after) if not a and b]
            self.assertEqual(report["changes"][0]["check_regressions"], lost)
            self.assertEqual(report["changes"][0]["check_improvements"], gained)
            self.assertEqual(report["no_regressions"], not lost)
            self.assertEqual(report["task_no_regressions"], not (all(before) and not all(after)))

    def test_missing_telemetry_is_failure_not_zero_or_dropped_check(self):
        b, c = run("b"), run("c")
        del c["results"][0]["usage"]
        report = compare(suite(), b, c, check_regressions=True)
        self.assertEqual(report["changes"][0]["check_regressions"], ["budget.input_tokens"])
        self.assertEqual(report["changes"][0]["usage_delta"], {})

    def test_status_tool_count_and_forbidden_attempts_have_stable_identities(self):
        b, c = run("b"), run("c")
        c["results"][0]["status"] = "cancelled"
        c["results"][0]["tool_calls"].append({"name": "forbidden", "arguments": {}, "outcome": "error", "output": None})
        report = compare(suite(), b, c, check_regressions=True)
        self.assertEqual(report["changes"][0]["check_regressions"],
                         ["result.completed", "tools.count", "tools.forbidden"])

    def test_identical_failed_runs_and_pure_improvements_still_pass(self):
        for candidate in (run("c"), run("c", (True, True, True, True))):
            report = compare(suite(), run("b"), candidate, check_regressions=True)
            self.assertTrue(report["no_regressions"])
            self.assertEqual(report["summary"]["check_regressions"], 0)

    def test_jsonl_parity_preserves_suite_order_not_result_order(self):
        s, b, c = suite(), run("b"), run("c", (False, False, False, False))
        s["tasks"].append({**copy.deepcopy(s["tasks"][0]), "id": "other"})
        b["results"].append({**copy.deepcopy(b["results"][0]), "task_id": "other"})
        c["results"].insert(0, {**copy.deepcopy(b["results"][0]), "task_id": "other"})
        root = self.inputs(s, b, c)
        for selected in (False, True):
            expected = compare(s, b, c, check_regressions=selected)
            self.assertEqual(materialize(list(self.stream(root, check_regressions=selected))), expected)

    def test_incomplete_extra_duplicate_and_wrong_suite_fail_before_jsonl_output(self):
        for fault in ("missing", "extra", "duplicate", "suite"):
            s, b, c = suite(), run("b"), run("c")
            if fault == "missing": c["results"].clear()
            if fault == "extra": c["results"][0]["task_id"] = "unknown"
            if fault == "duplicate": c["results"].append(copy.deepcopy(c["results"][0]))
            if fault == "suite": c["suite_id"] = "different"
            with self.assertRaises(ContractError): compare(s, b, c, check_regressions=True)
            root = self.inputs(s, b, c)
            with self.assertRaises(ContractError): next(self.stream(root, check_regressions=True))

    def test_option_is_explicit_boolean_and_rejects_before_storage(self):
        for value in (0, 1, None, "true", []):
            with self.assertRaises(ContractError): compare(suite(), run("b"), run("c"), check_regressions=value)
            with self.assertRaises(ContractError):
                next(compare_jsonl("missing", "missing", "missing", check_regressions=value))

    def test_ambiguous_generated_checks_fail_closed_for_both_transports(self):
        for fault in ("missing", "duplicate", "boolean"):
            def scorer():
                calls = 0
                def score(task, result):
                    nonlocal calls
                    row = _evaluate_task(task, result)
                    calls += 1
                    if calls == 2:
                        if fault == "missing": row["checks"].pop()
                        if fault == "duplicate": row["checks"].append(row["checks"][0])
                        if fault == "boolean": row["checks"][0]["passed"] = 1
                    return row
                return score
            with patch("weasley_agent_bench.scoring._evaluate_task", side_effect=scorer()), self.assertRaises(ContractError):
                compare(suite(), run("b"), run("c"), check_regressions=True)
            root = self.inputs(suite(), run("b"), run("c"))
            with patch("weasley_agent_bench.jsonl._evaluate_task", side_effect=scorer()), self.assertRaises(ContractError):
                next(self.stream(root, check_regressions=True))

    def test_input_and_result_evidence_remain_detached(self):
        values = (suite(), run("b"), run("c", (False, False, True, True)))
        original = copy.deepcopy(values)
        result = compare(*values, check_regressions=True)
        result["changes"][0]["check_regressions"].clear()
        self.assertEqual(values, original)
        self.assertFalse(compare(*values, check_regressions=True)["no_regressions"])

    def test_cli_opt_in_selects_exit_gate_without_changing_default(self):
        root = self.inputs(suite(), run("b"), run("c", (True, False, True, True)))
        for command, suffix in (("compare", "json"), ("compare-jsonl", "jsonl")):
            for selected, expected in ((False, 0), (True, 1)):
                out, err = io.StringIO(), io.StringIO()
                args = [command, *(str(root / f"{name}.{suffix}") for name in ("suite", "baseline", "candidate"))]
                if selected: args.append("--check-regressions")
                with redirect_stdout(out), redirect_stderr(err): code = main(args)
                self.assertEqual(code, expected); self.assertEqual(err.getvalue(), "")
                report = (materialize([json.loads(line) for line in out.getvalue().splitlines()])
                          if suffix == "jsonl" else json.loads(out.getvalue()))
                self.assertEqual(report, compare(suite(), run("b"), run("c", (True, False, True, True)), check_regressions=selected))

    def test_strict_jsonl_terminal_retains_cleanup_guarantee(self):
        root = self.inputs(suite(), run("b"), run("c"))
        scratch = root / "scratch"; scratch.mkdir()
        stream = self.stream(root, check_regressions=True, temporary_directory=scratch)
        self.addCleanup(stream.close)
        for event in stream:
            if event.get("complete"): break
        self.assertEqual(list(scratch.iterdir()), [])
        directory = tempfile.TemporaryDirectory
        class FailingCleanup(directory):
            def __exit__(self, *args):
                super().__exit__(*args)
                raise OSError("synthetic cleanup failure")
        seen = []
        with patch("weasley_agent_bench.jsonl.tempfile.TemporaryDirectory", FailingCleanup):
            with self.assertRaises(ContractError): seen.extend(self.stream(root, check_regressions=True))
        self.assertFalse(any(event.get("complete") for event in seen))
