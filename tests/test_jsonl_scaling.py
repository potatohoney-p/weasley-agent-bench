"""Small integration check for the synthetic scaling harness, not a memory gate."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from weasley_agent_bench import evaluate, load_document


ROOT = Path(__file__).resolve().parents[1]


class ScalingHarnessTests(unittest.TestCase):
    def test_cli_scores_equivalent_formats_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "fixtures"
            command = [sys.executable, "-B", "-m", "experiments.jsonl_scaling",
                       "--tasks", "3", "--output-bytes", "17", "--modes", "legacy", "jsonl",
                       "--output-dir", str(destination)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True,
                                    text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["synthetic"])
            case = report["cases"][0]
            self.assertIs(case["score_parity"], True)
            self.assertEqual([item["mode"] for item in case["measurements"]], ["legacy", "jsonl"])
            self.assertEqual((case["tasks"], case["output_bytes_per_task"]), (3, 17))
            measurement = case["measurements"][0]
            fixture = destination / "tasks-3-bytes-17"
            suite = load_document(fixture / "suite.json")
            run = load_document(fixture / "run.json")
            expected = evaluate(suite, run)
            self.assertEqual(measurement["summary"], expected["summary"])
            self.assertEqual(measurement["suite_sha256"], expected["suite_sha256"])
            self.assertEqual(measurement["summary"]["failed"], 1)
            self.assertGreater(measurement["tracemalloc_peak_bytes"], 0)
            self.assertEqual(len(measurement["logical_report_sha256"]), 64)
            self.assertEqual([len(row["tool_calls"][0]["output"].encode("ascii"))
                              for row in run["results"]], [17, 17, 17])
            for name, field in (("suite", "tasks"), ("run", "results")):
                records = [json.loads(line) for line in (fixture / f"{name}.jsonl").read_bytes().splitlines()]
                self.assertEqual(records[1:-1], (suite if name == "suite" else run)[field])
                self.assertEqual(records[-1], {"end": True, "count": 3})
            original = {path.name: path.read_bytes() for path in fixture.iterdir()}
            refused = subprocess.run(command, cwd=ROOT, capture_output=True,
                                     text=True, encoding="utf-8")
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertEqual(original, {path.name: path.read_bytes() for path in fixture.iterdir()})


if __name__ == "__main__":
    unittest.main()
