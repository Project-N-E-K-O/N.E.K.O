"""Screen consent revocation and the voice nudge's screen-source gate.

Revoking consent must clear the frame a session cached for proactive turns,
and a voice nudge whose request did not enable the screen source must not
carry that frame. Prompt-level behavior per provider lives in
test_realtime_proactive_text.py.
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from main_logic.core import LLMSessionManager
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode
from main_logic.proactive_chat import contracts, service
from main_logic.proactive_chat.decisions import _voice_fast_path_allows_vision


DUMMY_IMAGE_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP/Z"


def _realtime_client() -> OmniRealtimeClient:
    client = OmniRealtimeClient(
        base_url="wss://www.lanlan.tech/api/v1/realtime",
        api_key="test-key",
        model="free-model",
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        api_type="free",
    )
    client.ws = AsyncMock()
    return client


# ── update_proactive_vision_consent ─────────────────────────────────────────


@pytest.mark.unit
async def test_revocation_clears_realtime_cached_frame():
    client = _realtime_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._latest_image_captured_at = 42.0
    client._proactive_image_consumed = False
    manager = SimpleNamespace(
        lanlan_name="Yui",
        session=client,
        _proactive_vision_enabled=True,
        _proactive_vision_revocation_epoch=0,
    )

    LLMSessionManager.update_proactive_vision_consent(manager, False)

    assert client._latest_image_b64 is None
    assert client._proactive_image_consumed is True
    assert manager._proactive_vision_enabled is False
    await client.close()


@pytest.mark.unit
@pytest.mark.parametrize("value", [True, None, "false", 0])
async def test_non_revocation_keeps_realtime_cached_frame(value):
    client = _realtime_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False
    manager = SimpleNamespace(
        lanlan_name="Yui",
        session=client,
        _proactive_vision_enabled=True,
        _proactive_vision_revocation_epoch=0,
    )

    LLMSessionManager.update_proactive_vision_consent(manager, value)

    assert client._latest_image_b64 == DUMMY_IMAGE_B64
    assert client._proactive_image_consumed is False
    await client.close()


@pytest.mark.unit
async def test_repeated_consent_false_does_not_clear_manual_share_frames():
    """consent=false is re-sent on every settings save; only the first one revokes."""
    client = _realtime_client()
    manager = SimpleNamespace(
        lanlan_name="Yui",
        session=client,
        _proactive_vision_enabled=True,
        _proactive_vision_revocation_epoch=0,
        _latest_independent_visual_frame=None,
        _prerecord_visual_frames=[],
    )
    LLMSessionManager.update_proactive_vision_consent(manager, False)

    # Privacy mode on, manual share keeps sending frames.
    manual_frame = object()
    manager._latest_independent_visual_frame = manual_frame
    manager._prerecord_visual_frames = [manual_frame]
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False
    discard_at = client._visual_context_discard_at

    LLMSessionManager.update_proactive_vision_consent(manager, False)

    assert manager._latest_independent_visual_frame is manual_frame
    assert manager._prerecord_visual_frames == [manual_frame]
    assert client._latest_image_b64 == DUMMY_IMAGE_B64
    assert client._visual_context_discard_at == discard_at
    assert manager._proactive_vision_revocation_epoch == 1
    await client.close()


@pytest.mark.unit
def test_revocation_clears_independent_asr_frame_cache():
    manager = SimpleNamespace(
        lanlan_name="Yui",
        session=None,
        _proactive_vision_enabled=True,
        _proactive_vision_revocation_epoch=0,
        _latest_independent_visual_frame=object(),
        _prerecord_visual_frames=[object(), object()],
    )

    LLMSessionManager.update_proactive_vision_consent(manager, False)

    assert manager._latest_independent_visual_frame is None
    assert manager._prerecord_visual_frames == []


@pytest.mark.unit
def test_revocation_clears_offline_staged_proactive_screenshot():
    session = OmniOfflineClient.__new__(OmniOfflineClient)
    session.set_proactive_screenshot = MagicMock()
    manager = SimpleNamespace(
        lanlan_name="Yui",
        session=session,
        _proactive_vision_enabled=True,
        _proactive_vision_revocation_epoch=0,
    )

    LLMSessionManager.update_proactive_vision_consent(manager, False)

    session.set_proactive_screenshot.assert_called_once_with(None)


@pytest.mark.unit
def test_revocation_survives_a_failing_session_discard():
    session = SimpleNamespace(
        discard_cached_visual_context=MagicMock(side_effect=RuntimeError("boom")),
    )
    manager = SimpleNamespace(
        lanlan_name="Yui",
        session=session,
        _proactive_vision_enabled=True,
        _proactive_vision_revocation_epoch=0,
    )

    LLMSessionManager.update_proactive_vision_consent(manager, False)

    assert manager._proactive_vision_enabled is False
    assert manager._proactive_vision_revocation_epoch == 1


# ── trigger_voice_proactive_nudge → prompt_ephemeral(vision_allowed=...) ───


def _voice_manager(session):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Yui"
    mgr.user_language = "en"
    mgr.session = session
    mgr.is_active = True
    mgr.is_hot_swap_imminent = False
    mgr._takeover_active = False
    mgr._voice_proactive_inject_lock = asyncio.Lock()
    mgr.is_goodbye_silent = lambda: False
    mgr._independent_asr_user_turn_active = lambda: False
    mgr._proactive_vision_enabled = True
    return mgr


def _voice_session():
    session = OmniRealtimeClient.__new__(OmniRealtimeClient)
    session._proactive_inject_awaiting_outcome = False
    session.prompt_ephemeral = AsyncMock(return_value=True)
    return session


@pytest.mark.unit
@pytest.mark.parametrize(
    ("allow_vision", "consent", "expected"),
    [
        (True, True, True),
        (True, None, True),
        (False, True, False),
        (True, False, False),
    ],
)
async def test_voice_nudge_forwards_screen_gate(allow_vision, consent, expected):
    session = _voice_session()
    mgr = _voice_manager(session)
    mgr._proactive_vision_enabled = consent

    assert await LLMSessionManager.trigger_voice_proactive_nudge(
        mgr, allow_vision=allow_vision,
    ) is True

    vision_allowed = session.prompt_ephemeral.await_args.kwargs["vision_allowed"]
    assert vision_allowed() is expected


@pytest.mark.unit
async def test_voice_nudge_gate_reads_consent_live():
    """prompt_ephemeral re-reads the gate after its awaits; it must see a revocation."""
    session = _voice_session()
    mgr = _voice_manager(session)

    await LLMSessionManager.trigger_voice_proactive_nudge(mgr)
    vision_allowed = session.prompt_ephemeral.await_args.kwargs["vision_allowed"]
    assert vision_allowed() is True

    mgr._proactive_vision_enabled = False
    assert vision_allowed() is False


@pytest.mark.unit
async def test_voice_nudge_gate_stays_closed_after_revoke_and_regrant():
    """A quick off/on must not restore vision for the nudge already in flight."""
    session = _voice_session()
    mgr = _voice_manager(session)
    mgr._proactive_vision_revocation_epoch = 0

    await LLMSessionManager.trigger_voice_proactive_nudge(mgr)
    vision_allowed = session.prompt_ephemeral.await_args.kwargs["vision_allowed"]

    LLMSessionManager.update_proactive_vision_consent(mgr, False)
    LLMSessionManager.update_proactive_vision_consent(mgr, True)

    assert mgr._proactive_vision_enabled is True
    assert vision_allowed() is False


@pytest.mark.unit
async def test_voice_nudge_revocation_while_waiting_for_the_lock_closes_vision():
    session = _voice_session()
    mgr = _voice_manager(session)
    mgr._proactive_vision_revocation_epoch = 0
    await mgr._voice_proactive_inject_lock.acquire()

    nudge = asyncio.create_task(LLMSessionManager.trigger_voice_proactive_nudge(mgr))
    await asyncio.sleep(0)
    LLMSessionManager.update_proactive_vision_consent(mgr, False)
    LLMSessionManager.update_proactive_vision_consent(mgr, True)
    mgr._voice_proactive_inject_lock.release()
    assert await nudge is True

    vision_allowed = session.prompt_ephemeral.await_args.kwargs["vision_allowed"]
    assert vision_allowed() is False


# ── service voice fast path: enabled_modes decides allow_vision ─────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, True),  # older client without the field keeps the old behavior
        ({"enabled_modes": ["vision"]}, True),
        ({"enabled_modes": ["vision", "music"]}, True),
        ({"enabled_modes": []}, False),
        ({"enabled_modes": ["music"]}, False),
        ({"enabled_modes": ["window"]}, False),
        ({"enabled_modes": None}, False),
        ({"enabled_modes": "vision"}, False),
    ],
)
def test_voice_fast_path_allows_vision_follows_enabled_modes(payload, expected):
    command = contracts.ProactiveChatCommand.from_payload(payload)
    assert _voice_fast_path_allows_vision(
        enabled_modes=command.enabled_modes,
        enabled_modes_provided=command.enabled_modes_provided,
    ) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("enabled_modes", "expected"),
    [([], False), (["vision"], True)],
)
async def test_voice_fast_path_passes_allow_vision_to_nudge(
    monkeypatch, enabled_modes, expected,
):
    class _VoiceSession:
        pass

    monkeypatch.setattr(service, "OmniRealtimeClient", _VoiceSession)
    monkeypatch.setattr(
        service,
        "_advance_mini_game_invite_entry",
        lambda *_args, **_kwargs: None,
    )
    trigger = AsyncMock(return_value=False)
    manager = SimpleNamespace(
        is_active=True,
        session=_VoiceSession(),
        is_goodbye_silent=lambda: False,
        last_user_message_time=None,
        state=SimpleNamespace(can_start_proactive=lambda *, session: True),
        trigger_voice_proactive_nudge=trigger,
        user_language="en",
        _user_language_explicit=False,
        _conversation_render_language=None,
    )

    await service.handle_proactive_chat(
        contracts.ProactiveChatCommand.from_payload({
            "lanlan_name": "Yui",
            "voice_mode": True,
            "enabled_modes": enabled_modes,
        }),
        config_manager=SimpleNamespace(),
        session_manager=SimpleNamespace(get=lambda _lanlan_name: manager),
        character_data=("Master", "Yui", None, None, None, {}, None, None, None),
        game_route_active_for=lambda _lanlan_name: False,
        break_config_manager_provider=SimpleNamespace,
        run_mini_game_invite_short_circuit=AsyncMock(),
        push_mini_game_invite_options=AsyncMock(),
        push_mini_game_invite_resolved=AsyncMock(),
    )

    trigger.assert_awaited_once()
    assert trigger.await_args.kwargs["allow_vision"] is expected
