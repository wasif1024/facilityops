"""The incident team's budgets are code (TeamBudgets); every document that quotes them must agree."""

from pathlib import Path

import pytest

from backend.app.agents.team import TeamBudgets
from backend.app.agents.tools.runtime import DEFAULT_MAX_CALLS

ROOT = Path(__file__).resolve().parents[3]
B = TeamBudgets()
TOKENS_K = f"{B.max_tokens // 1000}k"
SECONDS = int(B.timeout_seconds)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # The spec's budget table is the reference for readers.
        ("docs/specification.md", [f"| Coordination rounds | {B.max_rounds} |", f"| Tokens per run | {B.max_tokens:,} |",
                                   f"| Wall-clock time per run | {SECONDS} s |", f"| Tool calls per specialist per run | {DEFAULT_MAX_CALLS} |"]),
        ("docs/architecture.md", [f"≤ {B.max_rounds} rounds, ≤ {TOKENS_K} tokens, ≤ {SECONDS} s"]),
        # The README diagram is generated from this source.
        ("docs/images/source/agent-orchestration.mjs", [f"≤ {B.max_rounds} rounds · ≤ {TOKENS_K} tokens · ≤ {DEFAULT_MAX_CALLS} tool calls per specialist · ≤ {SECONDS} s"]),
        ("RESPONSIBLE_AI.md", [f"{B.max_rounds} rounds, {TOKENS_K} tokens, {SECONDS} seconds and {DEFAULT_MAX_CALLS} tool calls"]),
    ],
)
def test_documents_quote_the_budgets_in_code(path, expected):
    text = (ROOT / path).read_text(encoding="utf-8")
    for phrase in expected:
        assert phrase in text, f"{path} is out of date: expected {phrase!r} (from TeamBudgets)"
