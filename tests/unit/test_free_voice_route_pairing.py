# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Guardrail: a free-tier voice is never delivered to the wrong region's server.

The two free routes serve disjoint voice catalogs — ``yui`` exists only on the
overseas node (free_intl, lanlan.app) and the ``voice-tone-*`` presets only on
the mainland node (free, lanlan.tech). Sending either to the other server is a
hard failure, so both directions must resolve to "deliver nothing" and let the
server pick its own default. The route is decided purely by the base_url of the
snapshot in hand, which is why a stale region verdict cannot desync the pairing.
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from main_logic.core import LLMSessionManager  # noqa: E402
from utils.api_config_loader import get_free_voices  # noqa: E402
from utils.tts.native_voice_registry import (  # noqa: E402
    ensure_builtin_native_voice_providers_loaded,
    is_free_preset_voice_id,
    is_saveable_native_voice,
)

ensure_builtin_native_voice_providers_loaded()

MAINLAND_URL = 'wss://www.lanlan.tech/core'
OVERSEAS_URL = 'wss://www.lanlan.app/core'
OVERSEAS_VOICE = 'yui'


def _mainland_voice() -> str:
    """The CN free preset bound to the default YUI character."""
    return get_free_voices()['yui_cn']


def _fake_cm(url):
    cfg = {
        'CORE_API_TYPE': 'free',
        'coreApi': 'free',
        'CORE_URL': url,
        'REALTIME_URL': url,
    }
    return SimpleNamespace(
        get_core_config=lambda: dict(cfg),
        voice_id_exists_in_any_storage=lambda _ref: False,
    )


def _mgr(voice_id, url):
    mgr = object.__new__(LLMSessionManager)
    mgr.core_api_type = 'free'
    mgr.voice_id = voice_id
    mgr._is_free_preset_voice = is_free_preset_voice_id(voice_id)
    mgr._config_manager = _fake_cm(url)
    mgr._is_livestream_active = lambda: False
    return mgr


def _delivered_voice(voice_id, url):
    """What _resolve_realtime_voice would hand to the realtime server on this route."""
    return LLMSessionManager._resolve_realtime_voice(_mgr(voice_id, url), {'base_url': url})


# ---------------------------------------------------------------------------
# Delivery gate: right route → delivered, wrong route → nothing
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_overseas_voice_is_delivered_on_overseas_route():
    assert _delivered_voice(OVERSEAS_VOICE, OVERSEAS_URL) == OVERSEAS_VOICE


@pytest.mark.unit
def test_overseas_voice_never_reaches_mainland_server():
    assert _delivered_voice(OVERSEAS_VOICE, MAINLAND_URL) is None


@pytest.mark.unit
def test_mainland_preset_is_delivered_on_mainland_route():
    voice = _mainland_voice()
    assert _delivered_voice(voice, MAINLAND_URL) == voice


@pytest.mark.unit
def test_mainland_preset_never_reaches_overseas_server():
    assert _delivered_voice(_mainland_voice(), OVERSEAS_URL) is None


@pytest.mark.unit
def test_every_mainland_preset_is_blocked_overseas():
    """Not just yui_cn: no voice-tone-* preset may leak to the overseas node."""
    for voice in sorted(set(get_free_voices().values())):
        assert _delivered_voice(voice, OVERSEAS_URL) is None, voice


# ---------------------------------------------------------------------------
# Storage side: only the overseas route may keep 'yui' on a character card
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize('url, saveable', [(OVERSEAS_URL, True), (MAINLAND_URL, False)])
def test_overseas_voice_saveable_only_on_overseas_route(url, saveable):
    assert is_saveable_native_voice(_fake_cm(url), OVERSEAS_VOICE) is saveable
