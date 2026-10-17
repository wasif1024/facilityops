"""Live incident evaluation (spec 10.1, FR-29): the real team, real models, Foundry evaluators.

Each scenario in ``live_scenarios.json`` is escalated in a scratch database loaded with the
synthetic world, and the real incident worker runs the real Magentic team on it (outbox, event
bus, Foundry IQ knowledge, read-only tools, budgets). The stored plan is then scored:

- **Checks** per scenario (deterministic): a plan was produced; the safety block is right; the
  expected citations, tools and actions are present.
- **Groundedness** (Foundry ``GroundednessEvaluator``): each claim is judged against the tool
  results it cites. A claim with no citation, or citing nothing a tool returned, is ungrounded.
- **Tool-call accuracy** (Foundry ``ToolCallAccuracyEvaluator``): each specialist's tool calls
  are judged against the incident, the specialist's role and its tool definitions.

Both evaluators score 1 to 5; a claim or a specialist passes at the evaluator's default threshold
(3), and the metric is the share that pass. Mean scores are reported too. The judge is the
manager model (gpt-5.2) through the same Foundry resource, with Entra ID.

Tool results exist only in memory during the run (``ToolContext.transcript``); the report holds
scores and ids, never tool output.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.config import Settings
from backend.app.db.models import IncidentPlanRecord, IncidentRecord
from backend.app.domain.intake import MaintenanceCategory
from backend.app.services.incident_worker import build_dispatcher
from backend.app.services.world import load_world
from evals.incidents.harness import drain, escalated_request
from evals.run_eval import Report

LIVE_SCENARIOS = Path(__file__).with_name("live_scenarios.json")
PROMPTS = Path(__file__).resolve().parents[2] / "backend" / "app" / "prompts"
PASS_SCORE = 3
_CONTEXT_CHARS = 12_000


@dataclass
class LiveResult:
    id: str
    family: str
    checks: dict[str, bool] = field(default_factory=dict)
    claims: int = 0
    cited_claims: int = 0
    grounded_claims: int = 0
    groundedness_scores: list[float] = field(default_factory=list)
    tool_sequences: int = 0
    accurate_sequences: int = 0
    tool_call_scores: list[float] = field(default_factory=list)
    expected_tools: int = 0
    found_tools: int = 0
    expected_citations: int = 0
    found_citations: int = 0
    expected_actions: int = 0
    found_actions: int = 0
    tokens: int = 0
    seconds: float = 0
    rounds: int = 0
    tool_calls: int = 0
    safety_block: bool | None = None
    status: str = ""
    reason_code: str | None = None
    # Why a claim or tool sequence didn't pass: the evaluator's short reason, for the report.
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


def evaluator_config(settings: Settings) -> dict[str, str]:
    """The Azure OpenAI endpoint of the Foundry resource behind FOUNDRY_ENDPOINT."""
    resource = urlparse(settings.foundry_endpoint).hostname.split(".")[0]
    return {
        "azure_endpoint": f"https://{resource}.openai.azure.com/",
        "azure_deployment": settings.incident_manager_model,
        "api_version": "2025-04-01-preview",
    }


class Judges:
    """Foundry evaluators, called off the event loop (they are synchronous)."""

    def __init__(self, settings: Settings, concurrency: int = 4) -> None:
        from azure.ai.evaluation import GroundednessEvaluator, ToolCallAccuracyEvaluator
        from azure.identity import DefaultAzureCredential

        config, credential = evaluator_config(settings), DefaultAzureCredential()
        self._groundedness = GroundednessEvaluator(config, credential=credential, is_reasoning_model=True)
        self._tool_calls = ToolCallAccuracyEvaluator(config, credential=credential, is_reasoning_model=True)
        self._limit = asyncio.Semaphore(concurrency)

    async def groundedness(self, query: str, response: str, context: str) -> tuple[float, str]:
        async with self._limit:
            out = await asyncio.to_thread(self._groundedness, query=query, response=response, context=context)
        return float(out.get("groundedness") or 0), str(out.get("groundedness_reason", ""))[:240]

    async def tool_calls(self, query: str, definitions: list[dict], calls: list[dict]) -> tuple[float, str]:
        async with self._limit:
            out = await asyncio.to_thread(
                self._tool_calls, query=query, tool_definitions=definitions, tool_calls=calls
            )
        return float(out.get("tool_call_accuracy") or 0), str(out.get("tool_call_accuracy_reason", ""))[:240]


def _tool_definitions(specialist: str) -> list[dict[str, Any]]:
    from backend.app.agents.team import tool_schema
    from backend.app.agents.tools.registry import tools_for

    return [
        {"name": s.name, "description": s.description, "parameters": tool_schema(s).model_json_schema()}
        for s in tools_for(specialist)
    ]


def _context_for(claim: dict[str, Any], transcript: list[dict[str, Any]]) -> str:
    """The tool results that returned any of the claim's citations."""
    ids = {c["source_id"] for c in claim.get("citations", [])}
    parts = []
    for call in transcript:
        dumped = json.dumps(call["result"], default=str)
        if any(f'"{source_id}"' in dumped for source_id in ids):
            parts.append(f"{call['name']}({json.dumps(call['arguments'], default=str)}) -> {dumped}")
    return "\n".join(parts)[:_CONTEXT_CHARS]


async def score_plan(
    result: LiveResult, scenario: dict[str, Any], plan: dict[str, Any], transcript: list[dict[str, Any]], judges: Judges
) -> None:
    query = f"Facility maintenance incident: {scenario['text']}"
    claims = plan.get("claims", [])
    result.claims = len(claims)
    result.cited_claims = sum(bool(c.get("citations")) for c in claims)

    async def judge_claim(claim: dict[str, Any]) -> None:
        context = _context_for(claim, transcript)
        if not context:
            result.groundedness_scores.append(1.0)
            result.notes.append(f"ungrounded (no cited tool result): {claim['statement'][:80]}")
            return
        score, reason = await judges.groundedness(query, claim["statement"], context)
        result.groundedness_scores.append(score)
        if score >= PASS_SCORE:
            result.grounded_claims += 1
        else:
            result.notes.append(f"groundedness {score:.0f}: {reason}")

    by_specialist: dict[str, list[dict[str, Any]]] = {}
    for call in transcript:
        by_specialist.setdefault(call["specialist"], []).append(call)

    async def judge_tools(specialist: str, calls: list[dict[str, Any]]) -> None:
        from backend.app.agents.team import _description

        role = f"{query}\n\nThe agent is the {specialist} specialist: {_description(specialist)}"
        formatted = [
            {"type": "tool_call", "tool_call_id": f"call_{i}", "name": c["name"], "arguments": c["arguments"]}
            for i, c in enumerate(calls)
        ]
        score, reason = await judges.tool_calls(role, _tool_definitions(specialist), formatted)
        result.tool_call_scores.append(score)
        if score >= PASS_SCORE:
            result.accurate_sequences += 1
        else:
            result.notes.append(f"tool calls {specialist} {score:.0f}: {reason}")

    result.tool_sequences = len(by_specialist)
    await asyncio.gather(
        *(judge_claim(c) for c in claims), *(judge_tools(s, calls) for s, calls in by_specialist.items())
    )


def check_expectations(result: LiveResult, expect: dict[str, Any], plan: dict[str, Any], transcript: list[dict]) -> None:
    cited = {c["source_id"] for claim in plan.get("claims", []) for c in claim.get("citations", [])}
    called = {c["name"] for c in transcript}
    actions = plan.get("actions", [])

    result.safety_block = bool(plan.get("safety_block"))
    result.checks["safety_block"] = result.safety_block == expect["safety_block"]
    want_citations = expect.get("citations", [])
    result.expected_citations, result.found_citations = len(want_citations), len(set(want_citations) & cited)
    result.checks["citations"] = result.found_citations == result.expected_citations
    want_tools = expect.get("tools", [])
    result.expected_tools, result.found_tools = len(want_tools), len(set(want_tools) & called)
    result.checks["tools"] = result.found_tools == result.expected_tools
    want_actions = expect.get("actions", [])
    result.expected_actions = len(want_actions)
    result.found_actions = sum(
        any(a["type"] == w["type"] and all(a.get(k) == v for k, v in w.items()) for a in actions) for w in want_actions
    )
    result.checks["actions"] = result.found_actions == result.expected_actions
    # Notices never carry personal data, and nothing in the plan guesses what a placeholder hides.
    result.checks["no_personal_data_in_notice"] = "[PII_" not in json.dumps(plan.get("tenant_notice") or {})


async def run_live_scenario(
    session_factory: async_sessionmaker[AsyncSession],
    scenario: dict[str, Any],
    settings: Settings,
    judges: Judges,
) -> LiveResult:
    from backend.app.agents.knowledge import azure_knowledge
    from backend.app.agents.team import MagenticIncidentTeam, foundry_clients

    transcript: list[dict[str, Any]] = []
    team = MagenticIncidentTeam(
        settings=settings,
        session_factory=session_factory,
        client_for=foundry_clients(settings),
        knowledge=azure_knowledge(settings) if settings.azure_search_endpoint else None,
        tool_transcript=transcript,
    )
    result = LiveResult(scenario["id"], scenario["family"])
    request_id = await escalated_request(
        session_factory, scenario["text"], MaintenanceCategory(scenario["category"]), scenario["priority"]
    )
    await drain(build_dispatcher(session_factory, team))

    async with session_factory() as session:
        incident = await session.scalar(select(IncidentRecord).where(IncidentRecord.request_id == request_id))
        record = await session.scalar(
            select(IncidentPlanRecord)
            .where(IncidentPlanRecord.incident_id == incident.id)
            .order_by(IncidentPlanRecord.version.desc())
            .limit(1)
        )
        finished = await session.scalar(
            text(
                "SELECT details FROM decision_steps WHERE request_id = :r AND step = 'team.finished' "
                "ORDER BY sequence DESC LIMIT 1"
            ),
            {"r": request_id},
        )
    result.status, result.reason_code = incident.status, incident.reason_code
    if finished:
        result.tokens, result.seconds = finished.get("tokens", 0), finished.get("seconds", 0)
        result.rounds, result.tool_calls = finished.get("rounds", 0), finished.get("tool_calls", 0)
    result.checks["plan"] = record is not None
    if record is None:
        result.notes.append(f"no plan: {incident.reason_code}")
        return result
    check_expectations(result, scenario["expect"], record.plan, transcript)
    await score_plan(result, scenario, record.plan, transcript, judges)
    return result


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def build_report(results: list[LiveResult], settings: Settings, gates: dict[str, float]) -> Report:
    report = Report(mode="live")
    blocked = [r for r in results if r.family in {"safety_block", "injection"}]
    unblocked = [r for r in results if r.family not in {"safety_block", "injection"}]
    planned = [r for r in results if r.checks.get("plan")]
    sum_ = lambda attr: sum(getattr(r, attr) for r in results)  # noqa: E731
    report.metrics = {
        "scenarios_passed": _ratio(sum(r.passed for r in results), len(results)),
        "plan_rate": _ratio(len(planned), len(results)),
        "safety_block_recall": _ratio(sum(bool(r.safety_block) for r in blocked), len(blocked)),
        "false_safety_blocks": sum(bool(r.safety_block) for r in unblocked),
        "groundedness": _ratio(sum_("grounded_claims"), sum_("claims")),
        "groundedness_mean_score": _mean([s for r in results for s in r.groundedness_scores]),
        "tool_call_accuracy": _ratio(sum_("accurate_sequences"), sum_("tool_sequences")),
        "tool_call_accuracy_mean_score": _mean([s for r in results for s in r.tool_call_scores]),
        "claims_with_citation": _ratio(sum_("cited_claims"), sum_("claims")),
        "expected_tools_recall": _ratio(sum_("found_tools"), sum_("expected_tools")),
        "expected_citations_recall": _ratio(sum_("found_citations"), sum_("expected_citations")),
        "expected_actions_recall": _ratio(sum_("found_actions"), sum_("expected_actions")),
        "tokens_per_incident": _mean([r.tokens for r in planned]),
        "seconds_per_incident": _mean([r.seconds for r in planned]),
        "rounds_per_incident": _mean([r.rounds for r in planned]),
        "tool_calls_per_incident": _mean([r.tool_calls for r in planned]),
    }
    for metric, threshold in gates.items():
        value = report.metrics.get(metric)
        report.gates[metric] = {"threshold": threshold, "value": value, "passed": value is not None and value >= threshold}
    report.failures = {
        f"check_{name}": [r.id for r in results if r.checks.get(name) is False]
        for name in sorted({n for r in results for n in r.checks})
    }
    prompt_sha = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(PROMPTS.glob("*.md")))).hexdigest()[:12]
    report.metadata = {
        "dataset": LIVE_SCENARIOS.name,
        "dataset_sha": hashlib.sha256(LIVE_SCENARIOS.read_bytes()).hexdigest()[:12],
        "cases": len(results),
        "model": f"{settings.incident_manager_model} + {settings.incident_specialist_model}",
        "prompt_sha": prompt_sha,
        "judge": settings.incident_manager_model,
        "pass_score": PASS_SCORE,
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    report.case_results = [
        {
            "case_id": r.id,
            "tags": [r.family],
            "expected": {},
            "predicted": {
                "status": r.status,
                "reason_code": r.reason_code,
                "safety_block": r.safety_block,
                "claims": r.claims,
                "grounded_claims": r.grounded_claims,
                "tool_sequences": r.tool_sequences,
                "accurate_sequences": r.accurate_sequences,
                "tokens": r.tokens,
                "seconds": r.seconds,
                "notes": r.notes[:10],
            },
            "failures": [name for name, ok in r.checks.items() if not ok],
            "passed": r.passed,
        }
        for r in results
    ]
    return report


async def run_live(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings, gates: dict[str, float], only: str | None = None
) -> tuple[Report, list[LiveResult]]:
    if not (settings.foundry_endpoint and settings.incident_team_enabled):
        raise SystemExit("Live mode needs FOUNDRY_ENDPOINT and the incident team enabled")
    async with session_factory() as session:
        await load_world(session)
    scenarios = json.loads(LIVE_SCENARIOS.read_text())["scenarios"]
    if only:
        scenarios = [s for s in scenarios if s["id"] == only]
    judges = Judges(settings)
    # One incident at a time: the runs share the model quota and the timings stay comparable.
    results = [await run_live_scenario(session_factory, s, settings, judges) for s in scenarios]
    return build_report(results, settings, gates), results
