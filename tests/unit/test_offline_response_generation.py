# -*- coding: utf-8 -*-
"""Response-generation invariants for ``OmniOfflineClient``.

In text mode ``prompt_ephemeral`` (proactive) and ``stream_text`` (a user turn)
overlap, and a bare ``_is_responding`` boolean cannot tell which of them set it.
Two real trampling paths follow: a response cancelled mid-reroll gets revived by
the reroll's own resume, and an older turn's ``finally`` clears the flag the
newer turn just raised. Making every set/clear name its generation turns both
into no-ops. These cases pin that down directly, because the production paths
that drive it (the long ``_streaming``/``_lifecycle`` flows) are impractical to
run end to end in a unit test.
"""
from __future__ import annotations

import pytest

from main_logic.omni_offline_client import OmniOfflineClient

pytestmark = pytest.mark.unit


def _make_client() -> OmniOfflineClient:
    """A bare client carrying only the state these paths touch."""
    client = object.__new__(OmniOfflineClient)
    client._is_responding = False
    client._response_generation = 0
    client._active_response_generation = None
    client._external_voice_submit_task = None
    client._pending_images = []
    client._genai_client = None
    client.llm = None
    return client


async def test_cancel_during_a_reroll_is_not_undone_by_the_resume():
    client = _make_client()
    generation = client._begin_response_generation()

    # stream_text 的 reroll：先暂停本世代，再在重试成功后恢复。
    assert client._pause_response_generation(generation) is True
    await client.cancel_response()

    assert client._resume_response_generation(generation) is False
    assert client._is_responding is False
    assert client._response_generation_is_active(generation) is False


async def test_an_older_turn_finishing_does_not_clear_the_newer_turns_flag():
    client = _make_client()
    older = client._begin_response_generation()
    newer = client._begin_response_generation()

    assert client._finish_response_generation(older) is False
    assert client._is_responding is True
    assert client._response_generation_is_active(newer) is True

    assert client._finish_response_generation(newer) is True
    assert client._is_responding is False


async def test_close_retires_the_active_generation_so_a_paused_turn_cannot_resume():
    client = _make_client()
    generation = client._begin_response_generation()
    client._pause_response_generation(generation)

    await client.close()

    assert client._resume_response_generation(generation) is False
    assert client._is_responding is False


async def test_handle_interruption_cancels_only_the_generation_that_is_live():
    client = _make_client()
    stale = client._begin_response_generation()
    client._finish_response_generation(stale)

    await client.handle_interruption()

    live = client._begin_response_generation()
    await client.handle_interruption()
    assert client._is_responding is False
    assert client._response_generation_is_active(live) is False
