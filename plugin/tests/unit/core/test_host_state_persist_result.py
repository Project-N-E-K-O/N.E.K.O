from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from plugin.core.entry_executor import EntryExecutor
from plugin.core.host import _persist_plugin_state
from plugin.sdk.shared.models import Err, Ok
from plugin.sdk.shared.models.exceptions import TransportError


class _RecordingLog:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message.format(*args))


@pytest.mark.asyncio
async def test_persist_plugin_state_raises_on_err_result() -> None:
    class _FailingPersistence:
        async def save(self, instance: object):
            del instance
            return Err(TransportError("disk full"))

    instance = SimpleNamespace(_state_persistence=_FailingPersistence())
    log = _RecordingLog()

    with pytest.raises(RuntimeError, match="state persistence save failed"):
        await _persist_plugin_state(instance, ["counter"], "freeze", log=log)

    assert len(log.warnings) == 1
    assert "freeze" in log.warnings[0]


@pytest.mark.asyncio
async def test_persist_plugin_state_allows_ok_false_for_off_backend() -> None:
    class _DisabledPersistence:
        async def save(self, instance: object):
            del instance
            return Ok(False)

    instance = SimpleNamespace(_state_persistence=_DisabledPersistence())
    log = _RecordingLog()

    await _persist_plugin_state(instance, ["counter"], "freeze", log=log)
    assert log.warnings == []


@pytest.mark.asyncio
async def test_persist_plugin_state_does_not_mask_internal_type_error() -> None:
    calls = 0

    class _InternalTypeErrorPersistence:
        async def save(self, instance: object):
            nonlocal calls
            del instance
            calls += 1
            raise TypeError("cannot serialize object")

    instance = SimpleNamespace(_state_persistence=_InternalTypeErrorPersistence())

    with pytest.raises(TypeError, match="cannot serialize object"):
        await _persist_plugin_state(instance, ["counter"], "auto")

    assert calls == 1


@pytest.mark.asyncio
async def test_persist_plugin_state_supports_legacy_three_arg_save() -> None:
    seen: list[tuple[object, list[str], str]] = []

    class _LegacyPersistence:
        async def save(self, instance: object, keys: list[str], *, reason: str = ""):
            seen.append((instance, list(keys), reason))
            return Ok(True)

    instance = SimpleNamespace(_freeze_checkpoint=_LegacyPersistence())
    await _persist_plugin_state(instance, ["a", "b"], "freeze")

    assert seen == [(instance, ["a", "b"], "freeze")]


@pytest.mark.asyncio
async def test_entry_executor_logs_failure_when_persist_rejects_err_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _FailingPersistence:
        async def save(self, instance: object):
            del instance
            return Err(TransportError("io failure"))

    class _Plugin:
        async def ping(self) -> dict[str, str]:
            return {"pong": "ok"}

    import contextlib

    instance = _Plugin()
    instance._state_persistence = _FailingPersistence()  # type: ignore[attr-defined]
    ctx = SimpleNamespace(
        _handler_scope=lambda _name: contextlib.nullcontext(),
        _run_scope=lambda _run_id: contextlib.nullcontext(),
        _lanlan_scope=lambda _lanlan: contextlib.nullcontext(),
    )

    async def _persist(reason: str) -> None:
        await _persist_plugin_state(instance, ["counter"], reason)

    executor = EntryExecutor(
        plugin_id="demo",
        context=ctx,
        instance=instance,
        entries={"ping": instance.ping},
        metadata={},
        events={},
        default_timeout=5.0,
        refresh=lambda: None,
        should_persist=lambda _m: True,
        persist=_persist,
        logger=logging.getLogger("test.entry_executor"),
    )

    with caplog.at_level(logging.WARNING, logger="test.entry_executor"):
        result = await executor.invoke(
            event_type="plugin_entry",
            event_id="ping",
            args={},
        )

    assert result == {"pong": "ok"}
    assert any("Plugin state persistence failed" in rec.message for rec in caplog.records)
