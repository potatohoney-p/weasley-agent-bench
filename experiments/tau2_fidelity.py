"""Check one historical derivative against frozen observation expectations.

This offline experiment checks adapter fidelity, never tau2 task success.
An optional local episode must satisfy the same minimized profile. Valid drift
exits 1; malformed or unsupported evidence exits 2 without a partial report.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from weasley_agent_bench import ContractError, evaluate, evaluate_jsonl
from weasley_agent_bench.contracts import _document_sha256, load_document
from weasley_agent_bench.tau2 import export_tau2_fidelity, load_tau2_episode

ROOT = Path(__file__).resolve().parents[1]
SUITE_SHA256 = 'bc70278cc01aba3bbd41ff05349e76e60b4c274307be536cfbad4b9e9a4e60be'
FIXTURE_SHA256 = '9fc77bb6ca72f58a42589ffa8bbac89d26fec1b0d0bef53ea2a8ddb523fec636'


def _write_jsonl(path: Path, document: dict, kind: str) -> None:
    field = 'tasks' if kind == 'suite' else 'results'
    header = {key: value for key, value in document.items() if key != field}
    header.update(format=f'weasley-agent-bench/{kind}-jsonl', format_version=1)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        for value in [header, *document[field], {'end': True, 'count': len(document[field])}]:
            stream.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + '\n')


def analyze_episode(row: dict) -> dict:
    """Use a frozen reference suite, never expectations generated from row."""
    suite = load_document(ROOT / 'examples/tau2/fidelity-suite.json')
    if _document_sha256(suite) != SUITE_SHA256:
        raise ContractError('tau2: reviewed reference suite changed')
    bundle = export_tau2_fidelity(suite, row, task_id='historical-observations', run_id='fidelity-candidate')
    legacy = evaluate(suite, bundle['run'])
    with tempfile.TemporaryDirectory(prefix='weasley-tau2-') as directory:
        suite_path, run_path = Path(directory) / 'suite.jsonl', Path(directory) / 'run.jsonl'
        _write_jsonl(suite_path, suite, 'suite')
        _write_jsonl(run_path, bundle['run'], 'run')
        events = list(evaluate_jsonl(suite_path, run_path))
    if not events or events[-1].get('complete') is not True:
        raise ContractError('tau2: missing JSONL terminal summary')
    bounded = {**{key: events[0][key] for key in ('schema_version', 'suite_id', 'suite_sha256', 'run_id')},
               'passed': events[-1]['passed'], 'summary': events[-1]['summary'],
               'results': [event['result'] for event in events[1:-1]]}
    if bounded != legacy:
        raise ContractError('tau2: transport reports differ')
    return {'adapter': bundle['adapter'], 'purpose': 'recorded observation fidelity only',
            'fidelity_passed': legacy['passed'], 'legacy_jsonl_report_parity': True,
            'matches_reviewed_fixture': bundle['provenance']['input_sha256'] == FIXTURE_SHA256,
            'task_success_evaluated': False, 'provider_quality_evaluated': False,
            'execution_authenticated': False, 'usage_reported': False,
            'report': legacy, 'provenance': bundle['provenance'],
            'losses': bundle['losses'], 'derived': bundle['derived']}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode', type=Path, help='local minimized profile; never changes reference expectations')
    args = parser.parse_args(argv)
    try:
        episode = load_tau2_episode(args.episode or ROOT / 'examples/tau2/task46-trial0.json')
        if args.episode is None and _document_sha256(episode) != FIXTURE_SHA256:
            raise ContractError('tau2: reviewed historical fixture changed')
        result = analyze_episode(episode)
    except (ContractError, OSError, ValueError, RecursionError):
        print(json.dumps({'error': 'tau2 fidelity input or evaluation failed'}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result['fidelity_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
