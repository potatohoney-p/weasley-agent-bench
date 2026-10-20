"""Measure known fault detection with and without tool/budget contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from weasley_agent_bench import ContractError, evaluate, load_document

ROOT = Path(__file__).resolve().parents[1]


def run_experiment() -> dict:
    suite = load_document(ROOT / "examples" / "suite.json")
    candidate = load_document(ROOT / "examples" / "candidate.json")
    ablated_suite = copy.deepcopy(suite)
    for task in ablated_suite["tasks"]:
        task["expect"] = {"final": task["expect"]["final"]}
        task.pop("budget", None)

    mutations = [
        ("valid-control", False, lambda r: None),
        ("stale-final-fact", True, lambda r: r["results"][0]["final"].update(city="Tokyo")),
        ("lost-child-finding", True, lambda r: r["results"][2]["final"].update(findings=[])),
        ("failed-recall-with-plausible-answer", True, lambda r: r["results"][0]["tool_calls"][0].update(outcome="error")),
        ("wrong-recall-arguments", True, lambda r: r["results"][0]["tool_calls"][0].update(arguments={"query": "unrelated"})),
        ("missing-retry", True, lambda r: r["results"][1]["tool_calls"].pop()),
        ("reversed-retry", True, lambda r: r["results"][1]["tool_calls"].reverse()),
        ("forbidden-send-attempt", True, lambda r: r["results"][3]["tool_calls"].append(
            {"name": "send_email", "arguments": {}, "outcome": "error", "output": None})),
        ("token-cap-exceeded", True, lambda r: r["results"][0]["usage"].update(input_tokens=301)),
        ("capped-usage-omitted", True, lambda r: r["results"][0].pop("usage")),
        ("cancelled-with-correct-final", True, lambda r: r["results"][2].update(status="cancelled")),
        ("missing-case", True, lambda r: r["results"].pop()),
        ("duplicate-case", True, lambda r: r["results"].append(copy.deepcopy(r["results"][0]))),
    ]

    def rejected(contract, run):
        try:
            report = evaluate(contract, run)
            return {"rejected": not report["passed"], "kind": "scored"}
        except ContractError:
            return {"rejected": True, "kind": "invalid_input"}

    rows = []
    for name, should_reject, mutate in mutations:
        run = copy.deepcopy(candidate)
        mutate(run)
        rows.append({
            "fault": name,
            "expected_rejection": should_reject,
            "full_contract": rejected(suite, run),
            "final_state_ablation": rejected(ablated_suite, run),
        })
    faults = [row for row in rows if row["expected_rejection"]]
    return {
        "experiment": "synthetic-fault-matrix-v1",
        "suite_sha256": evaluate(suite, candidate)["suite_sha256"],
        "scope": "Hand-authored mutations; no model calls or provider quality claims.",
        "ablation": "Remove tool and budget expectations; retain validation, coverage and completion checks.",
        "summary": {
            "faults": len(faults),
            "controls": len(rows) - len(faults),
            "full_contract_detected": sum(row["full_contract"]["rejected"] for row in faults),
            "ablation_detected": sum(row["final_state_ablation"]["rejected"] for row in faults),
            "full_contract_matches_expectations": all(row["full_contract"]["rejected"] == row["expected_rejection"] for row in rows),
        },
        "results": rows,
    }


def main():
    report = run_experiment()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["summary"]["full_contract_matches_expectations"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
