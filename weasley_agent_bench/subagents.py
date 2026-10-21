"""Strict offline adapter for a bounded foreground child-transcript v1 subset.

Consistency checks are not evidence of authenticity or actual tool execution.
"""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import ContractError, _pairs, _constant, _json_value, validate_run, validate_suite

SOURCE_REVISION = "72901e3f17eea4d56418b24e7a63f10c7ae22375"
MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise ContractError(f"subagents: {detail}")


def _json(text: str):
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
        _json_value(value, "payload")
        return value
    except (ValueError, TypeError, RecursionError):
        raise ContractError("subagents: invalid JSON payload") from None


def load_subagents_transcript(path: str) -> list[dict]:
    """Bound input size; never print transcript contents in diagnostics."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_TRANSCRIPT_BYTES + 1)
        _require(len(raw) <= MAX_TRANSCRIPT_BYTES, "transcript exceeds size limit")
        text = raw.decode("utf-8")
    except (OSError, UnicodeError):
        raise ContractError("subagents: cannot read UTF-8 transcript") from None
    _require(bool(text) and text.endswith("\n"), "incomplete JSONL framing")
    return [_json(line) for line in text[:-1].split("\n")]


def _text(message: dict) -> str:
    content = message.get("content")
    _require(isinstance(content, list), "expected text content array")
    _require(all(isinstance(p, dict) and p.get("type") == "text" and
                 isinstance(p.get("text"), str) for p in content), "unsupported content type")
    return "\n".join(p["text"] for p in content)


def export_subagents(suite: dict, records: list[dict], metadata: dict, *,
                     task_id: str, run_id: str) -> dict:
    """Export one task, only for a complete, successful single-attempt artifact.

    Tool calls retain start order. Only the terminal assistant message becomes
    final; text blocks are joined by newline, never parsed as structured JSON.
    """
    validate_suite(suite)
    _require(isinstance(metadata, dict), "metadata must be an object")
    identity = (metadata.get("runId"), metadata.get("agent"))
    _require(all(isinstance(v, str) and v.strip() for v in identity), "missing run identity")
    _require(type(metadata.get("exitCode")) is int and metadata["exitCode"] == 0,
             "only successful foreground exits are supported")
    _require(all(metadata.get(k) is None or metadata.get(k) == ""
                 for k in ("error", "transcriptError", "processSignal")),
             "metadata reports failure or interruption")
    attempts = metadata.get("modelAttempts")
    _require(isinstance(attempts, list) and len(attempts) == 1 and isinstance(attempts[0], dict),
             "exactly one model attempt is required")
    attempt = attempts[0]
    _require(attempt.get("success") is True and type(attempt.get("exitCode")) is int and
             attempt["exitCode"] == 0 and (attempt.get("error") is None or attempt.get("error") == "")
             and isinstance(attempt.get("model"), str) and bool(attempt["model"].strip()),
             "unsuccessful or malformed model attempt")
    _require(type(metadata.get("toolCount")) is int and metadata["toolCount"] >= 0,
             "explicit toolCount is required")
    _require(isinstance(records, list) and len(records) >= 2, "incomplete transcript")
    try:
        _json_value(records, "transcript")
    except (ContractError, RecursionError):
        raise ContractError("subagents: transcript must contain finite JSON values") from None
    _require(isinstance(records[0], dict), "record must be an object")
    child_index = records[0].get("childIndex")
    _require((child_index is None and "childIndex" not in records[0]) or
             (type(child_index) is int and child_index >= 0),
             "invalid child index")
    calls, announcements, ends, outputs = {}, {}, {}, {}
    final = None
    for index, record in enumerate(records):
        _require(isinstance(record, dict), "record must be an object")
        _require(type(record.get("version")) is int and record["version"] == 1 and
                 record.get("source") == "foreground" and record.get("childIndex") == child_index and
                 type(record.get("childIndex")) is type(child_index) and
                 ("childIndex" in record) == ("childIndex" in records[0]),
                 "unsupported transcript version or source")
        _require((record.get("runId"), record.get("agent")) == identity, "artifact identity mismatch")
        _require(final is None, "records after terminal assistant message")
        kind = record.get("recordType")
        if kind == "message":
            message = record.get("message")
            _require(isinstance(message, dict) and record.get("role") in ("user", "assistant", "toolResult")
                     and message.get("role") == record.get("role"),
                     "message role mismatch")
            role = record["role"]
            if role == "user":
                _require(index == 0 and record.get("sourceEventType") == "initial_prompt",
                         "only one initial prompt is supported")
                _require(_text(message) == record.get("text"), "prompt text mismatch")
            elif role == "assistant":
                _require(index > 0 and record.get("sourceEventType") == "message_end",
                         "unsupported assistant event")
                _require(all(v is None or v == "" for v in
                             (record.get("errorMessage"), message.get("errorMessage"))),
                         "assistant reports an error")
                reason = record.get("stopReason")
                _require(reason == message.get("stopReason") and reason in ("stop", "toolUse"),
                         "unsupported assistant stop reason")
                if reason == "stop":
                    _require(set(calls) == set(announcements) == set(ends) == set(outputs),
                             "incomplete tool lifecycle")
                    final = _text(message)
                    _require(record.get("text", "") == final, "final text mismatch")
                    _require(bool(final.strip()), "empty final output is unsupported")
                else:
                    content = message.get("content")
                    _require(isinstance(content, list), "missing assistant content")
                    added = 0
                    for part in content:
                        _require(isinstance(part, dict), "invalid assistant content")
                        if part.get("type") == "text":
                            _require(isinstance(part.get("text"), str), "invalid assistant text")
                            continue
                        _require(part.get("type") == "toolCall", "unsupported assistant content")
                        cid = part.get("id")
                        _require(isinstance(cid, str) and bool(cid) and cid not in announcements,
                                 "missing or duplicate announced tool id")
                        _require(isinstance(part.get("name"), str) and bool(part["name"]) and
                                 isinstance(part.get("arguments"), dict), "invalid announced tool call")
                        announcements[cid] = (part["name"], part["arguments"])
                        added += 1
                    _require(added > 0, "toolUse without tool calls")
                    observed_text = "\n".join(p["text"] for p in content if p["type"] == "text")
                    _require(record.get("text", "") == observed_text, "assistant text mismatch")
            elif role == "toolResult":
                _require(record.get("sourceEventType") in ("message_end", "tool_result_end"),
                         "unsupported tool result event")
                cid = record.get("toolCallId")
                _require(isinstance(cid, str) and cid in calls and cid not in outputs,
                         "unmatched or duplicate tool result")
                _require(record.get("toolName") == calls[cid]["name"] and
                         message.get("toolCallId") == cid and message.get("toolName") == record["toolName"],
                         "tool result identity mismatch")
                error = record.get("isError")
                _require(type(error) is bool and message.get("isError") is error,
                         "explicit consistent tool result outcome required")
                text = _text(message)
                _require(record.get("outputTruncated", False) is False and
                         "… payload truncated" not in text and record.get("text", "") == text,
                         "truncated or inconsistent tool result")
                outputs[cid] = (error, text)
            else:
                raise ContractError("subagents: unsupported message role")
        elif kind == "tool_start":
            cid = record.get("toolCallId")
            _require(isinstance(cid, str) and cid in announcements and cid not in calls,
                     "unannounced or duplicate tool start")
            payload = record.get("argsPayload")
            _require(isinstance(payload, str) and "… payload truncated" not in payload,
                     "missing or truncated arguments")
            arguments = _json(payload)
            _require(isinstance(arguments, dict) and
                     record.get("toolName") == announcements[cid][0] and
                     json.dumps(arguments, sort_keys=True, allow_nan=False) ==
                     json.dumps(announcements[cid][1], sort_keys=True, allow_nan=False),
                     "tool arguments mismatch")
            _require(record.get("sourceEventType") == "tool_execution_start", "invalid start event")
            calls[cid] = {"name": record["toolName"], "arguments": arguments}
        elif kind == "tool_end":
            cid = record.get("toolCallId")
            _require(isinstance(cid, str) and cid in calls and cid not in ends,
                     "unmatched or duplicate tool end")
            _require(record.get("sourceEventType") == "tool_execution_end" and
                     record.get("toolName") == calls[cid]["name"] and type(record.get("isError")) is bool,
                     "explicit matching tool end outcome required")
            ends[cid] = record["isError"]
        else:
            raise ContractError("subagents: unsupported or truncated transcript record")
    _require(records[0].get("role") == "user" and final is not None, "missing prompt or terminal output")
    _require(len(calls) == metadata["toolCount"], "toolCount mismatch")
    tool_calls = []
    for cid, call in calls.items():
        error, output = outputs[cid]
        _require(error is ends[cid], "conflicting tool outcomes")
        tool_calls.append({**call, "outcome": "error" if error else "success", "output": output})
    run = {"schema_version": 1, "suite_id": suite["suite_id"], "run_id": run_id,
           "results": [{"task_id": task_id, "status": "completed", "final": final,
                        "tool_calls": tool_calls}]}
    # Upstream aggregates default missing usage/duration to zero. Do not claim
    # those defaults are measured observations; this adapter emits no usage.
    validate_run(run, suite)
    return run
