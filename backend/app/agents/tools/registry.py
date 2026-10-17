"""Which specialist may use which tool (least privilege, spec 5.2).

A specialist is built with only its own tools; the manager and the communicator have none.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.agents.tools import facility, knowledge_tools
from backend.app.agents.tools.runtime import ToolFunction


@dataclass(frozen=True)
class ToolSpec:
    name: str
    function: ToolFunction
    # What the model reads to decide when to call it.
    description: str
    # Searches the knowledge base instead of the database.
    uses_knowledge: bool = False


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec("find_assets", facility.find_assets, "Find equipment by category, building or a few words of kind, model or location."),
        ToolSpec("get_asset", facility.get_asset, "Details of one asset: model, location, age, criticality, manual id."),
        ToolSpec("get_maintenance_history", facility.get_maintenance_history, "Past work on an asset and whether it is failing repeatedly."),
        ToolSpec("find_parts", facility.find_parts, "Parts that fit an equipment model, with stock, cost and lead time."),
        ToolSpec("get_vendor_terms", facility.get_vendor_terms, "Contracted vendors for a trade: response time, call-out fee, contract."),
        ToolSpec("find_available_technicians", facility.find_available_technicians, "Technicians with a skill available within a number of hours."),
        ToolSpec("check_permit_requirements", facility.check_permit_requirements, "Permits work on an asset needs, and whether a valid one is on record."),
        ToolSpec("search_manuals", knowledge_tools.search_manuals, "Equipment manual passages (error codes, diagnosis, service steps), with citations.", uses_knowledge=True),
        ToolSpec("get_safety_procedure", knowledge_tools.get_safety_procedure, "Safety and lockout/tagout procedures (permits, isolation), with citations.", uses_knowledge=True),
    )
}

SPECIALIST_TOOLS: dict[str, tuple[str, ...]] = {
    "diagnostician": ("search_manuals", "find_assets", "get_asset"),
    "asset_historian": ("find_assets", "get_asset", "get_maintenance_history"),
    "safety_compliance": ("get_safety_procedure", "get_asset", "check_permit_requirements"),
    "procurement": ("get_asset", "find_parts", "get_vendor_terms"),
    "scheduler": ("find_available_technicians",),
    "communicator": (),
}


def tools_for(specialist: str) -> list[ToolSpec]:
    return [TOOLS[name] for name in SPECIALIST_TOOLS[specialist]]
