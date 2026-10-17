"""Prompts are code: they must load, format, keep the framework schema and stay consistent with the tools."""

import json
import re

import pytest

from backend.app.agents.knowledge import parse_documents
from backend.app.agents.prompts import AGENTS, LEDGER_PLACEHOLDERS, load_ledger_prompts, load_prompt
from backend.app.agents.tools.registry import SPECIALIST_TOOLS, TOOLS
from backend.app.domain.incident_plan import IncidentPlan
from backend.app.services.world import WORLD_FILE

SPECIALISTS = [a for a in AGENTS if a != "incident_manager"]
FILLED = {"task": "TASK", "team": "TEAM", "names": "asset_historian, diagnostician", "facts": "F", "plan": "P", "old_facts": "O"}


def json_blocks(text: str) -> list[dict]:
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)```", text, flags=re.S)]


def known_source_ids() -> set[str]:
    world = json.loads(WORLD_FILE.read_text())
    ids = {f"asset:{a['id']}" for a in world["assets"]}
    ids |= {f"work_order:{r['id']}" for r in world["maintenance_records"]}
    ids |= {f"part:{p['sku']}" for p in world["parts"]}
    ids |= {v["contract_id"] for v in world["vendors"]}
    ids |= {f"technician:{t['id']}" for t in world["technicians"]}
    ids |= {p["id"] for p in world["permits"]}
    ids |= {p.doc_id for p in parse_documents()}
    return ids


def test_every_agent_has_its_own_prompt_file():
    shas = {load_prompt(name).sha for name in AGENTS}
    assert len(shas) == len(AGENTS) and all(load_prompt(n).text.startswith("# ") for n in AGENTS)


@pytest.mark.parametrize("section", list(LEDGER_PLACEHOLDERS))
def test_ledger_prompts_format_with_exactly_their_placeholders(section):
    prompts, _ = load_ledger_prompts()
    template = prompts[section]
    for placeholder in LEDGER_PLACEHOLDERS[section]:
        assert "{" + placeholder + "}" in template, f"{section} lost {{{placeholder}}}"
    template.format(**FILLED)  # raises on an unescaped literal brace or an unknown placeholder


def test_progress_ledger_keeps_the_framework_schema():
    prompts, _ = load_ledger_prompts()
    formatted = prompts["progress_ledger_prompt"].format(**FILLED)
    for key in ("is_request_satisfied", "is_in_loop", "is_progress_being_made", "next_speaker", "instruction_or_question"):
        assert f'"{key}"' in formatted


def test_final_answer_example_is_a_valid_incident_plan():
    prompts, _ = load_ledger_prompts()
    formatted = prompts["final_answer_prompt"].format(**FILLED)
    example = json.loads(formatted.split("Example:", 1)[1].strip())
    plan = IncidentPlan.model_validate(example)
    assert plan.safety_block and plan.flags() == ["safety_block"]


@pytest.mark.parametrize("specialist", SPECIALISTS)
def test_specialist_examples_are_valid_json_for_that_specialist(specialist):
    examples = json_blocks(load_prompt(specialist).text)
    assert len(examples) >= 2, "each specialist needs an output schema and at least one worked example"
    assert all(e["specialist"] == specialist for e in examples)


@pytest.mark.parametrize("specialist", SPECIALISTS)
def test_tool_table_matches_the_registry_exactly(specialist):
    text = load_prompt(specialist).text
    section = text.split("## How", 1)[0]  # the tools table comes before "How to work" / "How to write"
    listed = {name for name in TOOLS if f"`{name}`" in section}
    assert listed == set(SPECIALIST_TOOLS[specialist])


def test_manager_knows_every_specialist_and_their_tools():
    text = load_prompt("incident_manager").text
    for specialist, tools in SPECIALIST_TOOLS.items():
        assert f"`{specialist}`" in text
        assert all(f"`{tool}`" in text for tool in tools)


def test_every_citation_in_every_example_exists():
    known = known_source_ids()
    texts = [load_prompt(n).text for n in AGENTS] + [load_ledger_prompts()[0]["final_answer_prompt"].format(**FILLED)]
    cited = set(re.findall(r'"source_id": "([^"]+)"', "\n".join(texts)))
    # Schema templates use placeholders ("...", "<doc_id>", "id from a tool"); examples use real ids.
    cited = {c for c in cited if ":" in c and " " not in c and not c.startswith("<")}
    assert cited and cited <= known, f"unknown citations in prompts: {sorted(cited - known)}"


def test_communicator_examples_respect_its_own_limits():
    for example in json_blocks(load_prompt("communicator").text)[1:]:
        notice = example["notice"]
        assert len(notice["subject"]) <= 80 and len(notice["body"].split()) <= 120
        assert "[PII_" not in notice["body"] and "tech-" not in notice["body"]
