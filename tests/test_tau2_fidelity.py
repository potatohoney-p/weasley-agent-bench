from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from weasley_agent_bench import ContractError, evaluate, evaluate_jsonl, load_document

ROOT = Path(__file__).resolve().parents[1]


class Tau2FidelityTests(unittest.TestCase):
    def setUp(self):
        from weasley_agent_bench.tau2 import export_tau2_fidelity, load_tau2_episode
        self.export = export_tau2_fidelity
        self.load = load_tau2_episode
        self.row = self.load(ROOT / 'examples/tau2/task46-trial0.json')
        self.suite = load_document(ROOT / 'examples/tau2/fidelity-suite.json')

    def bundle(self, row=None, suite=None):
        return self.export(suite or self.suite, self.row if row is None else row,
                           task_id='historical-observations', run_id='recorded-reference')

    def failures(self, row):
        report = evaluate(self.suite, self.bundle(row)['run'])
        return [check['code'] for check in report['results'][0]['checks'] if not check['passed']]

    def test_historical_episode_preserves_native_observations(self):
        from weasley_agent_bench.tau2 import export_tau2_fidelity, load_tau2_episode

        episode = load_tau2_episode(ROOT / 'examples/tau2/task46-trial0.json')
        suite = load_document(ROOT / 'examples/tau2/fidelity-suite.json')
        bundle = export_tau2_fidelity(suite, episode, task_id='historical-observations', run_id='recorded-reference')
        result = bundle['run']['results'][0]
        self.assertEqual(result['tool_calls'], [{
            'name': 'transfer_to_human_agents',
            'arguments': {'summary': 'User wants to refund insurance only without canceling the flight. Policy does not allow removal of insurance after booking.'},
            'outcome': 'success', 'output': 'Transfer successful',
        }])
        self.assertEqual(result['final']['assistant_text'],
                         'YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON.')
        self.assertEqual(result['final']['call_id'], 'call_jSwZRs774js2PBliHRee5crf')
        self.assertNotIn('usage', result)
        self.assertEqual(bundle['provenance']['input_sha256'],
                         '9fc77bb6ca72f58a42589ffa8bbac89d26fec1b0d0bef53ea2a8ddb523fec636')
        self.assertTrue(evaluate(suite, bundle['run'])['passed'])

    def test_original_has_independent_literal_identity_hashes_and_complete_checks(self):
        bundle = self.bundle()
        result = bundle['run']['results'][0]
        self.assertEqual(result['final']['episode_identity'], {
            'id': 'bfaceb85-d727-4de5-b37c-e31fdf90fb39', 'task_id': '46', 'trial': 0, 'seed': 626729})
        self.assertEqual(result['final']['arguments_sha256'],
                         '24d1a8fa3001b98b41a65d7a4e6ee4a620d3581a3243522316f08c5cc05bebbf')
        report = evaluate(self.suite, bundle['run'])
        self.assertEqual(report['summary'], {'total': 1, 'passed': 1, 'failed': 0, 'missing': 0})
        self.assertEqual(report['suite_sha256'], 'bc70278cc01aba3bbd41ff05349e76e60b4c274307be536cfbad4b9e9a4e60be')
        self.assertEqual([check['code'] for check in report['results'][0]['checks']],
                         ['result.present', 'result.completed', 'final[0]', 'final[1]',
                          'final[2]', 'final[3]', 'tools.count', 'tools.step[0]'])
        for kind, document in [('input', self.row), ('suite', self.suite), ('run', bundle['run'])]:
            raw = json.dumps(document, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
            self.assertEqual(bundle['provenance'][kind + '_sha256'], hashlib.sha256(raw).hexdigest())

    def test_valid_argument_output_and_final_text_drift_exports_then_fails(self):
        from experiments.tau2_fidelity import analyze_episode
        cases = []
        row = copy.deepcopy(self.row)
        row['messages'][2]['tool_calls'][0]['arguments']['summary'] += ' altered'
        cases.append((row, ['final[3]', 'tools.step[0]']))
        row = copy.deepcopy(self.row)
        row['messages'][3]['content'] += '\n'
        cases.append((row, ['tools.step[0]']))
        row = copy.deepcopy(self.row)
        row['messages'][4]['content'] += ' '
        cases.append((row, ['final[0]']))
        row = copy.deepcopy(self.row)
        row['messages'][2]['tool_calls'][0]['name'] = 'different_tool'
        cases.append((row, ['tools.step[0]']))
        row = copy.deepcopy(self.row)
        row['messages'][3]['error'] = True
        cases.append((row, ['tools.step[0]']))
        for row, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.failures(row), expected)
                report = analyze_episode(row)
                self.assertFalse(report['fidelity_passed'])
                self.assertFalse(report['matches_reviewed_fixture'])
                self.assertTrue(report['legacy_jsonl_report_parity'])
        self.assertEqual(self.bundle(cases[-1][0])['run']['results'][0]['tool_calls'][0]['outcome'], 'error')

    def test_native_identity_changes_are_visible_without_normalization(self):
        row = copy.deepcopy(self.row)
        row['messages'][2]['tool_calls'][0]['id'] = row['messages'][3]['id'] = 'renamed-call'
        self.assertEqual(self.failures(row), ['final[2]'])
        for field, value in [('id', 'different-episode'), ('task_id', '47'), ('trial', 1), ('seed', 626730)]:
            row = copy.deepcopy(self.row)
            row[field] = value
            self.assertEqual(self.failures(row), ['final[1]'])

    def test_numeric_argument_hash_closes_legacy_numeric_equality_gap(self):
        row = copy.deepcopy(self.row)
        row['messages'][2]['tool_calls'][0]['arguments'] = {'n': 1}
        suite = copy.deepcopy(self.suite)
        suite['tasks'][0]['expect']['tools']['steps'][0]['arguments'][0]['equals'] = {'n': 1}
        expected_hash = hashlib.sha256(b'{"n":1}').hexdigest()
        suite['tasks'][0]['expect']['final'][3]['equals'] = expected_hash
        self.assertTrue(evaluate(suite, self.bundle(row, suite)['run'])['passed'])
        row['messages'][2]['tool_calls'][0]['arguments']['n'] = 1.0
        bundle = self.bundle(row, suite)
        self.assertEqual(bundle['run']['results'][0]['final']['arguments_sha256'],
                         hashlib.sha256(b'{"n":1.0}').hexdigest())
        checks = evaluate(suite, bundle['run'])['results'][0]['checks']
        self.assertEqual([check['code'] for check in checks if not check['passed']], ['final[3]'])

    def test_usage_and_unscored_dialogue_have_explicit_losses(self):
        baseline = self.bundle()
        self.assertEqual(baseline['provenance']['usage'], {
            'assistant_observed_turns': [2, 4], 'assistant_unreported_turns': [0], 'emitted': False})
        row = copy.deepcopy(self.row)
        row['messages'][0]['content'] = 'Different greeting'
        row['messages'][1]['content'] = 'Different context'
        row['messages'][0]['usage'] = {'prompt_tokens': 0, 'completion_tokens': 0}
        changed = self.bundle(row)
        self.assertEqual(changed['run'], baseline['run'])
        self.assertNotEqual(changed['provenance']['input_sha256'], baseline['provenance']['input_sha256'])
        paths = {item['path'] for item in changed['losses']}
        self.assertIn('/messages/0/content', paths)
        self.assertIn('/messages/1/content', paths)
        self.assertTrue(all(f'/messages/{index}/usage' in paths for index in (0, 1, 2, 4, 5)))
        self.assertNotIn('usage', changed['run']['results'][0])
        self.assertEqual(changed['provenance']['usage']['assistant_unreported_turns'], [])

    def test_source_order_nulls_pairing_and_terminals_are_strict(self):
        rows = []
        row = copy.deepcopy(self.row)
        row['messages'][0], row['messages'][1] = row['messages'][1], row['messages'][0]
        rows.append(row)
        for index in range(6):
            row = copy.deepcopy(self.row)
            row['messages'].pop(index)
            rows.append(row)
        row = copy.deepcopy(self.row)
        row['messages'].append(copy.deepcopy(row['messages'][-1]))
        rows.append(row)
        row = copy.deepcopy(self.row)
        row['messages'][2]['tool_calls'].append(copy.deepcopy(row['messages'][2]['tool_calls'][0]))
        rows.append(row)
        for index, field, value in [(3, 'id', 'orphan'), (2, 'content', 'mixed'), (1, 'tool_calls', []),
                                    (5, 'content', '###STOP###'), (4, 'content', '###TRANSFER###'),
                                    (0, 'turn_idx', False), (3, 'requestor', 'user')]:
            row = copy.deepcopy(self.row)
            row['messages'][index][field] = value
            rows.append(row)
        row = copy.deepcopy(self.row)
        row['termination_reason'] = 'max_steps'
        rows.append(row)
        for row in rows:
            with self.assertRaises(ContractError):
                self.bundle(row)

    def test_missing_or_nonboolean_native_outcomes_never_guess_success(self):
        for value in (None, 0, 1, 'false', [], {}):
            row = copy.deepcopy(self.row)
            row['messages'][3]['error'] = value
            with self.assertRaisesRegex(ContractError, 'boolean'):
                self.bundle(row)
        row = copy.deepcopy(self.row)
        del row['messages'][3]['error']
        with self.assertRaises(ContractError):
            self.bundle(row)

    def test_minimized_profile_rejects_metadata_and_invalid_json_types(self):
        rows = []
        for field, value in [('trial', False), ('seed', 0.0), ('seed', -1), ('trial', 2**53),
                             ('id', ' padded '), ('task_id', 46), ('reward_info', {}), ('raw_data', {})]:
            row = copy.deepcopy(self.row)
            row[field] = value
            rows.append(row)
        for index, field, value in [(3, 'content', {'not': 'saved text'}), (2, 'usage', {'prompt_tokens': True, 'completion_tokens': 0}),
                                    (4, 'content', '\ud800'), (0, 'private-sentinel', 'private-sentinel'),
                                    (2, 'raw_data', {})]:
            row = copy.deepcopy(self.row)
            row['messages'][index][field] = value
            rows.append(row)
        for arguments in ([1], {'n': float('inf')}, {1: 'nonstring key'}, {'n': (1, 2)}, {'n': '\ud800'}):
            row = copy.deepcopy(self.row)
            row['messages'][2]['tool_calls'][0]['arguments'] = arguments
            rows.append(row)
        for row in rows:
            with self.assertRaises(ContractError) as caught:
                self.bundle(row)
            self.assertNotIn('private-sentinel', str(caught.exception))

    def test_export_does_not_mutate_inputs_or_reuse_mutable_arguments(self):
        original = copy.deepcopy(self.row)
        suite = copy.deepcopy(self.suite)
        bundle = self.bundle()
        bundle['run']['results'][0]['tool_calls'][0]['arguments']['summary'] = 'changed'
        self.assertEqual(self.row, original)
        self.assertEqual(self.suite, suite)

    def test_deep_argument_conversion_failure_is_a_content_free_contract_error(self):
        row = copy.deepcopy(self.row)
        arguments = {'private-sentinel': 1}
        for _ in range(500):
            arguments = {'x': arguments}
        row['messages'][2]['tool_calls'][0]['arguments'] = arguments
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'deep.json'
            path.write_text(json.dumps(row), encoding='utf-8')
            loaded = self.load(path)
            with self.assertRaisesRegex(ContractError, 'conversion limits') as caught:
                self.bundle(loaded)
            self.assertNotIn('private-sentinel', str(caught.exception))
            result = subprocess.run([sys.executable, '-m', 'experiments.tau2_fidelity', '--episode', str(path)],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
            self.assertNotIn('Traceback', result.stderr)
            self.assertNotIn('private-sentinel', result.stderr)

    def test_bounded_loader_strict_json_paths_and_canonical_reordering(self):
        raw = json.dumps(self.row, ensure_ascii=True).encode('utf-8')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'episode.json'
            path.write_bytes(raw)
            self.assertEqual(self.load(path, max_bytes=len(raw)), self.row)
            with self.assertRaises(ContractError):
                self.load(path, max_bytes=len(raw) - 1)
            for value in (0, True, -1, 262145, 10**100):
                with self.assertRaises(ContractError):
                    self.load(path, max_bytes=value)
            for bad in (b'{', b'\xff', b'{"id":1,"id":2}', b'{"n":NaN}', b'[]', b' ' * 262145):
                path.write_bytes(bad)
                with self.assertRaises(ContractError):
                    self.load(path)
            path.write_bytes(json.dumps(dict(reversed(list(self.row.items()))), ensure_ascii=False, indent=2).replace('\n', '\r\n').encode('utf-8'))
            self.assertEqual(self.bundle(self.load(path)), self.bundle())
            for missing in (Path(directory) / 'private-sentinel', 'private-sentinel\0'):
                with self.assertRaises(ContractError) as caught:
                    self.load(missing)
                self.assertNotIn('private-sentinel', str(caught.exception))

    def test_legacy_jsonl_missing_evidence_and_record_boundary_parity(self):
        from experiments.tau2_fidelity import _write_jsonl
        with tempfile.TemporaryDirectory() as directory:
            suite_path, run_path = Path(directory) / 'suite.jsonl', Path(directory) / 'run.jsonl'
            run = self.bundle()['run']
            run['results'] = []
            _write_jsonl(suite_path, self.suite, 'suite')
            _write_jsonl(run_path, run, 'run')
            limit = max(map(len, suite_path.read_bytes().splitlines(keepends=True)))
            events = list(evaluate_jsonl(suite_path, run_path, max_record_bytes=limit))
            self.assertTrue(events[-1]['complete'])
            self.assertEqual(events[-1]['summary'], evaluate(self.suite, run)['summary'])
            self.assertEqual(events[-1]['summary'], {'total': 1, 'passed': 0, 'failed': 1, 'missing': 1})
            self.assertEqual(events[1]['result'], evaluate(self.suite, run)['results'][0])
            with self.assertRaises(ContractError):
                next(evaluate_jsonl(suite_path, run_path, max_record_bytes=limit - 1))
            run_path.write_bytes(run_path.read_bytes().rsplit(b'\n', 2)[0] + b'\n')
            with self.assertRaises(ContractError):
                next(evaluate_jsonl(suite_path, run_path))

    def test_cli_original_valid_drift_and_malformed_input_exit_semantics(self):
        command = [sys.executable, '-m', 'experiments.tau2_fidelity']
        passed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        report = json.loads(passed.stdout)
        self.assertTrue(report['fidelity_passed'])
        self.assertTrue(report['matches_reviewed_fixture'])
        self.assertTrue(report['legacy_jsonl_report_parity'])
        self.assertFalse(report['task_success_evaluated'])
        self.assertFalse(report['usage_reported'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            row = copy.deepcopy(self.row)
            row['messages'][4]['content'] += ' drift'
            path.write_text(json.dumps(row), encoding='utf-8')
            drift = subprocess.run(command + ['--episode', str(path)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(drift.returncode, 1, drift.stderr)
            self.assertFalse(json.loads(drift.stdout)['fidelity_passed'])
            row['messages'][3]['error'] = 'private-sentinel'
            path.write_text(json.dumps(row), encoding='utf-8')
            invalid = subprocess.run(command + ['--episode', str(path)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(invalid.stdout, '')
            self.assertNotIn('private-sentinel', invalid.stderr)
            self.assertNotIn('Traceback', invalid.stderr)

    def test_frozen_reference_suite_cannot_be_regenerated_by_candidate(self):
        from experiments import tau2_fidelity
        from unittest.mock import patch
        suite = copy.deepcopy(self.suite)
        suite['tasks'][0]['expect']['final'][0]['equals'] = 'changed expectation'
        with patch.object(tau2_fidelity, 'load_document', return_value=suite):
            with self.assertRaisesRegex(ContractError, 'reference suite changed'):
                tau2_fidelity.analyze_episode(self.row)


if __name__ == '__main__':
    unittest.main()
