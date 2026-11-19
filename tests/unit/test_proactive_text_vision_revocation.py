"""Screen consent revoked around a text-mode proactive screenshot (#3788).

A proactive round that used the screen stages its screenshot on the text
session, and the user's next text reply carries it to the model. Two windows
let a revoked screenshot through:

1. Staging: ``finish_proactive_delivery`` looked only at the current switch,
   so turning consent off and on again before the commit staged a screenshot
   the round took under the revoked consent.
2. Sending: ``stream_text`` copies the staged screenshot into a local before
   awaiting the vision-model switch, the image fit, the transcript callback
   and the retry backoff. A revocation during those awaits cleared the slot
   but not the copy, which then went out with the message.

The rule is the one the proactive round itself uses: consent holds only while
the switch is on AND the revocation epoch is the one the round started under.
"""
import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.core import LLMSessionManager
from main_logic.omni_offline_client import _streaming
from utils.image_history import image_receipt
from utils.llm_client import AIMessage, HumanMessage

from tests.unit.test_offline_provider_frame_publish import (
    _PUBLISHER,
    _SLEEP,
    _Spy,
    _connection_error,
    _decode,
    _make_client,
    _png_b64,
    _run_turn,
)
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
    staged_check = mgr.session._proactive_image_consent_check
    assert staged_check() is True
    _revoke(mgr)
    _regrant(mgr)
    # Revocation discards the staged screenshot together with its check.
    assert mgr.session._proactive_image_to_inject is None
    assert mgr.session._proactive_image_consent_check is None
    # The staged check itself follows the round's epoch, not just the switch,
    # for a copy taken before the discard (e.g. stream_text's local).
    assert staged_check() is False


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


def _assert_sent_without_screenshot(captured: list, user_attachment: bool) -> None:
    msg = _last_user_message(captured)
    expected = ["data:image/jpeg;base64,USER_FRAME_B64"] if user_attachment else []
    assert _image_urls(msg.content) == expected
    texts = [
        part for part in msg.content
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    # A revocation that reached the message through the history strip leaves
    # a receipt after the user's text; no other text is added.
    if texts[-1]["text"] != "这是什么呀":
        assert texts[-1]["text"] == image_receipt(1)
        texts = texts[:-1]
    assert texts[-1] == {"type": "text", "text": "这是什么呀"}


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

    _assert_sent_without_screenshot(captured, user_attachment)
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

    _assert_sent_without_screenshot(captured, user_attachment)
    assert fitted == [["PROACTIVE_B64"] + (["USER_FRAME_B64"] if user_attachment else [])]
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


def test_screenshot_staged_by_a_newer_round_during_the_fit_survives(monkeypatch):
    c, _captured = _make_offline_for_stream()
    c.set_proactive_screenshot("PROACTIVE_B64")
    real_fit = _streaming.fit_images_to_turn_budget

    async def newer_round_stages_while_fitting(images, budget):
        c.set_proactive_screenshot("NEWER_B64")
        return await real_fit(images, budget)

    monkeypatch.setattr(_streaming, "fit_images_to_turn_budget", newer_round_stages_while_fitting)

    asyncio.run(c.stream_text("这是什么呀"))

    assert c._proactive_image_to_inject == "NEWER_B64"


# ── 2b. after the message is in history: transcript callback, retry backoff ─

def _is_red(b64: str) -> bool:
    return _decode(b64).convert("RGB").getpixel((0, 0))[0] > 150


def _snapshotting_client(mgr, *, error=None):
    """Client whose stream records the image parts of each request as sent.

    The message objects are shared with history, so a later in-place change
    would otherwise rewrite what an earlier request appears to have carried.
    """
    client, _ = _make_client(error=error)
    user_frame = _png_b64(300, 200, (200, 30, 30))
    client._pending_images = [user_frame]
    shot = _png_b64(300, 200, (30, 200, 30))
    epoch = mgr._proactive_vision_revocation_epoch
    client.set_proactive_screenshot(
        shot,
        consent_check=lambda: (
            mgr._proactive_vision_enabled is not False
            and mgr._proactive_vision_revocation_epoch == epoch
        ),
    )
    requests: list[list[str]] = []

    async def _fake_astream(messages, **_overrides):
        requests.append([
            part["image_url"]["url"].split(",", 1)[1]
            for part in messages[-1].content
            if isinstance(part, dict) and part.get("type") == "image_url"
        ])
        yield SimpleNamespace(content="")
        raise error() if error is not None else RuntimeError("stop")

    client._astream_visible_with_tools = _fake_astream
    return client, shot, user_frame, requests


def test_revocation_in_the_transcript_callback_withholds_the_screenshot():
    mgr = _consent_mgr()
    client, _shot, _user_frame, requests = _snapshotting_client(mgr)

    async def revoke_in_callback(_text):
        _revoke(mgr)
        _regrant(mgr)

    client.on_input_transcript = revoke_in_callback
    spy = _Spy()
    with patch(_PUBLISHER, spy):
        _run_turn(client.stream_text("这是什么呀"))

    assert len(requests) == 1 and len(requests[0]) == 1
    assert _is_red(requests[0][0])  # only the user's frame went out
    # The history entry no longer carries the screenshot either.
    history_msg = next(
        m for m in reversed(client._conversation_history) if isinstance(m, HumanMessage)
    )
    assert [p.get("type") for p in history_msg.content] == ["image_url", "text"]
    # The frame bus sees what the model saw: the user's frame, labelled as such.
    assert spy.sources == ["user"]
    assert spy.images == requests[0]


def test_withheld_screenshot_keeps_history_image_kinds_aligned():
    """The in-turn withhold drops the screenshot's provenance entry with it."""
    mgr = _consent_mgr()
    client, _shot, _user_frame, _requests = _snapshotting_client(mgr)

    async def revoke_in_callback(_text):
        # Only the staged check notices: no session strip runs here.
        mgr._proactive_vision_enabled = False

    client.on_input_transcript = revoke_in_callback
    with patch(_PUBLISHER, _Spy()):
        _run_turn(client.stream_text("这是什么呀"))

    history_msg = next(
        m for m in reversed(client._conversation_history) if isinstance(m, HumanMessage)
    )
    assert [p.get("type") for p in history_msg.content] == ["image_url", "text"]
    assert history_msg.metadata["image_kinds"] == ["user"]


def test_session_strip_then_withhold_keeps_the_users_image():
    """Revocation strips history first; the withhold must not take the next image.

    With the session wired to the manager, revoking strips the screenshot from
    the message in history (leaving a receipt) before the attempt's withhold
    check runs. The first image left is then the user's own frame.
    """
    mgr = _consent_mgr()
    client, _shot, _user_frame, requests = _snapshotting_client(mgr)
    mgr.session = client

    async def revoke_in_callback(_text):
        _revoke(mgr)

    client.on_input_transcript = revoke_in_callback
    spy = _Spy()
    with patch(_PUBLISHER, spy):
        _run_turn(client.stream_text("这是什么呀"))

    assert len(requests) == 1 and len(requests[0]) == 1
    assert _is_red(requests[0][0])  # the user's frame still went out
    history_msg = next(
        m for m in reversed(client._conversation_history) if isinstance(m, HumanMessage)
    )
    assert history_msg.metadata["image_kinds"] == ["user"]
    assert history_msg.content[-1] == {"type": "text", "text": image_receipt(1)}
    assert spy.sources == ["user"]
    assert spy.images == requests[0]


def test_withheld_sole_screenshot_publishes_nothing_on_the_frame_bus(monkeypatch):
    mgr = _consent_mgr()
    client, _shot, _user_frame, requests = _snapshotting_client(mgr)
    client._pending_images = []

    async def revoke_in_callback(_text):
        _revoke(mgr)

    client.on_input_transcript = revoke_in_callback
    published: list = []

    async def record_publish(images, sources, **_kwargs):
        published.append((list(images), list(sources)))

    monkeypatch.setattr(client, "_publish_provider_frames", record_publish)
    with patch(_PUBLISHER, _Spy()):
        _run_turn(client.stream_text("这是什么呀"))

    assert requests == [[]]
    assert published == []


def test_screenshot_sent_on_the_first_attempt_is_withheld_from_retries_after_revocation():
    mgr = _consent_mgr()
    client, _shot, _user_frame, requests = _snapshotting_client(mgr, error=_connection_error)
    sleeps: list = []

    async def revoke_during_backoff(_delay):
        sleeps.append(_delay)
        if len(sleeps) == 1:
            _revoke(mgr)

    with patch(_SLEEP, revoke_during_backoff), patch(_PUBLISHER, _Spy()):
        _run_turn(client.stream_text("这是什么呀"))

    assert len(requests) == 3
    # Consent held on the first attempt: screenshot (green) + user frame (red).
    assert [_is_red(img) for img in requests[0]] == [False, True]
    assert all(len(r) == 1 and _is_red(r[0]) for r in requests[1:])


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
    assert c._proactive_image_to_inject == "SHOT_B64"

    async def revoke_and_regrant(*_args, **_kwargs):
        _revoke(mgr)
        _regrant(mgr)

    c.switch_model.side_effect = revoke_and_regrant

    await c.stream_text("这是什么呀")

    # The revocation really landed inside the turn that carried the screenshot.
    c.switch_model.assert_awaited_once()
    _assert_sent_without_screenshot(captured, user_attachment=False)


# ── 4. the round's epoch reaches the staging point ───────────────────────────

@pytest.mark.asyncio
async def test_delivery_forwards_the_round_epoch_to_staging():
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
    # Bound once at entry, so the commit sees the epoch the round started under.
    assert source.count("vision_consent_epoch =") == 1


# ── 5. a failed Gemini request falls back to OpenAI-compat within one attempt ─

@pytest.mark.parametrize("revoke", [False, True])
def test_revocation_before_the_gemini_fallback_withholds_the_screenshot(monkeypatch, revoke):
    from main_logic.omni_offline_client import OmniOfflineClient

    mgr = _consent_mgr()
    c, _captured = _make_offline_for_stream()
    # Run the real tool-aware stream down to the provider split.
    del c._astream_visible_with_tools
    c._use_genai_sdk = True
    c._genai_tools_unsupported = False
    _stage_under(mgr, c)
    requests: list[list[str]] = []

    def _image_parts(messages):
        return _image_urls(messages[-1].content)

    async def genai_fails_before_any_text(self, messages, **_overrides):
        requests.append(_image_parts(messages))
        if revoke:
            _revoke(mgr)
        raise RuntimeError("HTTP 503 before any chunk")
        yield  # pragma: no cover — mark as an async generator.

    async def openai_fallback(self, messages, **_overrides):
        requests.append(_image_parts(messages))
        raise RuntimeError("stop")
        yield  # pragma: no cover — mark as an async generator.

    monkeypatch.setattr(OmniOfflineClient, "_astream_genai_with_tools", genai_fails_before_any_text)
    monkeypatch.setattr(OmniOfflineClient, "_astream_openai_with_tools", openai_fallback)

    asyncio.run(c.stream_text("这是什么呀"))

    shot = "data:image/jpeg;base64,PROACTIVE_B64"
    assert requests[0] == [shot]  # Gemini request: consent still held.
    assert requests[1] == ([] if revoke else [shot])
