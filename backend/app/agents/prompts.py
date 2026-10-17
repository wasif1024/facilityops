"""Agent prompts: one Markdown file per agent in backend/app/prompts/.

Each prompt's ``sha`` (first 12 hex digits of SHA-256 of the file) is recorded with every model
call and on the decision trail, so any answer can be traced to the exact prompt that produced it.
The manager's ledger prompts are one file with a ``## <builder argument>`` section per prompt.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

AGENTS = (
    "incident_manager",
    "asset_historian",
    "diagnostician",
    "safety_compliance",
    "procurement",
    "scheduler",
    "communicator",
)
LEDGER_FILE = "incident_manager_ledgers"
# MagenticBuilder argument -> placeholders the framework fills in.
LEDGER_PLACEHOLDERS = {
    "task_ledger_facts_prompt": ("task",),
    "task_ledger_plan_prompt": ("team",),
    "task_ledger_full_prompt": ("task", "team", "facts", "plan"),
    "task_ledger_facts_update_prompt": ("task", "old_facts"),
    "task_ledger_plan_update_prompt": ("team",),
    "progress_ledger_prompt": ("task", "team", "names"),
    "final_answer_prompt": ("task",),
}


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    sha: str


def _read(name: str) -> tuple[str, str]:
    data = (PROMPTS_DIR / f"{name}.md").read_bytes()
    return data.decode("utf-8"), hashlib.sha256(data).hexdigest()[:12]


@cache
def load_prompt(name: str) -> Prompt:
    text, sha = _read(name)
    return Prompt(name, text, sha)


@cache
def load_ledger_prompts() -> tuple[dict[str, str], str]:
    """The manager's ledger prompts by builder argument, and the file's sha."""
    text, sha = _read(LEDGER_FILE)
    sections = re.split(r"^## (\w+)\s*$", text, flags=re.M)
    prompts = {sections[i]: sections[i + 1].strip() + "\n" for i in range(1, len(sections), 2)}
    missing = set(LEDGER_PLACEHOLDERS) - set(prompts)
    if missing:
        raise ValueError(f"{LEDGER_FILE}.md is missing sections: {sorted(missing)}")
    return prompts, sha
