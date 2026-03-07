from __future__ import annotations

import pytest

from plugin.sdk.call_chain import (
    AsyncCallChain,
    CallChain,
    CallChainTooDeepError,
    CircularCallError,
    get_call_chain,
    get_call_depth,
    is_in_call_chain,
)


@pytest.mark.plugin_unit
def test_call_chain_track_and_helpers() -> None:
    CallChain.clear()
    with CallChain.track("a.entry"):
        assert get_call_depth() == 1
        assert get_call_chain() == ["a.entry"]
        assert is_in_call_chain("a.entry") is True
        with CallChain.track("b.entry"):
            assert CallChain.get_depth() == 2
            assert "a.entry" in CallChain.format_chain()

    assert get_call_depth() == 0
    assert is_in_call_chain("a.entry") is False


@pytest.mark.plugin_unit
def test_call_chain_circular_detection() -> None:
    CallChain.clear()
    with CallChain.track("a.entry"):
        with pytest.raises(CircularCallError):
            with CallChain.track("a.entry"):
                pass


@pytest.mark.plugin_unit
def test_call_chain_depth_limit() -> None:
    CallChain.clear()
    with CallChain.track("a"):
        with pytest.raises(CallChainTooDeepError):
            with CallChain.track("b", max_depth=1):
                pass


@pytest.mark.plugin_unit
def test_async_call_chain_basic() -> None:
    if not AsyncCallChain.is_available():
        pytest.skip("AsyncCallChain contextvars unavailable")

    with AsyncCallChain.track("x.entry"):
        assert AsyncCallChain.get_depth() == 1
        assert AsyncCallChain.get_current_chain() == ["x.entry"]
