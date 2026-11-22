"""Unit tests for streaming token-tracker wrappers on normal exit, break, close, and failure."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
import gc
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import utils.token_tracker as tt
from utils.token_tracker.hooks import _handle_async_stream, _handle_sync_stream


def _chunk(prompt_tokens: int | None = None, completion_tokens: int | None = None, model: str = "gpt-test"):
    if prompt_tokens is None:
        return SimpleNamespace(usage=None, model=model)
    total = (prompt_tokens or 0) + (completion_tokens or 0)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total,
        prompt_tokens_details=None,
    )
    return SimpleNamespace(usage=usage, model=model)


class _FakeSyncStream:
    def __init__(self, items, error: BaseException | None = None):
        self._items = list(items)
        self._error = error
        self.closed = False
        self.exited = False

    def __iter__(self):
        for item in self._items:
            yield item
        if self._error is not None:
            raise self._error

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.exited = True
        return None


class _FakeAsyncStream:
    def __init__(self, items, error: BaseException | None = None):
        self._items = list(items)
        self._error = error
        self.closed = False
        self.exited = False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for item in self._items:
            yield item
        if self._error is not None:
            raise self._error

    async def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.exited = True
        return None


def test_sync_stream_normal_exhaustion_records_last_usage_once():
    rec = MagicMock()
    stream = _FakeSyncStream([_chunk(10, 2), _chunk(10, 5)])
    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = _handle_sync_stream(
            SimpleNamespace(), lambda *_a, **_kw: stream, (), {"model": "gpt-test"}, "conversation"
        )
        list(wrapper)
        wrapper.close()
        del wrapper
        gc.collect()

    rec.assert_called_once_with(
        model="gpt-test",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        cached_tokens=0,
        call_type="conversation",
        success=True,
    )


def test_sync_stream_normal_exhaustion_without_usage_is_noop():
    rec = MagicMock()
    stream = _FakeSyncStream([_chunk(None), _chunk(None)])
    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = _handle_sync_stream(
            SimpleNamespace(), lambda *_a, **_kw: stream, (), {"model": "gpt-test"}, "conversation"
        )
        list(wrapper)
        wrapper.close()

    rec.assert_not_called()


def test_sync_stream_break_after_usage_chunk_records_once():
    rec = MagicMock()
    stream = _FakeSyncStream([_chunk(12, 3), _chunk(12, 8)])
    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = _handle_sync_stream(
            SimpleNamespace(), lambda *_a, **_kw: stream, (), {"model": "gpt-test"}, "conversation"
        )
        with wrapper:
            for _ in wrapper:
                break
        wrapper.close()
        del wrapper
        gc.collect()

    assert stream.exited is True
    assert stream.closed is True
    rec.assert_called_once_with(
        model="gpt-test",
        prompt_tokens=12,
        completion_tokens=3,
        total_tokens=15,
        cached_tokens=0,
        call_type="conversation",
        success=True,
    )


def test_sync_stream_break_before_usage_chunk_records_failed_call_once():
    rec = MagicMock()
    stream = _FakeSyncStream([_chunk(None), _chunk(10, 5)])
    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = _handle_sync_stream(
            SimpleNamespace(), lambda *_a, **_kw: stream, (), {"model": "gpt-early"}, "summary"
        )
        for _ in wrapper:
            break
        wrapper.close()

    rec.assert_called_once_with(
        model="gpt-early",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        call_type="summary",
        success=False,
    )


def test_sync_stream_error_mid_stream_records_failed_once_and_reraises():
    rec = MagicMock()
    stream = _FakeSyncStream([_chunk(None)], error=RuntimeError("boom"))
    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = _handle_sync_stream(
            SimpleNamespace(), lambda *_a, **_kw: stream, (), {"model": "gpt-err"}, "conversation"
        )
        with pytest.raises(RuntimeError, match="boom"):
            list(wrapper)

    rec.assert_called_once_with(
        model="gpt-err",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        call_type="conversation",
        success=False,
    )


def test_sync_stream_create_failure_records_once():
    rec = MagicMock()

    def failing_create(*_a, **_kw):
        raise RuntimeError("connect fail")

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        with pytest.raises(RuntimeError, match="connect fail"):
            _handle_sync_stream(
                SimpleNamespace(), failing_create, (), {"model": "gpt-create-err"}, "conversation"
            )

    rec.assert_called_once_with(
        model="gpt-create-err",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        call_type="conversation",
        success=False,
    )


@pytest.mark.asyncio
async def test_async_stream_normal_exhaustion_and_close_records_once():
    rec = MagicMock()
    stream = _FakeAsyncStream([_chunk(20, 4), _chunk(20, 9)])

    async def fake_create(*_a, **_kw):
        return stream

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = await _handle_async_stream(
            SimpleNamespace(), fake_create, (), {"model": "gpt-async"}, "conversation"
        )
        async for _ in wrapper:
            pass
        await wrapper.aclose()
        await wrapper.close()

    rec.assert_called_once_with(
        model="gpt-test",
        prompt_tokens=20,
        completion_tokens=9,
        total_tokens=29,
        cached_tokens=0,
        call_type="conversation",
        success=True,
    )


@pytest.mark.asyncio
async def test_async_stream_aclosing_break_before_usage_records_failed_once():
    rec = MagicMock()
    stream = _FakeAsyncStream([_chunk(None), _chunk(20, 9)])

    async def fake_create(*_a, **_kw):
        return stream

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = await _handle_async_stream(
            SimpleNamespace(), fake_create, (), {"model": "gpt-async-abort"}, "conversation"
        )
        async with aclosing(wrapper):
            async for _ in wrapper:
                break

    assert stream.closed is True
    rec.assert_called_once_with(
        model="gpt-async-abort",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        call_type="conversation",
        success=False,
    )


@pytest.mark.asyncio
async def test_async_stream_aenter_break_after_usage_and_gc_records_once():
    rec = MagicMock()
    stream = _FakeAsyncStream([_chunk(15, 5), _chunk(15, 10)])

    async def fake_create(*_a, **_kw):
        return stream

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = await _handle_async_stream(
            SimpleNamespace(), fake_create, (), {"model": "gpt-async-ctx"}, "conversation"
        )
        async with wrapper:
            async for _ in wrapper:
                break
        await wrapper.aclose()
        del wrapper
        gc.collect()

    assert stream.exited is True
    rec.assert_called_once_with(
        model="gpt-test",
        prompt_tokens=15,
        completion_tokens=5,
        total_tokens=20,
        cached_tokens=0,
        call_type="conversation",
        success=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_cls", [RuntimeError, asyncio.CancelledError])
async def test_async_stream_error_or_cancel_records_failed_once(exc_cls):
    rec = MagicMock()
    stream = _FakeAsyncStream([_chunk(None)], error=exc_cls("aborted"))

    async def fake_create(*_a, **_kw):
        return stream

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        wrapper = await _handle_async_stream(
            SimpleNamespace(), fake_create, (), {"model": "gpt-async-err"}, "emotion"
        )
        with pytest.raises(exc_cls):
            async for _ in wrapper:
                pass
        await wrapper.aclose()

    rec.assert_called_once_with(
        model="gpt-async-err",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        call_type="emotion",
        success=False,
    )


@pytest.mark.asyncio
async def test_async_stream_create_failure_records_once():
    rec = MagicMock()

    async def failing_create(*_a, **_kw):
        raise RuntimeError("async connect fail")

    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)):
        with pytest.raises(RuntimeError, match="async connect fail"):
            await _handle_async_stream(
                SimpleNamespace(), failing_create, (), {"model": "gpt-async-create-err"}, "conversation"
            )

    rec.assert_called_once_with(
        model="gpt-async-create-err",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        call_type="conversation",
        success=False,
    )
