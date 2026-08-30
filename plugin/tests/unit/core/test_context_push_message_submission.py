from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.core import context as context_module
from plugin.core.context import PluginContext


class _Logger:
    def __init__(self) -> None:
        self.records: list[tuple[str, tuple[object, ...]]] = []

    def debug(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append((message, args))

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append((message, args))

    def error(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append((message, args))


class _Queue:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.items: list[dict[str, Any]] = []
        self.fast_items: list[dict[str, Any]] = []

    def put_nowait(self, payload: dict[str, Any]) -> None:
        if self.error is not None:
            raise self.error
        self.items.append(payload)

    def put_fast_nowait(self, payload: dict[str, Any]) -> None:
        if self.error is not None:
            raise self.error
        self.fast_items.append(payload)


class _Again(Exception):
    pass


def _context(
    tmp_path: Path,
    *,
    message_queue: object = None,
) -> tuple[PluginContext, _Logger]:
    logger = _Logger()
    return (
        PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo" / "plugin.toml",
            logger=logger,  # type: ignore[arg-type]
            status_queue=None,
            message_queue=message_queue,
        ),
        logger,
    )


@pytest.mark.plugin_unit
def test_plugin_messages_use_the_host_uplink_instead_of_direct_ingest(
    tmp_path: Path,
) -> None:
    host_queue = _Queue()
    ctx, _logger = _context(tmp_path, message_queue=host_queue)

    result = ctx.push_message(
        visibility=[],
        ai_behavior="respond",
        parts=[{"type": "text", "text": "authenticated cue"}],
        metadata={"plugin_id": "impersonated-plugin"},
    )

    assert result == {"submitted": True}
    # The host stamps the sender itself, so a plugin cannot pass itself off as
    # another one by putting an id in its own payload.
    assert host_queue.items[0]["plugin_id"] == "demo"


@pytest.mark.plugin_unit
def test_fast_mode_uses_the_authenticated_batching_uplink(
    tmp_path: Path,
) -> None:
    host_queue = _Queue()
    ctx, _logger = _context(tmp_path, message_queue=host_queue)

    with pytest.warns(DeprecationWarning, match="fast_mode.*v0.9"):
        result = ctx.push_message(parts=[], fast_mode=True)

    assert result == {"submitted": True}
    assert host_queue.items == []
    assert len(host_queue.fast_items) == 1


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_async_wrapper_returns_fallback_queue_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _Queue()
    monkeypatch.setattr(context_module, "zmq", None)
    ctx, _logger = _context(tmp_path, message_queue=queue)

    result = await ctx.push_message_async(parts=[])

    assert result == {"submitted": True}
    assert len(queue.items) == 1


@pytest.mark.plugin_unit
def test_fallback_queue_failure_is_distinguishable_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_marker = "private-queue-error"
    queue = _Queue(error=RuntimeError(private_marker))
    monkeypatch.setattr(context_module, "zmq", None)
    ctx, logger = _context(tmp_path, message_queue=queue)

    result = ctx.push_message(
        parts=[{"type": "text", "text": private_marker}],
    )

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "transport_error",
    }
    assert private_marker not in repr(logger.records)
    assert "RuntimeError" in repr(logger.records)


@pytest.mark.plugin_unit
def test_fallback_queue_backpressure_is_classified_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_marker = "private-queue-backpressure"
    queue = _Queue(error=_Again(private_marker))
    monkeypatch.setattr(context_module, "zmq", SimpleNamespace(Again=_Again))
    ctx, logger = _context(tmp_path, message_queue=queue)

    result = ctx.push_message(
        parts=[{"type": "text", "text": private_marker}],
    )

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "backpressure",
    }
    assert private_marker not in repr(logger.records)
    assert "_Again" in repr(logger.records)


@pytest.mark.plugin_unit
def test_missing_transports_report_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_module, "zmq", None)
    ctx, _logger = _context(tmp_path)

    result = ctx.push_message(parts=[])

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "transport_unavailable",
    }
