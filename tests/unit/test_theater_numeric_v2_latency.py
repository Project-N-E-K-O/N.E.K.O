"""Measure complete waits, including failures, without changing delivery or counting overlap twice."""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_latency as latency, numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import TurnRequestV2
from services.theater.numeric_v2_usage import invoke_with_usage, numeric_v2_usage_scope
from tests.unit.test_theater_numeric_v2_authored_transition_recovery import _case, _install
from tests.unit.test_theater_numeric_v2_runtime import _binding


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(latency, "_clock", lambda: clock.now)
    return clock


@pytest.mark.asyncio
async def test_call_records_duration_and_provider_cache_without_changing_public_usage(clock):
    response = SimpleNamespace(content="PRIVATE_BODY", response_metadata={"token_usage": {
        "prompt_tokens": 100, "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 80},
        "completion_tokens_details": {"reasoning_tokens": 0}, "debug": "SECRET_AUTH"}})
    async def invoke(messages):
        clock.now += 1.25
        return response

    with numeric_v2_usage_scope() as usage, latency.turn_latency_scope() as timing:
        actual = await invoke_with_usage(SimpleNamespace(ainvoke=invoke), [], stage="contract")
        clock.now += .25
        measured = timing.snapshot()
    assert actual is response
    assert usage == [{"stage": "contract", "input_tokens": 100, "output_tokens": 20}]
    assert measured["total_wall_ms"] == 1500
    assert measured["model_wall_ms"] == measured["model_sum_ms"] == 1250
    assert measured["other_wall_ms"] == 250
    row, = measured["model_calls"]
    assert row["cache_read_input_tokens"] == 80 and row["reasoning_tokens"] == 0
    assert row["status"] == "returned" and row["output_tokens"] == 20
    assert "PRIVATE_BODY" not in json.dumps(measured) and "SECRET_AUTH" not in json.dumps(measured)
    assert latency._current.get() is None


@pytest.mark.asyncio
async def test_parallel_model_time_is_the_union_not_the_sum(clock):
    first_started, second_started, first_finished = (asyncio.Event() for _ in range(3))
    async def first():
        clock.now = 2
        with latency.model_call_timing("first"):
            first_started.set()
            await second_started.wait()
            clock.now = 5
        first_finished.set()
    async def second():
        await first_started.wait()
        clock.now = 3
        with latency.model_call_timing("second"):
            second_started.set()
            await first_finished.wait()
            clock.now = 7
    with latency.turn_latency_scope() as timing:
        await asyncio.gather(first(), second())
        clock.now = 8
        measured = timing.snapshot()
    assert measured["total_wall_ms"] == 8000
    assert measured["model_sum_ms"] == 7000
    assert measured["model_wall_ms"] == 5000
    assert measured["other_wall_ms"] == 3000


@pytest.mark.asyncio
async def test_concurrent_turn_measurements_do_not_leak_into_each_other():
    async def run(stage):
        with latency.turn_latency_scope() as timing:
            with latency.model_call_timing(stage):
                await asyncio.sleep(0)
            return timing.snapshot()
    first, second = await asyncio.gather(run("actor"), run("contract"))
    assert [row["stage"] for row in first["model_calls"]] == ["actor"]
    assert [row["stage"] for row in second["model_calls"]] == ["contract"]
    assert latency._current.get() is None
    with latency.model_call_timing("outside") as row:
        assert row is None


@pytest.mark.asyncio
async def test_failed_provider_call_keeps_wait_and_unknown_usage_without_exception_text(clock):
    async def invoke(messages):
        clock.now += 2
        raise RuntimeError("SECRET_AUTH_PRIVATE_BODY")
    with latency.turn_latency_scope() as timing, numeric_v2_usage_scope() as usage:
        with pytest.raises(RuntimeError):
            await invoke_with_usage(SimpleNamespace(ainvoke=invoke), [], stage="contract_confirmation")
        measured = timing.snapshot()
    row, = measured["model_calls"]
    assert row["duration_ms"] == measured["model_wall_ms"] == 2000
    assert row["status"] == "failed" and row["error_type"] == "RuntimeError"
    assert row["input_tokens"] is row["output_tokens"] is row["cache_read_input_tokens"] is None
    assert usage == [{"stage": "contract_confirmation", "input_tokens": None, "output_tokens": None}]
    assert "SECRET_AUTH" not in json.dumps(measured)


@pytest.mark.parametrize("usage,expected", [
    ({}, {"input_tokens": None, "output_tokens": None, "cache_read_input_tokens": None, "reasoning_tokens": None}),
    ({"input_tokens": 10, "cache_creation_input_tokens": 4, "cache_read_input_tokens": 6, "output_tokens": 3},
     {"input_tokens": 20, "output_tokens": 3, "cache_read_input_tokens": 6, "reasoning_tokens": None}),
    ({"prompt_tokens": True, "completion_tokens": -1, "prompt_tokens_details": {"cached_tokens": "80"}},
     {"input_tokens": None, "output_tokens": None, "cache_read_input_tokens": None, "reasoning_tokens": None}),
])
def test_unknown_provider_token_measurements_are_not_invented(usage, expected):
    assert latency.reported_tokens(usage) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["fallback", "write_fence", "cancelled", "program_invitation"])
async def test_public_turn_logs_all_outcomes_with_complete_total_and_no_private_prose(
        tmp_path, monkeypatch, caplog, clock, outcome):
    runtime, current = await _case(tmp_path)
    _install(monkeypatch, runtime)
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")
    monkeypatch.setenv("NEKO_THEATER_TRACE_DIR", str(tmp_path / "traces"))
    caplog.set_level(logging.INFO, logger=wf.__name__)
    before = runtime.store._path(current.session.session_id).read_bytes()
    response = SimpleNamespace(content="PRIVATE_MODEL_BODY", response_metadata={"token_usage": {
        "prompt_tokens": 10, "completion_tokens": 2}})

    async def invoke(messages):
        clock.now += 1
        if outcome == "cancelled":
            raise asyncio.CancelledError("PRIVATE_CANCELLATION")
        return response

    async def rejected(**kwargs):
        await invoke_with_usage(SimpleNamespace(ainvoke=invoke), [], stage="contract")
        clock.now += .2
        raise wf.NumericV2ActorOutputError("numeric_v2_contract_violation")

    async def before_commit():
        clock.now += .2
        if outcome == "write_fence":
            raise ValueError("PRIVATE_WRITE_FENCE")

    def checkpoint(*args):
        clock.now += .1
        return (runtime.prepare_turn(current, TurnRequestV2("timing", 0, "PRIVATE_PLAYER"), ()),
                {"performance": "接下来，你想怎么继续？", "suggested_inputs": []})

    monkeypatch.setattr(wf, "_execute_numeric_v2_turn", rejected)
    if outcome == "program_invitation":
        monkeypatch.setattr(wf, "_checkpoint_invitation", checkpoint)
    diagnostics = {}
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("timing", 0, "PRIVATE_PLAYER"), ensure_current_binding=lambda _: _binding(),
        before_commit=before_commit, diagnostics_sink=diagnostics)
    if outcome in {"cancelled", "write_fence"}:
        error = asyncio.CancelledError if outcome == "cancelled" else ValueError
        with pytest.raises(error):
            await wf.execute_numeric_v2_turn(**kwargs)
        assert runtime.store._path(current.session.session_id).read_bytes() == before
    else:
        result = await wf.execute_numeric_v2_turn(**kwargs)
        assert result.stored.session.revision == 1
    measured = diagnostics["latency"]
    expected_status = {"fallback": "safe_fallback", "write_fence": "failed", "cancelled": "cancelled",
                       "program_invitation": "completed"}[outcome]
    assert measured["status"] == expected_status
    if outcome == "program_invitation":
        assert measured["total_wall_ms"] == 300 and measured["model_calls"] == []
    else:
        assert measured["model_wall_ms"] == 1000
        assert measured["total_wall_ms"] == (1000 if outcome == "cancelled" else 1400)
        assert measured["model_calls"][0]["status"] == ("cancelled" if outcome == "cancelled" else "returned")
    assert diagnostics["timings_ms"]["total_wall"] == measured["total_wall_ms"]
    if outcome != "cancelled":
        assert diagnostics["timings_ms"]["commit_work"] >= 0
    assert "Numeric v2 workflow timing:" in caplog.text
    assert "PRIVATE_" not in caplog.text
    trace, = (tmp_path / "traces").glob("*.jsonl")
    recorded = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert next(row["data"] for row in recorded if row["event"] == "turn.latency") == measured
    assert latency._current.get() is None


@pytest.mark.asyncio
async def test_invalid_accept_control_is_timed_without_binding_or_model_calls(tmp_path, monkeypatch, clock):
    runtime, current = await _case(tmp_path)
    calls = _install(monkeypatch, runtime)
    def no_binding(_):
        raise AssertionError("invalid controls must fail before reading persona")
    diagnostics = {}
    with pytest.raises(wf.NumericV2RuntimeError, match="numeric_invitation_acceptance_not_available"):
        await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("invalid-control", 0, "", "accept_invitation"),
            ensure_current_binding=no_binding, diagnostics_sink=diagnostics)
    assert calls == {"actor": 0, "contract": 0}
    assert diagnostics["latency"]["model_calls"] == []
    assert diagnostics["latency"]["status"] == "failed"
    assert latency._current.get() is None
