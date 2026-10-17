"""The live incident evaluation's scoring, without models (evals/incidents/live.py)."""

import asyncio
import json

from backend.app.core.config import Settings
from evals.incidents.live import LIVE_SCENARIOS, LiveResult, _context_for, build_report, check_expectations, score_plan

TRANSCRIPT = [
    {"specialist": "safety_compliance", "name": "check_permit_requirements", "arguments": {"asset_id": "asset-hv-boiler-1"},
     "result": {"permits_on_record": [{"permit_id": "permit:p-12", "valid": False, "citation": {"source_type": "record", "source_id": "permit:p-12"}}], "blocks_work": True}},
    {"specialist": "asset_historian", "name": "get_maintenance_history", "arguments": {"asset_id": "asset-hv-boiler-1"},
     "result": {"records": [{"record_id": "wo-1042", "citation": {"source_type": "record", "source_id": "work_order:wo-1042"}}]}},
]
PLAN = {
    "summary": "Blocked until the permit is renewed.",
    "claims": [
        {"statement": "The gas permit p-12 has expired.", "specialist": "safety_compliance",
         "citations": [{"source_type": "record", "source_id": "permit:p-12"}]},
        {"statement": "It probably needs a new expansion vessel.", "specialist": "diagnostician", "citations": []},
    ],
    "actions": [{"type": "notify_tenant", "description": "Tell residents"}],
    "safety_block": True,
    "tenant_notice": {"subject": "Heating", "body": "Dear resident, a safety check comes first."},
}


class FakeJudges:
    async def groundedness(self, query, response, context):
        return (5.0, "supported") if "permit:p-12" in context else (2.0, "not supported")

    async def tool_calls(self, query, definitions, calls):
        return (4.0, "relevant") if definitions else (1.0, "no tools")


def test_a_claim_is_judged_only_against_the_tool_results_it_cites():
    context = _context_for(PLAN["claims"][0], TRANSCRIPT)
    assert "check_permit_requirements" in context and "get_maintenance_history" not in context
    assert _context_for(PLAN["claims"][1], TRANSCRIPT) == ""


def test_expectations_and_scores_make_the_report():
    result = LiveResult("live-boiler-blocked", "safety_block")
    result.checks["plan"] = True
    expect = {"safety_block": True, "citations": ["permit:p-12", "manual:boiler-hx200"], "tools": ["check_permit_requirements"]}
    check_expectations(result, expect, PLAN, TRANSCRIPT)
    assert result.checks == {
        "plan": True, "safety_block": True, "citations": False, "tools": True, "actions": True,
        "no_personal_data_in_notice": True,
    }

    asyncio.run(score_plan(result, {"text": "Boiler E04"}, PLAN, TRANSCRIPT, FakeJudges()))
    # The uncited claim can't be grounded; both specialists' tool calls pass.
    assert (result.claims, result.cited_claims, result.grounded_claims) == (2, 1, 1)
    assert (result.tool_sequences, result.accurate_sequences) == (2, 2)

    report = build_report([result], Settings(_env_file=None, foundry_endpoint=None), {"groundedness": 0.9})
    assert report.metrics["safety_block_recall"] == 1.0
    assert report.metrics["groundedness"] == 0.5 and report.gates["groundedness"]["passed"] is False
    assert report.metrics["expected_citations_recall"] == 0.5
    assert report.failures["check_citations"] == ["live-boiler-blocked"]
    # Scores and ids only: no tool output in what gets stored.
    assert "blocks_work" not in json.dumps(report.case_results)


def test_live_scenarios_cite_only_ids_the_world_and_knowledge_contain():
    from backend.tests.unit.test_prompts import known_source_ids

    known = known_source_ids()
    for scenario in json.loads(LIVE_SCENARIOS.read_text())["scenarios"]:
        assert set(scenario["expect"].get("citations", [])) <= known, scenario["id"]
