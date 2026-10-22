"""Offline observation fidelity for one minimized historical tau2 profile.

No task reward, tool execution, provider request or consumption estimate occurs.
The supported derivative has six ordered turns and one assistant-owned call.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .contracts import (
    ContractError, _constant, _document_sha256, _json_value, _pairs,
    validate_run, validate_suite,
)

PROFILE = 'tau2-minimized-single-call-transfer-fidelity-v1'
MAX_EPISODE_BYTES = 256 * 1024
_ROLES = ('assistant', 'user', 'assistant', 'tool', 'assistant', 'user')
_IDENTITY = ('id', 'task_id', 'trial', 'seed')
_TERMINALS = ('###STOP###', '###TRANSFER###', '###OUT-OF-SCOPE###')


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ContractError('tau2: ' + reason)


def _shape(value, keys: set[str]) -> None:
    _require(type(value) is dict and value.keys() == keys, 'unsupported or missing fields')


def _identifier(value) -> None:
    _require(type(value) is str and bool(value) and value.strip() == value,
             'invalid native identity')


def _validate_episode(row: dict) -> None:
    _shape(row, {'id', 'task_id', 'trial', 'seed', 'termination_reason', 'messages'})
    try:
        _json_value(row, 'episode')
        # Reject non-UTF-8 scalar strings, including inside argument objects.
        json.dumps(row, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ContractError('tau2: expected finite UTF-8 JSON values') from None
    _identifier(row['id'])
    _identifier(row['task_id'])
    for field in ('trial', 'seed'):
        _require(type(row[field]) is int and 0 <= row[field] <= 2**53 - 1,
                 'invalid trial or seed')
    _require(row['termination_reason'] == 'user_stop', 'unsupported recorded terminal')
    messages = row['messages']
    _require(type(messages) is list and len(messages) == 6, 'expected six ordered messages')
    for index, (message, role) in enumerate(zip(messages, _ROLES)):
        _shape(message, {'id', 'role', 'content', 'requestor', 'error', 'turn_idx'} if role == 'tool'
               else {'role', 'content', 'tool_calls', 'turn_idx', 'usage'})
        _require(message['role'] == role, 'unsupported message order or role')
        _require(type(message['turn_idx']) is int and message['turn_idx'] == index,
                 'turn indices must match message order')
        if role == 'tool':
            _identifier(message['id'])
            _require(message['requestor'] == 'assistant', 'unsupported result owner')
            _require(type(message['error']) is bool, 'explicit boolean tool outcome required')
            _require(type(message['content']) is str, 'tool output must be saved text')
            continue
        usage = message['usage']
        if usage is not None:
            _shape(usage, {'prompt_tokens', 'completion_tokens'})
            _require(all(type(value) is int and 0 <= value <= 2**53 - 1 for value in usage.values()),
                     'invalid recorded usage')
        if index == 2:
            _require(message['content'] is None, 'mixed text and call turns are unsupported')
            calls = message['tool_calls']
            _require(type(calls) is list and len(calls) == 1, 'exactly one native call required')
            call = calls[0]
            _shape(call, {'id', 'name', 'arguments', 'requestor'})
            _identifier(call['id'])
            _identifier(call['name'])
            _require(type(call['arguments']) is dict, 'arguments must be an object')
            _require(call['requestor'] == 'assistant', 'unsupported call owner')
        else:
            _require(type(message['content']) is str and bool(message['content']), 'expected saved text')
            _require(message['tool_calls'] is None, 'additional or overlapping calls are unsupported')
    _require(messages[2]['tool_calls'][0]['id'] == messages[3]['id'], 'unmatched native call/result')
    for index in (0, 1, 4):
        _require(not any(marker in messages[index]['content'] for marker in _TERMINALS),
                 'earlier recorded terminal signal')
    _require(messages[5]['content'] == '###TRANSFER###', 'unsupported terminal user signal')


def load_tau2_episode(path: str | Path, *, max_bytes: int = MAX_EPISODE_BYTES) -> dict:
    """Read only a bounded local minimized derivative; never follow manifest URLs."""
    _require(type(max_bytes) is int and 1 <= max_bytes <= MAX_EPISODE_BYTES,
             'byte limit must be between 1 and 262144')
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(max_bytes + 1)
        _require(len(raw) <= max_bytes, 'episode exceeds byte limit')
        row = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
        _validate_episode(row)
        return row
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise ContractError('tau2: cannot read supported bounded UTF-8 episode') from None


def export_tau2_fidelity(suite: dict, row: dict, *, task_id: str, run_id: str) -> dict:
    """Return a run plus conversion accounting; expectations come only from suite.

    Completed means the supported recorded transcript closed, not task success.
    The derived final object is a fidelity observation, not a native model answer.
    Native arguments, outcome and exact saved output remain ordinary tool fields.
    In-memory callers own input allocation limits; the file loader is bounded.
    """
    validate_suite(suite)
    _validate_episode(row)
    messages = row['messages']
    call, result = messages[2]['tool_calls'][0], messages[3]
    identity = {key: row[key] for key in _IDENTITY}
    try:
        arguments = copy.deepcopy(call['arguments'])
    except RecursionError:
        raise ContractError('tau2: arguments exceed conversion limits') from None
    run = {'schema_version': 1, 'suite_id': suite['suite_id'], 'run_id': run_id, 'results': [{
        'task_id': task_id, 'status': 'completed',
        'final': {'assistant_text': messages[4]['content'], 'episode_identity': identity,
                  'call_id': call['id'], 'arguments_sha256': _document_sha256(call['arguments'])},
        'tool_calls': [{'name': call['name'], 'arguments': arguments,
                        'outcome': 'error' if result['error'] else 'success', 'output': result['content']}],
    }]}
    validate_run(run, suite)
    losses = []

    def omit(path: str, reason: str) -> None:
        losses.append({'path': path, 'reason': reason})

    for index, message in enumerate(messages):
        prefix = f'/messages/{index}'
        for field in ('role', 'turn_idx'):
            omit(prefix + '/' + field, 'validated profile position; no separate scored field')
        if index == 3:
            omit(prefix + '/requestor', 'validated assistant ownership')
        else:
            omit(prefix + '/usage', 'all usage unreported; partial counts are not measured totals')
            if index == 2:
                omit(prefix + '/content', 'validated null call-turn text')
                omit(prefix + '/tool_calls/0/requestor', 'validated assistant ownership')
            else:
                omit(prefix + '/tool_calls', 'validated null call list')
            if index in (0, 1):
                omit(prefix + '/content', 'dialogue context not scored; full input digest still binds it')
            if index == 5:
                omit(prefix + '/content', 'terminal user signal validated and retained in provenance')
    omit('/termination_reason', 'validated recorded terminal retained in provenance')
    observed = [index for index in (0, 2, 4) if messages[index]['usage'] is not None]
    missing = [index for index in (0, 2, 4) if messages[index]['usage'] is None]
    return {
        'adapter': PROFILE, 'run': run,
        'provenance': {'input_sha256': _document_sha256(row), 'identity': dict(identity),
                       'suite_sha256': _document_sha256(suite), 'run_sha256': _document_sha256(run),
                       'source_terminal': row['termination_reason'],
                       'terminal_signal': messages[5]['content'],
                       'usage': {'assistant_observed_turns': observed,
                                 'assistant_unreported_turns': missing, 'emitted': False}},
        'losses': sorted(losses, key=lambda entry: entry['path']),
        'derived': {'status': 'closed supported recorded conversation, not task success',
                    'final': 'observation envelope, not a native structured model answer',
                    'arguments_sha256': 'canonical JSON hash preserving integer/float representation',
                    'task_id': 'caller-selected fidelity contract', 'run_id': 'caller-selected artifact label'},
    }
