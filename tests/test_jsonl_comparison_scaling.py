"""Public CLI smoke check; timings and memory observations are not test gates."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from weasley_agent_bench import compare, load_document


ROOT = Path(__file__).resolve().parents[1]


class ComparisonScalingTests(unittest.TestCase):
    def test_cli_pairing_digest_usage_fixture_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "fixtures"
            command = [sys.executable, "-B", "-m", "experiments.jsonl_comparison_scaling",
                       "--tasks", "8", "--output-bytes", "17", "--modes", "legacy", "jsonl",
                       "--output-dir", str(destination)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["synthetic"])
            case = report["cases"][0]
            self.assertTrue(case["comparison_parity"])
            self.assertEqual(case["output_bytes_per_task_per_run"], 17)
            expected_summary = {"total": 8, "regressions": 2, "improvements": 2,
                                "baseline_passed": 4, "candidate_passed": 4}
            for measurement in case["measurements"]:
                self.assertEqual(measurement["summary"], expected_summary)
                self.assertFalse(measurement["no_regressions"])
                self.assertFalse(measurement["candidate_passed"])
                self.assertGreater(measurement["tracemalloc_peak_bytes"], 0)
            self.assertEqual(case["measurements"][0]["logical_report_sha256"],
                             case["measurements"][1]["logical_report_sha256"])
            fixture = destination / "tasks-8-bytes-17"
            suite, baseline, candidate = [load_document(fixture / f"{name}.json")
                                          for name in ("suite", "baseline", "candidate")]
            comparison = compare(suite, baseline, candidate)
            self.assertEqual(comparison["summary"], expected_summary)
            self.assertEqual(comparison["changes"][0]["usage_delta"],
                             {"input_tokens": -10, "output_tokens": -10, "duration_ms": 0.25})
            self.assertEqual(comparison["changes"][1]["usage_delta"],
                             {"input_tokens": -10, "duration_ms": 0.25})
            self.assertEqual(comparison["changes"][2]["usage_delta"]["output_tokens"], 2)
            self.assertEqual([row["task_id"] for row in baseline["results"]],
                             [f"task-{index:09d}" for index in reversed(range(8))])
            self.assertEqual([row["task_id"] for row in candidate["results"]],
                             [f"task-{index:09d}" for index in (0, 2, 4, 6, 1, 3, 5, 7)])
            for run in (baseline, candidate):
                self.assertTrue(all(len(row["tool_calls"][0]["output"].encode("ascii")) == 17
                                    for row in run["results"]))
            original = {path.name: path.read_bytes() for path in fixture.iterdir()}
            refused = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertEqual(original, {path.name: path.read_bytes() for path in fixture.iterdir()})


if __name__ == "__main__":
    unittest.main()
