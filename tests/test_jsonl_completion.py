"""Synthetic lifecycle faults; never providers or performance measurements."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from weasley_agent_bench import (
    ContractError, evaluate_jsonl, compare_jsonl, compare_repeated_jsonl, load_document,
)
from weasley_agent_bench import cli
from test_jsonl import records, write_records
from test_jsonl_repeated import suite_records, batch_records

ROOT = Path(__file__).resolve().parents[1]


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        for name in ("suite", "baseline", "candidate"):
            doc = load_document(ROOT / "examples" / f"{name}.json")
            write_records(self.root / name, records(doc, "suite" if name == "suite" else "run"))
            repeated = load_document(ROOT / "examples/repeated" / f"{name}.json")
            write_records(self.root / f"repeated-{name}", suite_records(repeated) if name == "suite" else batch_records(repeated))

    def cases(self):
        for command, function, names in (
            ("evaluate-jsonl", evaluate_jsonl, ["suite", "candidate"]),
            ("compare-jsonl", compare_jsonl, ["suite", "baseline", "candidate"]),
            ("compare-repeated-jsonl", compare_repeated_jsonl,
             ["repeated-suite", "repeated-baseline", "repeated-candidate"]),
        ):
            paths = [self.root / name for name in names]
            options = {"temporary_directory": self.scratch}
            argv = [command, *map(str, paths), "--temporary-directory", str(self.scratch)]
            if command == "compare-repeated-jsonl":
                options["resamples"] = 100
                argv += ["--resamples", "100"]
            yield command, function, paths, options, argv

    def test_terminal_consumers_have_no_remaining_scratch_or_open_connection(self):
        connect = sqlite3.connect
        connections = []
        def tracking(*args, **kwargs):
            db = connect(*args, **kwargs)
            connections.append(db)
            return db
        with patch("weasley_agent_bench.jsonl.sqlite3.connect", side_effect=tracking):
            for name, function, paths, options, _ in self.cases():
                with self.subTest(name=name):
                    iterator = function(*paths, **options)
                    self.addCleanup(iterator.close)
                    for event in iterator:
                        if event.get("complete"):
                            break
                    self.assertTrue(event["complete"])
                    self.assertEqual(list(self.scratch.iterdir()), [])
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connections[-1].execute("SELECT 1")
                    # No extra next()/close()/GC is necessary for resource release.
                    self.assertEqual(list(iterator), [])

    def test_early_close_releases_real_scratch_without_completion(self):
        for name, function, paths, options, _ in self.cases():
            with self.subTest(name=name):
                iterator = function(*paths, **options)
                self.assertNotIn("complete", next(iterator))
                self.assertTrue(list(self.scratch.iterdir()))
                iterator.close()
                self.assertEqual(list(self.scratch.iterdir()), [])
                self.assertEqual(list(iterator), [])

    def failure_patch(self, boundary):
        if boundary == "database":
            connect = sqlite3.connect
            class FailingConnection(sqlite3.Connection):
                def close(self):
                    super().close()
                    raise sqlite3.OperationalError("synthetic close failure")
            return patch("weasley_agent_bench.jsonl.sqlite3.connect",
                         side_effect=lambda *args, **kwargs: connect(*args, **kwargs, factory=FailingConnection))
        directory = tempfile.TemporaryDirectory
        class FailingDirectory(directory):
            def __exit__(self, *args):
                super().__exit__(*args)
                raise OSError("synthetic removal failure")
        return patch("weasley_agent_bench.jsonl.tempfile.TemporaryDirectory", FailingDirectory)

    def test_cleanup_failures_never_yield_complete(self):
        for boundary in ("database", "directory"):
            for name, function, paths, options, _ in self.cases():
                with self.subTest(boundary=boundary, name=name), self.failure_patch(boundary):
                    observed = []
                    with self.assertRaisesRegex(ContractError, "temporary storage failure"):
                        for event in function(*paths, **options):
                            observed.append(event)
                    self.assertTrue(observed)
                    self.assertFalse(any(row.get("complete") for row in observed))
                    self.assertEqual(list(self.scratch.iterdir()), [])

    def test_removal_failure_with_residual_scratch_does_not_claim_completion(self):
        directory = tempfile.TemporaryDirectory
        created = []
        class RefusedRemoval(directory):
            def __exit__(self, *args):
                created.append(self)
                raise PermissionError("synthetic refusal before removal")
        for name, function, paths, options, _ in self.cases():
            with self.subTest(name=name):
                observed = []
                try:
                    with patch("weasley_agent_bench.jsonl.tempfile.TemporaryDirectory", RefusedRemoval):
                        with self.assertRaisesRegex(ContractError, "temporary storage failure"):
                            observed.extend(function(*paths, **options))
                    self.assertFalse(any(row.get("complete") for row in observed))
                    self.assertTrue(list(self.scratch.iterdir()))
                finally:
                    for resource in created:
                        resource.cleanup()
                    created.clear()
                self.assertEqual(list(self.scratch.iterdir()), [])

    def test_cli_cleanup_failure_returns_error_without_terminal(self):
        for boundary in ("database", "directory"):
            for name, _, _, _, argv in self.cases():
                with self.subTest(boundary=boundary, name=name):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with self.failure_patch(boundary), redirect_stdout(stdout), redirect_stderr(stderr):
                        code = cli.main(argv)
                    self.assertEqual(code, 2)
                    self.assertIn("temporary storage failure", json.loads(stderr.getvalue())["error"])
                    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
                    self.assertFalse(any(row.get("complete") for row in events))

    def test_early_close_preserves_cleanup_exception(self):
        for name, function, paths, options, _ in self.cases():
            with self.subTest(name=name), self.failure_patch("database"):
                iterator = function(*paths, **options)
                next(iterator)
                with self.assertRaisesRegex(ContractError, "temporary storage failure"):
                    iterator.close()
                self.assertEqual(list(self.scratch.iterdir()), [])

    def test_cli_output_failure_closes_even_a_retained_iterator(self):
        class RejectOutput(io.StringIO):
            def write(self, value):
                raise OSError("synthetic output failure")
        for name, function, paths, options, argv in self.cases():
            with self.subTest(name=name):
                iterator = function(*paths, **options)
                self.addCleanup(iterator.close)
                # Keep our own reference: cleanup must not depend on refcount/GC.
                with patch.object(cli, function.__name__, return_value=iterator), redirect_stdout(RejectOutput()):
                    with self.assertRaisesRegex(OSError, "synthetic output failure"):
                        cli.main(argv)
                self.assertEqual(list(self.scratch.iterdir()), [])
                self.assertEqual(list(iterator), [])

    def test_successful_cli_streams_match_library_and_exit_policy(self):
        for name, function, paths, options, argv in self.cases():
            with self.subTest(name=name):
                expected = list(function(*paths, **options))
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = cli.main(argv)
                self.assertEqual([json.loads(line) for line in stdout.getvalue().splitlines()], expected)
                terminal = expected[-1]
                self.assertEqual(code, 0 if terminal.get("passed", terminal.get("no_regressions", True)) else 1)
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(list(self.scratch.iterdir()), [])
