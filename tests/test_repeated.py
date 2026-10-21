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

from weasley_agent_bench import ContractError, compare_repeated, evaluate, load_document

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "repeated"


def fixtures():
    return tuple(load_document(FIXTURES / f"{name}.json")
                 for name in ("suite", "baseline", "candidate"))


class RepeatedTests(unittest.TestCase):
    def test_synthetic_fixture_matches_counts_and_exact_bootstrap_distribution(self):
        suite, baseline, candidate = fixtures()
        report = compare_repeated(suite, baseline, candidate, seed=17)
        self.assertEqual(report["summary"], {
            "tasks": 3, "trial_pairs": 4, "baseline_pass_rate": 7 / 12,
            "candidate_pass_rate": 8 / 12, "pass_rate_delta": 1 / 12,
        })
        self.assertEqual(report["suite_sha256"], evaluate(suite, baseline["trials"][0]["run"])["suite_sha256"])
        # Independent exhaustive oracle: 4^4 equally likely draws. The fixture's
        # seeded Monte Carlo quantiles equal these exact discrete quantiles.
        totals = sorted(sum(draw) for draw in itertools.product((0, 2, -1, 0), repeat=4))
        self.assertEqual(report["interval"], {
            "lower": totals[(len(totals) + 39) // 40 - 1] / 12,
            "upper": totals[(39 * len(totals) + 39) // 40 - 1] / 12,
        })
        self.assertEqual(report["interval"], {"lower": -0.25, "upper": 0.5})
        self.assertEqual(report["tasks"][1]["regressions"], 3)
        self.assertEqual(report["tasks"][1]["improvements"], 1)
        self.assertNotIn("passed", report)
        self.assertNotIn("no_regressions", report)
        json.dumps(report, allow_nan=False)

    def test_identical_pairs_have_zero_interval_despite_trial_variation(self):
        suite, baseline, _ = fixtures()
        report = compare_repeated(suite, baseline, baseline, resamples=100)
        self.assertEqual(report["interval"], {"lower": 0, "upper": 0})
        self.assertEqual(report["summary"]["pass_rate_delta"], 0)

    def test_duplicating_correlated_tasks_does_not_shrink_interval(self):
        suite, baseline, candidate = fixtures()
        expected = compare_repeated(suite, baseline, candidate, seed=19, resamples=333)
        for task in copy.deepcopy(suite["tasks"]):
            task["id"] += "-copy"
            suite["tasks"].append(task)
        for batch in (baseline, candidate):
            for trial in batch["trials"]:
                for result in copy.deepcopy(trial["run"]["results"]):
                    result["task_id"] += "-copy"
                    trial["run"]["results"].append(result)
        report = compare_repeated(suite, baseline, candidate, seed=19, resamples=333)
        self.assertEqual(report["interval"], expected["interval"])
        self.assertEqual(report["summary"]["pass_rate_delta"], expected["summary"]["pass_rate_delta"])

    def test_constant_improvements_and_regressions_reach_exact_bounds(self):
        for direction in (-1, 1):
            suite, baseline, candidate = fixtures()
            for batch, passed in ((baseline, direction < 0), (candidate, direction > 0)):
                for trial in batch["trials"]:
                    for result in trial["run"]["results"]:
                        result["final"] = passed
            report = compare_repeated(suite, baseline, candidate, resamples=100)
            self.assertEqual(report["interval"], {"lower": direction, "upper": direction})
            self.assertEqual(report["summary"]["pass_rate_delta"], direction)

    def test_error_cancelled_and_failed_tool_contracts_remain_failures(self):
        suite, baseline, candidate = fixtures()
        for task in suite["tasks"]:
            task["expect"]["tools"] = {"mode": "exact", "steps": []}
        candidate["trials"][0]["run"]["results"][0]["status"] = "error"
        candidate["trials"][1]["run"]["results"][0]["status"] = "cancelled"
        candidate["trials"][3]["run"]["results"][0]["tool_calls"].append({
            "name": "unexpected", "arguments": {}, "outcome": "success", "output": None,
        })
        report = compare_repeated(suite, baseline, candidate, resamples=100)
        self.assertEqual(report["tasks"][0]["candidate_passed"], 0)
        self.assertEqual(report["tasks"][0]["regressions"], 3)

    def test_order_invariance_repeatability_no_mutation_or_global_rng_change(self):
        suite, baseline, candidate = fixtures()
        original = copy.deepcopy((suite, baseline, candidate))
        rng_state = random.getstate()
        expected = compare_repeated(suite, baseline, candidate, seed=42, resamples=789)
        self.assertEqual((suite, baseline, candidate), original)
        self.assertEqual(random.getstate(), rng_state)
        self.assertEqual(compare_repeated(*original, seed=42, resamples=789), expected)
        for batch in (baseline, candidate):
            batch["trials"].reverse()
            for trial in batch["trials"]:
                trial["run"]["results"].reverse()
        self.assertEqual(compare_repeated(suite, baseline, candidate, seed=42, resamples=789), expected)

    def test_missing_unmatched_duplicate_trials_and_reused_run_ids_rejected(self):
        mutations = [
            lambda b: b["trials"].pop(),
            lambda b: b["trials"][0].update(trial_id="unpaired"),
            lambda b: b["trials"].append(copy.deepcopy(b["trials"][0])),
            lambda b: b["trials"][1]["run"].update(run_id=b["trials"][0]["run"]["run_id"]),
        ]
        for side in (1, 2):
            for mutation in mutations:
                with self.subTest(side=side, mutation=mutation):
                    inputs = fixtures()
                    mutation(inputs[side])
                    with self.assertRaises(ContractError):
                        compare_repeated(*inputs, resamples=100)

    def test_zero_and_one_trial_rejected_not_zero_uncertainty(self):
        for size in (0, 1):
            inputs = fixtures()
            for batch in inputs[1:]:
                batch["trials"] = batch["trials"][:size]
            with self.subTest(size=size), self.assertRaisesRegex(ContractError, "at least two"):
                compare_repeated(*inputs)

    def test_incomplete_duplicate_unknown_and_mixed_suite_runs_rejected(self):
        mutations = [
            lambda run: run["results"].pop(),
            lambda run: run["results"].clear(),
            lambda run: run["results"].append(copy.deepcopy(run["results"][0])),
            lambda run: run["results"][0].update(task_id="other"),
            lambda run: run.update(suite_id="other"),
        ]
        for side in (1, 2):
            for mutation in mutations:
                inputs = fixtures()
                mutation(inputs[side]["trials"][1]["run"])
                with self.subTest(side=side, mutation=mutation), self.assertRaisesRegex(
                    ContractError, r"(baseline|candidate)\.trials\[1\]\.run"
                ):
                    compare_repeated(*inputs, resamples=100)

    def test_strict_batch_and_trial_shape_validation(self):
        mutations = [
            lambda b: b.update(schema_version=True),
            lambda b: b.update(schema_version=2),
            lambda b: b.update(suite_id="other"),
            lambda b: b.update(batch_id=" "),
            lambda b: b.update(unknown="ignored?"),
            lambda b: b.pop("trials"),
            lambda b: b.update(trials={}),
            lambda b: b["trials"].__setitem__(0, None),
            lambda b: b["trials"][0].update(trial_id=[]),
            lambda b: b["trials"][0].update(trial_id=""),
            lambda b: b["trials"][0].update(unknown=True),
            lambda b: b["trials"][0].update(run=None),
            lambda b: b["trials"][0].pop("run"),
        ]
        for mutation in mutations:
            inputs = fixtures()
            mutation(inputs[2])
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ContractError, "candidate"):
                compare_repeated(*inputs, resamples=100)
        for value in (None, [], False, "batch"):
            with self.subTest(value=value), self.assertRaises(ContractError):
                compare_repeated(fixtures()[0], value, fixtures()[2], resamples=100)

    def test_analysis_options_are_bounded_integers(self):
        for option, values in {
            "seed": [-1, 2**64, True, 1.0, float("nan"), float("inf"), "1", None],
            "resamples": [0, 99, 100001, False, 100.0, float("nan"), float("inf"), "100", None],
        }.items():
            for value in values:
                with self.subTest(option=option, value=value), self.assertRaisesRegex(
                    ContractError, f"analysis.{option}"
                ):
                    compare_repeated(*fixtures(), **{option: value})
        for seed in (0, 2**64 - 1):
            self.assertEqual(compare_repeated(*fixtures(), seed=seed, resamples=100)["analysis"]["seed"], seed)


class RepeatedCliTests(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "weasley_agent_bench", "compare-repeated", *map(str, args)],
            cwd=ROOT, capture_output=True, text=True,
        )

    def test_cli_output_matches_library_and_is_byte_repeatable(self):
        paths = [FIXTURES / f"{name}.json" for name in ("suite", "baseline", "candidate")]
        first = self.cli(*paths, "--seed", "17")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stderr, "")
        self.assertEqual(json.loads(first.stdout), compare_repeated(*fixtures(), seed=17))
        self.assertEqual(first.stdout, self.cli(*paths, "--seed", "17").stdout)

    def test_negative_change_is_descriptive_not_a_cli_failure(self):
        result = self.cli(*(FIXTURES / f"{name}.json" for name in ("suite", "candidate", "baseline")))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(json.loads(result.stdout)["summary"]["pass_rate_delta"], 0)

    def test_invalid_option_or_incomplete_trial_returns_json_error_without_score(self):
        paths = [FIXTURES / f"{name}.json" for name in ("suite", "baseline", "candidate")]
        for args in (["--seed", "-1"], ["--resamples", "99"], ["--seed", str(2**64)]):
            result = self.cli(*paths, *args)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertIn("error", json.loads(result.stderr))
        suite, baseline, candidate = fixtures()
        candidate["trials"][0]["run"]["results"].pop()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "incomplete.json"
            path.write_text(json.dumps(candidate), encoding="utf-8")
            result = self.cli(paths[0], paths[1], path)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("one result for every suite task", json.loads(result.stderr)["error"])


if __name__ == "__main__":
    unittest.main()
