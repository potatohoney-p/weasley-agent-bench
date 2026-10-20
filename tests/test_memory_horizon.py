import copy
import json
from pathlib import Path
import tempfile
import unittest

from experiments.memory_horizon import LatestRecord, RollingState, SessionOnly, export, make_episodes, make_suite, run_experiment
from weasley_agent_bench import evaluate, load_document


class MemoryHorizonTests(unittest.TestCase):
    def test_known_heldout_answers_and_provenance_after_correction_retraction_restore(self):
        tasks = {task["id"]: task for task in make_suite(make_episodes())["tasks"]}
        self.assertEqual(tasks["s0-question-0"]["expect"]["final"], [
            {"path": "/office", "equals": "서울"},
            {"path": "/evidence_ids", "equals": ["s0-fact-0"]},
        ])
        self.assertEqual(tasks["s3-question-0"]["expect"]["final"], [
            {"path": "/office", "equals": None},
            {"path": "/evidence_ids", "equals": ["s3-retract-0"]},
        ])
        self.assertEqual(tasks["s4-question-0"]["expect"]["final"], [
            {"path": "/office", "equals": "서울 동부"},
            {"path": "/evidence_ids", "equals": ["s4-fact-0"]},
        ])

    def test_exact_memory_baseline_results_are_reproducible(self):
        first = run_experiment()
        self.assertEqual(first, run_experiment())
        report, suite, runs, episodes = first
        self.assertEqual((report["sessions"], report["facts"], report["questions"]), (6, 19, 48))
        self.assertEqual([row["correct"] for row in report["baselines"]], [19, 24, 48])
        self.assertEqual([row["final_stored_record_count"] for row in report["baselines"]], [2, 4, 8])
        self.assertNotIn("timing_environment", report)
        self.assertEqual(len(runs), 3)
        self.assertTrue(evaluate(suite, runs["latest-record"])["passed"])
        self.assertNotIn('"expect"', json.dumps(episodes))
        self.assertTrue(all("usage" not in item for run in runs.values() for item in run["results"]))

    def test_future_correction_never_changes_earlier_labels(self):
        episodes = make_episodes()
        before = make_suite(episodes)
        episodes[-1]["events"][0]["office"] = "future mutation"
        after = make_suite(episodes)
        self.assertEqual(before["tasks"][:40], after["tasks"][:40])
        self.assertNotEqual(before["tasks"][40:], after["tasks"][40:])

    def test_session_reset_and_latest_record_retention(self):
        for baseline_type, expected in ((SessionOnly, {"office": None, "evidence_ids": []}),
                                        (LatestRecord, {"office": "서울", "evidence_ids": ["new"]})):
            baseline = baseline_type()
            baseline.observe({"key": "team", "event_id": "old", "operation": "set", "office": "Tokyo"})
            baseline.observe({"key": "team", "event_id": "new", "operation": "set", "office": "서울"})
            baseline.start_session()
            self.assertEqual(baseline.query("team")[0], expected)

    def test_rolling_summary_evicts_oldest_record_and_refreshes_corrected_key(self):
        baseline = RollingState(capacity=2)
        for key, event_id in (("a", "a1"), ("b", "b1"), ("a", "a2"), ("c", "c1")):
            baseline.observe({"key": key, "event_id": event_id, "operation": "set", "office": event_id})
        self.assertEqual(list(baseline.records), ["a", "c"])
        self.assertEqual(baseline.query("a")[0]["evidence_ids"], ["a2"])
        self.assertEqual(baseline.query("b")[0]["evidence_ids"], [])

    def test_stale_values_and_right_values_with_wrong_evidence_fail(self):
        _, suite, runs, _ = run_experiment()
        run = copy.deepcopy(runs["latest-record"])
        row = next(row for row in run["results"] if row["task_id"] == "s4-question-0")
        row["final"]["evidence_ids"] = ["s0-fact-0"]
        score = evaluate(suite, run)
        self.assertEqual(score["summary"]["failed"], 1)
        row["final"]["evidence_ids"] = ["s4-fact-0"]
        row["final"]["office"] = "서울"
        self.assertEqual(evaluate(suite, run)["summary"]["failed"], 1)

    def test_optional_timing_is_measured_separately_from_functional_score(self):
        report, *_ = run_experiment(timing_trials=2)
        self.assertIn("timing_environment", report)
        for row in report["baselines"]:
            self.assertEqual(row["lookup_timing"]["samples"], 96)
            self.assertGreaterEqual(row["lookup_timing"]["median_us"], 0)
            self.assertGreaterEqual(row["lookup_timing"]["p95_us"], row["lookup_timing"]["median_us"])
        for value in (-1, 10001, True, 0.5):
            with self.assertRaises(ValueError):
                run_experiment(value)

    def test_exported_artifacts_reproduce_scores_and_never_overwrite(self):
        artifacts = run_experiment()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "export"
            export(directory, *artifacts)
            report = evaluate(load_document(directory / "suite.json"), load_document(directory / "latest-record.json"))
            self.assertTrue(report["passed"])
            self.assertEqual(report["summary"]["total"], 48)
            with self.assertRaises(FileExistsError):
                export(directory, *artifacts)


if __name__ == "__main__":
    unittest.main()
