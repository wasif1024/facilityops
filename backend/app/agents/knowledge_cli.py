"""Command line for the knowledge base (used by scripts/knowledge.sh).

    python -m backend.app.agents.knowledge_cli setup          # index + passages + knowledge base
    python -m backend.app.agents.knowledge_cli search "E04"   # what search_manuals would return
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from backend.app.agents.knowledge import azure_knowledge, setup_knowledge
from backend.app.core.config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/knowledge.sh")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Create or update the index, passages and knowledge base")
    search = commands.add_parser("search", help="Search manuals (or procedures) as the agents would")
    search.add_argument("query")
    search.add_argument("--procedures", action="store_true", help="Search safety procedures instead of manuals")
    search.add_argument("--model", help="Equipment model, for example HX-200")
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "setup":
        result = setup_knowledge(settings)
        print(
            f"knowledge base '{result['knowledge_base']}' ready: {result['passages']} passages "
            f"from {result['documents']} documents in index '{result['index']}'"
        )
        return 0

    found = asyncio.run(
        azure_knowledge(settings).search(
            args.query, doc_types=["procedure" if args.procedures else "manual"], model=args.model
        )
    )
    for passage in found["passages"]:
        print(f"[{passage['citation']['source_id']} § {passage['section']}] {passage['text'][:160]}")
    print(f"\n{len(found['passages'])} passage(s); {found['injected_passages_dropped']} dropped by Prompt Shields")
    return 0


if __name__ == "__main__":
    sys.exit(main())
