"""The token budget can't be switched off by a response that lacks usage data (spec 6.1)."""

from types import SimpleNamespace

from backend.app.agents.team import ESTIMATE_CHARS_PER_TOKEN, ESTIMATE_MIN_TOKENS_PER_CALL, _TokenMeter


def response(usage, text=""):
    return SimpleNamespace(usage_details=usage, text=text)


def test_reported_total_is_used():
    meter = _TokenMeter(limit=1000)
    meter.add(response({"input_token_count": 300, "output_token_count": 50, "total_token_count": 350}))
    assert (meter.used, meter.estimated_calls) == (350, 0)


def test_input_and_output_are_summed_when_the_total_is_missing():
    meter = _TokenMeter(limit=1000)
    meter.add(response({"input_token_count": 300, "output_token_count": 50}))
    assert (meter.used, meter.estimated_calls) == (350, 0)


def test_missing_usage_is_charged_a_conservative_estimate():
    for usage in (None, {}, {"total_token_count": 0}, {"input_token_count": None}):
        meter = _TokenMeter(limit=1000)
        meter.add(response(usage, text="short"), sent_chars=10)
        assert meter.used == ESTIMATE_MIN_TOKENS_PER_CALL and meter.estimated_calls == 1


def test_a_large_unreported_call_is_estimated_from_its_size():
    meter = _TokenMeter(limit=10**6)
    meter.add(response(None, text="x" * 30_000), sent_chars=60_000)
    # 90k characters: more tokens than a real ~4 characters per token would give.
    assert meter.used == 90_000 // ESTIMATE_CHARS_PER_TOKEN > 90_000 // 4


def test_a_missing_response_still_counts():
    meter = _TokenMeter(limit=1000)
    meter.add(None, sent_chars=0)
    assert meter.used == ESTIMATE_MIN_TOKENS_PER_CALL
