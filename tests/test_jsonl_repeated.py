from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from weasley_agent_bench import ContractError, compare_repeated, load_document

ROOT = Path(__file__).resolve().parents[1]


def suite_records(suite):
    return [{"format": "weasley-agent-bench/suite-jsonl", "format_version": 1,
             "schema_version": 1, "suite_id": suite["suite_id"]},
            *suite["tasks"], {"end": True, "count": len(suite["tasks"])}]


def batch_records(batch):
    rows = [{"format": "weasley-agent-bench/batch-jsonl", "format_version": 1,
             **{key: value for key, value in batch.items() if key != "trials"}}]
    for trial in batch["trials"]:
        run = trial["run"]
        rows.append({"trial_id": trial["trial_id"],
                     "run": {key: value for key, value in run.items() if key != "results"}})
        rows.extend(run["results"])
        rows.append({"end_trial": True, "count": len(run["results"])})
    rows.append({"end": True, "count": len(batch["trials"])})
    return rows


def write_records(path, rows):
    path.write_bytes(b"".join((json.dumps(row, ensure_ascii=True) + "\n").encode("utf-8") for row in rows))


def materialize(events):
    header, *rows = events
    terminal = rows.pop()
    assert header["format"] == "weasley-agent-bench/repeated-comparison-jsonl"
    assert terminal["complete"] is True
    fields = ("schema_version", "suite_id", "suite_sha256", "baseline_batch_id", "candidate_batch_id", "analysis")
    return {**{key: header[key] for key in fields},
            **{key: terminal[key] for key in ("summary", "interval")},
            "trials": [row["trial"] for row in rows if "trial" in row],
            "tasks": [row["task"] for row in rows if "task" in row]}


class JsonlRepeatedTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.paths = [self.root / (name + ".jsonl") for name in ("suite", "baseline", "candidate")]
        self.inputs = [load_document(ROOT / "examples/repeated" / (name + ".json"))
                       for name in ("suite", "baseline", "candidate")]
        self.write_inputs()

    def write_inputs(self):
        write_records(self.paths[0], suite_records(self.inputs[0]))
        for path, batch in zip(self.paths[1:], self.inputs[1:]):
            write_records(path, batch_records(batch))

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "weasley_agent_bench", "compare-repeated-jsonl",
                               *map(str, self.paths), *args], cwd=ROOT, capture_output=True,
                              text=True, check=False)

    def analyze(self, **options):
        from weasley_agent_bench import compare_repeated_jsonl
        return compare_repeated_jsonl(*self.paths, **options)

    def test_cli_matches_legacy_whole_trial_analysis(self):
        result = self.cli("--seed", "17")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        report = materialize([json.loads(line) for line in result.stdout.splitlines()])
        self.assertEqual(report, compare_repeated(*self.inputs, seed=17))
        self.assertEqual(report["interval"], {"lower": -0.25, "upper": 0.5})
        self.assertNotIn("passed", report)
        self.assertNotIn("no_regressions", report)

    def test_exhaustive_oracle_and_correlated_task_copies_preserve_interval(self):
        totals = sorted(sum(draw) for draw in itertools.product((0, 2, -1, 0), repeat=4))
        expected = {"lower": totals[(len(totals) + 39) // 40 - 1] / 12,
                    "upper": totals[(39 * len(totals) + 39) // 40 - 1] / 12}
        original = materialize(list(self.analyze(seed=17)))
        self.assertEqual(original["interval"], expected)
        for task in copy.deepcopy(self.inputs[0]["tasks"]):
            task["id"] += "-correlated-copy"
            self.inputs[0]["tasks"].append(task)
        for batch in self.inputs[1:]:
            for trial in batch["trials"]:
                for result in copy.deepcopy(trial["run"]["results"]):
                    result["task_id"] += "-correlated-copy"
                    trial["run"]["results"].append(result)
        self.write_inputs()
        duplicated = materialize(list(self.analyze(seed=17)))
        self.assertEqual(duplicated["interval"], original["interval"])
        self.assertEqual(duplicated["summary"]["pass_rate_delta"], original["summary"]["pass_rate_delta"])
        self.assertEqual(duplicated["summary"]["trial_pairs"], 4)

    def test_arbitrary_trial_and_result_order_repeats_without_changing_global_rng(self):
        state = random.getstate()
        expected = compare_repeated(*self.inputs, seed=42, resamples=333)
        for batch in self.inputs[1:]:
            batch["trials"].reverse()
            for trial in batch["trials"]:
                trial["run"]["results"].reverse()
        self.write_inputs()
        before = [path.read_bytes() for path in self.paths]
        self.assertEqual(materialize(list(self.analyze(seed=42, resamples=333))), expected)
        self.assertEqual(materialize(list(self.analyze(seed=42, resamples=333))), expected)
        self.assertEqual(random.getstate(), state)
        self.assertEqual([path.read_bytes() for path in self.paths], before)

    def test_unicode_trial_order_matches_python_including_escape_and_surrogate_ids(self):
        ids = ["é", "a", "\x00", "\ud800", "𐀀", "\uffff", '"', "\\", "a\x00"]
        for side, batch in enumerate(self.inputs[1:]):
            original = copy.deepcopy(batch["trials"])
            batch["trials"] = []
            for index, identity in enumerate(ids):
                trial = copy.deepcopy(original[index % 4])
                trial["trial_id"] = identity
                trial["run"]["run_id"] = f"run-{side}-{index}-\ud800"
                batch["trials"].append(trial)
            batch["trials"].reverse()
        self.write_inputs()
        report = materialize(list(self.analyze(seed=2**64 - 1, resamples=789)))
        self.assertEqual(report, compare_repeated(*self.inputs, seed=2**64 - 1, resamples=789))
        self.assertEqual([row["trial_id"] for row in report["trials"]], sorted(ids))

    def test_identical_outcomes_and_cross_side_run_ids_do_not_imply_independence(self):
        self.inputs[2] = copy.deepcopy(self.inputs[1])
        self.write_inputs()
        report = materialize(list(self.analyze(resamples=100)))
        self.assertEqual(report, compare_repeated(*self.inputs, resamples=100))
        self.assertEqual(report["interval"], {"lower": 0, "upper": 0})
        self.assertIn("independent representative trial pairs", report["analysis"]["scope"])
        # Equal contents with distinct valid IDs are permitted declarations,
        # never evidence that the runner actually performed independent trials.
        for batch in self.inputs[1:]:
            for index, trial in enumerate(batch["trials"]):
                run = copy.deepcopy(batch["trials"][0]["run"])
                run["run_id"] = f"declared-distinct-{index}"
                trial["run"] = run
        self.write_inputs()
        self.assertEqual(materialize(list(self.analyze(resamples=100))),
                         compare_repeated(*self.inputs, resamples=100))

    def test_negative_change_is_descriptive_success_with_no_gate_flags(self):
        self.inputs[1], self.inputs[2] = self.inputs[2], self.inputs[1]
        self.write_inputs()
        result = self.cli("--resamples", "100")
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        report = materialize(events)
        self.assertLess(report["summary"]["pass_rate_delta"], 0)
        self.assertNotIn("passed", events[-1])
        self.assertNotIn("no_regressions", events[-1])

    def test_status_tool_budget_and_missing_usage_failures_match_legacy(self):
        for task in self.inputs[0]["tasks"]:
            task["expect"]["tools"] = {"mode": "exact", "steps": []}
            task["budget"] = {"max_tool_calls": 0, "max_input_tokens": 2}
        for batch in self.inputs[1:]:
            for trial in batch["trials"]:
                for result in trial["run"]["results"]:
                    result["usage"] = {"input_tokens": 2}
        self.inputs[2]["trials"][0]["run"]["results"][0]["status"] = "error"
        self.inputs[2]["trials"][1]["run"]["results"][0]["status"] = "cancelled"
        self.inputs[2]["trials"][2]["run"]["results"][0].pop("usage")
        self.inputs[2]["trials"][3]["run"]["results"][0]["tool_calls"] = [
            {"name": "extra", "arguments": {}, "outcome": "success", "output": None}]
        self.write_inputs()
        self.assertEqual(materialize(list(self.analyze(resamples=100))),
                         compare_repeated(*self.inputs, resamples=100))

    def test_missing_unmatched_duplicate_trials_and_reused_run_ids_rejected(self):
        original = copy.deepcopy(self.inputs)
        mutations = [lambda b: b["trials"].pop(),
                     lambda b: b["trials"][0].update(trial_id="unpaired"),
                     lambda b: b["trials"].append(copy.deepcopy(b["trials"][0])),
                     lambda b: b["trials"][1]["run"].update(run_id=b["trials"][0]["run"]["run_id"])]
        for side in (1, 2):
            for mutation in mutations:
                self.inputs = copy.deepcopy(original)
                mutation(self.inputs[side])
                self.write_inputs()
                with self.subTest(side=side, mutation=mutation), self.assertRaises(ContractError):
                    next(self.analyze(resamples=100))
        for size in (0, 1):
            self.inputs = copy.deepcopy(original)
            for batch in self.inputs[1:]:
                batch["trials"] = batch["trials"][:size]
            self.write_inputs()
            with self.assertRaisesRegex(ContractError, "at least two"):
                next(self.analyze(resamples=100))

    def test_incomplete_duplicate_unknown_and_wrong_suite_runs_never_emit_header(self):
        original = copy.deepcopy(self.inputs)
        mutations = [lambda r: r["results"].pop(), lambda r: r["results"].clear(),
                     lambda r: r["results"].append(copy.deepcopy(r["results"][0])),
                     lambda r: r["results"][0].update(task_id="unknown"),
                     lambda r: r.update(suite_id="other")]
        for side in (1, 2):
            for mutation in mutations:
                self.inputs = copy.deepcopy(original)
                mutation(self.inputs[side]["trials"][-1]["run"])
                self.write_inputs()
                with self.subTest(side=side, mutation=mutation), self.assertRaisesRegex(ContractError, "JSONL line"):
                    next(self.analyze(resamples=100))

    def test_strict_headers_and_nested_metadata_reject_extra_or_mistyped_fields(self):
        original = batch_records(self.inputs[2])
        mutations = [(0, "format", "other"), (0, "format_version", True), (0, "schema_version", 2),
                     (0, "batch_id", ""), (0, "suite_id", "other"), (0, "url", "private-payload-sentinel"),
                     (1, "trial_id", ""), (1, "run", None), (1, "unknown", True)]
        for index, key, value in mutations:
            rows = copy.deepcopy(original)
            rows[index][key] = value
            write_records(self.paths[2], rows)
            with self.subTest(key=key), self.assertRaises(ContractError) as error:
                next(self.analyze(resamples=100))
            self.assertNotIn("private-payload-sentinel", str(error.exception))
        for key, value in (("schema_version", True), ("schema_version", 2), ("run_id", ""),
                           ("results", []), ("suite_id", "other")):
            rows = copy.deepcopy(original)
            rows[1]["run"][key] = value
            write_records(self.paths[2], rows)
            with self.assertRaisesRegex(ContractError, "invalid trial header"):
                next(self.analyze(resamples=100))

    def test_late_framing_and_decode_errors_have_empty_cli_stdout(self):
        for side in (1, 2):
            original = batch_records(self.inputs[side])
            cases = [original[:-1], original[:-2], original + [{}],
                     original[:-1] + [{"end": True, "count": 99}],
                     original[:-2] + [{"end_trial": True, "count": True}, original[-1]],
                     original[:3] + [original[1]] + original[3:]]
            for rows in cases:
                self.write_inputs()
                write_records(self.paths[side], rows)
                result = self.cli("--resamples", "100")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("JSONL line", json.loads(result.stderr)["error"])
        for raw in (b'\xff\n', b'{"private-payload-sentinel":NaN}\n', b'{"a":1,"a":2}\n',
                    b'[]\n', b'\n', b'[' * 2000 + b'\n'):
            self.write_inputs()
            lines = self.paths[2].read_bytes().splitlines(keepends=True)
            self.paths[2].write_bytes(b''.join(lines[:-1]) + raw)
            result = self.cli("--resamples", "100")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("private-payload-sentinel", result.stderr)

    def test_record_bounds_apply_to_each_result_not_whole_trial(self):
        for batch in self.inputs[1:]:
            for trial in batch["trials"]:
                for result in trial["run"]["results"]:
                    result["final"] = "x" * 1024
        self.write_inputs()
        limit = max(len(line) for path in self.paths for line in path.read_bytes().splitlines(keepends=True))
        self.assertEqual(materialize(list(self.analyze(resamples=100, max_record_bytes=limit))),
                         compare_repeated(*self.inputs, resamples=100))
        self.assertGreater(self.paths[1].stat().st_size, limit * 3)
        with self.assertRaisesRegex(ContractError, "record exceeds"):
            next(self.analyze(resamples=100, max_record_bytes=limit - 1))

    def test_lf_crlf_and_final_record_without_newline_preserve_report(self):
        expected = compare_repeated(*self.inputs, resamples=100)
        for path in self.paths:
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n").rstrip(b"\r\n"))
        self.assertEqual(materialize(list(self.analyze(resamples=100))), expected)

    def test_options_and_explicit_file_errors_are_content_safe(self):
        for option, values in {"seed": [-1, 2**64, True, 1.0],
                               "resamples": [99, 100001, False, 100.0],
                               "max_record_bytes": [0, -1, True, sys.maxsize]}.items():
            for value in values:
                with self.assertRaisesRegex(ContractError, option):
                    next(self.analyze(**{option: value}))
        from weasley_agent_bench import compare_repeated_jsonl
        for index in range(3):
            for invalid in (str(self.root / "private-payload-sentinel"), "private-payload-sentinel\x00"):
                paths = list(self.paths)
                paths[index] = invalid
                with self.assertRaisesRegex(ContractError, "cannot read input") as error:
                    next(compare_repeated_jsonl(*paths, resamples=100))
                self.assertNotIn("private-payload-sentinel", str(error.exception))

    def test_cleanup_after_complete_error_and_early_close(self):
        scratch = self.root / "scratch"
        scratch.mkdir()
        list(self.analyze(resamples=100, temporary_directory=scratch))
        self.assertEqual(list(scratch.iterdir()), [])
        for consumed in (1, 2, 6):
            stream = self.analyze(resamples=100, temporary_directory=scratch)
            for _ in range(consumed):
                next(stream)
            self.assertTrue(list(scratch.iterdir()))
            stream.close()
            self.assertEqual(list(scratch.iterdir()), [])
        self.inputs[2]["trials"][-1]["run"]["results"].pop()
        self.write_inputs()
        with self.assertRaises(ContractError):
            next(self.analyze(resamples=100, temporary_directory=scratch))
        self.assertEqual(list(scratch.iterdir()), [])
        with self.assertRaisesRegex(ContractError, "temporary storage failure"):
            next(self.analyze(resamples=100, temporary_directory=self.root / "absent"))

    def test_mid_trial_scoring_failure_releases_storage_before_any_output(self):
        nested = None
        for _ in range(500):
            nested = [nested]
        self.inputs[0]["tasks"][1]["expect"] = {"final": [{"path": "", "equals": nested}]}
        self.inputs[1]["trials"][0]["run"]["results"][1]["final"] = nested
        self.write_inputs()
        scratch = self.root / "scratch"
        scratch.mkdir()
        with self.assertRaisesRegex(ContractError, "baseline JSONL line 4: invalid result contract or scoring limit"):
            next(self.analyze(resamples=100, temporary_directory=scratch))
        self.assertEqual(list(scratch.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
