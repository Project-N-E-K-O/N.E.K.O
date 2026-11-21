# -*- coding: utf-8 -*-
"""Ban-topic gate at the shared proactive delivery boundary (#3013 R1 / R2).

The Phase 2 output gate only ever saw the Phase 2 line. Two holes remained:

- R1: cards delivered with the line (web / music / meme) carry their own
  visible title, so a generic line plus a card titled with the banned topic
  went straight through.
- R2: the break reminders and the mini-game invite return before Phase 2 and
  assemble their own line, so no ban was checked at all.

Every text channel now calls one gate on the full visible payload before its
line can be spoken. Each test below pairs a blocked case with a pass-through
control on the same harness, so a gate that blocks everything fails too.
"""  # noqa: DOCSTRING_CJK  # Test drafts are user-facing Chinese lines.

from __future__ import annotations

import ast
import inspect
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import memory.user_directives as user_directives_module
from main_logic.proactive_chat import break_reminders
from main_logic.proactive_chat import mini_game_invite as invites
from main_logic.proactive_chat import service
from main_logic.proactive_chat import state as proactive_state
from main_logic.proactive_chat.contracts import (
    PROACTIVE_REASON_PASS_DUPLICATE,
    PROACTIVE_REASON_PASS_USER_DIRECTIVE,
)
from main_logic.proactive_chat.delivery import _commit_proactive_delivery
from main_logic.proactive_chat.directive_gate import (
    _proactive_delivery_directive_hit_count,
)


def _install_directives(monkeypatch, terms):
    manager = MagicMock()
    manager.get_active_terms.return_value = list(terms)
    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", lambda: manager,
    )
    return manager


class _NeverPreemptedState:
    proactive_sid = "proactive-sid"

    @staticmethod
    def is_proactive_preempted(*_args):
        return False


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------


def test_payload_scans_every_visible_card_field(monkeypatch):
    _install_directives(monkeypatch, ["裁员"])
    assert _proactive_delivery_directive_hit_count("Neko", "看这个", None) == 0
    for field in ("title", "artist", "source"):
        links = [{"url": "https://example.com", field: "某公司大规模裁员"}]
        assert _proactive_delivery_directive_hit_count("Neko", "看这个", links) == 1


def test_payload_ignores_invisible_fields(monkeypatch):
    # url / mode never reach the screen; matching them would drop deliveries
    # for text the user cannot see.
    _install_directives(monkeypatch, ["layoffs"])
    links = [{"url": "https://example.com/layoffs", "mode": "layoffs", "title": "news"}]
    assert _proactive_delivery_directive_hit_count("Neko", "look", links) == 0


def test_payload_scans_url_only_when_the_card_shows_it(monkeypatch):
    # The source card falls back to showing the url when there is no title.
    _install_directives(monkeypatch, ["layoffs"])
    untitled = [{"url": "https://example.com/layoffs"}]
    assert _proactive_delivery_directive_hit_count("Neko", "look", untitled) == 1


def test_payload_skips_internal_music_marker(monkeypatch):
    # "音乐推荐" tags music links internally and is never displayed.
    _install_directives(monkeypatch, ["推荐"])
    links = [{"title": "晴天", "artist": "某歌手", "source": "音乐推荐",
              "url": "https://example.com/m"}]
    assert _proactive_delivery_directive_hit_count("Neko", "听听这首", links) == 0


def test_payload_pieces_do_not_join_across_boundaries(monkeypatch):
    # Line "...work" + title "out..." must not read as "workout".
    _install_directives(monkeypatch, ["workout"])
    links = [{"title": "out of office"}]
    assert _proactive_delivery_directive_hit_count("Neko", "back to work", links) == 0


# ---------------------------------------------------------------------------
# Normal Phase 2 commit: line and cards
# ---------------------------------------------------------------------------


def _phase2_mgr():
    return SimpleNamespace(
        current_speech_id="proactive-sid",
        last_user_engagement_time=100.0,
        state=_NeverPreemptedState(),
        feed_tts_chunk=AsyncMock(return_value=True),
        finish_proactive_delivery=AsyncMock(return_value=True),
        handle_new_message=AsyncMock(),
    )


async def _commit(mgr, *, response_text, source_tag="CHAT", **links):
    return await _commit_proactive_delivery(
        mgr=mgr,
        proactive_sid="proactive-sid",
        lanlan_name="Neko",
        response_text=response_text,
        source_tag=source_tag,
        active_channels=[],
        selected_web_link=links.get("web"),
        selected_music_link=links.get("music"),
        selected_meme_link=links.get("meme"),
        music_content=None,
        is_music_used=False,
        is_playing_music=False,
        music_cooldown=False,
        vision_content=None,
        phase2_use_vision=False,
        screenshot_b64=None,
        proactive_lang="zh",
        master_name="博士",
    )


_PHASE2_CASES = [
    pytest.param("今天裁员的新闻好多", "CHAT", {}, id="line"),
    pytest.param(
        "看这个", "WEB",
        {"web": {"title": "[新闻] 某公司大规模裁员", "url": "https://example.com/a",
                 "source": "新闻"}},
        id="web-card-title",
    ),
    pytest.param(
        "听听这首", "MUSIC",
        {"music": {"title": "裁员之歌", "artist": "某歌手", "url": "https://example.com/m"}},
        id="music-card-title",
    ),
    pytest.param(
        "哈哈", "MEME",
        {"meme": {"title": "裁员表情包", "url": "https://example.com/meme.png"}},
        id="meme-card-title",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "tag", "links"), _PHASE2_CASES)
async def test_phase2_commit_blocks_ban_in_line_or_card(monkeypatch, text, tag, links):
    _install_directives(monkeypatch, ["裁员"])
    mgr = _phase2_mgr()

    commit = await _commit(mgr, response_text=text, source_tag=tag, **links)

    assert commit.delivery is None
    assert commit.result.body["reason_code"] == PROACTIVE_REASON_PASS_USER_DIRECTIVE
    assert commit.result.body["directive_match_count"] == 1
    mgr.feed_tts_chunk.assert_not_awaited()
    mgr.finish_proactive_delivery.assert_not_awaited()
    # The claimed turn is released, as the Phase 2 output gate does.
    mgr.handle_new_message.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "tag", "links"), _PHASE2_CASES)
async def test_phase2_commit_delivers_without_ban(monkeypatch, text, tag, links):
    _install_directives(monkeypatch, ["股票"])
    mgr = _phase2_mgr()

    commit = await _commit(mgr, response_text=text, source_tag=tag, **links)

    assert commit.result is None
    assert commit.delivery is not None
    mgr.feed_tts_chunk.assert_awaited_once()
    mgr.finish_proactive_delivery.assert_awaited_once()
    mgr.handle_new_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Break reminders (anti-slack / work-break / work-break + game invite)
# ---------------------------------------------------------------------------


class _FakeStreamingLlm:
    def __init__(self, text):
        self.text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def astream(self, _messages):
        yield SimpleNamespace(content=self.text)


class _FakeRegenLlm:
    def __init__(self, text):
        self.text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def ainvoke(self, _messages):
        return SimpleNamespace(content=self.text)


def _break_harness(monkeypatch, *llms, initial_repeats=False):
    import memory.anti_repeat as anti_repeat_module

    monkeypatch.setattr(
        break_reminders, "create_chat_llm_async", AsyncMock(side_effect=list(llms)),
    )
    monkeypatch.setattr(break_reminders, "_record_proactive_chat", MagicMock())
    record_decision = MagicMock()
    monkeypatch.setattr(break_reminders, "record_anti_repeat_decision", record_decision)
    quiet = anti_repeat_module.UnansweredProactiveRepeatSignal(
        triggered=False, match_count=0, considered_count=0,
        best_similarity=0.0, repeated_terms=(),
    )
    first = anti_repeat_module.UnansweredProactiveRepeatSignal(
        triggered=True, match_count=2, considered_count=2,
        best_similarity=0.9, repeated_terms=("喝水休息",),
    ) if initial_repeats else quiet
    corpus = MagicMock()
    corpus.apreload = AsyncMock()
    corpus.score_unanswered_proactive_draft.side_effect = [first, quiet]
    monkeypatch.setattr(break_reminders, "get_anti_repeat_corpus", lambda: corpus)
    mgr = SimpleNamespace(
        prepare_proactive_delivery=AsyncMock(return_value=True),
        current_speech_id="break-sid",
        state=SimpleNamespace(
            fire=AsyncMock(),
            is_proactive_preempted=MagicMock(return_value=False),
            proactive_sid="break-sid",
        ),
        feed_tts_chunk=AsyncMock(return_value=True),
        finish_proactive_delivery=AsyncMock(return_value=True),
        handle_new_message=AsyncMock(),
        proactive_engagement_observation_started_at=100.0,
        last_user_message_time=None,
        last_user_engagement_time=None,
    )
    config_manager = SimpleNamespace(
        aget_model_api_config=AsyncMock(return_value={
            "model": "fake-model",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "fake-key",
            "provider_type": "openai_compatible",
        })
    )
    return mgr, config_manager, corpus, record_decision


async def _deliver_break(mgr, config_manager, channel="work_break"):
    return await break_reminders._deliver_break_reminder_via_llm(
        lanlan_name="Neko",
        mgr=mgr,
        config_manager=config_manager,
        system_prompt="Generate one reminder.",
        channel=channel,
        lang="zh",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["anti_slack", "work_break", "work_break_game_invite"])
async def test_break_reminder_blocks_ban_before_anti_repeat(monkeypatch, channel):
    _install_directives(monkeypatch, ["喝水"])
    mgr, config_manager, corpus, record_decision = _break_harness(
        monkeypatch, _FakeStreamingLlm("记得起来喝水休息一下。"),
    )

    result = await _deliver_break(mgr, config_manager, channel)

    assert result.directive_blocked is True
    assert result.consumes_source is True
    assert result.delivered_text is None
    mgr.feed_tts_chunk.assert_not_awaited()
    mgr.finish_proactive_delivery.assert_not_awaited()
    mgr.handle_new_message.assert_awaited_once_with()
    # Ahead of the anti-repeat checks, whose records persist draft fragments.
    corpus.score_unanswered_proactive_draft.assert_not_called()
    record_decision.assert_not_called()


@pytest.mark.asyncio
async def test_break_reminder_blocks_ban_introduced_by_regen(monkeypatch):
    _install_directives(monkeypatch, ["眼睛"])
    mgr, config_manager, corpus, record_decision = _break_harness(
        monkeypatch,
        _FakeStreamingLlm("记得起来喝水休息一下。"),
        _FakeRegenLlm("先望望远处，让眼睛放松一会儿吧。"),
        initial_repeats=True,
    )

    result = await _deliver_break(mgr, config_manager)

    assert result.directive_blocked is True
    mgr.feed_tts_chunk.assert_not_awaited()
    mgr.finish_proactive_delivery.assert_not_awaited()
    # Only the initial draft was scored; the banned rewrite never was, and the
    # ban is not recorded as a repetition outcome.
    assert corpus.score_unanswered_proactive_draft.call_count == 1
    record_decision.assert_not_called()


@pytest.mark.asyncio
async def test_break_reminder_delivers_without_ban(monkeypatch):
    _install_directives(monkeypatch, ["股票"])
    mgr, config_manager, _corpus, _record = _break_harness(
        monkeypatch, _FakeStreamingLlm("记得起来喝水休息一下。"),
    )

    result = await _deliver_break(mgr, config_manager)

    assert result.delivered_text == "记得起来喝水休息一下。"
    assert result.directive_blocked is False
    assert result.consumes_source is False
    mgr.feed_tts_chunk.assert_awaited_once()
    mgr.finish_proactive_delivery.assert_awaited_once()


_COMBO_PROMPT = "Invite the user to play a game during the break."
_PLAIN_PROMPT = "Remind the user to drink water."


async def _deliver_combo(mgr, config_manager):
    return await break_reminders._deliver_break_reminder_via_llm(
        lanlan_name="Neko",
        mgr=mgr,
        config_manager=config_manager,
        system_prompt=_COMBO_PROMPT,
        channel="work_break_game_invite",
        lang="zh",
        directive_fallback=(_PLAIN_PROMPT, "work_break"),
    )


def _system_prompts(make_llm_calls):
    return [call.args[0][0].content for call in make_llm_calls]


@pytest.mark.asyncio
async def test_combo_reminder_ban_falls_back_to_plain_reminder(monkeypatch):
    _install_directives(monkeypatch, ["游戏"])
    mgr, config_manager, _corpus, _record = _break_harness(
        monkeypatch,
        _FakeStreamingLlm("休息一下，一起玩个小游戏吧？"),
        _FakeStreamingLlm("记得起来喝口水。"),
    )
    streams = []
    original = break_reminders.create_chat_llm_async

    async def recording(*args, **kwargs):
        llm = await original(*args, **kwargs)
        astream = llm.astream

        async def record_stream(messages):
            streams.append(messages[0].content)
            async for chunk in astream(messages):
                yield chunk

        llm.astream = record_stream
        return llm

    monkeypatch.setattr(break_reminders, "create_chat_llm_async", recording)

    result = await _deliver_combo(mgr, config_manager)

    assert result.delivered_text == "记得起来喝口水。"
    assert result.delivered_channel == "work_break"
    assert result.consumes_source is False
    # Exactly two model calls: the combo draft, then the plain reminder.
    assert streams == [_COMBO_PROMPT, _PLAIN_PROMPT]
    mgr.feed_tts_chunk.assert_awaited_once()
    assert mgr.feed_tts_chunk.await_args.args[0] == "记得起来喝口水。"
    mgr.finish_proactive_delivery.assert_awaited_once()
    # The turn stayed claimed across the switch.
    mgr.handle_new_message.assert_not_awaited()
    mgr.prepare_proactive_delivery.assert_awaited_once()


@pytest.mark.asyncio
async def test_combo_reminder_without_ban_delivers_combo(monkeypatch):
    _install_directives(monkeypatch, ["股票"])
    mgr, config_manager, _corpus, _record = _break_harness(
        monkeypatch, _FakeStreamingLlm("休息一下，一起玩个小游戏吧？"),
    )

    result = await _deliver_combo(mgr, config_manager)

    assert result.delivered_text == "休息一下，一起玩个小游戏吧？"
    assert result.delivered_channel == "work_break_game_invite"
    assert break_reminders.create_chat_llm_async.await_count == 1


@pytest.mark.asyncio
async def test_combo_reminder_fallback_that_also_hits_is_consumed(monkeypatch):
    _install_directives(monkeypatch, ["游戏", "喝水"])
    mgr, config_manager, corpus, _record = _break_harness(
        monkeypatch,
        _FakeStreamingLlm("休息一下，一起玩个小游戏吧？"),
        _FakeStreamingLlm("记得起来喝水。"),
    )

    result = await _deliver_combo(mgr, config_manager)

    assert result.directive_blocked is True
    assert result.consumes_source is True
    # Bounded at two calls: no third attempt after the fallback hits.
    assert break_reminders.create_chat_llm_async.await_count == 2
    mgr.feed_tts_chunk.assert_not_awaited()
    mgr.finish_proactive_delivery.assert_not_awaited()
    mgr.handle_new_message.assert_awaited_once_with()
    corpus.score_unanswered_proactive_draft.assert_not_called()


def test_service_wires_combo_fallback_to_plain_reminder():
    source = inspect.getsource(service.handle_proactive_chat)
    assert 'directive_fallback=(wb_system_prompt, "work_break")' in source
    assert 'reminder_delivery.delivered_channel == "work_break"' in source
    # Both the fallback and the plain branch finish through one helper.
    assert source.count("return await _end_work_break_delivered()") == 2


@pytest.mark.parametrize("label", ["anti-slack reminder", "work-break reminder"])
def test_break_reminder_ban_reports_directive_not_duplicate(label):
    blocked = service._suppressed_break_reminder_body(
        break_reminders.BreakReminderDeliveryResult(directive_blocked=True), label,
    )
    repeated = service._suppressed_break_reminder_body(
        break_reminders.BreakReminderDeliveryResult(repeat_suppressed=True), label,
    )
    assert blocked["action"] == "pass"
    assert blocked["reason_code"] == PROACTIVE_REASON_PASS_USER_DIRECTIVE
    assert repeated["reason_code"] == PROACTIVE_REASON_PASS_DUPLICATE
    assert repeated["message"] == f"{label} suppressed as repeated"


# ---------------------------------------------------------------------------
# Mini-game invite
# ---------------------------------------------------------------------------


@pytest.fixture
def _invite_harness(monkeypatch):
    monkeypatch.setattr(invites, "MINI_GAME_INVITE_ENABLED", True)
    monkeypatch.setattr(invites, "MINI_GAME_INVITE_FORCE_GAME_TYPE", None)
    monkeypatch.setattr(invites, "MINI_GAME_INVITE_TRIGGER_PROBABILITY", 1.0)
    monkeypatch.setattr(invites, "_ensure_proactive_chat_totals_loaded", AsyncMock())
    monkeypatch.setattr(invites, "_record_invite_delivery_persistent", AsyncMock())
    monkeypatch.setattr(invites, "_record_proactive_chat", MagicMock())
    monkeypatch.setattr(
        invites, "_pick_mini_game_type", lambda *_args, **_kwargs: "soccer",
    )
    invites._mini_game_invite_state.clear()
    proactive_state._invite_ever_delivered.clear()
    yield
    invites._mini_game_invite_state.clear()
    proactive_state._invite_ever_delivered.clear()


def _invite_mgr():
    mgr = MagicMock()
    mgr.prepare_proactive_delivery = AsyncMock(return_value=True)
    mgr.finish_proactive_delivery = AsyncMock(return_value=True)
    mgr.feed_tts_chunk = AsyncMock(return_value=True)
    mgr.handle_new_message = AsyncMock()
    mgr.current_speech_id = "invite-sid"
    mgr.last_user_engagement_time = 100.0
    mgr.state = MagicMock()
    mgr.state.fire = AsyncMock()
    mgr.state.is_proactive_preempted.return_value = False
    mgr.state.proactive_sid = "invite-sid"
    mgr.websocket = None
    return mgr


async def _attempt_invite(mgr):
    return await invites._attempt_mini_game_invite_delivery(
        lanlan_name="Neko",
        mgr=mgr,
        activity_snapshot=SimpleNamespace(
            state="casual_browsing", propensity="open", unfinished_thread=None,
        ),
        invite_lang="zh",
        master_name="博士",
        push_options_compat=False,
    )


@pytest.mark.asyncio
async def test_mini_game_invite_ban_falls_through_without_claiming(
    monkeypatch, _invite_harness,
):
    monkeypatch.setattr(
        invites, "_render_mini_game_invite_line",
        lambda *_args, **_kwargs: "要不要一起踢足球？",
    )
    _install_directives(monkeypatch, ["足球"])
    mgr = _invite_mgr()

    out = await _attempt_invite(mgr)

    # None hands the round back to the normal pipeline instead of ending it,
    # so a ban on games cannot silence the force-first branch forever.
    assert out is None
    mgr.prepare_proactive_delivery.assert_not_awaited()
    mgr.feed_tts_chunk.assert_not_awaited()
    mgr.finish_proactive_delivery.assert_not_awaited()
    assert "Neko" not in proactive_state._invite_ever_delivered


@pytest.mark.asyncio
async def test_mini_game_invite_delivers_without_ban(monkeypatch, _invite_harness):
    monkeypatch.setattr(
        invites, "_render_mini_game_invite_line",
        lambda *_args, **_kwargs: "要不要一起踢足球？",
    )
    _install_directives(monkeypatch, ["股票"])
    mgr = _invite_mgr()

    out = await _attempt_invite(mgr)

    assert out is not None and out["action"] == "chat"
    mgr.feed_tts_chunk.assert_awaited_once()
    mgr.finish_proactive_delivery.assert_awaited_once()


def _theater_harness(monkeypatch, composed):
    monkeypatch.setattr(
        invites, "_pick_mini_game_type", lambda *_args, **_kwargs: "theater",
    )
    monkeypatch.setattr(
        invites, "_select_theater_story",
        AsyncMock(return_value={"title": "雨夜来信", "background": "一个故事"}),
    )
    monkeypatch.setattr(
        invites, "_render_mini_game_invite_line",
        lambda *_args, **_kwargs: "要不要一起演《雨夜来信》？",
    )
    compose = AsyncMock(return_value=composed)
    monkeypatch.setattr(invites, "_compose_theater_invite_line", compose)
    return compose


@pytest.mark.asyncio
async def test_theater_invite_banned_story_skips_compose_call(
    monkeypatch, _invite_harness,
):
    compose = _theater_harness(monkeypatch, "今晚想和你演一个下雨天的故事。")
    _install_directives(monkeypatch, ["雨夜来信"])
    mgr = _invite_mgr()

    out = await _attempt_invite(mgr)

    assert out is None
    # The fixed line names the story; a banned story never pays for the
    # compose model call, so force-first retries cost nothing.
    compose.assert_not_awaited()
    mgr.prepare_proactive_delivery.assert_not_awaited()


@pytest.mark.asyncio
async def test_theater_invite_banned_composition_falls_back_to_fixed_line(
    monkeypatch, _invite_harness,
):
    _theater_harness(monkeypatch, "下雨天最适合聊加班的烦恼了，来演一段吧？")
    _install_directives(monkeypatch, ["加班"])
    mgr = _invite_mgr()

    out = await _attempt_invite(mgr)

    assert out is not None and out["action"] == "chat"
    spoken = mgr.feed_tts_chunk.await_args.args[0]
    assert spoken == "要不要一起演《雨夜来信》？"


# ---------------------------------------------------------------------------
# Structure: no text channel may reach the TTS feed without the gate
# ---------------------------------------------------------------------------


_GATE = "_proactive_delivery_directive_block"


def test_every_proactive_feed_is_preceded_by_the_gate():
    """A new channel that speaks without the shared gate reopens R2."""
    package = pathlib.Path(inspect.getfile(service)).parent
    game_router = package.parents[1] / "main_routers" / "game_router"
    checked = []
    for path in sorted([*package.glob("*.py"), *game_router.glob("*.py")]):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            feed_lines, gate_lines = [], []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and sub.attr == "feed_tts_chunk":
                    feed_lines.append(sub.lineno)
                elif isinstance(sub, ast.Constant) and sub.value == "feed_tts_chunk":
                    feed_lines.append(sub.lineno)
                elif isinstance(sub, ast.Name) and sub.id == _GATE:
                    gate_lines.append(sub.lineno)
            if not feed_lines:
                continue
            checked.append(f"{path.name}:{node.name}")
            assert gate_lines and min(gate_lines) < min(feed_lines), (
                f"{path.name}:{node.name} feeds proactive TTS without {_GATE}"
            )
    # Phase 2 commit, break reminders, mini-game invite, postgame bubble. If this shrinks the
    # scan lost its target; if it grows, the new channel was checked above.
    assert {
        "delivery.py:_commit_proactive_delivery",
        "break_reminders.py:_deliver_break_reminder_via_llm",
        "mini_game_invite.py:_attempt_mini_game_invite_delivery",
        "postgame.py:_deliver_postgame_text_bubble",
    } <= set(checked)
