"""Synthetic valid nested JSON must not fail in equality before serializer limits."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from weasley_agent_bench.scoring import _equal, evaluate, compare
from weasley_agent_bench.repeated import compare_repeated


def nested(leaf, depth=500):
    for index in range(depth):
        leaf = [leaf] if index % 2 else {'node': leaf}
    return leaf


def artifacts(expected, actual, field='final'):
    assertion = [{'path': '', 'equals': expected}]
    expect = {'final': assertion}
    calls = []
    if field != 'final':
        expected = {'value': expected} if field == 'arguments' else expected
        actual = {'value': actual} if field == 'arguments' else actual
        expect = {'tools': {'mode': 'exact', 'steps': [
            {'name': 'probe', 'outcome': 'success', field: [{'path': '', 'equals': expected}]}]}}
        calls = [{'name': 'probe', 'outcome': 'success', 'arguments': actual if field == 'arguments' else {},
                  'output': actual if field == 'output' else None}]
    suite = {'schema_version': 1, 'suite_id': 'nested', 'tasks': [{'id': 't', 'prompt': 'synthetic', 'expect': expect}]}
    run = {'schema_version': 1, 'suite_id': 'nested', 'run_id': 'r', 'results': [
        {'task_id': 't', 'status': 'completed', 'final': actual if field == 'final' else None, 'tool_calls': calls}]}
    return suite, run


class DeepEqualityTests(unittest.TestCase):
    def test_deep_final_equal_and_distinct(self):
        for left, right, passed in [(1, 1.0, True), (True, 1, False), ('a', 'b', False)]:
            with self.subTest(left=left, right=right):
                suite, run = artifacts(nested(left), nested(right))
                self.assertEqual(evaluate(suite, run)['passed'], passed)

    def test_deep_tool_arguments_and_outputs(self):
        for field in ('arguments', 'output'):
            for right, passed in [(1, True), (False, False)]:
                with self.subTest(field=field, right=right):
                    suite, run = artifacts(nested(1), nested(right), field)
                    self.assertEqual(evaluate(suite, run)['passed'], passed)

    def test_deep_subsequence(self):
        suite, run = artifacts(nested(1), nested(1), 'output')
        suite['tasks'][0]['expect']['tools']['mode'] = 'subsequence'
        self.assertTrue(evaluate(suite, run)['passed'])

    def test_deep_paired_regression(self):
        suite, before = artifacts(nested(1), nested(1))
        _, after = artifacts(nested(1), nested(2))
        after['run_id'] = 'candidate'
        report = compare(suite, before, after)
        self.assertFalse(report['no_regressions'])
        self.assertEqual(report['summary']['regressions'], 1)

    def test_deep_repeated_analysis(self):
        suite, before = artifacts(nested(1), nested(1))
        _, after = artifacts(nested(1), nested(2))
        def batch(run, name):
            return {'schema_version': 1, 'suite_id': 'nested', 'batch_id': name, 'trials': [
                {'trial_id': str(i), 'run': {**run, 'run_id': str(i)}} for i in range(2)]}
        report = compare_repeated(suite, batch(before, 'a'), batch(after, 'b'), resamples=100)
        self.assertEqual(report['summary']['pass_rate_delta'], -1)

    def test_saved_json_cli_parity(self):
        suite, run = artifacts(nested(1), nested(1))
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('suite.json', 'run.json')]
            for path, obj in zip(paths, (suite, run)):
                path.write_text(json.dumps(obj), encoding='utf-8')
            result = subprocess.run([sys.executable, '-m', 'weasley_agent_bench', 'evaluate', *map(str, paths)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), evaluate(suite, run))

    def test_wide_siblings_and_no_input_mutation(self):
        left = {'a': list(range(10000)), 'b': {'x': True, 'y': None}}
        right = {'b': {'y': None, 'x': True}, 'a': list(range(10000))}
        before = copy.deepcopy((left, right))
        self.assertTrue(_equal(left, right))
        self.assertEqual((left, right), before)
        right['a'][-1] = -1
        self.assertFalse(_equal(left, right))

    def test_nested_empty_containers_and_types(self):
        for left, right, passed in [([], [], True), ({}, {}, True), ([], {}, False),
                                   ([1], [1, 2], False), ({'a': 1}, {'b': 1}, False),
                                   ([True], [1], False), ([None], [None], True)]:
            with self.subTest(left=left, right=right):
                self.assertEqual(_equal(nested(left), nested(right)), passed)

    def test_jsonl_evaluation_and_comparison_parity(self):
        from test_jsonl_compare import write_jsonl, materialize
        from weasley_agent_bench import evaluate_jsonl, compare_jsonl
        suite, before = artifacts(nested(1), nested(1))
        _, after = artifacts(nested(1), nested(False))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / name for name in ('suite', 'before', 'after')]
            for path, document, kind in zip(paths, (suite, before, after), ('suite', 'run', 'run')):
                write_jsonl(path, document, kind)
            events = list(evaluate_jsonl(*paths[:2]))
            self.assertTrue(events[-1]['complete'])
            self.assertEqual([e['result'] for e in events[1:-1]], evaluate(suite, before)['results'])
            self.assertEqual(materialize(list(compare_jsonl(*paths))), compare(suite, before, after))

    def test_jsonl_repeated_parity(self):
        from test_jsonl_repeated import write_records, suite_records, batch_records, materialize
        from weasley_agent_bench import compare_repeated_jsonl
        suite, before = artifacts(nested(1), nested(1))
        _, after = artifacts(nested(1), nested(2))
        def batch(run, name):
            return {'schema_version': 1, 'suite_id': 'nested', 'batch_id': name, 'trials': [
                {'trial_id': str(i), 'run': {**run, 'run_id': str(i)}} for i in range(2)]}
        baseline, candidate = batch(before, 'a'), batch(after, 'b')
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('suite', 'before', 'after')]
            for path, rows in zip(paths, (suite_records(suite), batch_records(baseline), batch_records(candidate))):
                write_records(path, rows)
            self.assertEqual(materialize(list(compare_repeated_jsonl(*paths, resamples=100))),
                             compare_repeated(suite, baseline, candidate, resamples=100))
