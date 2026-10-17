"""Knowledge tools (spec 5.4): manuals for the diagnostician, procedures for safety & compliance.

Both search the Foundry IQ knowledge base; every passage is screened by Prompt Shields before it
is returned (see backend/app/agents/knowledge.py), and each comes with a citation to its
document and section.
"""

from __future__ import annotations

from typing import Any

from backend.app.agents.knowledge import KnowledgeSearch


async def search_manuals(knowledge: KnowledgeSearch, *, query: str, model: str | None = None) -> dict[str, Any]:
    """Equipment manual passages: error codes, diagnosis, parts, service steps."""
    return await knowledge.search(query, doc_types=["manual"], model=model)


async def get_safety_procedure(knowledge: KnowledgeSearch, *, query: str, model: str | None = None) -> dict[str, Any]:
    """Safety and lockout/tagout procedure passages: permits, isolation, verification."""
    return await knowledge.search(query, doc_types=["procedure"], model=model)
