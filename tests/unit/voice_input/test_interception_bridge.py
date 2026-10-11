from __future__ import annotations

import asyncio

import pytest

from main_logic.voice_input.interception import (
    ActiveSessionInterceptionBridge,
    InterceptionDecision,
    InterceptionResult,
)


class _Runtime:
    def __init__(self, *, block: bool = False, close_ok: bool = True) -> None:
        self.block = block
        self.close_ok = close_ok
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed: list[str] = []

    async def process(self, pcm16: bytes, **_kwargs):
        self.started.set()
        if self.block:
            await self.release.wait()
        return InterceptionResult(InterceptionDecision.KEEP, pcm16=b"owner")

    async def close(self, reason: str = "retired") -> None:
        self.closed.append(reason)
        if not self.close_ok:
            return False


class _Factory:
    def __init__(self, *, block: bool = False, close_ok: bool = True) -> None:
        self.block = block
        self.close_ok = close_ok
        self.created: list[_Runtime] = []
        self.close_calls = 0

    def create(self, _generation, *, ingress_token):
        runtime = _Runtime(block=self.block, close_ok=self.close_ok)
        self.block = False
        self.created.append(runtime)
        return runtime

    def close(self) -> None:
        self.close_calls += 1


def _kwargs(generation=None, token=None):
    return {
        "sample_rate_hz": 16_000,
        "generation": generation,
        "ingress_token": token,
        "captured_at": 1.0,
    }


@pytest.mark.asyncio
async def test_first_none_generation_still_creates_runtime_and_bridge_does_not_close_factory():
    factory = _Factory()
    bridge = ActiveSessionInterceptionBridge(factory)

    result = await bridge.process(b"mixed", **_kwargs())
    await bridge.close()

    assert result.decision is InterceptionDecision.KEEP
    assert len(factory.created) == 1
    assert factory.close_calls == 0


@pytest.mark.asyncio
async def test_admission_capacity_rejects_without_queueing_second_process():
    factory = _Factory(block=True)
    bridge = ActiveSessionInterceptionBridge(factory, max_inflight=1)
    first = asyncio.create_task(bridge.process(b"one", **_kwargs("g", "i")))
    await asyncio.sleep(0)
    await factory.created[0].started.wait()

    second = await bridge.process(b"two", **_kwargs("g", "i"))
    assert second.decision is InterceptionDecision.UNAVAILABLE
    assert second.reason == "interception_capacity"
    factory.created[0].release.set()
    await first
    await bridge.close()


@pytest.mark.asyncio
async def test_cancelled_caller_retires_runtime_and_late_result_cannot_reuse_it():
    factory = _Factory(block=True)
    bridge = ActiveSessionInterceptionBridge(factory, close_timeout_s=0.05)
    task = asyncio.create_task(bridge.process(b"one", **_kwargs("g", "i")))
    await asyncio.sleep(0)
    await factory.created[0].started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert factory.created[0].closed == ["interception_process_cancelled"]
    result = await bridge.process(b"two", **_kwargs("g", "i"))
    assert result.decision is InterceptionDecision.KEEP
    assert len(factory.created) == 2
    await bridge.close()


@pytest.mark.asyncio
async def test_failed_retirement_blocks_generation_replacement():
    factory = _Factory(close_ok=False)
    bridge = ActiveSessionInterceptionBridge(factory, close_timeout_s=0.01)
    first = await bridge.process(b"one", **_kwargs("g1", "i1"))
    assert first.decision is InterceptionDecision.KEEP

    result = await bridge.process(b"two", **_kwargs("g2", "i2"))
    assert result.decision is InterceptionDecision.UNAVAILABLE
    assert result.reason == "interception_runtime_retirement_pending"
    assert len(factory.created) == 1


def test_timeout_parameters_must_be_finite_positive():
    factory = _Factory()
    with pytest.raises(ValueError):
        ActiveSessionInterceptionBridge(factory, process_timeout_s=float("inf"))
    with pytest.raises(ValueError):
        ActiveSessionInterceptionBridge(factory, close_timeout_s=0)
