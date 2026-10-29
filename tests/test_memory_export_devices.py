"""Portable export names must reject device aliases without opening devices."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.memory_horizon import export, run_experiment
from weasley_agent_bench import ContractError, load_document, verify_provenance


class MemoryExportDeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report, cls.suite, cls.runs, cls.episodes = run_experiment()

    def assert_rejected_without_io(self, name, revision=None):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'output'
            # A failing baseline must never open a real Windows device.
            with patch.object(Path, 'exists', side_effect=AssertionError('unexpected preflight I/O')), \
                    patch.object(Path, 'mkdir', side_effect=AssertionError('unexpected mkdir')), \
                    patch('experiments.memory_horizon.create_provenance',
                          side_effect=AssertionError('unexpected provenance construction')):
                with self.assertRaisesRegex(ContractError, 'reserved.*device'):
                    export(directory, self.report, self.suite,
                           {name: self.runs['latest-record']}, self.episodes,
                           runner_revision=revision)
            self.assertFalse(directory.exists())

    def test_device_families_fail_before_filesystem_io(self):
        for name in ('CON', 'PRN', 'AUX', 'NUL',
                     *(f'COM{i}' for i in range(1, 10)),
                     *(f'LPT{i}' for i in range(1, 10))):
            with self.subTest(name=name):
                self.assert_rejected_without_io(name)

    def test_case_and_extensions_still_name_devices(self):
        for name in ('nul', 'cOn', 'Aux.v2', 'NUL.tar.gz', 'com1.baseline',
                     'LpT9.provenance', 'PRN.', 'con..version'):
            for revision in (None, 'synthetic-r1'):
                with self.subTest(name=name, revision=revision):
                    self.assert_rejected_without_io(name, revision)

    def test_invalid_device_later_in_mapping_prevents_all_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'output'
            with patch.object(Path, 'exists', side_effect=AssertionError('unexpected I/O')):
                with self.assertRaisesRegex(ContractError, 'reserved.*device'):
                    export(directory, self.report, self.suite,
                           {'valid': self.runs['latest-record'], 'NUL': self.runs['latest-record']},
                           self.episodes)
            self.assertFalse(directory.exists())

    def test_valid_near_matches_roundtrip_on_every_platform(self):
        names = ('console', 'null', 'auxiliary', 'COM10', 'LPT10', 'COM0',
                 'NUL-safe', 'safe.NUL', 'model.v2_candidate-1')
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'output'
            export(directory, self.report, self.suite,
                   {name: self.runs['latest-record'] for name in names}, self.episodes,
                   runner_revision='synthetic-r1')
            for name in names:
                run = load_document(directory / f'{name}.json')
                self.assertEqual(run, self.runs['latest-record'])
                self.assertTrue(verify_provenance(self.suite, run,
                    load_document(directory / f'{name}.provenance.json'))['consistent'])
            self.assertEqual(len(list(directory.iterdir())), 3 + 2 * len(names))
