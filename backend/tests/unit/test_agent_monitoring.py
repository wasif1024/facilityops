from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.services.agent_monitoring import (
    AgentMonitoringMiddleware,
    monitoring_scope,
)


class FakeRecorder:
    def __init__(self) -> None:
        self.samples = []

    async def record(self, sample) -> None:
        self.samples.append(sample)


def _context(result=None):
    return SimpleNamespace(agent=SimpleNamespace(name="Maintenance Triage Classifier"), result=result)


@pytest.mark.asyncio
async def test_records_usage_and_request_scope_on_success():
    recorder = FakeRecorder()
    middleware = AgentMonitoringMiddleware(recorder, model="gpt-test", prompt_sha="abc123")
    context = _context()
    request_id = uuid4()

    async def call_next():
        context.result = SimpleNamespace(
            usage_details={
                "input_token_count": 120,
                "output_token_count": 30,
                "total_token_count": 150,
            }
        )

    with monitoring_scope(request_id=request_id):
        await middleware.process(context, call_next)

    [sample] = recorder.samples
    assert sample.status == "succeeded"
    assert sample.agent_name == "Maintenance Triage Classifier"
    assert (sample.input_tokens, sample.output_tokens, sample.total_tokens) == (120, 30, 150)
    assert sample.request_id == request_id
    assert sample.evaluation_run_id is None
    assert sample.model == "gpt-test" and sample.prompt_sha == "abc123"


@pytest.mark.asyncio
async def test_records_failure_and_reraises():
    recorder = FakeRecorder()
    middleware = AgentMonitoringMiddleware(recorder, model=None, prompt_sha=None)

    async def call_next():
        raise TimeoutError("model did not answer")

    with pytest.raises(TimeoutError):
        await middleware.process(_context(), call_next)

    [sample] = recorder.samples
    assert sample.status == "failed"
    assert sample.error_type == "TimeoutError"
    assert sample.total_tokens is None


@pytest.mark.asyncio
async def test_scope_does_not_leak_outside_the_block():
    recorder = FakeRecorder()
    middleware = AgentMonitoringMiddleware(recorder, model=None, prompt_sha=None)

    async def call_next():
        return None

    with monitoring_scope(evaluation_run_id=uuid4()):
        pass
    await middleware.process(_context(), call_next)

    assert recorder.samples[0].evaluation_run_id is None
