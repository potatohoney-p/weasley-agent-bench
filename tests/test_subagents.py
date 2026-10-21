"""Synthetic writer-produced fixture, not a recorded model run."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from weasley_agent_bench.contracts import ContractError, load_document
from weasley_agent_bench.scoring import evaluate
from weasley_agent_bench.subagents import export_subagents, load_subagents_transcript

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "subagents"


class SubagentsTests(unittest.TestCase):
    def setUp(self):
        self.suite = load_document(FIXTURE / "suite.json")
        self.records = load_subagents_transcript(FIXTURE / "synthetic.transcript.jsonl")
        self.metadata = load_document(FIXTURE / "synthetic.metadata.json")

    def export(self):
        return export_subagents(self.suite, self.records, self.metadata,
                                task_id="read-note", run_id="synthetic-export")

    def test_writer_fixture_equals_handwritten_run_and_score(self):
        expected = load_document(FIXTURE / "expected-run.json")
        self.assertEqual(expected, self.export())
        self.assertEqual(evaluate(self.suite, expected), evaluate(self.suite, self.export()))
        self.assertTrue(evaluate(self.suite, self.export())["passed"])

    def test_missing_or_defaulted_usage_is_not_fabricated(self):
        self.assertNotIn("usage", self.export()["results"][0])
        self.metadata.pop("durationMs")
        self.metadata.pop("usage")
        self.assertNotIn("usage", self.export()["results"][0])

    def test_missing_each_lifecycle_record_rejected(self):
        original = copy.deepcopy(self.records)
        for index in range(len(original)):
            with self.subTest(index=index):
                self.records = original[:index] + original[index + 1:]
                with self.assertRaises(ContractError): self.export()

    def test_duplicate_each_record_rejected(self):
        original = copy.deepcopy(self.records)
        for index in range(len(original)):
            with self.subTest(index=index):
                self.records = original[:index] + [original[index]] + original[index:]
                with self.assertRaises(ContractError): self.export()

    def test_reject_unsupported_or_inconsistent_records(self):
        cases = [(0,"version",True),(0,"source","async"),(0,"runId","other"),
                 (1,"stopReason","error"),(2,"argsPayload",'{"path":"a","path":"b"}'),
                 (2,"argsPayload",'{"n":NaN}'),(2,"argsPayload",'{}'),
                 (3,"isError",None),(3,"isError",True),(4,"outputTruncated",True),
                 (4,"isError",None),(5,"stopReason","length"),(5,"text","other"),
                 (5,"childIndex",1),(5,"recordType","truncated")]
        original = copy.deepcopy(self.records)
        for index,key,value in cases:
            with self.subTest(index=index,key=key,value=value):
                self.records = copy.deepcopy(original)
                self.records[index][key] = value
                with self.assertRaises(ContractError): self.export()

    def test_reject_metadata_failures(self):
        original = copy.deepcopy(self.metadata)
        for key,value in [("runId","other"),("exitCode",True),("exitCode",1),
                          ("toolCount",0),("toolCount",None),("modelAttempts",[]),
                          ("modelAttempts",original["modelAttempts"] * 2),
                          ("transcriptError","write failure"),("processSignal","SIGTERM")]:
            with self.subTest(key=key,value=value):
                self.metadata = {**original,key:value}
                with self.assertRaises(ContractError): self.export()

    def test_tool_error_remains_error(self):
        self.records[3]["isError"] = True
        self.records[4]["isError"] = True
        self.records[4]["message"]["isError"] = True
        run = self.export()
        self.assertEqual(run["results"][0]["tool_calls"][0]["outcome"], "error")
        self.assertFalse(evaluate(self.suite,run)["passed"])

    def test_end_and_result_order_both_supported(self):
        self.records[3],self.records[4] = self.records[4],self.records[3]
        self.assertTrue(evaluate(self.suite,self.export())["passed"])

    def test_no_implicit_json_parsing(self):
        self.records[-1]["text"] = '{"value":1}'
        self.records[-1]["message"]["content"] = [{"type":"text","text":'{"value":1}'}]
        self.assertEqual(self.export()["results"][0]["final"], '{"value":1}')

    def test_non_text_final_rejected(self):
        self.records[-1]["message"]["content"] = [{"type":"image","data":"synthetic"}]
        with self.assertRaises(ContractError): self.export()

    def test_jsonl_errors_do_not_echo_content(self):
        for text in ['{"secret":"PRIVATE-SENTINEL",}\n', '{}', '\n', '{"version":1,"version":2}\n']:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "transcript"
                path.write_text(text, encoding="utf-8", newline="\n")
                with self.assertRaises(ContractError) as caught:
                    load_subagents_transcript(path)
                self.assertNotIn("PRIVATE-SENTINEL", str(caught.exception))

    def test_cli_reproducible_export(self):
        result = subprocess.run([sys.executable,"-m","weasley_agent_bench","export-subagents",
            str(FIXTURE/"suite.json"),str(FIXTURE/"synthetic.transcript.jsonl"),
            str(FIXTURE/"synthetic.metadata.json"),"--task-id","read-note","--run-id","synthetic-export"],
            cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),self.export())

    def test_parallel_calls_keep_start_order_not_completion_order(self):
        second = {"type":"toolCall","id":"call-2","name":"read","arguments":{"path":"second"}}
        self.records[1]["message"]["content"].append(second)
        start = copy.deepcopy(self.records[2])
        start.update(toolCallId="call-2", argsPayload='{"path":"second"}')
        end = copy.deepcopy(self.records[3]); end["toolCallId"] = "call-2"
        output = copy.deepcopy(self.records[4]); output["toolCallId"] = "call-2"
        output["message"]["toolCallId"] = "call-2"
        # First started call completes last.
        self.records[3:3] = [start,end,output]
        self.metadata["toolCount"] = 2
        calls = self.export()["results"][0]["tool_calls"]
        self.assertEqual([c["arguments"]["path"] for c in calls], ["synthetic-note.txt","second"])

    def test_zero_tool_run_needs_explicit_zero_count(self):
        self.records = [self.records[0],self.records[-1]]
        self.metadata["toolCount"] = 0
        self.assertEqual(self.export()["results"][0]["tool_calls"], [])
        del self.metadata["toolCount"]
        with self.assertRaises(ContractError): self.export()

    def test_more_than_one_terminal_message_is_not_silently_selected(self):
        self.records.append(copy.deepcopy(self.records[-1]))
        with self.assertRaises(ContractError): self.export()

    def test_argument_boolean_is_not_equal_to_number(self):
        self.records[1]["message"]["content"][1]["arguments"] = {"n":True}
        self.records[2]["argsPayload"] = '{"n":1}'
        with self.assertRaises(ContractError): self.export()

    def test_direct_api_rejects_non_json_arguments(self):
        for value in [float("nan"), object()]:
            self.records[1]["message"]["content"][1]["arguments"] = {"n":value}
            with self.assertRaises(ContractError): self.export()

    def test_budgeted_unknown_usage_remains_visible_failure(self):
        self.suite["tasks"][0]["budget"]["max_duration_ms"] = 1000
        self.assertFalse(evaluate(self.suite,self.export())["passed"])

    def test_jsonl_literal_unicode_separators_are_text_not_lines(self):
        for char in ["\u0085", "\u2028", "\u2029"]:
            with self.subTest(char=repr(char)), tempfile.TemporaryDirectory() as directory:
                records = copy.deepcopy(self.records)
                records[-1]["text"] = "before" + char + "after"
                records[-1]["message"]["content"] = [{"type":"text","text":records[-1]["text"]}]
                path = Path(directory) / "transcript"
                path.write_text("".join(json.dumps(r,ensure_ascii=False) + "\n" for r in records), encoding="utf-8", newline="\n")
                self.assertEqual(load_subagents_transcript(path),records)

    def test_overflow_numeric_arguments_are_content_safe_contract_error(self):
        self.records[2]["argsPayload"] = '{"PRIVATE-SENTINEL":1e999}'
        with self.assertRaises(ContractError) as caught: self.export()
        self.assertNotIn("PRIVATE-SENTINEL",str(caught.exception))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transcript"
            path.write_text("".join(json.dumps(r) + "\n" for r in self.records), encoding="utf-8", newline="\n")
            result = subprocess.run([sys.executable,"-m","weasley_agent_bench","export-subagents",
                str(FIXTURE/"suite.json"),str(path),str(FIXTURE/"synthetic.metadata.json"),
                "--task-id","read-note","--run-id","synthetic-export"],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertEqual(result.stdout,"")
            self.assertEqual(json.loads(result.stderr),{"error":"subagents: invalid JSON payload"})

    def test_malformed_falsey_errors_and_model_are_rejected(self):
        for value in [[],{},False,0]:
            with self.subTest(value=value):
                self.metadata["error"] = value
                with self.assertRaises(ContractError): self.export()
        self.metadata.pop("error")
        self.metadata["modelAttempts"][0]["model"] = []
        with self.assertRaises(ContractError): self.export()

    def test_intermediate_text_mismatch_rejected(self):
        self.records[1]["text"] = "contradiction"
        with self.assertRaises(ContractError): self.export()

    def test_null_child_index_is_not_missing(self):
        for record in self.records: record.pop("childIndex")
        self.records[-1]["childIndex"] = None
        with self.assertRaises(ContractError): self.export()

    def test_first_record_must_be_object(self):
        for value in [None,False,0,1.5,"text",[]]:
            with self.subTest(value=value):
                self.records[0] = value
                with self.assertRaises(ContractError): self.export()

    def test_missing_message_roles_rejected(self):
        del self.records[-1]["role"]
        del self.records[-1]["message"]["role"]
        with self.assertRaises(ContractError): self.export()
