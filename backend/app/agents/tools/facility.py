"""Read-only facility tools for the incident team's specialists (spec 5.2).

Each tool takes an open read-only session (see runtime.run_tool) and returns plain JSON with a
``citation`` the specialist can put on its claims. They read synthetic facility data only; none
can reach requests, the PII mapping or users. Technicians are returned as profile ids and
skills, never names.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import (
    AssetRecord,
    MaintenanceRecord,
    PartRecord,
    PermitRecord,
    TechnicianProfileRecord,
    VendorRecord,
    WorkOrderRecord,
)

MAX_RESULTS = 10
# Two repairs on the same asset within this window is a repeat failure (spec 4.3).
REPEAT_FAILURE_DAYS = 30
# What work on each kind of asset requires before anyone opens it up.
REQUIRED_PERMITS = {
    "electrical": ["electrical_isolation"],
    "gas boiler": ["gas_work"],
}


def _asset(a: AssetRecord) -> dict[str, Any]:
    return {
        "asset_id": a.id,
        "building_id": a.building_id,
        "category": a.category,
        "kind": a.kind,
        "manufacturer": a.manufacturer,
        "model": a.model,
        "location": a.location,
        "age_years": round((date.today() - a.installed_on).days / 365, 1),
        "criticality": a.criticality,
        "manual_id": a.manual_id,
        "citation": {"source_type": "record", "source_id": f"asset:{a.id}"},
    }


async def find_assets(
    session: AsyncSession, *, category: str | None = None, text: str | None = None, building_id: str | None = None
) -> dict[str, Any]:
    """Assets by category, building, or a few words of kind, model or location."""
    query = select(AssetRecord)
    if category:
        query = query.where(AssetRecord.category == category.lower())
    if building_id:
        query = query.where(AssetRecord.building_id == building_id)
    if text:
        for word in text.lower().split()[:5]:
            pattern = f"%{word}%"
            query = query.where(
                or_(
                    func.lower(AssetRecord.kind).like(pattern),
                    func.lower(AssetRecord.model).like(pattern),
                    func.lower(AssetRecord.location).like(pattern),
                    func.lower(AssetRecord.manufacturer).like(pattern),
                )
            )
    rows = await session.scalars(query.order_by(AssetRecord.id).limit(MAX_RESULTS))
    return {"assets": [_asset(a) for a in rows]}


async def get_asset(session: AsyncSession, *, asset_id: str) -> dict[str, Any]:
    asset = await session.get(AssetRecord, asset_id)
    return {"found": True, **_asset(asset)} if asset else {"found": False, "asset_id": asset_id}


async def get_maintenance_history(session: AsyncSession, *, asset_id: str, days: int = 180) -> dict[str, Any]:
    """Past work on an asset, newest first, and whether it is failing repeatedly."""
    since = date.today() - timedelta(days=max(1, min(days, 730)))
    rows = list(
        await session.scalars(
            select(MaintenanceRecord)
            .where(MaintenanceRecord.asset_id == asset_id, MaintenanceRecord.occurred_on >= since)
            .order_by(MaintenanceRecord.occurred_on.desc())
            .limit(MAX_RESULTS)
        )
    )
    recent_repairs = [
        r for r in rows if r.kind == "repair" and (date.today() - r.occurred_on).days <= REPEAT_FAILURE_DAYS
    ]
    return {
        "asset_id": asset_id,
        "records": [
            {
                "record_id": r.id,
                "days_ago": (date.today() - r.occurred_on).days,
                "kind": r.kind,
                "fault_code": r.fault_code,
                "summary": r.summary,
                "cost": r.cost,
                "citation": {"source_type": "record", "source_id": f"work_order:{r.id}"},
            }
            for r in rows
        ],
        "repeat_failure": len(recent_repairs) >= 2,
        "repairs_last_30_days": len(recent_repairs),
    }


async def find_parts(session: AsyncSession, *, model: str | None = None, text: str | None = None) -> dict[str, Any]:
    """Parts that fit an equipment model, with stock, cost and lead time."""
    query = select(PartRecord)
    if model:
        query = query.where(PartRecord.fits_models.contains([model]))
    if text:
        query = query.where(func.lower(PartRecord.name).like(f"%{text.lower()[:40]}%"))
    rows = await session.scalars(query.order_by(PartRecord.sku).limit(MAX_RESULTS))
    return {
        "parts": [
            {
                "sku": p.sku,
                "name": p.name,
                "in_stock": p.stock,
                "unit_cost": p.unit_cost,
                "vendor_id": p.vendor_id,
                "lead_time_days": p.lead_time_days if p.stock == 0 else 0,
                "citation": {"source_type": "record", "source_id": f"part:{p.sku}"},
            }
            for p in rows
        ]
    }


async def get_vendor_terms(session: AsyncSession, *, trade: str) -> dict[str, Any]:
    """Contracted vendors for a trade: response time (SLA), call-out fee, contract."""
    rows = await session.scalars(
        select(VendorRecord).where(VendorRecord.trades.contains([trade.lower()])).order_by(VendorRecord.sla_hours)
    )
    return {
        "vendors": [
            {
                "vendor_id": v.id,
                "name": v.name,
                "sla_hours": v.sla_hours,
                "callout_fee": v.callout_fee,
                "contract_ends_in_days": (v.contract_ends - date.today()).days,
                "citation": {"source_type": "contract", "source_id": v.contract_id, "section": "SLA"},
            }
            for v in rows
        ]
    }


async def find_available_technicians(
    session: AsyncSession, *, skill: str, within_hours: int = 24, now: datetime | None = None
) -> dict[str, Any]:
    """Technicians with a skill who start a shift (or are on call) within the window.

    Returns profile ids, skills and open job counts only: no names or contact details.
    """
    now = now or datetime.now(UTC)
    within = max(1, min(within_hours, 168))
    rows = await session.scalars(
        select(TechnicianProfileRecord).where(TechnicianProfileRecord.skills.contains([skill.lower()]))
    )
    found = []
    for t in rows:
        next_start = _next_shift_start(t, now)
        if t.on_call or (next_start is not None and next_start - now <= timedelta(hours=within)):
            open_jobs = 0
            if t.user_id:
                open_jobs = await session.scalar(
                    select(func.count()).select_from(WorkOrderRecord).where(
                        WorkOrderRecord.assigned_technician_id == t.user_id, WorkOrderRecord.status == "open"
                    )
                ) or 0
            found.append(
                {
                    "technician_id": t.id,
                    "skills": t.skills,
                    "on_call": t.on_call,
                    "available_in_hours": 0 if next_start is None or next_start <= now else round((next_start - now).total_seconds() / 3600, 1),
                    "open_jobs": open_jobs,
                    "citation": {"source_type": "record", "source_id": f"technician:{t.id}"},
                }
            )
    found.sort(key=lambda t: (t["available_in_hours"], t["open_jobs"]))
    return {"technicians": found[:MAX_RESULTS]}


def _next_shift_start(t: TechnicianProfileRecord, now: datetime) -> datetime | None:
    """Now, if the technician is on shift; otherwise the start of their next shift (UTC)."""
    for offset in range(8):
        day = (now + timedelta(days=offset)).date()
        if day.weekday() not in t.weekdays:
            continue
        start = datetime(day.year, day.month, day.day, t.shift_start_hour, tzinfo=UTC)
        end = datetime(day.year, day.month, day.day, t.shift_end_hour, tzinfo=UTC)
        if offset == 0 and start <= now < end:
            return now
        if start > now:
            return start
    return None


async def check_permit_requirements(session: AsyncSession, *, asset_id: str) -> dict[str, Any]:
    """Which permits work on this asset needs, and whether a valid one is on record."""
    asset = await session.get(AssetRecord, asset_id)
    if asset is None:
        return {"found": False, "asset_id": asset_id}
    required = REQUIRED_PERMITS.get(asset.kind, []) or REQUIRED_PERMITS.get(asset.category, [])
    permits = list(await session.scalars(select(PermitRecord).where(PermitRecord.asset_id == asset_id)))
    today = date.today()
    on_record = [
        {
            "permit_id": p.id,
            "kind": p.kind,
            "valid": p.valid_until >= today,
            "valid_until_in_days": (p.valid_until - today).days,
            "citation": {"source_type": "record", "source_id": p.id},
        }
        for p in permits
    ]
    missing = [kind for kind in required if not any(p["kind"] == kind and p["valid"] for p in on_record)]
    return {
        "found": True,
        "asset_id": asset_id,
        "required_permits": required,
        "permits_on_record": on_record,
        "missing_permits": missing,
        # The safety specialist must block a plan while a required permit is missing.
        "blocks_work": bool(missing),
    }
