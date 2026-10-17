"""Load the synthetic facility world (backend/app/fixtures/synthetic_world.json).

Idempotent: rows are upserted by id, so it can run on every `scripts/db.sh seed-world` and in
every eval. Relative dates (days_ago, days_from_now) are turned into real dates against today.
Never loaded when APP_ENV=production.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AssetRecord,
    BuildingRecord,
    MaintenanceRecord,
    PartRecord,
    PermitRecord,
    TechnicianProfileRecord,
    UserRecord,
    VendorRecord,
)
from backend.app.domain.rbac import IdentityProvider

WORLD_FILE = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_world.json"


async def _upsert(session: AsyncSession, model, rows: list[dict[str, Any]], key: str) -> None:
    for row in rows:
        statement = insert(model).values(**row)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[key], set_={k: statement.excluded[k] for k in row if k != key}
            )
        )


async def load_world(session: AsyncSession, path: Path = WORLD_FILE, *, today: date | None = None) -> dict[str, int]:
    data = json.loads(path.read_text())
    today = today or date.today()
    ago = lambda days: today - timedelta(days=days)  # noqa: E731

    await _upsert(session, BuildingRecord, data["buildings"], "id")
    await _upsert(
        session,
        AssetRecord,
        [
            {**{k: v for k, v in a.items() if k != "installed_years_ago"}, "installed_on": ago(365 * a["installed_years_ago"])}
            for a in data["assets"]
        ],
        "id",
    )
    await _upsert(
        session,
        MaintenanceRecord,
        [{**{k: v for k, v in r.items() if k != "days_ago"}, "occurred_on": ago(r["days_ago"])} for r in data["maintenance_records"]],
        "id",
    )
    await _upsert(
        session,
        VendorRecord,
        [
            {**{k: v for k, v in v_.items() if k != "contract_ends_days_from_now"}, "contract_ends": ago(-v_["contract_ends_days_from_now"])}
            for v_ in data["vendors"]
        ],
        "id",
    )
    await _upsert(session, PartRecord, data["parts"], "sku")

    technicians = []
    for t in data["technicians"]:
        user_id = None
        if external_id := t.get("user_external_id"):
            user_id = await session.scalar(
                select(UserRecord.id).where(
                    UserRecord.identity_provider == IdentityProvider.DEVELOPMENT.value,
                    UserRecord.external_id == external_id,
                )
            )
        technicians.append({**{k: v for k, v in t.items() if k != "user_external_id"}, "user_id": user_id})
    await _upsert(session, TechnicianProfileRecord, technicians, "id")
    await _upsert(
        session,
        PermitRecord,
        [{**{k: v for k, v in p.items() if k != "valid_days_from_now"}, "valid_until": ago(-p["valid_days_from_now"])} for p in data["permits"]],
        "id",
    )
    await session.commit()
    return {key: len(value) for key, value in data.items() if isinstance(value, list)}
