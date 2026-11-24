# -*- coding: utf-8 -*-
"""The LLM retry exception lists must not import the Anthropic SDK.

They are evaluated in ``except`` clauses on the event loop, for every
exception leaving an LLM call, a cancelled stream included. With the SDK no
longer warmed for users without an Anthropic model, importing it there would
stall the loop on the first interrupted reply. Until a client has loaded the
SDK no Anthropic error can be in flight, so the lists simply leave it out --
and must not cache that answer, or a client built later would lose its retries.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from utils.llm_client import retry

pytestmark = pytest.mark.unit


class _Err(Exception):
    pass


def _fake_anthropic(*names):
    module = types.ModuleType("anthropic")
    for name in names:
        setattr(module, name, type(name, (_Err,), {}))
    return module


@pytest.fixture
def no_anthropic(monkeypatch):
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    monkeypatch.setattr(retry, "_ANTHROPIC_RETRY_EXCEPTION_TYPES", None)

    def _load(*names):
        names = names or retry._ANTHROPIC_RETRY_EXCEPTION_NAMES
        module = _fake_anthropic(*names)
        monkeypatch.setitem(sys.modules, "anthropic", module)
        return module

    return _load


def test_unloaded_sdk_gives_no_types_and_stays_unloaded(no_anthropic):
    assert retry.anthropic_retry_error_types() == ()
    assert "anthropic" not in sys.modules
    assert retry._ANTHROPIC_RETRY_EXCEPTION_TYPES is None


def test_types_appear_once_a_client_loaded_the_sdk(no_anthropic):
    assert retry.anthropic_retry_error_types() == ()
    module = no_anthropic()
    found = retry.anthropic_retry_error_types()
    assert found == tuple(getattr(module, n) for n in retry._ANTHROPIC_RETRY_EXCEPTION_NAMES)
    assert retry._ANTHROPIC_RETRY_EXCEPTION_TYPES == found


def test_half_imported_sdk_is_not_cached(no_anthropic):
    no_anthropic("APIConnectionError")
    assert len(retry.anthropic_retry_error_types()) == 1
    assert retry._ANTHROPIC_RETRY_EXCEPTION_TYPES is None


def test_offline_client_retry_list_is_not_frozen_without_anthropic(no_anthropic, monkeypatch):
    from main_logic.omni_offline_client import _shared

    monkeypatch.setattr(_shared, "_LLM_RETRY_ERROR_TYPES", None)
    first = _shared._llm_retry_error_types()
    assert _shared._LLM_RETRY_ERROR_TYPES is None
    module = no_anthropic()
    second = _shared._llm_retry_error_types()
    assert module.RateLimitError in second
    assert set(first) < set(second)
    assert _shared._LLM_RETRY_ERROR_TYPES == second


def test_proactive_retry_list_is_not_frozen_without_anthropic(no_anthropic, monkeypatch):
    from main_logic.proactive_chat import generation

    monkeypatch.setattr(generation, "_PROACTIVE_LLM_RETRY_ERROR_TYPES", None)
    first = generation._proactive_llm_retry_error_types()
    assert asyncio.TimeoutError in first
    assert generation._PROACTIVE_LLM_RETRY_ERROR_TYPES is None
    module = no_anthropic()
    second = generation._proactive_llm_retry_error_types()
    assert module.APITimeoutError in second
    assert generation._PROACTIVE_LLM_RETRY_ERROR_TYPES == second


def test_snapshot_reports_final_only_for_the_full_set(no_anthropic):
    assert retry.chat_retry_error_types_snapshot()[1] is False
    no_anthropic("APIConnectionError", "RateLimitError")
    types, final = retry.chat_retry_error_types_snapshot()
    assert final is False
    assert sys.modules["anthropic"].RateLimitError in types
    no_anthropic()
    assert retry.chat_retry_error_types_snapshot()[1] is True


def test_offline_client_does_not_cache_a_half_imported_sdk(no_anthropic, monkeypatch):
    from main_logic.omni_offline_client import _shared

    monkeypatch.setattr(_shared, "_LLM_RETRY_ERROR_TYPES", None)
    no_anthropic("APIConnectionError")
    assert sys.modules["anthropic"].APIConnectionError in _shared._llm_retry_error_types()
    assert _shared._LLM_RETRY_ERROR_TYPES is None
    module = no_anthropic()
    assert module.InternalServerError in _shared._llm_retry_error_types()
    assert _shared._LLM_RETRY_ERROR_TYPES is not None


def test_proactive_does_not_cache_a_half_imported_sdk(no_anthropic, monkeypatch):
    from main_logic.proactive_chat import generation

    monkeypatch.setattr(generation, "_PROACTIVE_LLM_RETRY_ERROR_TYPES", None)
    no_anthropic("APIConnectionError")
    generation._proactive_llm_retry_error_types()
    assert generation._PROACTIVE_LLM_RETRY_ERROR_TYPES is None
    module = no_anthropic()
    assert module.InternalServerError in generation._proactive_llm_retry_error_types()
    assert generation._PROACTIVE_LLM_RETRY_ERROR_TYPES is not None


@pytest.mark.parametrize("caller", ["offline", "proactive"])
def test_callers_use_one_read_for_list_and_finality(caller, no_anthropic, monkeypatch):
    """The SDK finishing its import between building the list and checking it
    must not get the earlier, partial list cached."""
    from main_logic.omni_offline_client import _shared
    from main_logic.proactive_chat import generation

    monkeypatch.setattr(_shared, "_LLM_RETRY_ERROR_TYPES", None)
    monkeypatch.setattr(generation, "_PROACTIVE_LLM_RETRY_ERROR_TYPES", None)
    no_anthropic("APIConnectionError")
    real_snapshot = retry._anthropic_retry_error_types_snapshot
    reads = []

    def _finishes_after_first_read():
        result = real_snapshot()
        if not reads:
            no_anthropic()  # the import completes on another thread
        reads.append(result)
        return result

    monkeypatch.setattr(retry, "_anthropic_retry_error_types_snapshot", _finishes_after_first_read)
    if caller == "offline":
        _shared._llm_retry_error_types()
        assert _shared._LLM_RETRY_ERROR_TYPES is None
    else:
        generation._proactive_llm_retry_error_types()
        assert generation._PROACTIVE_LLM_RETRY_ERROR_TYPES is None
