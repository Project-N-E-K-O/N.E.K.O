"""Turning Proactive Chat or its Screen Share option off ends the screen
access of proactive rounds already in flight (issue #3800).

Those switches only gate new requests in the browser. The frontend sends
``proactive_vision_invalidate`` on the allowed -> not allowed change; the
backend moves the proactive-vision revocation epoch, which every in-flight
round compares on each visual step. Unlike a consent revocation it keeps
consent granted and clears no frame cache (manual sharing is not governed by
these switches).
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from main_logic.core import LLMSessionManager
from main_logic.proactive_chat.generation import (
    _check_proactive_vision_consent,
    _proactive_vision_consent_checker,
)

from tests.unit.test_proactive_vision_revocation import _voice_manager, _voice_session
from tests.unit.test_proactive_vision_screenshot_staging import _make_mgr, _real_session

_REPO = Path(__file__).resolve().parents[2]


def _invalidate(mgr) -> None:
    LLMSessionManager.invalidate_proactive_vision_rounds(mgr)


@pytest.mark.unit
def test_invalidate_moves_only_the_proactive_epoch():
    session = SimpleNamespace(
        discard_auto_captured_history_images=MagicMock(),
        discard_cached_visual_context=MagicMock(),
    )
    frame = object()
    mgr = SimpleNamespace(
        session=session,
        _proactive_vision_enabled=True,
        _proactive_vision_revocation_epoch=3,
        _screen_consent_revocation_epoch=5,
        _latest_independent_visual_frame=frame,
        _prerecord_visual_frames=[frame],
    )

    _invalidate(mgr)
    _invalidate(mgr)

    assert mgr._proactive_vision_revocation_epoch == 5
    # Consent stays granted; manual-share caches and their epoch stay.
    assert mgr._proactive_vision_enabled is True
    assert mgr._screen_consent_revocation_epoch == 5
    assert mgr._latest_independent_visual_frame is frame
    assert mgr._prerecord_visual_frames == [frame]
    session.discard_auto_captured_history_images.assert_not_called()
    session.discard_cached_visual_context.assert_not_called()


@pytest.mark.unit
async def test_voice_nudge_in_flight_loses_the_screen():
    session = _voice_session()
    mgr = _voice_manager(session)
    mgr._proactive_vision_revocation_epoch = 0

    await LLMSessionManager.trigger_voice_proactive_nudge(mgr, allow_vision=True)
    vision_allowed = session.prompt_ephemeral.await_args.kwargs["vision_allowed"]
    assert vision_allowed() is True

    _invalidate(mgr)

    assert vision_allowed() is False


@pytest.mark.unit
async def test_voice_nudge_judges_vision_against_the_request_entry_epoch():
    """An invalidation between the HTTP entry and the nudge already counts."""
    session = _voice_session()
    mgr = _voice_manager(session)
    mgr._proactive_vision_revocation_epoch = 0
    entry_epoch = mgr._proactive_vision_revocation_epoch
    _invalidate(mgr)

    await LLMSessionManager.trigger_voice_proactive_nudge(
        mgr, allow_vision=True, vision_consent_epoch=entry_epoch,
    )
    vision_allowed = session.prompt_ephemeral.await_args.kwargs["vision_allowed"]
    assert vision_allowed() is False


@pytest.mark.unit
async def test_text_round_in_flight_loses_the_screen():
    mgr = SimpleNamespace(
        _proactive_vision_enabled=True, _proactive_vision_revocation_epoch=0,
    )
    check = _proactive_vision_consent_checker(mgr, mgr._proactive_vision_revocation_epoch)
    assert check() is True

    _invalidate(mgr)

    assert check() is False
    with pytest.raises(PermissionError):
        await _check_proactive_vision_consent(check)


@pytest.mark.unit
async def test_text_round_invalidated_before_commit_stages_no_screenshot():
    mgr = _make_mgr()
    mgr.current_speech_id = "s"
    mgr._proactive_vision_enabled = True
    mgr._proactive_vision_revocation_epoch = 0
    mgr.session = _real_session()
    round_epoch = mgr._proactive_vision_revocation_epoch

    _invalidate(mgr)

    assert await LLMSessionManager.finish_proactive_delivery(
        mgr, "你这屏幕上的图好好看～", expected_speech_id="s",
        vision_screenshot_b64="SHOT_B64",
        vision_consent_epoch=round_epoch,
    ) is True
    assert mgr.session._proactive_image_to_inject is None


@pytest.mark.unit
async def test_staged_screenshot_is_held_back_after_invalidate():
    mgr = _make_mgr()
    mgr.current_speech_id = "s"
    mgr._proactive_vision_enabled = True
    mgr._proactive_vision_revocation_epoch = 0
    mgr.session = _real_session()

    await LLMSessionManager.finish_proactive_delivery(
        mgr, "你这屏幕上的图好好看～", expected_speech_id="s",
        vision_screenshot_b64="SHOT_B64",
        vision_consent_epoch=mgr._proactive_vision_revocation_epoch,
    )
    staged_check = mgr.session._proactive_image_consent_check
    assert staged_check() is True

    _invalidate(mgr)

    # stream_text re-checks this before the staged screenshot goes out.
    assert staged_check() is False


@pytest.mark.unit
def test_router_dispatches_invalidate_without_closing_superseded_sockets():
    router_source = (_REPO / "main_routers" / "websocket_router.py").read_text(
        encoding="utf-8"
    )
    current = router_source.split('if action == "proactive_vision_invalidate":', 1)[1]
    assert current.split("continue", 1)[0].strip().endswith(
        "session_manager[lanlan_name].invalidate_proactive_vision_rounds()"
    )
    superseded_gate = router_source.split(
        "if (session_id.get(lanlan_name) != this_session_id", 1
    )[1].split('action = message.get("action")', 1)[0]
    branch = superseded_gate.split(
        'if message.get("action") == "proactive_vision_invalidate":', 1
    )[1].split("continue", 1)[0]
    assert "invalidate_proactive_vision_rounds()" in branch
    assert superseded_gate.index('"proactive_vision_invalidate"') < superseded_gate.index(
        "await websocket.close()"
    )
