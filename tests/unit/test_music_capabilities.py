from copy import deepcopy
import pytest
from brain.music_capabilities import cloud_music_actions, scoped_music_catalog
from plugin.plugins.netease_music.cloud_entry import CLOUD_SCHEMA
from utils.music_capability_context import music_capability_prompt


@pytest.mark.parametrize(
    "text,actions",
    [
        ("看看我的网易云账号", ["account"]),
        ("列出我收藏的网易云歌单", ["playlists"]),
        ("网易云Rock歌单里面有哪些歌", ["tracks"]),
        ("播放我的网易云Rock歌单", ["play"]),
        ("看下网易云喜欢的音乐", ["liked"]),
        ("网易云日推是什么", ["daily"]),
        ("我收藏的网易云歌手", ["artists"]),
        ("在网易云新建歌单夜跑", ["create"]),
        ("把这首加入我的网易云Rock歌单", ["add"]),
        ("从网易云Rock歌单移除这首", ["remove"]),
        ("网易云收藏这首", ["like"]),
        ("收藏这首", ["like"]),
        ("把这首收藏一下", ["like"]),
        ("收藏正在听的歌", ["like"]),
        ("保存这首歌", ["like"]),
        ("N.E.K.O 帮我收藏这首", ["like"]),
        ("把这首加入夜跑歌单", ["add"]),
        ("把这首收藏到夜跑歌单", ["add"]),
        ("收藏这首到夜跑歌单", ["add"]),
        ("把这首加入喜欢的音乐", ["like"]),
        ("收藏这首到本地夜读歌单", []),
        ("把整组歌曲保存为本地歌单午后", []),
        ("收藏这篇文章", []),
        ("把文章加入我的阅读收藏夹", []),
        ("取消网易云这首歌的红心", ["unlike"]),
        ("用网易云播放晴天", []),
        ("搜网易云的dream pop歌单", []),
        ("列出N.E.K.O本地歌单", []),
        ("吃饭了吗", []),
        ("看看我的GitHub账号", []),
    ],
)
def test_operation_selection(text, actions):
    assert cloud_music_actions(text) == actions


def test_only_needed_schema_and_cached_catalog_unchanged():
    catalog = [
        {
            "id": "netease_music",
            "description": "all music abilities",
            "entries": [
                {"id": "playlist"},
                {"id": "play_netease_music"},
                {"id": "cloud_music", "input_schema": deepcopy(CLOUD_SCHEMA)},
            ],
        }
    ]
    before = deepcopy(catalog)
    account = scoped_music_catalog(catalog, "网易云账号昵称")
    entry = account[0]["entries"][0]
    assert entry["id"] == "cloud_music"
    assert entry["input_schema"]["properties"] == {
        "action": {"type": "string", "enum": ["account"]}
    }
    unrelated = scoped_music_catalog(catalog, "吃午饭了")
    assert "cloud_music" not in [e["id"] for e in unrelated[0]["entries"]]
    add = scoped_music_catalog(catalog, "把这首加入网易云Rock歌单")[0]["entries"][0]
    assert set(add["input_schema"]["properties"]) == {"action", "playlist", "song"}
    assert catalog == before


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


def test_next_page_retains_previous_library_operation():
    assert cloud_music_actions(
        "下一页", "user: 看下网易云喜欢的音乐\nassistant: ...\nuser: 下一页"
    ) == ["liked"]
    assert (
        cloud_music_actions(
            "下一页", "user: 看下网易云喜欢的音乐\nuser: 查一下明天天气\nuser: 下一页"
        )
        == []
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


def test_local_save_actions_require_explicit_destination_even_without_cloud_entry():
    catalog = [
        {
            "id": "netease_music",
            "entries": [
                {
                    "id": "playlist",
                    "input_schema": {
                        "properties": {
                            "action": {"enum": ["save", "save_current", "pause"]}
                        }
                    },
                }
            ],
        }
    ]
    for text in ["收藏这首", "随便聊聊"]:
        scoped = scoped_music_catalog(catalog, text)
        for entry in scoped[0]["entries"]:
            assert (
                "save_current"
                not in entry["input_schema"]["properties"]["action"]["enum"]
            )
    local = scoped_music_catalog(catalog, "把这首存到本地夜读歌单")
    assert local == catalog


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
        _ctx={**_ctx(), "latest_user_request": "保存到本地夜读歌单"},
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
