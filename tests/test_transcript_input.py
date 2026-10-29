"""Bounded file ingestion errors remain content-safe ContractErrors."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from weasley_agent_bench.cli import main
from weasley_agent_bench.contracts import ContractError
from weasley_agent_bench.subagents import load_subagents_transcript

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'examples/subagents'
READ_ERROR = 'subagents: cannot read UTF-8 transcript'


class TranscriptInputTests(unittest.TestCase):
    def test_invalid_paths_are_content_safe_contract_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in ['PRIVATE-SENTINEL\0trace', '\ud800',
                         str(Path(directory) / 'PRIVATE-SENTINEL-missing'), directory]:
                with self.subTest(path=repr(path)):
                    with self.assertRaises(ContractError) as caught:
                        load_subagents_transcript(path)
                    self.assertEqual(str(caught.exception), READ_ERROR)

    def test_cli_invalid_transcript_path_is_json_error(self):
        # NUL cannot appear in OS argv; this exercises the documented main(argv) API.
        for path in ['PRIVATE-SENTINEL\0trace', '\ud800']:
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(path=repr(path)):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = main(['export-subagents', str(FIXTURE / 'suite.json'), path,
                                   str(FIXTURE / 'synthetic.metadata.json'),
                                   '--task-id', 'read-note', '--run-id', 'test'])
                self.assertEqual(status, 2)
                self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(json.loads(stderr.getvalue()), {'error': READ_ERROR})

    def test_read_failures_do_not_echo_exception_details(self):
        for error in [PermissionError('PRIVATE-SENTINEL'), OSError('PRIVATE-SENTINEL')]:
            with self.subTest(error=type(error).__name__), patch.object(Path, 'open', side_effect=error):
                with self.assertRaises(ContractError) as caught:
                    load_subagents_transcript('unused')
                self.assertEqual(str(caught.exception), READ_ERROR)
        class BrokenRead(io.BytesIO):
            def read(self, *args):
                raise OSError('PRIVATE-SENTINEL')
        with patch.object(Path, 'open', return_value=BrokenRead()):
            with self.assertRaises(ContractError) as caught:
                load_subagents_transcript('unused')
            self.assertEqual(str(caught.exception), READ_ERROR)

    def test_size_limit_is_bytes_and_preserves_specific_error(self):
        raw = '{"x":"é"}\n'.encode('utf8')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace'
            path.write_bytes(raw)
            with patch('weasley_agent_bench.subagents.MAX_TRANSCRIPT_BYTES', len(raw)):
                self.assertEqual(load_subagents_transcript(path), [{'x': 'é'}])
            with patch('weasley_agent_bench.subagents.MAX_TRANSCRIPT_BYTES', len(raw) - 1):
                with self.assertRaisesRegex(ContractError, '^subagents: transcript exceeds size limit$'):
                    load_subagents_transcript(path)

    def test_reader_requests_only_limit_plus_one(self):
        class RecordingRead(io.BytesIO):
            def read(self, count):
                self.count = count
                return super().read(count)
        stream = RecordingRead(b'{}\n' * 10)
        with patch('weasley_agent_bench.subagents.MAX_TRANSCRIPT_BYTES', 8), patch.object(Path, 'open', return_value=stream):
            with self.assertRaisesRegex(ContractError, 'transcript exceeds size limit'):
                load_subagents_transcript('unused')
        self.assertEqual(stream.count, 9)
        self.assertTrue(stream.closed)

    def test_invalid_encoding_and_framing_remain_distinct(self):
        cases = [(b'\xff\n', READ_ERROR), (b'{}', 'subagents: incomplete JSONL framing'),
                 (b'', 'subagents: incomplete JSONL framing'),
                 (b'\n', 'subagents: invalid JSON payload'),
                 (b'{"PRIVATE-SENTINEL":}\n', 'subagents: invalid JSON payload'),
                 (b'\xef\xbb\xbf{}\n', 'subagents: invalid JSON payload')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace'
            for raw, error in cases:
                with self.subTest(raw=raw):
                    path.write_bytes(raw)
                    with self.assertRaises(ContractError) as caught:
                        load_subagents_transcript(path)
                    self.assertEqual(str(caught.exception), error)

    def test_valid_lf_crlf_and_unicode_separator_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace'
            for newline in ['\n', '\r\n']:
                raw = (json.dumps({'text': 'a\u2028b'}, ensure_ascii=False) + newline).encode('utf8')
                path.write_bytes(raw)
                self.assertEqual(load_subagents_transcript(path), [{'text': 'a\u2028b'}])
