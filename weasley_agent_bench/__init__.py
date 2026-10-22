"""Provider-neutral offline agent evaluation. No model or tool execution."""

from .contracts import ContractError, load_document, validate_run, validate_suite
from .scoring import compare, evaluate
from .jsonl import compare_jsonl, evaluate_jsonl
from .repeated import compare_repeated
from .provenance import create_provenance, verify_provenance
from .linting import lint_suite
from .subagents import export_subagents, load_subagents_transcript

__all__ = [
    "ContractError", "compare", "compare_repeated", "evaluate", "evaluate_jsonl", "load_document", "validate_run",
    "validate_suite", "create_provenance", "verify_provenance", "lint_suite", "export_subagents", "load_subagents_transcript",
    "compare_jsonl",
]
