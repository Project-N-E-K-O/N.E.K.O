"""Screen consent revoked around a text-mode proactive screenshot (#3788).

A proactive round that used the screen stages its screenshot on the text
session, and the user's next text reply carries it to the model. Two windows
let a revoked screenshot through:

1. Staging: ``finish_proactive_delivery`` looked only at the current switch,
   so turning consent off and on again before the commit staged a screenshot
   the round took under the revoked consent.
2. Sending: ``stream_text`` copies the staged screenshot into a local before
   awaiting the vision-model switch and the image fit. A revocation during
   those awaits cleared the slot but not the copy.

The rule is the one the proactive round itself uses: consent holds only while
the switch is on AND the revocation epoch is the one the round started under.
"""
import asyncio
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.core import LLMSessionManager
from main_logic.omni_offline_client import _streaming
from utils.llm_client import AIMessage

from tests.unit.test_proactive_vision_screenshot_staging import (
    _image_urls,
    _last_user_message,
    _make_mgr,
    _make_offline_for_stream,
    _real_session,
)


def _revoke(mgr) -> None:
    LLMSessionManager.update_proactive_vision_consent(mgr, False)


def _regrant(mgr) -> None:
    LLMSessionManager.update_proactive_vision_consent(mgr, True)


def _consent_mgr() -> LLMSessionManager:
    mgr = _make_mgr()
    mgr.current_speech_id = "s"
    mgr._proactive_vision_enabled = True
    mgr._proactive_vision_revocation_epoch = 0
    return mgr


# ── 1. staging is bound to the round's epoch ─────────────────────────────────

@pytest.mark.asyncio
async def test_revoked_and_regranted_round_does_not_stage_its_screenshot():
    mgr = _consent_mgr()
    mgr.session = _real_session()
    mgr.session._proactive_image_to_inject = "OLDER_SHOT"
    round_epoch = mgr._proactive_vision_revocation_epoch
    # Revoked and granted again between generation and the commit.
    _revoke(mgr)
    _regrant(mgr)

    committed = await LLMSessionManager.finish_proactive_delivery(
        mgr, "你这屏幕上的图好好看～", expected_speech_id="s",
        vision_screenshot_b64="SHOT_B64",
        vision_consent_epoch=round_epoch,
    )

    assert committed is True
    # Neither this round's screenshot nor an older one stays staged.
    assert mgr.session._proactive_image_to_inject is None


@pytest.mark.asyncio
async def test_round_under_unchanged_consent_stages_its_screenshot():
    mgr = _consent_mgr()
    mgr.session = _real_session()

    await LLMSessionManager.finish_proactive_delivery(
        mgr, "你这屏幕上的图好好看～", expected_speech_id="s",
        vision_screenshot_b64="SHOT_B64",
        vision_consent_epoch=mgr._proactive_vision_revocation_epoch,
    )

    assert mgr.session._proactive_image_to_inject == "SHOT_B64"
    assert mgr.session._proactive_image_consent_check() is True
    # The staged check follows the round's epoch, not just the switch.
    _revoke(mgr)
    _regrant(mgr)
    assert mgr.session._proactive_image_consent_check() is False


# ── 2. stream_text re-checks the copy it already holds ───────────────────────

def _stage_under(mgr, client) -> None:
    epoch = mgr._proactive_vision_revocation_epoch
    client.set_proactive_screenshot(
        "PROACTIVE_B64",
        consent_check=lambda: (
            mgr._proactive_vision_enabled is not False
            and mgr._proactive_vision_revocation_epoch == epoch
        ),
    )


def test_revocation_before_the_reply_sends_text_only():
    mgr = _consent_mgr()
    c, captured = _make_offline_for_stream()
    _stage_under(mgr, c)
    _revoke(mgr)
    _regrant(mgr)

    asyncio.run(c.stream_text("这是什么呀"))

    msg = _last_user_message(captured)
    assert msg.content == "这是什么呀"
    c.switch_model.assert_not_awaited()
    assert c._proactive_image_to_inject is None


@pytest.mark.parametrize("user_attachment", [False, True])
def test_revocation_during_model_switch_drops_only_the_proactive_screenshot(user_attachment):
    mgr = _consent_mgr()
    c, captured = _make_offline_for_stream()
    if user_attachment:
        c._pending_images = ["USER_FRAME_B64"]
    _stage_under(mgr, c)

    async def revoke_while_switching(*_args, **_kwargs):
        _revoke(mgr)

    c.switch_model.side_effect = revoke_while_switching

    asyncio.run(c.stream_text("这是什么呀"))

    msg = _last_user_message(captured)
    if user_attachment:
        assert _image_urls(msg.content) == ["data:image/jpeg;base64,USER_FRAME_B64"]
    else:
        assert msg.content == "这是什么呀"
    assert c._proactive_image_to_inject is None


@pytest.mark.parametrize("user_attachment", [False, True])
def test_revocation_during_image_fit_drops_only_the_proactive_screenshot(
    monkeypatch, user_attachment,
):
    mgr = _consent_mgr()
    c, captured = _make_offline_for_stream()
    if user_attachment:
        c._pending_images = ["USER_FRAME_B64"]
    _stage_under(mgr, c)
    real_fit = _streaming.fit_images_to_turn_budget
    fitted: list = []

    async def revoke_while_fitting(images, budget):
        fitted.append(list(images))
        if len(fitted) == 1:
            _revoke(mgr)
            _regrant(mgr)
        return await real_fit(images, budget)

    monkeypatch.setattr(_streaming, "fit_images_to_turn_budget", revoke_while_fitting)

    asyncio.run(c.stream_text("这是什么呀"))

    msg = _last_user_message(captured)
    if user_attachment:
        assert _image_urls(msg.content) == ["data:image/jpeg;base64,USER_FRAME_B64"]
    else:
        assert msg.content == "这是什么呀"
    assert "PROACTIVE_B64" in fitted[0]
    assert c._proactive_image_to_inject is None


def test_screenshot_still_sent_while_consent_holds():
    mgr = _consent_mgr()
    c, captured = _make_offline_for_stream()
    _stage_under(mgr, c)

    asyncio.run(c.stream_text("这是什么呀"))

    msg = _last_user_message(captured)
    assert _image_urls(msg.content) == ["data:image/jpeg;base64,PROACTIVE_B64"]


def test_unreadable_consent_does_not_send_the_screenshot():
    c, captured = _make_offline_for_stream()

    def broken_check() -> bool:
        raise RuntimeError("consent unreadable")

    c.set_proactive_screenshot("PROACTIVE_B64", consent_check=broken_check)

    asyncio.run(c.stream_text("这是什么呀"))

    assert _last_user_message(captured).content == "这是什么呀"


# ── 3. end to end: commit, then revoke and re-grant before the user replies ──

@pytest.mark.asyncio
async def test_committed_screenshot_not_sent_after_revoke_and_regrant():
    mgr = _consent_mgr()
    c, captured = _make_offline_for_stream()
    mgr.session = c

    assert await LLMSessionManager.finish_proactive_delivery(
        mgr, "你这屏幕上的图好好看～", expected_speech_id="s",
        vision_screenshot_b64="SHOT_B64",
        vision_consent_epoch=mgr._proactive_vision_revocation_epoch,
    ) is True
    assert isinstance(c._conversation_history[-1], AIMessage)

    async def revoke_and_regrant(*_args, **_kwargs):
        _revoke(mgr)
        _regrant(mgr)

    c.switch_model.side_effect = revoke_and_regrant

    await c.stream_text("这是什么呀")

    assert _last_user_message(captured).content == "这是什么呀"


# ── 4. the round's epoch reaches the staging point ───────────────────────────

@pytest.mark.asyncio
async def test_delivery_forwards_the_round_epoch_to_staging():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from main_logic.proactive_chat import delivery

    mgr = SimpleNamespace(
        current_speech_id="s", last_user_engagement_time=0,
        state=SimpleNamespace(proactive_sid="s", is_proactive_preempted=lambda sid: False),
        handle_new_message=AsyncMock(),
        feed_tts_chunk=AsyncMock(return_value=True),
        finish_proactive_delivery=AsyncMock(return_value=True),
    )

    await delivery._commit_proactive_delivery(
        mgr=mgr, proactive_sid="s", lanlan_name="test", response_text="你这屏幕上的图好好看～",
        source_tag="CHAT", active_channels=["vision"], selected_web_link=None,
        selected_music_link=None, selected_meme_link=None, music_content=None,
        is_music_used=False, is_playing_music=False, music_cooldown=False,
        vision_content=None, phase2_use_vision=True, screenshot_b64="SHOT_B64",
        proactive_lang="zh", master_name="主人", vision_consent_epoch=7,
    )

    kwargs = mgr.finish_proactive_delivery.await_args.kwargs
    assert kwargs["vision_screenshot_b64"] == "SHOT_B64"
    assert kwargs["vision_consent_epoch"] == 7


def test_service_commits_under_the_epoch_the_round_started_with():
    import inspect

    from main_logic.proactive_chat import service

    source = inspect.getsource(service.handle_proactive_chat)
    commit = source.index("delivery_commit = await _commit_proactive_delivery(")
    call = source[commit:source.index("\n        )\n", commit)]
    assert "vision_consent_epoch=vision_consent_epoch" in call
