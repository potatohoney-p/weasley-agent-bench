"""Temporary-directory path failures stay inside the existing CLI boundary."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from weasley_agent_bench.cli import main
from weasley_agent_bench.contracts import ContractError
from weasley_agent_bench.jsonl import _storage, evaluate_jsonl, compare_jsonl, verify_provenance_jsonl
from weasley_agent_bench.provenance import create_provenance
from weasley_agent_bench.repeated_jsonl import compare_repeated_jsonl


class StoragePathTests(unittest.TestCase):
    def test_real_nul_path_fails_before_opening_inputs_for_all_iterators(self):
        for function, paths in [(evaluate_jsonl, ('unused', 'unused')),
                                (compare_jsonl, ('unused', 'unused', 'unused')),
                                (compare_repeated_jsonl, ('unused', 'unused', 'unused'))]:
            with self.subTest(function=function.__name__):
                with self.assertRaisesRegex(ContractError, '^JSONL evaluation temporary storage failure$'):
                    next(function(*paths, temporary_directory='PRIVATE-SENTINEL\0'))

    def test_cli_returns_json_error_without_output_for_invalid_storage_path(self):
        for command, paths in [('evaluate-jsonl', ['unused', 'unused']),
                               ('compare-jsonl', ['unused'] * 3),
                               ('compare-repeated-jsonl', ['unused'] * 3)]:
            out, err = io.StringIO(), io.StringIO()
            with self.subTest(command=command), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main([command, *paths, '--temporary-directory', 'PRIVATE-SENTINEL\0'])
                self.assertEqual(code, 2)
                self.assertEqual(out.getvalue(), '')
                self.assertEqual(json.loads(err.getvalue()), {'error': 'JSONL evaluation temporary storage failure'})

    def test_provenance_path_has_same_boundary_after_sidecar_validation(self):
        suite = {'schema_version': 1, 'suite_id': 'synthetic', 'tasks': [
            {'id': 'task', 'prompt': 'synthetic', 'expect': {'final': [{'path': '', 'equals': 1}]}}]}
        run = {'schema_version': 1, 'suite_id': 'synthetic', 'run_id': 'run', 'results': []}
        with tempfile.TemporaryDirectory() as root:
            sidecar = Path(root) / 'sidecar.json'
            sidecar.write_text(json.dumps(create_provenance(suite, run, runner_revision='synthetic')))
            with self.assertRaisesRegex(ContractError, '^JSONL evaluation temporary storage failure$'):
                verify_provenance_jsonl('unused', 'unused', sidecar, temporary_directory='PRIVATE-SENTINEL\0')
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(['verify-provenance-jsonl', 'unused', 'unused', str(sidecar),
                             '--temporary-directory', 'PRIVATE-SENTINEL\0'])
            self.assertEqual(code, 2)
            self.assertEqual(out.getvalue(), '')
            self.assertEqual(json.loads(err.getvalue()), {'error': 'JSONL evaluation temporary storage failure'})

    def test_creation_encoding_failure_is_sanitized(self):
        error = UnicodeEncodeError('utf-8', '\ud800', 0, 1, 'PRIVATE-SENTINEL')
        with patch('weasley_agent_bench.jsonl.tempfile.TemporaryDirectory', side_effect=error):
            with self.assertRaisesRegex(ContractError, '^JSONL evaluation temporary storage failure$'):
                with _storage(100, None):
                    self.fail('creation failed')

    def test_body_value_error_is_not_reclassified_and_storage_is_cleaned(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, '^intentional body error$'):
                with _storage(100, root):
                    raise ValueError('intentional body error')
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_valid_storage_still_closes_and_removes_directory(self):
        with tempfile.TemporaryDirectory() as root:
            with _storage(100, root) as db:
                self.assertEqual(db.execute('SELECT 1').fetchone(), (1,))
                self.assertEqual(len(list(Path(root).iterdir())), 1)
            self.assertEqual(list(Path(root).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
