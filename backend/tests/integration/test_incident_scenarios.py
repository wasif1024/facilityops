"""Every scenario in evals/incidents/scenarios.json, in scripted mode (FR-29): the CI gate."""

import pytest

from evals.incidents.harness import load, run_scenario

DATA = load()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", DATA["scenarios"], ids=[s["id"] for s in DATA["scenarios"]])
async def test_scenario(client, session_factory, scenario):
    result = await run_scenario(session_factory, scenario, DATA["plans"])
    failed = {k: v for k, v in {**result.checks, **result.gates}.items() if not v}
    assert result.passed, f"{result.id}: failed {failed}; {result.detail}"
