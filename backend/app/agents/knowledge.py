"""The incident team's knowledge: synthetic manuals and procedures in a Foundry IQ knowledge base.

Built on Azure AI Search agentic retrieval, generally available API 2026-04-01, in its minimal
*extractive* mode: the knowledge base returns the relevant passages and our own agents reason
over them. No language model runs inside the search service, so no extra identity or role is
needed, and every call is covered by the GA service level.

- ``setup_knowledge`` (``scripts/knowledge.sh setup``): index, passages, knowledge source and
  knowledge base, created or updated idempotently with Entra ID.
- ``KnowledgeSearch``: what the ``search_manuals`` and ``get_safety_procedure`` tools call. Every
  retrieved passage is screened by Prompt Shields as a *document* (indirect prompt injection)
  before an agent sees it; a flagged passage is dropped and counted, never shown.

Documents live in backend/app/fixtures/knowledge/ (all invented).
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.core.config import Settings
from backend.app.domain.decision_trail import note

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge"
MAX_PASSAGE_CHARS = 1_200
MAX_RESULTS = 5


@dataclass(frozen=True)
class Passage:
    key: str
    doc_id: str
    doc_type: str  # manual, procedure or note (untrusted)
    title: str
    section: str
    category: str
    models: list[str]
    content: str

    def as_index_document(self) -> dict[str, Any]:
        return {
            "id": self.key,
            "doc_id": self.doc_id,
            "doc_type": self.doc_type,
            "title": self.title,
            "section": self.section,
            "category": self.category,
            "models": self.models,
            "content": self.content,
        }


def _key(doc_id: str, section: str) -> str:
    """Index keys allow letters, digits, dash, underscore and equals only."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", f"{doc_id}--{section}").strip("-").lower()[:200]


def parse_documents(directory: Path = KNOWLEDGE_DIR) -> list[Passage]:
    """One passage per ``##`` section of every document (README files are skipped)."""
    passages: list[Passage] = []
    for path in sorted(directory.rglob("*.md")):
        if path.name == "README.md":
            continue
        text = path.read_text()
        meta_block, _, body = text.partition("\n---\n")
        meta = dict(
            line.split(": ", 1) for line in meta_block.removeprefix("---\n").splitlines() if ": " in line
        )
        for chunk in re.split(r"^## ", body, flags=re.M)[1:]:
            section, _, content = chunk.partition("\n")
            passages.append(
                Passage(
                    key=_key(meta["id"], section),
                    doc_id=meta["id"],
                    doc_type=meta["type"],
                    title=meta["title"],
                    section=section.strip(),
                    category=meta["category"],
                    models=[m.strip() for m in meta.get("models", "").split(",") if m.strip()],
                    content=" ".join(content.split()),
                )
            )
    return passages


def setup_knowledge(settings: Settings, directory: Path = KNOWLEDGE_DIR) -> dict[str, Any]:
    """Create or update the index, its passages, the knowledge source and the knowledge base."""
    if not settings.azure_search_endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is not configured")
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.models import (
        KnowledgeBase,
        KnowledgeSourceReference,
        SearchableField,
        SearchFieldDataType,
        SearchIndex,
        SearchIndexFieldReference,
        SearchIndexKnowledgeSource,
        SearchIndexKnowledgeSourceParameters,
        SemanticConfiguration,
        SemanticField,
        SemanticPrioritizedFields,
        SemanticSearch,
        SimpleField,
    )

    credential = DefaultAzureCredential()
    endpoint = settings.azure_search_endpoint
    index_client = SearchIndexClient(endpoint=endpoint, credential=credential)
    semantic_config = "passages"
    index_client.create_or_update_index(
        SearchIndex(
            name=settings.knowledge_index_name,
            fields=[
                SimpleField(name="id", type=SearchFieldDataType.String, key=True),
                SimpleField(name="doc_id", type=SearchFieldDataType.String, filterable=True),
                SimpleField(name="doc_type", type=SearchFieldDataType.String, filterable=True, facetable=True),
                SearchableField(name="title", type=SearchFieldDataType.String),
                SearchableField(name="section", type=SearchFieldDataType.String),
                SimpleField(name="category", type=SearchFieldDataType.String, filterable=True),
                SearchableField(
                    name="models", collection=True, type=SearchFieldDataType.Collection(SearchFieldDataType.String), filterable=True
                ),
                SearchableField(name="content", type=SearchFieldDataType.String),
            ],
            semantic_search=SemanticSearch(
                configurations=[
                    SemanticConfiguration(
                        name=semantic_config,
                        prioritized_fields=SemanticPrioritizedFields(
                            title_field=SemanticField(field_name="title"),
                            content_fields=[SemanticField(field_name="content")],
                            keywords_fields=[SemanticField(field_name="section"), SemanticField(field_name="models")],
                        ),
                    )
                ],
                default_configuration_name=semantic_config,
            ),
        )
    )
    passages = parse_documents(directory)
    SearchClient(endpoint=endpoint, index_name=settings.knowledge_index_name, credential=credential).merge_or_upload_documents(
        [p.as_index_document() for p in passages]
    )
    index_client.create_or_update_knowledge_source(
        SearchIndexKnowledgeSource(
            name=settings.knowledge_source_name,
            description="Synthetic equipment manuals, safety procedures and untrusted notes for FacilityOps AI.",
            search_index_parameters=SearchIndexKnowledgeSourceParameters(
                search_index_name=settings.knowledge_index_name,
                semantic_configuration_name=semantic_config,
                source_data_fields=[
                    SearchIndexFieldReference(name=name)
                    for name in ("doc_id", "doc_type", "title", "section", "models", "content")
                ],
            ),
        )
    )
    index_client.create_or_update_knowledge_base(
        KnowledgeBase(
            name=settings.knowledge_base_name,
            description="Knowledge for the FacilityOps incident team: manuals and safety procedures.",
            knowledge_sources=[KnowledgeSourceReference(name=settings.knowledge_source_name)],
        )
    )
    return {
        "index": settings.knowledge_index_name,
        "passages": len(passages),
        "documents": len({p.doc_id for p in passages}),
        "knowledge_source": settings.knowledge_source_name,
        "knowledge_base": settings.knowledge_base_name,
    }


Retriever = Callable[[str], Awaitable[list[dict[str, Any]]]]
Screener = Callable[[Sequence[str]], Awaitable[list[bool]]]


class KnowledgeSearch:
    """Retrieve passages, screen them with Prompt Shields, and return them with citations.

    ``retriever`` returns raw passages ({doc_id, doc_type, title, section, models, content});
    ``screener`` returns, per passage text, whether an injected instruction was detected.
    Both are injectable so the logic is tested without Azure.
    """

    def __init__(self, retriever: Retriever, screener: Screener) -> None:
        self._retrieve = retriever
        self._screen = screener

    async def search(
        self, query: str, *, doc_types: Sequence[str], model: str | None = None
    ) -> dict[str, Any]:
        raw = await self._retrieve(query)
        candidates = [
            p for p in raw if p.get("doc_type") in doc_types or p.get("doc_type") == "note"
        ]
        if model:
            # Passages for the asked-for model first; general ones (no model) stay eligible.
            candidates.sort(key=lambda p: model not in (p.get("models") or []))
        candidates = candidates[: MAX_RESULTS * 2]
        flags = await self._screen([p.get("content", "") for p in candidates]) if candidates else []
        kept, dropped = [], 0
        for passage, attack in zip(candidates, flags, strict=True):
            # Untrusted notes are never returned, even when they look harmless; attacks never are.
            if attack or passage.get("doc_type") == "note":
                dropped += attack
                continue
            kept.append(
                {
                    "doc_id": passage["doc_id"],
                    "title": passage["title"],
                    "section": passage["section"],
                    "text": passage["content"][:MAX_PASSAGE_CHARS],
                    "citation": {
                        "source_type": passage["doc_type"],
                        "source_id": passage["doc_id"],
                        "section": passage["section"],
                    },
                }
            )
        kept = kept[:MAX_RESULTS]
        note(passages_retrieved=len(raw), passages_returned=len(kept), injected_passages_dropped=dropped)
        return {"passages": kept, "injected_passages_dropped": dropped}


def azure_knowledge(settings: Settings) -> KnowledgeSearch:
    """The production wiring: the Foundry IQ knowledge base and Azure AI Content Safety."""
    from backend.app.services.azure_adapters import AzureContentSafetyInspector

    inspector = AzureContentSafetyInspector(settings)

    async def retrieve(query: str) -> list[dict[str, Any]]:
        from azure.identity.aio import DefaultAzureCredential
        from azure.search.documents.knowledgebases.aio import KnowledgeBaseRetrievalClient
        from azure.search.documents.knowledgebases.models import (
            KnowledgeBaseRetrievalRequest,
            KnowledgeRetrievalSemanticIntent,
            SearchIndexKnowledgeSourceParams,
        )

        if not settings.azure_search_endpoint:
            raise RuntimeError("AZURE_SEARCH_ENDPOINT is not configured")
        async with DefaultAzureCredential() as credential:
            async with KnowledgeBaseRetrievalClient(
                endpoint=settings.azure_search_endpoint,
                knowledge_base_name=settings.knowledge_base_name,
                credential=credential,
            ) as client:
                result = await client.retrieve(
                    KnowledgeBaseRetrievalRequest(
                        intents=[KnowledgeRetrievalSemanticIntent(search=query[:500])],
                        knowledge_source_params=[
                            SearchIndexKnowledgeSourceParams(
                                knowledge_source_name=settings.knowledge_source_name,
                                include_reference_source_data=True,
                            )
                        ],
                    )
                )
        note(service="Foundry IQ knowledge base", version="Azure AI Search 2026-04-01")
        return _passages(result)

    return KnowledgeSearch(retrieve, inspector.screen_documents)


def _passages(result: Any) -> list[dict[str, Any]]:
    """The passages of a retrieve response, from its references' source data."""
    passages = []
    for reference in getattr(result, "references", None) or []:
        data = getattr(reference, "source_data", None) or {}
        if isinstance(data, str):
            data = json.loads(data)
        if data.get("doc_id"):
            passages.append(data)
    return passages
