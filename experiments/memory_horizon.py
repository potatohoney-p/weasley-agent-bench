"""Synthetic cross-session memory task suite and deterministic toy baselines."""

from __future__ import annotations

import argparse
from collections import OrderedDict
import copy
import json
import math
from pathlib import Path
import platform
import statistics
import time

from weasley_agent_bench import evaluate


def make_episodes() -> list[dict]:
    """Eight synthetic teams, six sessions, corrections and one retraction.

    Questions are held out of the fact stream. No answer labels are passed to
    a baseline; an external runner should likewise receive only events/questions.
    """
    offices = ["서울", "Tokyo", "Paris", "Berlin", "서울 동부", "Osaka", "Lyon", "Bonn"]
    episodes = []
    for session in range(6):
        indices = range(8) if session == 0 else ((2 * session) % 8, (2 * session + 1) % 8)
        events = [{
            "event_id": f"s{session}-fact-{index}", "key": f"team-{index}",
            "operation": "set", "office": offices[(index + session) % len(offices)],
        } for index in indices]
        if session == 3:
            events.append({"event_id": "s3-retract-0", "key": "team-0", "operation": "retract", "office": None})
        questions = [{
            "id": f"s{session}-question-{index}", "key": f"team-{index}",
            "prompt": f"At the end of session {session}, what is the current office of synthetic team-{index}? Return office and evidence_ids. An explicit retraction means office=null and cites the retraction event. Do not use future sessions.",
        } for index in range(8)]
        episodes.append({"session_id": f"session-{session}", "events": events, "questions": questions})
    return episodes


def make_suite(episodes: list[dict]) -> dict:
    """Construct evaluation labels from each historical prefix, never future data."""
    current = {}
    tasks = []
    for episode in episodes:
        for event in episode["events"]:
            current[event["key"]] = event
        for question in episode["questions"]:
            event = current.get(question["key"])
            office = event["office"] if event else None
            evidence = [event["event_id"]] if event else []
            tasks.append({
                "id": question["id"], "prompt": question["prompt"],
                "tags": ["synthetic-memory", "cross-session", episode["session_id"]],
                "expect": {"final": [
                    {"path": "/office", "equals": office},
                    {"path": "/evidence_ids", "equals": evidence},
                ]},
            })
    return {"schema_version": 1, "suite_id": "synthetic-memory-horizon-v1", "tasks": tasks}


def _answer(event: dict | None) -> dict:
    if event is None:
        return {"office": None, "evidence_ids": []}
    return {"office": event["office"], "evidence_ids": [event["event_id"]]}


class SessionOnly:
    """Keep only latest records observed since the most recent session reset."""

    name = "session-only"

    def __init__(self):
        self.records = {}

    def start_session(self):
        self.records.clear()

    def observe(self, event):
        self.records[event["key"]] = event

    def query(self, key):
        return _answer(self.records.get(key)), list(self.records.values())


class RollingState(SessionOnly):
    """A bounded structured summary, not a model-generated natural-language one."""

    name = "rolling-state-4"

    def __init__(self, capacity=4):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        self.capacity = capacity
        self.records = OrderedDict()

    def start_session(self):
        pass

    def observe(self, event):
        key = event["key"]
        self.records[key] = event
        self.records.move_to_end(key)
        while len(self.records) > self.capacity:
            self.records.popitem(last=False)


class LatestRecord(SessionOnly):
    """An exact-key dictionary lookup, not semantic search or a learned retriever."""

    name = "latest-record"

    def start_session(self):
        pass

    def query(self, key):
        event = self.records.get(key)
        return _answer(event), [event] if event else []


def _characters(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def run_experiment(timing_trials: int = 0) -> tuple[dict, dict, dict, list[dict]]:
    if type(timing_trials) is not int or not 0 <= timing_trials <= 10000:
        raise ValueError("timing_trials must be an integer from 0 to 10000")
    episodes = make_episodes()
    suite = make_suite(episodes)
    runs = {}
    summaries = []
    for baseline_type in (SessionOnly, RollingState, LatestRecord):
        baseline = baseline_type()
        results = []
        context_characters = []
        token_proxies = []
        latencies_ns = []
        for episode in episodes:
            baseline.start_session()
            for event in episode["events"]:
                baseline.observe(copy.deepcopy(event))
            for question in episode["questions"]:
                final, context = baseline.query(question["key"])
                characters = _characters(context)
                context_characters.append(characters)
                token_proxies.append(math.ceil(characters / 4))
                results.append({"task_id": question["id"], "status": "completed",
                                "final": final, "tool_calls": []})
                for _ in range(timing_trials):
                    started = time.perf_counter_ns()
                    baseline.query(question["key"])
                    latencies_ns.append(time.perf_counter_ns() - started)
        run = {"schema_version": 1, "suite_id": suite["suite_id"],
               "run_id": f"synthetic-{baseline.name}", "results": results}
        runs[baseline.name] = run
        score = evaluate(suite, run)
        summary = {
            "baseline": baseline.name,
            "correct": score["summary"]["passed"],
            "questions": len(results),
            "accuracy": score["summary"]["passed"] / len(results),
            "mean_context_characters": statistics.mean(context_characters),
            "mean_context_token_proxy": statistics.mean(token_proxies),
            "final_stored_record_count": len(baseline.records),
            "final_stored_characters": _characters(list(baseline.records.values())),
            "failed_task_ids": [row["task_id"] for row in score["results"] if not row["passed"]],
        }
        if latencies_ns:
            ordered = sorted(latencies_ns)
            summary["lookup_timing"] = {
                "samples": len(ordered), "trials_per_question": timing_trials,
                "median_us": statistics.median(ordered) / 1000,
                "p95_us": ordered[math.ceil(0.95 * len(ordered)) - 1] / 1000,
            }
        summaries.append(summary)
    report = {
        "experiment": "synthetic-memory-horizon-v1",
        "suite_sha256": evaluate(suite, runs["latest-record"])["suite_sha256"],
        "sessions": len(episodes),
        "facts": sum(len(episode["events"]) for episode in episodes),
        "questions": len(suite["tasks"]),
        "context_proxy": "ceil(serialized context Unicode characters / 4); not tokenizer or billing usage",
        "scope": "Synthetic exact-key tasks and toy deterministic baselines; no model calls.",
        "baselines": summaries,
    }
    if timing_trials:
        report["timing_environment"] = {
            "python": platform.python_version(), "platform": platform.system(),
            "clock": "perf_counter_ns", "clock_resolution_seconds": time.get_clock_info("perf_counter").resolution,
            "scope": "In-process lookup and context selection only; excludes ingestion, models and network.",
        }
    return report, suite, runs, episodes


def export(directory: Path, report: dict, suite: dict, runs: dict, episodes: list) -> None:
    files = {"report.json": report, "suite.json": suite, "episodes.json": episodes,
             **{f"{name}.json": run for name, run in runs.items()}}
    if any((directory / name).exists() for name in files):
        raise FileExistsError("export would overwrite an existing artifact; choose a fresh directory")
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        (directory / name).write_text(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timing-trials", type=int, default=0, help="optional measured lookups per question, 0..10000")
    parser.add_argument("--output-dir", type=Path, help="optional fresh directory for suite, episodes, runs and report")
    args = parser.parse_args(argv)
    if not 0 <= args.timing_trials <= 10000:
        parser.error("--timing-trials must be from 0 to 10000")
    artifacts = run_experiment(args.timing_trials)
    if args.output_dir:
        try:
            export(args.output_dir, *artifacts)
        except OSError as error:
            parser.error(str(error))
    print(json.dumps(artifacts[0], sort_keys=True, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
