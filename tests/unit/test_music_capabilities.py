from copy import deepcopy
import pytest
from brain.music_capabilities import scoped_music_catalog
from plugin.plugins.netease_music.cloud_entry import CLOUD_SCHEMA
from utils.music_capability_context import music_capability_prompt


def test_main_chat_hint_has_no_operation_catalog():
    text = music_capability_prompt(
        {"status": "running", "entries": [{"id": "cloud_music"}]}
    )
    assert len(text) < 170
    assert "收藏默认存网易云" in text and "只有明确说本地" in text
    assert "cloud_music" not in text and "action" not in text and "offset" not in text
    assert music_capability_prompt({"status": "stopped"}) == ""
    assert (
        music_capability_prompt(
            {"plugin": {"status": "running", "entries": [{"id": "cloud_music"}]}}
        )
        == text
    )
    assert (
        music_capability_prompt(
            {
                "plugin": {
                    "status": "running",
                    "runtime_enabled": False,
                    "entries": [{"id": "cloud_music"}],
                }
            }
        )
        == ""
    )


@pytest.mark.asyncio
async def test_query_page_survives_result_pipeline_but_history_is_compact(monkeypatch):
    from app.agent_server import results
    from utils.result_parser import parse_plugin_result
    from utils.tokenize import count_tokens
    from plugin.plugins.netease_music.cloud_entry import cloud_result_summary
    from main_logic.core.callback_render import _build_callback_instruction
    from unittest.mock import AsyncMock

    data = {
        "scope": "netease_cloud",
        "action": "playlists",
        "total": 163,
        "offset": 0,
        "next_offset": 20,
        "items": [
            {"id": i, "name": f"Cloud playlist {i}", "kind": "created"}
            for i in range(20)
        ],
    }
    data["summary"] = cloud_result_summary(data)
    detail = parse_plugin_result(
        data,
        plugin_id="netease_music",
        entry_id="cloud_music",
        llm_result_fields=["summary"],
    )
    assert "Cloud playlist 19" in detail and '"next_offset": 20' in detail
    emit = AsyncMock()
    monkeypatch.setattr(results, "_emit_main_event", emit)
    await results._emit_task_result(
        "YUI",
        task_id="test",
        channel="user_plugin",
        success=True,
        summary=detail,
        detail=detail,
        plugin_id="netease_music",
        entry_id="cloud_music",
    )
    event = emit.call_args.kwargs
    assert event["metadata"] == {"music_library_evidence": True}
    assert "Cloud playlist 19" in event["summary"]
    cb = {**event, "origin": "task_result"}
    full = _build_callback_instruction(
        [cb], lang="zh", lanlan_name="YUI", master_name="User"
    )
    compact = _build_callback_instruction(
        [cb], lang="zh", lanlan_name="YUI", master_name="User", compact_music=True
    )
    assert "Cloud playlist 19" in full
    assert count_tokens(compact) < count_tokens(full)
    assert "网易云查询摘要" in compact and "next_offset" in compact


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["save", "save_current"])
async def test_local_plugin_blocks_implicit_save_and_accepts_explicit_local(action):
    from unittest.mock import AsyncMock
    from plugin.tests.unit.plugins.netease_music.test_plugin import (
        _make_plugin,
        _Provider,
        _ctx,
    )
    from plugin.plugins.netease_music.playlists import Queue

    fake, _, _ = _make_plugin(_Provider())
    fake._queues = {"lanlan:Mika": Queue("Test", [])}
    fake._save_current = AsyncMock()
    fake._store_tracks = lambda *args, **kwargs: ("added", 0)
    result = await fake.playlist(
        action, name="夜读", _ctx={**_ctx(), "latest_user_request": "收藏这首"}
    )
    assert result["finish"]["data"]["status"] == "blocked"
    fake._save_current.assert_not_awaited()
    await fake.playlist(
        action,
        name="夜读",
        _ctx={**_ctx(), "latest_user_request": "保存到本地夜读歌单", "authorized_action": action},
    )
    if action == "save_current":
        fake._save_current.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_callback_still_handles_missing_queue():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from plugin.plugins.netease_music.playlists import PlaylistMixin

    fake = SimpleNamespace(
        _target_lanlan=lambda ctx: "YUI",
        _session_key=lambda t: t,
        _queues={},
        _finish_silent=AsyncMock(),
    )
    await PlaylistMixin.playlist_terminal(fake, "old", "ended", _ctx={})
    fake._finish_silent.assert_awaited_once()


from tests.unit.test_offline_turn_cancellation_e2e import _client, _text


@pytest.mark.parametrize("replacement", [None, "用户附件摘要"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_music_prefix_is_full_for_current_turn_but_compact_in_next_request(
    replacement, cancel, monkeypatch
):
    import asyncio
    from main_logic.core.callback_render import _build_callback_instruction
    from utils.tokenize import count_tokens

    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", lambda: None)
    callback = {
        "summary": "网页: test\n来源: https://example.org/page\n"
        + "evidence detail " * 500
        + " TAIL_SECRET",
        "status": "completed",
        "context_source": "music_library.result",
        "origin": "task_result",
    }
    other = {"summary": "Other callback must remain intact.", "origin": "event"}
    kwargs = dict(lang="zh", lanlan_name="YUI", master_name="主人")
    full = _build_callback_instruction([callback, other], **kwargs)
    receipt = _build_callback_instruction(
        [callback, other], compact_music=True, **kwargs
    )
    assert count_tokens(receipt) < 1500
    assert "TAIL_SECRET" not in receipt
    assert "https://example.org/page" in receipt
    assert other["summary"] in receipt
    client = _client()

    async def interrupted():
        raise asyncio.CancelledError()

    client.script = [[_text("Answer"), interrupted if cancel else _text("", "stop")]]
    call = client.stream_text(
        "actual user request",
        system_prefix=full,
        system_prefix_history=receipt,
        history_replacement_text=replacement,
    )
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        await call
    assert "TAIL_SECRET" in str(client.requests[0])
    history = str(client._conversation_history)
    assert "TAIL_SECRET" not in history
    assert "https://example.org/page" in history
    assert (replacement or "actual user request") in history
    client.script = [[_text("Followup"), _text("", "stop")]]
    await client.stream_text("next question")
    assert "TAIL_SECRET" not in str(client.requests[-1])


@pytest.mark.parametrize("lang", ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt", "tchinese"])
def test_capability_and_compact_history_use_prompt_locale(lang):
    from config.prompts.prompts_music_capability import MUSIC_CAPABILITY, MUSIC_HISTORY_PREFIX
    from config.prompts._locale import normalize_prompt_locale
    from utils.music_capability_context import compact_music_history
    locale = normalize_prompt_locale(lang)
    assert music_capability_prompt({"status": "running", "entries": [{"id": "cloud_music"}]}, lang).strip() == MUSIC_CAPABILITY[locale]
    assert compact_music_history("track detail", lang).startswith(MUSIC_HISTORY_PREFIX[locale])


@pytest.mark.parametrize("action", ["save", "save_current"])
async def test_proactive_local_save_is_denied_even_with_explicit_local_words(action):
    from plugin.tests.unit.plugins.netease_music.test_plugin import _make_plugin, _Provider, _ctx
    plugin, _, _ = _make_plugin(_Provider())
    result = await plugin.playlist(action, name="Night", _ctx={**_ctx(), "request_source": "proactive", "latest_user_request": "保存这首到本地歌单"})
    assert result["finish"]["data"]["status"] == "blocked"
