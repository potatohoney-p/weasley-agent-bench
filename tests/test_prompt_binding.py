"""Exact saved-prompt binding is opt-in consistency, not execution evidence."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from weasley_agent_bench.contracts import ContractError, load_document
from weasley_agent_bench.subagents import export_subagents, load_subagents_transcript

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'examples/subagents'


class PromptBindingTests(unittest.TestCase):
    def setUp(self):
        self.suite = load_document(FIXTURE / 'suite.json')
        self.records = load_subagents_transcript(FIXTURE / 'synthetic.transcript.jsonl')
        self.metadata = load_document(FIXTURE / 'synthetic.metadata.json')

    def export(self, **options):
        return export_subagents(self.suite, self.records, self.metadata,
                                task_id='read-note', run_id='synthetic-export', **options)

    def prompt(self, text):
        self.records[0]['text'] = text
        self.records[0]['message']['content'] = [{'type': 'text', 'text': text}]

    def test_exact_match_preserves_export_bytes(self):
        self.assertEqual(self.export(), self.export(require_prompt_match=True))

    def test_default_expanded_prompt_remains_supported(self):
        original = self.export()
        self.prompt('Expanded context\n' + self.suite['tasks'][0]['prompt'])
        self.assertEqual(original, self.export())
        with self.assertRaisesRegex(ContractError, 'initial prompt does not match'):
            self.export(require_prompt_match=True)

    def test_exact_mode_never_normalizes_whitespace_case_or_unicode(self):
        for expected, actual in [('task', 'task '), ('task', 'Task'),
                                 ('line\nnext', 'line\r\nnext'), ('é', 'e\u0301'),
                                 ('task', 'PRIVATE-SENTINEL')]:
            with self.subTest(expected=expected, actual=actual):
                self.suite['tasks'][0]['prompt'] = expected
                self.prompt(actual)
                with self.assertRaises(ContractError) as caught:
                    self.export(require_prompt_match=True)
                self.assertEqual(str(caught.exception),
                                 'subagents: initial prompt does not match selected suite task')

    def test_selected_task_not_first_task_controls_binding(self):
        other = copy.deepcopy(self.suite['tasks'][0])
        other.update(id='other', prompt='Different prompt')
        self.suite['tasks'].insert(0, other)
        self.assertEqual(self.export(require_prompt_match=True)['results'][0]['task_id'], 'read-note')
        with self.assertRaisesRegex(ContractError, 'initial prompt does not match'):
            export_subagents(self.suite, self.records, self.metadata,
                             task_id='other', run_id='other-run', require_prompt_match=True)

    def test_binding_uses_joined_initial_message_not_metadata(self):
        self.suite['tasks'][0]['prompt'] = 'first\nsecond'
        self.records[0]['text'] = 'first\nsecond'
        self.records[0]['message']['content'] = [
            {'type': 'text', 'text': 'first'}, {'type': 'text', 'text': 'second'}]
        for metadata_task in [None, 'Unexpanded task']:
            self.metadata['task'] = metadata_task
            self.export(require_prompt_match=True)
        self.metadata['task'] = self.suite['tasks'][0]['prompt']
        self.prompt('Different actual initial prompt')
        with self.assertRaises(ContractError):
            self.export(require_prompt_match=True)

    def test_bad_option_and_unknown_task_fail_closed(self):
        for option in [None, 0, 1, 'false', [], {}]:
            with self.subTest(option=option), self.assertRaises(ContractError):
                self.export(require_prompt_match=option)
        for task_id in ['unknown', None, [], {}]:
            with self.subTest(task_id=task_id), self.assertRaises(ContractError):
                export_subagents(self.suite, self.records, self.metadata,
                                 task_id=task_id, run_id='run', require_prompt_match=True)

    def test_cli_matching_and_mismatch_are_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / 'trace.jsonl'
            command = [sys.executable, '-m', 'weasley_agent_bench', 'export-subagents',
                       str(FIXTURE / 'suite.json'), str(transcript),
                       str(FIXTURE / 'synthetic.metadata.json'), '--task-id', 'read-note',
                       '--run-id', 'synthetic-export', '--require-prompt-match']
            for matching in [True, False]:
                if not matching:
                    self.prompt('PRIVATE-SENTINEL')
                transcript.write_text(''.join(json.dumps(r) + '\n' for r in self.records), encoding='utf8')
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0 if matching else 2)
                if matching:
                    self.assertEqual(json.loads(result.stdout), self.export(require_prompt_match=True))
                    self.assertEqual(result.stderr, '')
                else:
                    self.assertEqual(result.stdout, '')
                    self.assertEqual(json.loads(result.stderr), {'error':
                        'subagents: initial prompt does not match selected suite task'})
