"""Opt-in subprocess integration. Synthetic child, never a model/provider run."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from weasley_agent_bench.contracts import ContractError
from weasley_agent_bench.scoring import evaluate
from weasley_agent_bench.subagents import export_subagents, load_subagents_transcript

ROOT = Path(__file__).resolve().parents[1]
SOURCE = os.environ.get("WEASLEY_SUBAGENTS_TEST_SOURCE")


@unittest.skipUnless(SOURCE and os.name == "posix", "opt-in: pinned upstream checkout and POSIX Node 24 required")
class SubagentsRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.captures = {}
        for mode in ("success", "nonzero", "incomplete"):
            cls.captures[mode] = cls.capture(mode)
        cls.suite = json.loads((ROOT / "examples/subagents/suite.json").read_text(encoding="utf8"))
        cls.suite["tasks"][0]["expect"]["final"][0]["equals"] = "synthetic note"

    @staticmethod
    def capture(mode):
        result = subprocess.run(["node", "--experimental-strip-types",
            str(ROOT / "scripts/capture_subagents_runtime.mjs"), SOURCE, mode],
            cwd=ROOT, capture_output=True, text=True, encoding="utf8", timeout=30)
        if result.returncode:
            raise AssertionError(f"Synthetic runtime capture failed ({mode}): {result.stderr}")
        return json.loads(result.stdout)

    def export(self, capture):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "transcript.jsonl"
            path.write_bytes(capture["transcript"].encode("utf8"))
            records = load_subagents_transcript(str(path))
        return export_subagents(self.suite, records, capture["metadata"],
                                task_id="read-note", run_id="synthetic-runtime-export")

    def test_generated_metadata_and_cli_roundtrip(self):
        capture = self.captures["success"]
        self.assertEqual(capture["kind"], "synthetic-runtime-integration")
        self.assertEqual(capture["metadata"]["task"], self.suite["tasks"][0]["prompt"])
        self.assertEqual(capture["observed"], {"exitCode": 0, "finalOutput": "synthetic note", "toolCount": 1})
        exported = self.export(capture)
        expected = {"schema_version": 1, "suite_id": self.suite["suite_id"],
            "run_id": "synthetic-runtime-export", "results": [{"task_id": "read-note",
            "status": "completed", "final": "synthetic note", "tool_calls": [
            {"name": "read", "arguments": {"path": "synthetic-note.txt"},
             "outcome": "success", "output": "synthetic note"}]}]}
        self.assertEqual(exported, expected)
        self.assertTrue(evaluate(self.suite, exported)["passed"])
        # Runtime's normalized zero-default usage must not become benchmark facts.
        self.assertIn("usage", capture["metadata"])
        self.assertNotIn("usage", exported["results"][0])
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "suite.json").write_text(json.dumps(self.suite), encoding="utf8")
            (folder / "meta.json").write_text(json.dumps(capture["metadata"]), encoding="utf8")
            (folder / "trace.jsonl").write_bytes(capture["transcript"].encode("utf8"))
            result = subprocess.run([sys.executable, "-m", "weasley_agent_bench", "export-subagents",
                str(folder / "suite.json"), str(folder / "trace.jsonl"), str(folder / "meta.json"),
                "--task-id", "read-note", "--run-id", "synthetic-runtime-export"],
                cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), expected)

    def test_repeated_execution_has_identical_benchmark_projection(self):
        # Raw metadata/timestamps/temporary paths intentionally are not byte stable.
        self.assertEqual(self.export(self.captures["success"]), self.export(self.capture("success")))

    def test_runtime_nonzero_exit_is_rejected(self):
        capture = self.captures["nonzero"]
        self.assertEqual(capture["metadata"]["exitCode"], 7)
        self.assertFalse(capture["metadata"]["modelAttempts"][0]["success"])
        with self.assertRaisesRegex(ContractError, "successful foreground exits"):
            self.export(capture)

    def test_runtime_missing_result_is_rejected_even_with_zero_exit(self):
        capture = self.captures["incomplete"]
        self.assertEqual(capture["metadata"]["exitCode"], 0)
        with self.assertRaisesRegex(ContractError, "incomplete tool lifecycle"):
            self.export(capture)

    def test_mismatched_generated_identity_is_rejected(self):
        capture = copy.deepcopy(self.captures["success"])
        capture["metadata"]["runId"] = "a-different-run"
        with self.assertRaisesRegex(ContractError, "artifact identity mismatch"):
            self.export(capture)
