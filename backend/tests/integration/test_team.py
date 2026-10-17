"""The Magentic incident team, end to end with scripted models (FR-21, FR-22, FR-23, FR-29).

Real orchestration, real harness specialists, real read-only tools on the synthetic world, real
budgets and decision trail; only the models' words are scripted.
"""

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio

from backend.app.agents.knowledge import KnowledgeSearch
from backend.app.agents.scripted import ScriptedChatClient, progress
from backend.app.agents.team import MANAGER, MagenticIncidentTeam, TeamBudgets
from backend.app.core.config import Settings
from backend.app.services.agent_monitoring import SqlAgentRunRecorder
from backend.app.services.incident_worker import IncidentContext
from backend.app.services.world import load_world

from .conftest import scalar
from .test_operations import make_user

PLAN = {
    "summary": "Boiler HX-200 is losing pressure (E04 again); test the expansion vessel.",
    "claims": [
        {"statement": "E04 means pressure below 0.8 bar.", "specialist": "diagnostician",
         "citations": [{"source_type": "manual", "source_id": "manual:boiler-hx200", "section": "6.2 Error codes"}]},
        {"statement": "E04 was repaired 12 days ago.", "specialist": "asset_historian",
         "citations": [{"source_type": "record", "source_id": "work_order:wo-1042", "section": None}]},
        {"statement": "An invented citation must not survive.", "specialist": "diagnostician",
         "citations": [{"source_type": "manual", "source_id": "manual:made-up", "section": "1"}]},
    ],
    "actions": [{"type": "dispatch_technician", "description": "Dispatch tech-001", "estimated_cost": 120}],
    "safety_block": False,
    "safety_notes": None,
    "open_questions": [],
}


def fake_knowledge() -> KnowledgeSearch:
    async def retrieve(query):
        return [{"doc_id": "manual:boiler-hx200", "doc_type": "manual", "title": "HX-200", "section": "6.2 Error codes",
                 "models": ["HX-200"], "content": "E04: low system pressure, below 0.8 bar."}]

    async def screen(texts):
        return [False] * len(texts)

    return KnowledgeSearch(retrieve, screen)


def context() -> IncidentContext:
    return IncidentContext(
        incident_id=uuid4(), request_id=uuid4(), escalation_reason="high_priority_equipment",
        redacted_description="Boiler in plant room B shows E04 again; [PII_0001] says radiators are cold.",
        translated_description=None, category="hvac", priority=4,
    )


def clients(manager_replies, specialists):
    scripted = {MANAGER: ScriptedChatClient(manager_replies)}
    for name in ("asset_historian", "diagnostician", "safety_compliance", "procurement", "scheduler", "communicator"):
        scripted[name] = ScriptedChatClient(specialists.get(name, []))
    return scripted


def team(session_factory, scripted, **budgets) -> MagenticIncidentTeam:
    return MagenticIncidentTeam(
        settings=Settings(_env_file=None, app_env="test"),
        session_factory=session_factory,
        client_for=scripted.__getitem__,
        recorder=SqlAgentRunRecorder(session_factory),
        knowledge=fake_knowledge(),
        budgets=TeamBudgets(**budgets),
    )


@pytest_asyncio.fixture
async def world(session_factory):
    await make_user(session_factory, "tech", "technician")
    async with session_factory() as session:
        await load_world(session)


GROUNDED_RUN = (
    ["FACTS", "PLAN",
     progress("asset_historian", "Find the boiler in plant room B and its history."),
     progress("diagnostician", "What does E04 mean on the HX-200 (asset-hv-boiler-1)?"),
     progress("diagnostician", "done", done=True),
     PLAN],
    {
        "asset_historian": [
            {"call": "find_assets", "args": {"category": "hvac", "text": "boiler plant room B"}},
            {"call": "get_maintenance_history", "args": {"asset_id": "asset-hv-boiler-1"}},
            {"specialist": "asset_historian", "asset_id": "asset-hv-boiler-1", "claims": [], "repeat_failure": False, "open_questions": [], "confidence": 0.9},
        ],
        "diagnostician": [
            {"call": "search_manuals", "args": {"query": "E04 low pressure", "model": "HX-200"}},
            {"specialist": "diagnostician", "likely_cause": "expansion vessel", "claims": [], "recommended_checks": [], "open_questions": [], "confidence": 0.8},
        ],
    },
)


@pytest.mark.asyncio
async def test_team_produces_a_grounded_plan_with_every_step_recorded(world, session_factory):
    scripted = clients(*GROUNDED_RUN)
    outcome = await team(session_factory, scripted).investigate(context())

    assert outcome.reason_code is None and outcome.plan is not None
    # The invented citation was removed: the claim stays, but now shows as unverified.
    assert [len(c.citations) for c in outcome.plan.claims] == [1, 1, 0]
    assert outcome.plan.unverified_claims == 1

    steps = [s.step for s in outcome.steps]
    assert steps[:3] == ["tool.find_assets", "tool.get_maintenance_history", "tool.search_manuals"]
    assert "team.plan_created" in steps and steps.count("team.round") == 3
    assert "team.citations_removed" in steps and steps[-1] == "team.finished"

    # The specialists only got the manager's instruction, and every scripted reply was used.
    assert all(client.remaining == 0 for client in scripted.values())
    # Every model call was recorded, with its content and the prompt version.
    runs = await scalar(session_factory, "SELECT count(*) FROM agent_runs WHERE prompt_sha IS NOT NULL")
    contents = await scalar(session_factory, "SELECT count(*) FROM agent_run_contents")
    assert runs >= 6 and contents == runs


@pytest.mark.asyncio
async def test_a_plan_that_is_not_valid_json_goes_to_a_person(world, session_factory):
    manager, specialists = GROUNDED_RUN
    scripted = clients([*manager[:-1], "I think we should replace the boiler."], specialists)
    outcome = await team(session_factory, scripted).investigate(context())
    assert outcome.plan is None and outcome.reason_code == "invalid_plan"


@pytest.mark.asyncio
async def test_the_token_budget_stops_the_run(world, session_factory):
    scripted = clients(*GROUNDED_RUN)
    outcome = await team(session_factory, scripted, max_tokens=600).investigate(context())
    assert outcome.plan is None and outcome.reason_code == "token_budget_exhausted"
    assert outcome.steps[-1].details["tokens"] >= 600


@pytest.mark.asyncio
async def test_the_token_budget_holds_when_responses_report_no_usage(world, session_factory):
    manager, specialists = GROUNDED_RUN
    scripted = {name: ScriptedChatClient(c._replies, usage=None) for name, c in clients(manager, specialists).items()}
    outcome = await team(session_factory, scripted, max_tokens=5_000).investigate(context())
    finished = outcome.steps[-1].details
    assert outcome.plan is None and outcome.reason_code == "token_budget_exhausted"
    assert finished["tokens"] >= 5_000 and finished["tokens_estimated_calls"] >= 1


@pytest.mark.asyncio
async def test_the_time_budget_stops_the_run(world, session_factory):
    class Slow(ScriptedChatClient):
        def _next(self, messages):
            import time
            time.sleep(0.3)
            return super()._next(messages)

    manager, specialists = GROUNDED_RUN
    scripted = clients(manager, specialists)
    scripted[MANAGER] = Slow(manager)
    outcome = await team(session_factory, scripted, timeout_seconds=0.5).investigate(context())
    assert outcome.reason_code == "team_timeout"
