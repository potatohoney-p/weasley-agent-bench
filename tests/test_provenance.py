import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from experiments.memory_horizon import export, run_experiment
from weasley_agent_bench import ContractError, evaluate, load_document
import weasley_agent_bench as bench


ROOT = Path(__file__).resolve().parents[1]


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.suite = load_document(ROOT / "examples/suite.json")
        self.run = load_document(ROOT / "examples/candidate.json")

    def create(self):
        return bench.create_provenance(self.suite, self.run, runner_revision="synthetic-runner-r1")

    def test_canonical_digests_and_creation_are_deterministic_without_mutation(self):
        original = copy.deepcopy((self.suite, self.run))
        provenance = self.create()
        self.assertEqual(provenance, self.create())
        self.assertEqual((self.suite, self.run), original)
        for name, value in (("suite", self.suite), ("run", self.run)):
            canonical = json.dumps(value, sort_keys=True, ensure_ascii=True,
                                   separators=(",", ":"), allow_nan=False)
            self.assertEqual(provenance[f"{name}_sha256"],
                             hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        self.assertEqual(provenance["suite_sha256"], evaluate(self.suite, self.run)["suite_sha256"])

    def test_matching_artifacts_and_explicit_revision_are_consistent(self):
        result = bench.verify_provenance(self.suite, self.run, self.create(),
                                         expected_runner_revision="synthetic-runner-r1")
        self.assertTrue(result["consistent"])
        self.assertEqual(result["runner_revision_check"], "matched")
        self.assertEqual(result["runner_revision"], "synthetic-runner-r1")

    def test_same_id_changed_contract_is_rejected_only_by_opt_in_verification(self):
        provenance = self.create()
        self.suite["tasks"][0]["expect"]["final"][0]["equals"] = "different contract"
        # Rescoring remains intentional and valid, not a provenance assertion.
        self.assertFalse(evaluate(self.suite, self.run)["passed"])
        with self.assertRaisesRegex(ContractError, r"provenance\.suite_sha256"):
            bench.verify_provenance(self.suite, self.run, provenance)

    def test_changed_prompt_is_part_of_suite_binding(self):
        provenance = self.create()
        self.suite["tasks"][0]["prompt"] += " changed prompt"
        self.assertTrue(evaluate(self.suite, self.run)["passed"])
        with self.assertRaisesRegex(ContractError, r"provenance\.suite_sha256"):
            bench.verify_provenance(self.suite, self.run, provenance)

    def test_swapped_run_with_reused_id_cannot_pass_digest_verification(self):
        provenance = self.create()
        swapped = load_document(ROOT / "examples/baseline.json")
        swapped["run_id"] = self.run["run_id"]
        with self.assertRaisesRegex(ContractError, r"provenance\.run_sha256"):
            bench.verify_provenance(self.suite, swapped, provenance)

    def test_object_key_order_is_ignored_but_array_order_is_bound(self):
        provenance = self.create()
        self.suite = dict(reversed(list(self.suite.items())))
        self.run = dict(reversed(list(self.run.items())))
        self.assertTrue(bench.verify_provenance(self.suite, self.run, provenance)["consistent"])
        self.run["results"].reverse()
        self.assertTrue(evaluate(self.suite, self.run)["passed"])
        with self.assertRaisesRegex(ContractError, r"provenance\.run_sha256"):
            bench.verify_provenance(self.suite, self.run, provenance)

    def test_revision_is_declared_unless_an_expected_value_is_supplied(self):
        provenance = self.create()
        result = bench.verify_provenance(self.suite, self.run, provenance)
        self.assertEqual(result["runner_revision_check"], "not_requested")
        with self.assertRaisesRegex(ContractError, r"provenance\.runner_revision"):
            bench.verify_provenance(self.suite, self.run, provenance,
                                    expected_runner_revision="synthetic-runner-r2")
        with self.assertRaises(ContractError):
            bench.verify_provenance(self.suite, self.run, provenance, expected_runner_revision="")

    def test_sidecar_is_versioned_and_strict(self):
        changes = [
            lambda p: p.update(schema_version=True),
            lambda p: p.update(schema_version=2),
            lambda p: p.pop("run_sha256"),
            lambda p: p.update(extra="ignored?"),
            lambda p: p.update(suite_sha256="f" * 63),
            lambda p: p.update(suite_sha256="G" * 64),
            lambda p: p.update(run_sha256="A" * 64),
            lambda p: p.update(runner_revision=" "),
            lambda p: p.update(suite_id="another-suite"),
            lambda p: p.update(run_id="another-run"),
        ]
        for change in changes:
            provenance = self.create()
            change(provenance)
            with self.subTest(provenance=provenance), self.assertRaises(ContractError):
                bench.verify_provenance(self.suite, self.run, provenance)

    def test_partial_failed_runs_can_be_bound_without_claiming_success(self):
        self.run["results"].pop()
        provenance = self.create()
        self.assertTrue(bench.verify_provenance(self.suite, self.run, provenance)["consistent"])
        self.assertFalse(evaluate(self.suite, self.run)["passed"])

    def test_memory_export_sidecars_roundtrip_and_refuse_overwrite(self):
        artifacts = run_experiment()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "export"
            export(directory, *artifacts, runner_revision="synthetic-memory-r1")
            suite = load_document(directory / "suite.json")
            for name in artifacts[2]:
                result = bench.verify_provenance(
                    suite, load_document(directory / f"{name}.json"),
                    load_document(directory / f"{name}.provenance.json"),
                    expected_runner_revision="synthetic-memory-r1")
                self.assertTrue(result["consistent"])
            with self.assertRaises(FileExistsError):
                export(directory, *artifacts, runner_revision="synthetic-memory-r1")

    def test_legacy_export_is_unchanged_and_sidecar_collision_writes_nothing(self):
        artifacts = run_experiment()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "legacy"
            export(directory, *artifacts)
            self.assertEqual({path.name for path in directory.iterdir()}, {
                "suite.json", "report.json", "episodes.json", "session-only.json",
                "rolling-state-4.json", "latest-record.json",
            })
            blocked = Path(temporary) / "blocked"
            blocked.mkdir()
            existing = blocked / "latest-record.provenance.json"
            existing.write_text("keep this artifact", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                export(blocked, *artifacts, runner_revision="synthetic-memory-r1")
            self.assertEqual(list(blocked.iterdir()), [existing])
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep this artifact")

    def test_generated_sidecar_name_cannot_silently_replace_a_run(self):
        report, suite, runs, episodes = run_experiment()
        runs["session-only.provenance"] = runs.pop("latest-record")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "export"
            with self.assertRaisesRegex(ContractError, "filenames.*unique"):
                export(directory, report, suite, runs, episodes, runner_revision="r1")
            self.assertFalse(directory.exists())

    def test_memory_cli_revision_requires_export_and_invalid_revision_writes_nothing(self):
        def cli(*args):
            return subprocess.run([sys.executable, "-m", "experiments.memory_horizon", *map(str, args)],
                                  cwd=ROOT, capture_output=True, text=True)

        missing_output = cli("--runner-revision", "r1")
        self.assertEqual(missing_output.returncode, 2)
        self.assertIn("requires --output-dir", missing_output.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "invalid"
            invalid = cli("--output-dir", directory, "--runner-revision", " ")
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("runner_revision", invalid.stderr)
            self.assertFalse(directory.exists())
            valid = cli("--output-dir", directory, "--runner-revision", "r1")
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertEqual([row["correct"] for row in json.loads(valid.stdout)["baselines"]], [19, 24, 48])
            self.assertEqual(len(list(directory.glob("*.provenance.json"))), 3)

    def test_cli_creation_verification_and_mismatch_exit_codes(self):
        def cli(*args):
            return subprocess.run([sys.executable, "-m", "weasley_agent_bench", *map(str, args)],
                                  cwd=ROOT, capture_output=True, text=True)

        suite = ROOT / "examples/suite.json"
        run = ROOT / "examples/candidate.json"
        created = cli("create-provenance", suite, run, "--runner-revision", "runner-r1")
        self.assertEqual(created.returncode, 0, created.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            sidecar = Path(temporary) / "provenance.json"
            sidecar.write_text(created.stdout, encoding="utf-8")
            verified = cli("verify-provenance", suite, run, sidecar,
                           "--expected-runner-revision", "runner-r1")
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertTrue(json.loads(verified.stdout)["consistent"])
            failed = cli("verify-provenance", suite, run, sidecar,
                         "--expected-runner-revision", "runner-r2")
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(failed.stdout, "")
            self.assertIn("runner_revision", json.loads(failed.stderr)["error"])
            failed = cli("verify-provenance", suite, ROOT / "examples/baseline.json", sidecar)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(failed.stdout, "")
            self.assertIn("run_id", json.loads(failed.stderr)["error"])
            changed_suite = Path(temporary) / "changed-suite.json"
            self.suite["tasks"][0]["prompt"] += " different prompt"
            changed_suite.write_text(json.dumps(self.suite), encoding="utf-8")
            failed = cli("verify-provenance", changed_suite, run, sidecar)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(failed.stdout, "")
            self.assertIn("suite_sha256", json.loads(failed.stderr)["error"])
            # Sidecar files use the same strict JSON loader as saved runs.
            sidecar.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
            failed = cli("verify-provenance", suite, run, sidecar)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(failed.stdout, "")
            self.assertIn("duplicate key", json.loads(failed.stderr)["error"])

    def test_unknown_sidecar_field_does_not_echo_pasted_content_in_cli_errors(self):
        provenance = self.create()
        provenance["PRIVATE_SENTINEL_FIELD_CONTENT"] = "PRIVATE_SENTINEL_VALUE_CONTENT"
        with tempfile.TemporaryDirectory() as temporary:
            sidecar = Path(temporary) / "provenance.json"
            sidecar.write_text(json.dumps(provenance), encoding="utf-8")
            result = subprocess.run([
                sys.executable, "-m", "weasley_agent_bench", "verify-provenance",
                str(ROOT / "examples/suite.json"), str(ROOT / "examples/candidate.json"), str(sidecar),
            ], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertEqual(json.loads(result.stderr)["error"], "provenance: unknown fields are not allowed")
            self.assertNotIn("PRIVATE_SENTINEL", result.stderr)


if __name__ == "__main__":
    unittest.main()
