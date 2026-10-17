import pytest
from pydantic import ValidationError

from backend.app.domain.incident_plan import IncidentPlan

GROUNDED = {
    "summary": "Repressurise the boiler and dispatch an HVAC technician.",
    "claims": [
        {"statement": "E04 means low pressure.", "specialist": "diagnostician",
         "citations": [{"source_type": "manual", "source_id": "manual:boiler-hx200", "section": "6.2"}]}
    ],
    "actions": [{"type": "dispatch_technician", "description": "HVAC technician today", "estimated_cost": 120}],
}


def test_a_grounded_affordable_plan_has_no_flags():
    plan = IncidentPlan.model_validate(GROUNDED)
    assert plan.flags() == [] and plan.estimated_total_cost == 120 and plan.claims[0].verified


def test_flags_for_safety_cost_and_missing_citations():
    plan = IncidentPlan.model_validate(
        {
            **GROUNDED,
            "claims": [{"statement": "Probably the valve.", "specialist": "diagnostician"}],
            "actions": [{"type": "order_part", "description": "Compressor", "estimated_cost": 4200}],
            "safety_block": True,
        }
    )
    assert plan.flags() == ["safety_block", "high_cost", "unverified_claims"]


def test_stored_plans_round_trip_without_trusting_computed_fields():
    stored = IncidentPlan.model_validate(GROUNDED).model_dump(mode="json")
    stored["estimated_total_cost"] = 0  # tampered or stale: derived again on load
    assert IncidentPlan.from_stored(stored).estimated_total_cost == 120


def test_model_output_outside_the_schema_is_refused():
    with pytest.raises(ValidationError):
        IncidentPlan.model_validate({**GROUNDED, "actions": [{"type": "wire_money", "description": "x"}]})
    with pytest.raises(ValidationError):
        IncidentPlan.model_validate({**GROUNDED, "approved": True})
