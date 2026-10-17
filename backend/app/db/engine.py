"""One way to open the database, for the API, the worker and migrations.

Locally DATABASE_URL carries the user and (optionally) a password. In Azure there is no password:
with DATABASE_ENTRA_AUTH=true each new connection signs in with a Microsoft Entra token for the
app's managed identity (Azure Database for PostgreSQL flexible server, Entra authentication).
Tokens are cached by azure-identity and only needed when a connection is opened.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from backend.app.core.config import Settings

POSTGRES_ENTRA_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


def use_entra_password(sync_engine: Engine) -> None:
    """Give every new connection a fresh Entra token as its password."""
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()

    @event.listens_for(sync_engine, "do_connect")
    def _token(dialect: Any, record: Any, cargs: Any, cparams: dict[str, Any]) -> None:
        cparams["password"] = credential.get_token(POSTGRES_ENTRA_SCOPE).token


def create_engine(settings: Settings, **kwargs: Any) -> AsyncEngine:
    kwargs.setdefault("pool_pre_ping", True)
    if settings.database_entra_auth:
        # Tokens last about an hour; recycle connections well before a server-side session limit.
        kwargs.setdefault("pool_recycle", 1800)
    engine = create_async_engine(settings.database_url, **kwargs)
    if settings.database_entra_auth:
        use_entra_password(engine.sync_engine)
    return engine
