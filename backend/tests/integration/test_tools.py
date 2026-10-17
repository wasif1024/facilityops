"""The incident team's read-only tools against the synthetic world (spec 5.2, 5.4)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.app.agents.tools import facility
from backend.app.agents.tools.registry import SPECIALIST_TOOLS, TOOLS, tools_for
from backend.app.agents.tools.runtime import ToolContext, run_tool
from backend.app.services.world import load_world

from .conftest import scalar
from .test_operations import make_user


@pytest_asyncio.fixture
async def world(session_factory):
    await make_user(session_factory, "tech", "technician")
    async with session_factory() as session:
        await load_world(session)
    return ToolContext(session_factory, request_id=uuid4())


async def call(ctx, name, specialist="diagnostician", **kwargs):
    return await run_tool(ctx, specialist, name, TOOLS[name].function, **kwargs)


@pytest.mark.asyncio
async def test_assets_are_found_by_category_and_words(world):
    result = await call(world, "find_assets", category="hvac", text="boiler")
    assert [a["asset_id"] for a in result["assets"]] == ["asset-hv-boiler-1", "asset-mc-boiler-1"]
    assert result["assets"][0]["manual_id"] == "manual:boiler-hx200"
    assert result["assets"][0]["citation"] == {"source_type": "record", "source_id": "asset:asset-hv-boiler-1"}


@pytest.mark.asyncio
async def test_history_shows_recent_work_and_repeat_failures(world):
    boiler = await call(world, "get_maintenance_history", "asset_historian", asset_id="asset-hv-boiler-1")
    assert boiler["records"][0]["record_id"] == "wo-1042" and boiler["records"][0]["days_ago"] == 12
    assert boiler["repeat_failure"] is False

    pump = await call(world, "get_maintenance_history", "asset_historian", asset_id="asset-mc-pump-1")
    assert pump["repeat_failure"] is True and pump["repairs_last_30_days"] == 2


@pytest.mark.asyncio
async def test_parts_and_vendor_terms(world):
    parts = await call(world, "find_parts", "procurement", model="HX-200")
    sensor = next(p for p in parts["parts"] if p["sku"] == "hx200-pressure-sensor")
    assert (sensor["in_stock"], sensor["lead_time_days"]) == (0, 3)

    vendors = await call(world, "get_vendor_terms", "procurement", trade="hvac")
    assert [v["vendor_id"] for v in vendors["vendors"]] == ["vendor-thermaserve", "vendor-coolserve"]
    assert vendors["vendors"][0]["citation"]["source_id"] == "contract:thermaserve-2026"


@pytest.mark.asyncio
async def test_technicians_are_ids_and_skills_never_names(world, session_factory):
    async with session_factory() as session:
        monday_ten = datetime(2026, 10, 19, 10, tzinfo=UTC)
        result = await facility.find_available_technicians(session, skill="hvac", now=monday_ten)
    ids = [t["technician_id"] for t in result["technicians"]]
    assert ids[0] == "tech-001" and result["technicians"][0]["available_in_hours"] == 0
    assert "tech-002" in ids  # on call
    assert not any(key in str(result) for key in ("display_name", "email", "Taylor"))


@pytest.mark.asyncio
async def test_permits_block_work_when_missing_or_expired(world):
    board = await call(world, "check_permit_requirements", "safety_compliance", asset_id="asset-hv-db3")
    assert board["required_permits"] == ["electrical_isolation"] and board["blocks_work"] is False

    boiler = await call(world, "check_permit_requirements", "safety_compliance", asset_id="asset-hv-boiler-1")
    assert boiler["missing_permits"] == ["gas_work"] and boiler["blocks_work"] is True  # permit p-12 expired


@pytest.mark.asyncio
async def test_each_specialist_has_a_budget_and_every_call_is_recorded(world):
    for _ in range(5):
        await call(world, "get_asset", asset_id="asset-hv-boiler-1")
    refused = await call(world, "get_asset", asset_id="asset-hv-boiler-1")
    assert refused["error"] == "tool_budget_exhausted"
    # Another specialist still has its own budget.
    assert (await call(world, "get_asset", "procurement", asset_id="asset-hv-boiler-1"))["found"] is True

    assert len(world.steps) == 7
    assert world.steps[5].outcome.value == "flagged" and world.steps[5].reason_code == "tool_budget_exhausted"
    assert world.steps[0].details == {
        "specialist": "diagnostician",
        "arguments": {"asset_id": "asset-hv-boiler-1"},
        "results": 1,
    }


@pytest.mark.asyncio
async def test_tools_cannot_write_even_if_their_code_tries(world, session_factory):
    async def rogue(session, **_):
        await session.execute(text("INSERT INTO buildings (id, name, units) VALUES ('evil', 'x', 1)"))
        return {}

    result = await run_tool(world, "diagnostician", "rogue", rogue)
    assert "error" in result
    assert await scalar(session_factory, "SELECT count(*) FROM buildings WHERE id = 'evil'") == 0
    assert world.steps[-1].outcome.value == "failed"


@pytest.mark.asyncio
async def test_free_text_arguments_are_never_recorded(world):
    await call(world, "find_assets", text="the tenant in flat 12C says the boiler in plant room B is banging")
    assert world.steps[-1].details["arguments"]["text"].endswith("chars>")


def test_least_privilege_mapping():
    assert SPECIALIST_TOOLS["communicator"] == ()
    assert all(name in TOOLS for names in SPECIALIST_TOOLS.values() for name in names)
    assert [t.name for t in tools_for("scheduler")] == ["find_available_technicians"]


@pytest.mark.asyncio
async def test_assets_match_the_building_name_too(world):
    result = await call(world, "find_assets", category="hvac", text="boiler plant room B Harbor View")
    assert [a["asset_id"] for a in result["assets"]] == ["asset-hv-boiler-1"]


@pytest.mark.asyncio
async def test_parts_match_words_not_the_whole_phrase(world):
    # The live run's procurement query, which found nothing when matched as one string.
    result = await call(world, "find_parts", "procurement", model="HX-200", text="24 L expansion vessel for Thermacore HX-200 boiler")
    assert result["parts"][0]["sku"] == "exp-vessel-24l"  # best match first; the sensor still appears below
