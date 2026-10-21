"""Paired trial bootstrap for pass-rate changes on a fixed task suite."""

from __future__ import annotations

import random

from .contracts import ContractError, _array, _fail, _object, _string, validate_suite
from .scoring import evaluate


def _score_batch(suite: dict, batch: dict, path: str) -> dict[str, dict]:
    _object(batch, path, {"schema_version", "suite_id", "batch_id", "trials"})
    if type(batch["schema_version"]) is not int or batch["schema_version"] != 1:
        _fail(f"{path}.schema_version", "only version 1 is supported")
    if batch["suite_id"] != suite["suite_id"]:
        _fail(f"{path}.suite_id", "does not match the task suite")
    _string(batch["batch_id"], f"{path}.batch_id")
    _array(batch["trials"], f"{path}.trials")
    if len(batch["trials"]) < 2:
        _fail(f"{path}.trials", "at least two complete trials are required")
    scores: dict[str, dict] = {}
    run_ids: set[str] = set()
    for index, trial in enumerate(batch["trials"]):
        location = f"{path}.trials[{index}]"
        _object(trial, location, {"trial_id", "run"})
        _string(trial["trial_id"], f"{location}.trial_id")
        if trial["trial_id"] in scores:
            _fail(f"{location}.trial_id", "duplicate trial ID")
        try:
            score = evaluate(suite, trial["run"])
        except ContractError as error:
            raise ContractError(f"{location}.run: {error}") from error
        if score["summary"]["missing"]:
            _fail(f"{location}.run.results", "one result for every suite task is required")
        if score["run_id"] in run_ids:
            _fail(f"{location}.run.run_id", "duplicate run ID within this batch")
        run_ids.add(score["run_id"])
        scores[trial["trial_id"]] = score
    return scores


def compare_repeated(
    suite: dict, baseline: dict, candidate: dict, *, seed: int = 0, resamples: int = 10000,
) -> dict:
    """Describe paired repeated runs; this is not a regression/significance gate.

    Resample complete trial pairs, retaining every task and both conditions
    together. The target is variation across repeated trials on this fixed suite,
    not generalization to unseen tasks. Independence across trials is an
    assumption supplied by the runner, not something saved results can prove.
    """
    validate_suite(suite)
    if type(seed) is not int or not 0 <= seed <= 2**64 - 1:
        _fail("analysis.seed", "expected an integer from 0 through 2^64 - 1")
    if type(resamples) is not int or not 100 <= resamples <= 100000:
        _fail("analysis.resamples", "expected an integer from 100 through 100000")
    before = _score_batch(suite, baseline, "baseline")
    after = _score_batch(suite, candidate, "candidate")
    if before.keys() != after.keys():
        _fail("candidate.trials", "trial IDs must match baseline exactly; no unpaired trials are dropped")
    trial_ids = sorted(before)
    task_count = len(suite["tasks"])
    trial_count = len(trial_ids)
    denominator = task_count * trial_count
    trials = []
    differences = []
    for trial_id in trial_ids:
        old, new = before[trial_id], after[trial_id]
        old_count, new_count = old["summary"]["passed"], new["summary"]["passed"]
        differences.append(new_count - old_count)
        trials.append({
            "trial_id": trial_id,
            "baseline_run_id": old["run_id"],
            "candidate_run_id": new["run_id"],
            "baseline_passed": old_count,
            "candidate_passed": new_count,
            "pass_rate_delta": (new_count - old_count) / task_count,
        })

    # Sort integer totals and divide only at report time to avoid accumulating
    # floating-point rounding. Use a local RNG without changing global state.
    rng = random.Random(seed)
    totals = sorted(
        sum(differences[rng.randrange(trial_count)] for _ in trial_ids)
        for _ in range(resamples)
    )
    # Empirical inverse-CDF percentiles: ceil(p * B) - 1, without interpolation.
    lower = totals[(resamples + 39) // 40 - 1] / denominator
    upper = totals[(39 * resamples + 39) // 40 - 1] / denominator
    tasks = []
    for index, task in enumerate(suite["tasks"]):
        old = [before[trial_id]["results"][index]["passed"] for trial_id in trial_ids]
        new = [after[trial_id]["results"][index]["passed"] for trial_id in trial_ids]
        tasks.append({
            "task_id": task["id"],
            "baseline_passed": sum(old),
            "candidate_passed": sum(new),
            "improvements": sum(not left and right for left, right in zip(old, new)),
            "regressions": sum(left and not right for left, right in zip(old, new)),
            "pass_rate_delta": (sum(new) - sum(old)) / trial_count,
        })
    return {
        "schema_version": 1,
        "suite_id": suite["suite_id"],
        "suite_sha256": before[trial_ids[0]]["suite_sha256"],
        "baseline_batch_id": baseline["batch_id"],
        "candidate_batch_id": candidate["batch_id"],
        "analysis": {
            "method": "paired-trial-percentile-bootstrap-v1",
            "estimand": "candidate-minus-baseline mean pass rate on the fixed suite",
            "resampling_unit": "complete paired suite trial",
            "scope": "conditional on this fixed suite and independent representative trial pairs",
            "confidence_level": 0.95,
            "seed": seed,
            "resamples": resamples,
            "rng": "python.random.Random",
            "quantiles": "empirical inverse CDF, no interpolation",
        },
        "summary": {
            "tasks": task_count,
            "trial_pairs": trial_count,
            "baseline_pass_rate": sum(row["baseline_passed"] for row in trials) / denominator,
            "candidate_pass_rate": sum(row["candidate_passed"] for row in trials) / denominator,
            "pass_rate_delta": sum(differences) / denominator,
        },
        "interval": {"lower": lower, "upper": upper},
        "trials": trials,
        "tasks": tasks,
    }
