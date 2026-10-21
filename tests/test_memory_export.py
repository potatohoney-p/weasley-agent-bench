"""Synthetic benchmark exports must not overwrite competing saved evidence."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import copy
import io
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from experiments.memory_horizon import export, main, run_experiment
from weasley_agent_bench import evaluate, load_document, verify_provenance


class MemoryExportTests(unittest.TestCase):
    def test_artifact_created_after_preflight_is_preserved(self):
        artifacts = run_experiment()
        sentinel = b'{"synthetic_competing_evidence": true}\n'
        for name, revision in [('report.json', None), ('latest-record.json', None),
                               ('report.json', 'synthetic-r1'),
                               ('latest-record.provenance.json', 'synthetic-r1')]:
            with self.subTest(name=name, revision=revision), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / 'export'
                target = directory / name
                original_mkdir = Path.mkdir

                def competing_create(path, *args, **kwargs):
                    original_mkdir(path, *args, **kwargs)
                    if path == directory:
                        target.write_bytes(sentinel)

                # The external file appears after preflight, before writing begins.
                with patch.object(Path, 'mkdir', new=competing_create):
                    with self.assertRaises(FileExistsError):
                        export(directory, *artifacts, runner_revision=revision)
                self.assertEqual(target.read_bytes(), sentinel)
                if name == 'report.json':
                    self.assertEqual(set(directory.iterdir()), {target})

    def test_concurrent_exports_preserve_one_complete_winner(self):
        artifacts = run_experiment()
        for revision in [None, 'synthetic-concurrent-r1']:
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / 'export'
                gate = Barrier(2)
                original_mkdir = Path.mkdir

                def simultaneous_create(path, *args, **kwargs):
                    original_mkdir(path, *args, **kwargs)
                    if path == directory:
                        # Both public calls passed preflight before either writes.
                        gate.wait(timeout=10)

                def attempt(index):
                    report, suite, runs, episodes = copy.deepcopy(artifacts)
                    suite['suite_id'] = f'synthetic-contender-{index}'
                    for run in runs.values():
                        run['suite_id'] = suite['suite_id']
                    report['suite_sha256'] = evaluate(suite, runs['latest-record'])['suite_sha256']
                    try:
                        export(directory, report, suite, runs, episodes, runner_revision=revision)
                        return 'success', suite['suite_id']
                    except FileExistsError:
                        return 'collision', suite['suite_id']

                with patch.object(Path, 'mkdir', new=simultaneous_create), ThreadPoolExecutor(max_workers=2) as pool:
                    outcomes = list(pool.map(attempt, [1, 2]))
                self.assertCountEqual([status for status, _ in outcomes], ['success', 'collision'])
                winner = next(suite_id for status, suite_id in outcomes if status == 'success')
                suite = load_document(directory / 'suite.json')
                self.assertEqual(suite['suite_id'], winner)
                for name in artifacts[2]:
                    run = load_document(directory / f'{name}.json')
                    self.assertEqual(run['suite_id'], winner)
                    if revision is not None:
                        sidecar = load_document(directory / f'{name}.provenance.json')
                        self.assertTrue(verify_provenance(suite, run, sidecar,
                                                       expected_runner_revision=revision)['consistent'])
                score = evaluate(suite, load_document(directory / 'latest-record.json'))
                self.assertTrue(score['passed'])
                self.assertEqual(load_document(directory / 'report.json')['suite_sha256'], score['suite_sha256'])

    def test_cli_late_collision_exits_two_without_success_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'export'
            target = directory / 'report.json'
            original_mkdir = Path.mkdir

            def competing_create(path, *args, **kwargs):
                original_mkdir(path, *args, **kwargs)
                if path == directory:
                    target.write_text('synthetic competing evidence', encoding='utf8')

            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(Path, 'mkdir', new=competing_create), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as caught:
                    main(['--output-dir', str(directory), '--runner-revision', 'synthetic-r1'])
            self.assertEqual(caught.exception.code, 2)
            self.assertEqual(stdout.getvalue(), '')
            self.assertIn('error:', stderr.getvalue())
            self.assertNotIn('Traceback', stderr.getvalue())
            self.assertEqual(target.read_text(encoding='utf8'), 'synthetic competing evidence')
