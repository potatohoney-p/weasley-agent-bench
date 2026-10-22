from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from weasley_agent_bench import ContractError, load_document, verify_provenance

ROOT = Path(__file__).resolve().parents[1]


def oracle(suite, run):
    # Independent whole-document encoder: no production digest/JSONL helpers.
    def digest(document):
        encoded = json.dumps(document, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    return {"schema_version": 1, "suite_id": suite["suite_id"], "run_id": run["run_id"],
            "suite_sha256": digest(suite), "run_sha256": digest(run),
            "runner_revision": "synthetic-runner-r1"}


def write_jsonl(path, document, kind):
    field = "tasks" if kind == "suite" else "results"
    header = {key: value for key, value in document.items() if key != field}
    header.update(format=f"weasley-agent-bench/{kind}-jsonl", format_version=1)
    records = [header, *document[field], {"end": True, "count": len(document[field])}]
    path.write_bytes(b"".join((json.dumps(record) + "\n").encode("utf-8") for record in records))


class JsonlProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.suite_path = self.root / "suite.jsonl"
        self.run_path = self.root / "run.jsonl"
        self.sidecar_path = self.root / "provenance.json"
        self.suite = load_document(ROOT / "examples/suite.json")
        self.run = load_document(ROOT / "examples/candidate.json")
        self.sidecar = oracle(self.suite, self.run)
        self.write_inputs()

    def write_inputs(self):
        write_jsonl(self.suite_path, self.suite, "suite")
        write_jsonl(self.run_path, self.run, "run")
        self.sidecar_path.write_text(json.dumps(self.sidecar, indent=2), encoding="utf-8")

    def cli(self, *options):
        return subprocess.run(
            [sys.executable, "-m", "weasley_agent_bench", "verify-provenance-jsonl",
             str(self.suite_path), str(self.run_path), str(self.sidecar_path), *options],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def verify(self, **options):
        from weasley_agent_bench import verify_provenance_jsonl
        return verify_provenance_jsonl(self.suite_path, self.run_path, self.sidecar_path, **options)

    def test_cli_matches_independent_oracle_and_legacy_report(self):
        result = self.cli("--expected-runner-revision", "synthetic-runner-r1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout),
                         verify_provenance(self.suite, self.run, self.sidecar,
                                           expected_runner_revision="synthetic-runner-r1"))

    def test_original_result_order_and_empty_partial_failed_runs_match_legacy(self):
        for size in (4, 2, 0):
            with self.subTest(size=size):
                self.run = load_document(ROOT / "examples/baseline.json")
                self.run["results"] = self.run["results"][::-1][:size]
                self.sidecar = oracle(self.suite, self.run)
                self.write_inputs()
                self.assertEqual(self.verify(), verify_provenance(self.suite, self.run, self.sidecar))

    def test_same_ids_do_not_hide_mutated_suite_or_run(self):
        for document, field, value, error_field in (
            ("suite", "prompt", "different historical prompt", "suite_sha256"),
            ("suite", "tags", ["different-contract"], "suite_sha256"),
            ("suite", "budget", {"max_tool_calls": 999}, "suite_sha256"),
            ("run", "final", "private-payload-sentinel", "run_sha256"),
            ("run", "usage", {"input_tokens": 1}, "run_sha256"),
            ("run", "status", "cancelled", "run_sha256"),
        ):
            with self.subTest(document=document, field=field):
                suite, run = copy.deepcopy(self.suite), copy.deepcopy(self.run)
                target = suite["tasks"][0] if document == "suite" else run["results"][0]
                target[field] = value
                write_jsonl(self.suite_path, suite, "suite")
                write_jsonl(self.run_path, run, "run")
                result = self.cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(f"provenance.{error_field}", json.loads(result.stderr)["error"])
                self.assertNotIn("private-payload-sentinel", result.stderr)

    def test_record_order_and_numeric_representation_are_bound(self):
        self.run["results"][0]["final"] = 1
        self.sidecar = oracle(self.suite, self.run)
        self.write_inputs()
        self.assertTrue(self.verify()["consistent"])
        self.run["results"][0]["final"] = 1.0
        write_jsonl(self.run_path, self.run, "run")
        with self.assertRaisesRegex(ContractError, "run_sha256"):
            self.verify()
        self.run["results"][0]["final"] = 1
        self.run["results"].reverse()
        write_jsonl(self.run_path, self.run, "run")
        with self.assertRaisesRegex(ContractError, "run_sha256"):
            self.verify()
        self.write_inputs()
        self.suite["tasks"].reverse()
        write_jsonl(self.suite_path, self.suite, "suite")
        with self.assertRaisesRegex(ContractError, "suite_sha256"):
            self.verify()

    def test_formatting_key_order_escaping_and_line_endings_are_not_bound(self):
        for path in (self.suite_path, self.run_path):
            records = [json.loads(line) for line in path.read_bytes().splitlines()]
            path.write_bytes(b"\r\n".join(
                json.dumps(dict(reversed(list(record.items()))), ensure_ascii=False,
                           separators=(", ", ": ")).encode("utf-8") for record in records))
        self.assertEqual(self.verify(), verify_provenance(self.suite, self.run, self.sidecar))

    def test_unicode_ids_surrogates_nulls_and_nested_values_match_oracle(self):
        self.suite["suite_id"] = self.run["suite_id"] = "synthetic-서울-\ud800"
        self.run["run_id"] = "run-\x00-é"
        self.suite["tasks"][0]["id"] = self.run["results"][0]["task_id"] = "task-\ud800"
        self.run["results"][0]["final"] = {"é": [True, None, 1.0, -0.0, {"x": "e\u0301"}]}
        self.sidecar = oracle(self.suite, self.run)
        self.write_inputs()
        self.assertTrue(self.verify()["consistent"])
        self.run["results"][0]["final"]["é"][-1]["x"] = "é"
        write_jsonl(self.run_path, self.run, "run")
        with self.assertRaisesRegex(ContractError, "run_sha256"):
            self.verify()

    def test_sidecar_requires_both_digests_identity_and_revision(self):
        for field, value in (("suite_sha256", "0" * 64), ("run_sha256", "0" * 64),
                             ("suite_id", "wrong"), ("run_id", "wrong"),
                             ("suite_sha256", "A" * 64), ("run_sha256", ""),
                             ("schema_version", True), ("runner_revision", "")):
            with self.subTest(field=field, value=value):
                sidecar = {**self.sidecar, field: value}
                self.sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
                with self.assertRaisesRegex(ContractError, f"provenance.{field}"):
                    self.verify()
        for field in self.sidecar:
            sidecar = dict(self.sidecar)
            del sidecar[field]
            self.sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
            with self.assertRaises(ContractError):
                self.verify()
        self.write_inputs()
        with self.assertRaisesRegex(ContractError, "expected runner revision"):
            self.verify(expected_runner_revision="different")
        with self.assertRaisesRegex(ContractError, "expected_runner_revision"):
            self.verify(expected_runner_revision="")

    def test_sidecar_paths_urls_and_report_fields_are_rejected_without_following(self):
        for field, value in (("path", "../../private-payload-sentinel"),
                             ("suite_path", "C:\\private-payload-sentinel"),
                             ("url", "https://invalid.example/private-payload-sentinel"),
                             ("path", "bad\x00path"), ("consistent", True)):
            self.sidecar_path.write_text(json.dumps({**self.sidecar, field: value}), encoding="utf-8")
            original_open = Path.open
            opened = []

            def observe(path, *args, **kwargs):
                opened.append(path)
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", observe):
                with self.assertRaisesRegex(ContractError, "unknown fields") as error:
                    self.verify()
            self.assertEqual(opened, [self.sidecar_path])
            self.assertNotIn("private-payload-sentinel", str(error.exception))

    def test_strict_sidecar_json_before_success(self):
        original = self.sidecar_path.read_bytes()
        cases = (original[:-1], original + b"{}", b"[]", b"null", b'{"x":NaN}',
                 b"\xff", b'{' + b'"schema_version":1,' + original[1:], b"[" * 2000)
        for raw in cases:
            with self.subTest(raw=raw[:20]):
                self.sidecar_path.write_bytes(raw)
                result = self.cli()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("provenance", json.loads(result.stderr)["error"])

    def test_duplicates_unknown_ids_and_truncation_cannot_verify_even_with_matching_hash(self):
        for kind, document in (("suite", self.suite), ("run", self.run)):
            field = "tasks" if kind == "suite" else "results"
            path = self.suite_path if kind == "suite" else self.run_path
            for defect in ("duplicate", "truncated", "bad-count", "trailing", "bad-json"):
                with self.subTest(kind=kind, defect=defect):
                    self.write_inputs()
                    modified = copy.deepcopy(document)
                    if defect == "duplicate":
                        modified[field].append(copy.deepcopy(modified[field][0]))
                        sidecar = oracle(modified, self.run) if kind == "suite" else oracle(self.suite, modified)
                        self.sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
                    write_jsonl(path, modified, kind)
                    lines = path.read_bytes().splitlines(keepends=True)
                    if defect == "truncated":
                        lines.pop()
                    elif defect == "bad-count":
                        lines[-1] = b'{"end":true,"count":99}\n'
                    elif defect == "trailing":
                        lines.append(b'{}\n')
                    elif defect == "bad-json":
                        lines[-2] = b'{"private-payload-sentinel":NaN}\n'
                    path.write_bytes(b"".join(lines))
                    result = self.cli()
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(f"{kind} JSONL line", json.loads(result.stderr)["error"])
                    self.assertNotIn("private-payload-sentinel", result.stderr)
        self.run["results"][0]["task_id"] = "unknown"
        self.sidecar = oracle(self.suite, self.run)
        self.write_inputs()
        with self.assertRaisesRegex(ContractError, "run JSONL line 2"):
            self.verify()

    def test_missing_and_malformed_explicit_paths_are_content_safe(self):
        from weasley_agent_bench import verify_provenance_jsonl
        paths = [self.suite_path, self.run_path, self.sidecar_path]
        for index in range(3):
            for missing in (self.root / "private-payload-sentinel", "private-payload-sentinel\x00"):
                selected = list(paths)
                selected[index] = missing
                with self.assertRaisesRegex(ContractError, "cannot read input") as error:
                    verify_provenance_jsonl(*selected)
                self.assertNotIn("private-payload-sentinel", str(error.exception))

    def test_sidecar_and_record_bounds_are_exact_and_cannot_be_disabled(self):
        sidecar_size = self.sidecar_path.stat().st_size
        record_size = max(len(line) for path in (self.suite_path, self.run_path)
                          for line in path.read_bytes().splitlines(keepends=True))
        self.assertTrue(self.verify(max_sidecar_bytes=sidecar_size, max_record_bytes=record_size)["consistent"])
        self.assertTrue(self.verify(max_sidecar_bytes=sys.maxsize - 1)["consistent"])
        with self.assertRaisesRegex(ContractError, "exceeds max_sidecar_bytes"):
            self.verify(max_sidecar_bytes=sidecar_size - 1)
        with self.assertRaisesRegex(ContractError, "exceeds max_record_bytes"):
            self.verify(max_record_bytes=record_size - 1)
        for option in ("max_sidecar_bytes", "max_record_bytes"):
            for value in (0, -1, True, 1.5, sys.maxsize, 10**100):
                with self.assertRaisesRegex(ContractError, option):
                    self.verify(**{option: value})
        result = self.cli("--max-sidecar-bytes", str(sidecar_size - 1))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_multichunk_sidecar_is_bounded_by_total_document_bytes(self):
        self.sidecar["runner_revision"] = "synthetic-" + "x" * 70000
        self.write_inputs()
        size = self.sidecar_path.stat().st_size
        self.assertTrue(self.verify(max_sidecar_bytes=size)["consistent"])
        with self.assertRaisesRegex(ContractError, "exceeds max_sidecar_bytes"):
            self.verify(max_sidecar_bytes=size - 1)

    def test_storage_released_after_success_mismatch_and_late_error(self):
        scratch = self.root / "scratch"
        scratch.mkdir()
        self.verify(temporary_directory=scratch)
        self.assertEqual(list(scratch.iterdir()), [])
        self.run["results"][0]["final"] = "changed"
        write_jsonl(self.run_path, self.run, "run")
        with self.assertRaises(ContractError):
            self.verify(temporary_directory=scratch)
        self.assertEqual(list(scratch.iterdir()), [])
        with self.run_path.open("ab") as stream:
            stream.write(b"{}\n")
        with self.assertRaises(ContractError):
            self.verify(temporary_directory=scratch)
        self.assertEqual(list(scratch.iterdir()), [])
        with self.assertRaisesRegex(ContractError, "temporary storage failure"):
            self.verify(temporary_directory=self.root / "absent")


if __name__ == "__main__":
    unittest.main()
