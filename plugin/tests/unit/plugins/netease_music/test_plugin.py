from __future__ import annotations

import ast
import asyncio
import json
import tomllib
from threading import Lock
from pathlib import Path
from typing import Any

import pytest

from plugin.plugins.netease_music import NeteaseMusicPlugin
from plugin.plugins.netease_music.models import PlayRequest, ResolvedMedia, SongCandidate
from plugin.plugins.netease_music.provider import (
    MediaUnavailableError,
    ProviderError,
)
from plugin.sdk.plugin import Err, Ok
from plugin.sdk.shared.constants import EVENT_META_ATTR
from plugin.sdk.shared.i18n import load_plugin_i18n_from_dir


PLUGIN_DIR = Path(__file__).resolve().parents[4] / "plugins" / "netease_music"
I18N_DIR = PLUGIN_DIR / "i18n"


def _song(
    song_id: int = 1,
    name: str = "晴天",
    artist: str = "周杰伦",
    album: str = "叶惠美",
) -> SongCandidate:
    return SongCandidate(
        song_id=song_id,
        name=name,
        artist=artist,
        album=album,
    )


class _Provider:
    def __init__(
        self,
        *,
        candidates: list[SongCandidate] | None = None,
        media: ResolvedMedia | None = None,
        search_error: BaseException | None = None,
        media_error: BaseException | None = None,
    ) -> None:
        self.candidates = [_song()] if candidates is None else candidates
        self.media = media or ResolvedMedia(
            url="https://m10.music.126.net/token/song.mp3",
            hostname="m10.music.126.net",
        )
        self.search_error = search_error
        self.media_error = media_error
        self.search_calls: list[str] = []
        self.resolve_calls: list[int] = []
        self.closed = False

    async def search(self, query: str) -> list[SongCandidate]:
        self.search_calls.append(query)
        if self.search_error is not None:
            raise self.search_error
        return list(self.candidates)

    async def resolve_media(self, song_id: int) -> ResolvedMedia:
        self.resolve_calls.append(song_id)
        if self.media_error is not None:
            raise self.media_error
        return self.media

    async def aclose(self) -> None:
        self.closed = True

    async def __aenter__(self) -> _Provider:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


def _make_plugin(
    provider: object,
    *,
    receipts: list[dict[str, object]] | None = None,
) -> tuple[NeteaseMusicPlugin, list[dict[str, object]], list[dict[str, object]]]:
    plugin = NeteaseMusicPlugin.__new__(NeteaseMusicPlugin)
    plugin._library_lock = Lock()
    plugin._provider_factory = lambda: provider
    plugin._closed = False
    plugin._generation_counter = 0
    plugin._session_generations = {}
    plugin.i18n = load_plugin_i18n_from_dir(I18N_DIR, default_locale="zh-CN")

    pushes: list[dict[str, object]] = []
    finish_calls: list[dict[str, object]] = []
    pending_receipts = list(receipts or [])

    def push_message(**kwargs: object) -> dict[str, object]:
        pushes.append(dict(kwargs))
        if pending_receipts:
            return pending_receipts.pop(0)
        return {"submitted": True}

    async def finish(**kwargs: object) -> dict[str, object]:
        finish_calls.append(dict(kwargs))
        return {"finish": dict(kwargs)}

    plugin.push_message = push_message  # type: ignore[method-assign]
    plugin.finish = finish  # type: ignore[method-assign]
    return plugin, pushes, finish_calls


def _ctx(
    *,
    lanlan_name: str = "Mika",
    conversation_id: str = "conversation-1",
    language: str | None = None,
) -> dict[str, str]:
    result = {
        "lanlan_name": lanlan_name,
        "conversation_id": conversation_id,
        "request_source": "user",
    }
    if language is not None:
        result["language"] = language
    return result


def _action(push: dict[str, object]) -> dict[str, object]:
    parts = push["parts"]
    assert isinstance(parts, list) and len(parts) == 1
    part = parts[0]
    assert isinstance(part, dict)
    return part


@pytest.mark.plugin_unit
def test_public_entry_contract_has_one_model_validated_entry() -> None:
    meta = getattr(NeteaseMusicPlugin.play_netease_music, EVENT_META_ATTR)

    assert meta.event_type == "plugin_entry"
    assert meta.id == "play_netease_music"
    assert meta.params is PlayRequest
    assert meta.timeout == 20.0
    assert meta.model_validate is True
    assert set(meta.input_schema["properties"]) == {"query"}

    entries = []
    for value in vars(NeteaseMusicPlugin).values():
        candidate_meta = getattr(value, EVENT_META_ATTR, None)
        if candidate_meta is not None and candidate_meta.event_type == "plugin_entry":
            entries.append(candidate_meta.id)
    assert entries == ["play_netease_music"]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_entry_is_closed_until_startup() -> None:
    provider = _Provider()
    plugin, pushes, finish_calls = _make_plugin(provider)
    plugin._closed = True

    result = await plugin.play_netease_music(PlayRequest(query="晴天"), _ctx())

    assert result["finish"]["data"]["status"] == "superseded"
    assert provider.search_calls == []
    assert pushes == []
    assert finish_calls[-1]["delivery"] == "silent"


@pytest.mark.plugin_unit
def test_manifest_is_default_off_and_has_no_keyword_trigger() -> None:
    with (PLUGIN_DIR / "plugin.toml").open("rb") as stream:
        manifest = tomllib.load(stream)

    plugin_meta = manifest["plugin"]
    assert plugin_meta["id"] == "netease_music"
    assert plugin_meta["entry"] == "plugin.plugins.netease_music:NeteaseMusicPlugin"
    assert plugin_meta["name"] == "网易云音乐"
    assert "歌单连播" in plugin_meta["description"]
    assert "keywords" not in plugin_meta
    assert manifest["plugin_runtime"] == {"enabled": False, "auto_start": False}


@pytest.mark.plugin_unit
def test_source_has_no_listener_llm_tool_cookie_or_core_music_import() -> None:
    source = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_import_prefixes = (
        "main_logic",
        "main_routers",
        "realtime",
        "utils.music_crawlers",
        "plugin.plugins.music_pusher",
    )

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported
        for prefix in forbidden_import_prefixes
    )

    assert "llm_tool" not in source
    assert "@message" not in source
    assert "@timer" not in source
    assert "Cookie" not in source


@pytest.mark.plugin_unit
def test_all_locales_have_the_same_required_keys() -> None:
    locale_paths = sorted(I18N_DIR.glob("*.json"))
    assert [path.stem for path in locale_paths] == [
        "en",
        "es",
        "ja",
        "ko",
        "pt",
        "ru",
        "zh-CN",
        "zh-TW",
    ]

    bundles = [json.loads(path.read_text(encoding="utf-8")) for path in locale_paths]
    expected_keys = set(bundles[0])
    assert expected_keys
    assert all(set(bundle) == expected_keys for bundle in bundles)
    assert {
        "plugin.name",
        "plugin.description",
        "plugin.short_description",
        "entry.play.name",
        "entry.play.description",
        "entry.play.param.query",
        "messages.no_results",
        "messages.unavailable",
        "messages.submitted",
        "errors.missing_target",
        "errors.search_failed",
        "errors.media_failed",
        "errors.delivery_failed",
        "errors.message_delivery_failed",
        "playlist.added",
        "playlist.ended",
    } <= expected_keys


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_missing_target_returns_error_without_search_or_push() -> None:
    provider = _Provider()
    plugin, pushes, finish_calls = _make_plugin(provider)

    result = await plugin.play_netease_music(PlayRequest(query="晴天"), {})

    assert isinstance(result, Err)
    assert result.error.code == "missing_session_target"
    assert provider.search_calls == []
    assert pushes == []
    assert finish_calls == []


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_clear_match_submits_allowlist_then_play_to_same_target() -> None:
    provider = _Provider()
    plugin, pushes, finish_calls = _make_plugin(provider)

    result = await plugin.play_netease_music(
        PlayRequest(query="晴天 周杰伦"),
        _ctx(),
    )

    assert result["finish"]["delivery"] == "silent"
    assert len(pushes) == 2

    allowlist = pushes[0]
    assert allowlist["source"] == "netease_music"
    assert allowlist["target_lanlan"] == "Mika"
    assert allowlist["visibility"] == []
    assert allowlist["ai_behavior"] == "blind"
    assert _action(allowlist) == {
        "type": "ui_action",
        "action": "media_allowlist_add",
        "domains": ["m10.music.126.net"],
    }

    play = pushes[1]
    assert play["target_lanlan"] == "Mika"
    assert play["visibility"] == ["chat"]
    assert play["ai_behavior"] == "blind"
    assert _action(play) == {
        "type": "ui_action",
        "action": "media_play_url",
        "url": "https://m10.music.126.net/token/song.mp3",
        "media_type": "audio",
        "name": "晴天",
        "artist": "周杰伦",
        "netease_id": 1,
    }

    assert finish_calls[-1]["data"]["status"] == "submitted"
    assert "url" not in repr(finish_calls[-1]["data"]).lower()


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_duplicate_exact_titles_play_first_search_result() -> None:
    first = _song(song_id=1, name="纸短情长", artist="烟把儿", album="纸短情长")
    provider = _Provider(
        candidates=[
            first,
            _song(song_id=2, name="纸短情长", artist="花粥", album="纸短情长"),
        ]
    )
    plugin, pushes, finish_calls = _make_plugin(provider)

    result = await plugin.play_netease_music(PlayRequest(query="纸短情长"), _ctx())

    assert result["finish"]["delivery"] == "silent"
    assert provider.resolve_calls == [first.song_id]
    assert len(pushes) == 2
    assert _action(pushes[1])["artist"] == "烟把儿"
    assert finish_calls[-1]["data"]["status"] == "submitted"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_later_exact_beats_first_and_wrong_artist_never_plays() -> None:
    wrong = _song(song_id=1, name="Heavy Water", artist="Andreas Waldetoft")
    exact = _song(song_id=2, name="Heavy Water", artist="Grouper")
    provider = _Provider(candidates=[wrong, exact])
    plugin, pushes, _ = _make_plugin(provider)
    await plugin.play_netease_music(PlayRequest(query="Heavy Water Grouper"), _ctx())
    assert provider.resolve_calls == [2]
    assert _action(pushes[-1])["artist"] == "Grouper"
    provider.candidates = [wrong]
    pushes.clear()
    await plugin.play_netease_music(PlayRequest(query="Heavy Water Grouper"), _ctx())
    assert provider.resolve_calls == [2]
    assert not any(_action(p).get("action") == "media_play_url" for p in pushes)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_empty_and_unavailable_are_visible_but_do_not_play() -> None:
    empty_plugin, empty_pushes, empty_finishes = _make_plugin(
        _Provider(candidates=[])
    )
    empty_result = await empty_plugin.play_netease_music(
        PlayRequest(query="不存在的歌"),
        _ctx(language="en"),
    )

    assert empty_result["finish"]["delivery"] == "silent"
    assert empty_finishes[-1]["data"]["status"] == "no_results"
    assert "No matching" in _action(empty_pushes[0])["text"]

    unavailable_provider = _Provider(
        media_error=MediaUnavailableError("not anonymously playable")
    )
    unavailable_plugin, unavailable_pushes, unavailable_finishes = _make_plugin(
        unavailable_provider
    )
    unavailable_result = await unavailable_plugin.play_netease_music(
        PlayRequest(query="晴天"),
        _ctx(),
    )

    assert unavailable_result["finish"]["delivery"] == "silent"
    assert unavailable_finishes[-1]["data"]["status"] == "unavailable"
    assert len(unavailable_pushes) == 1
    assert _action(unavailable_pushes[0])["type"] == "text"
    assert not any(
        _action(push).get("action") == "media_play_url"
        for push in unavailable_pushes
    )


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_provider_failure_returns_sdk_error_without_ui_side_effect() -> None:
    plugin, pushes, finish_calls = _make_plugin(
        _Provider(search_error=ProviderError("upstream failed"))
    )

    result = await plugin.play_netease_music(PlayRequest(query="晴天"), _ctx())

    assert isinstance(result, Err)
    assert result.error.code == "search_failed"
    assert "upstream failed" not in str(result.error)
    assert pushes == []
    assert finish_calls == []


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_allowlist_rejection_prevents_play_submission() -> None:
    plugin, pushes, finish_calls = _make_plugin(
        _Provider(),
        receipts=[
            {
                "ok": False,
                "submitted": False,
                "reason": "transport_unavailable",
            }
        ],
    )

    result = await plugin.play_netease_music(PlayRequest(query="晴天"), _ctx())

    assert isinstance(result, Err)
    assert result.error.code == "delivery_rejected"
    assert len(pushes) == 1
    assert _action(pushes[0])["action"] == "media_allowlist_add"
    assert finish_calls == []


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_result_message_rejection_uses_message_delivery_error() -> None:
    plugin, pushes, finish_calls = _make_plugin(
        _Provider(candidates=[]),
        receipts=[
            {
                "ok": False,
                "submitted": False,
                "reason": "transport_unavailable",
            }
        ],
    )

    result = await plugin.play_netease_music(
        PlayRequest(query="missing"),
        _ctx(language="en"),
    )

    assert isinstance(result, Err)
    assert result.error.code == "delivery_rejected"
    assert "result message" in str(result.error).lower()
    assert "playback" not in str(result.error).lower()
    assert len(pushes) == 1
    assert _action(pushes[0])["type"] == "text"
    assert finish_calls == []


class _RacingProvider(_Provider):
    def __init__(self) -> None:
        super().__init__()
        self.started: dict[str, asyncio.Event] = {}
        self.releases: dict[str, asyncio.Event] = {}

    async def search(self, query: str) -> list[SongCandidate]:
        self.search_calls.append(query)
        self.started.setdefault(query, asyncio.Event()).set()
        await self.releases.setdefault(query, asyncio.Event()).wait()
        if query == "新歌":
            return [_song(song_id=2, name="新歌", artist="新歌手")]
        return [_song(song_id=1, name="旧歌", artist="旧歌手")]

    async def resolve_media(self, song_id: int) -> ResolvedMedia:
        self.resolve_calls.append(song_id)
        return ResolvedMedia(
            url=f"https://m10.music.126.net/token/{song_id}.mp3",
            hostname="m10.music.126.net",
        )


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_new_request_supersedes_old_request_for_same_lanlan() -> None:
    provider = _RacingProvider()
    plugin, pushes, finish_calls = _make_plugin(provider)

    old_task = asyncio.create_task(
        plugin.play_netease_music(
            PlayRequest(query="旧歌"),
            _ctx(conversation_id="turn-old"),
        )
    )
    await provider.started.setdefault("旧歌", asyncio.Event()).wait()

    new_task = asyncio.create_task(
        plugin.play_netease_music(
            PlayRequest(query="新歌"),
            _ctx(conversation_id="turn-new"),
        )
    )
    await provider.started.setdefault("新歌", asyncio.Event()).wait()
    provider.releases["新歌"].set()
    new_result = await new_task

    provider.releases["旧歌"].set()
    old_result = await old_task

    assert new_result["finish"]["data"]["status"] == "submitted"
    assert old_result["finish"]["data"]["status"] == "superseded"
    assert [_action(push).get("action") for push in pushes] == [
        "media_allowlist_add",
        "media_play_url",
    ]
    assert _action(pushes[-1])["name"] == "新歌"
    assert [call["delivery"] for call in finish_calls] == ["silent", "silent"]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_different_lanlan_targets_do_not_supersede_each_other() -> None:
    provider = _RacingProvider()
    plugin, pushes, _finish_calls = _make_plugin(provider)

    old_task = asyncio.create_task(
        plugin.play_netease_music(
            PlayRequest(query="旧歌"),
            _ctx(lanlan_name="Mika", conversation_id="turn-old"),
        )
    )
    new_task = asyncio.create_task(
        plugin.play_netease_music(
            PlayRequest(query="新歌"),
            _ctx(lanlan_name="Yuki", conversation_id="turn-new"),
        )
    )
    await provider.started.setdefault("旧歌", asyncio.Event()).wait()
    await provider.started.setdefault("新歌", asyncio.Event()).wait()
    provider.releases["旧歌"].set()
    provider.releases["新歌"].set()

    results = await asyncio.gather(old_task, new_task)

    assert {result["finish"]["data"]["status"] for result in results} == {
        "submitted"
    }
    assert [_action(push).get("action") for push in pushes].count(
        "media_play_url"
    ) == 2


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_shutdown_invalidates_inflight_request_and_entry_closes_provider() -> None:
    provider = _RacingProvider()
    plugin, pushes, _finish_calls = _make_plugin(provider)

    task = asyncio.create_task(
        plugin.play_netease_music(PlayRequest(query="旧歌"), _ctx())
    )
    await provider.started.setdefault("旧歌", asyncio.Event()).wait()

    shutdown_result = await plugin.shutdown()
    assert provider.closed is False
    provider.releases["旧歌"].set()
    result = await task

    assert isinstance(shutdown_result, Ok)
    assert provider.closed is True
    assert result["finish"]["data"]["status"] == "superseded"
    assert pushes == []


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_cancelled_error_is_not_swallowed() -> None:
    plugin, pushes, finish_calls = _make_plugin(
        _Provider(search_error=asyncio.CancelledError())
    )

    with pytest.raises(asyncio.CancelledError):
        await plugin.play_netease_music(PlayRequest(query="晴天"), _ctx())

    assert pushes == []
    assert finish_calls == []


@pytest.mark.asyncio
async def test_queue_natural_end_is_once_and_saved_ids_survive_new_instance(tmp_path):
    from unittest.mock import AsyncMock

    rows = [_song(song_id=i, name=str(i), artist="Band") for i in (11, 12, 13)]
    provider = _Provider(candidates=rows)
    provider.playlist = AsyncMock(return_value=("NetEase list", rows))
    plugin, pushes, _ = _make_plugin(provider)
    plugin._queues = {}
    plugin.data_path = lambda: tmp_path
    await plugin.playlist("search", name="indie", _ctx=_ctx())
    assert provider.resolve_calls == [11]
    ticket = _action(pushes[-1])["queue_ticket"]
    await plugin.playlist("save", name="读书", _ctx={**_ctx(), "latest_user_request": "保存到本地歌单"})
    assert "https:" not in plugin._library_file("Mika").read_text()
    await plugin.playlist_terminal(ticket, "ended", _ctx={"lanlan_name": "Other"})
    await plugin.playlist_terminal(ticket, "ended", _ctx=_ctx())
    await plugin.playlist_terminal(ticket, "ended", _ctx=_ctx())
    assert provider.resolve_calls == [11, 12]
    await plugin.playlist_terminal(
        _action(pushes[-1])["queue_ticket"], "closed", _ctx=_ctx()
    )
    assert provider.resolve_calls == [11, 12]
    fresh, _, _ = _make_plugin(provider)
    fresh._queues = {}
    fresh.data_path = lambda: tmp_path
    await fresh.playlist("replay", name="读书", _ctx=_ctx())
    assert provider.resolve_calls == [11, 12, 11]
    assert [t.song_id for t in fresh._queues["lanlan:Mika"].tracks] == [11, 12, 13]


@pytest.mark.asyncio
async def test_missing_replay_reports_exact_names_without_replacing_current_queue(tmp_path):
    from plugin.plugins.netease_music.playlists import Queue

    provider = _Provider()
    plugin, pushes, _ = _make_plugin(provider)
    queue = Queue("Current", [_song(), _song(2)], cursor=1, ticket="a" * 32)
    plugin._queues = {"lanlan:Mika": queue}
    plugin.data_path = lambda: tmp_path
    library = {"夜读": [[3, "Song", "Artist"]], "点歌台精选": [[4, "Other", "Band"]]}
    plugin._write_library("Mika", library)
    plugin._write_library("Other", {"Other character's private list": [[9, "Private", "Band"]]})

    result = await plugin.playlist("replay", name="夜读歌单", _ctx=_ctx())

    data = result["finish"]["data"]
    assert data["status"] == "playlist_not_found"
    assert data["requested_name"] == "夜读歌单"
    assert data["names"] == ["夜读", "点歌台精选"]
    assert "夜读歌单" in data["summary"] and "点歌台精选" in data["summary"]
    assert "private" not in data["summary"]
    assert data["confirmation_submitted"] is True
    assert pushes[0]["ai_behavior"] == "respond"
    assert _action(pushes[0])["type"] == "text"
    assert provider.resolve_calls == [] and not provider.closed
    assert plugin._queues["lanlan:Mika"] is queue
    assert (queue.cursor, queue.ticket) == (1, "a" * 32)
    assert plugin._library("Mika") == library

    # The failed lookup must leave the playing queue able to advance.
    await plugin.playlist_terminal(queue.ticket, "ended", _ctx=_ctx())
    assert provider.resolve_calls == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,status", [("", "playlist_name_required"), ("Missing", "playlist_not_found")])
async def test_missing_replay_handles_empty_library_and_failed_receipt(tmp_path, name, status):
    provider = _Provider()
    plugin, pushes, _ = _make_plugin(provider, receipts=[{"submitted": False}])
    plugin._queues = {}
    plugin.data_path = lambda: tmp_path

    result = await plugin.playlist("replay", name=name, _ctx=_ctx(language="en"))

    data = result["finish"]["data"]
    assert data["status"] == status
    assert data["names"] == []
    assert data["confirmation_submitted"] is False
    assert "playlist." not in data["summary"]
    assert "saved local playlist" in data["summary"].lower()
    assert provider.resolve_calls == [] and not provider.closed
    assert plugin._queues == {}
    assert len(pushes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("name,song_id", [("夜读", 1), ("夜读歌单", 2), ("点歌台", 3)])
async def test_replay_preserves_literal_saved_names(tmp_path, name, song_id):
    provider = _Provider()
    plugin, _, _ = _make_plugin(provider)
    plugin._queues = {}
    plugin.data_path = lambda: tmp_path
    plugin._write_library("Mika", {
        "夜读": [[1, "First", "Artist"]],
        "夜读歌单": [[2, "Second", "Artist"]],
        "点歌台": [[3, "Third", "Artist"]],
    })

    if name in ("夜读", "夜读歌单"):
        await plugin.playlist("replay", name=name, _ctx=_ctx())
        assert provider.resolve_calls == []
    await plugin.playlist("replay", name=name, name_confirmed=True, _ctx=_ctx())

    assert provider.resolve_calls == [song_id]
    assert plugin._queues["lanlan:Mika"].name == name


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", ["夜读", "夜读歌单"])
@pytest.mark.parametrize("premature_confirmation", [False, True])
async def test_colliding_replay_names_require_selection_before_playing(tmp_path, requested, premature_confirmation):
    provider = _Provider()
    plugin, pushes, _ = _make_plugin(provider)
    plugin._queues = {}
    plugin.data_path = lambda: tmp_path
    plugin._write_library("Mika", {
        "夜读": [[1, "First", "Artist"]],
        "夜读歌单": [[2, "Second", "Artist"]],
        "Other": [[3, "Third", "Artist"]],
    })

    result = await plugin.playlist("replay", name=requested, name_confirmed=premature_confirmation, _ctx=_ctx())

    assert result["finish"]["data"]["status"] == "playlist_name_ambiguous"
    assert result["finish"]["data"]["names"] == ["夜读", "夜读歌单"]
    assert provider.resolve_calls == []
    assert not provider.closed
    assert pushes[0]["ai_behavior"] == "respond"
    assert plugin._queues == {}

    await plugin.playlist("replay", name="夜读歌单", name_confirmed=True, _ctx=_ctx())
    assert provider.resolve_calls == [2]
    assert plugin._queues["lanlan:Mika"].name == "夜读歌单"


@pytest.mark.asyncio
async def test_stop_invalidates_pending_playlist_name_choice(tmp_path):
    provider = _Provider()
    plugin, _, _ = _make_plugin(provider)
    plugin._queues = {}
    plugin.data_path = lambda: tmp_path
    plugin._write_library("Mika", {"夜读": [[1, "First", "Artist"]], "夜读歌单": [[2, "Second", "Artist"]]})
    await plugin.playlist("replay", name="夜读", _ctx=_ctx())
    await plugin.playlist("stop", _ctx=_ctx())

    result = await plugin.playlist("replay", name="夜读歌单", name_confirmed=True, _ctx=_ctx())

    assert result["finish"]["data"]["status"] == "playlist_name_ambiguous"
    assert provider.resolve_calls == []


@pytest.mark.asyncio
async def test_superseded_missing_replay_does_not_send_stale_suggestions(tmp_path, monkeypatch):
    import threading

    plugin, pushes, _ = _make_plugin(_Provider())
    plugin._queues = {}
    plugin.data_path = lambda: tmp_path
    entered = threading.Event()
    release = threading.Event()

    def delayed_library(target):
        entered.set()
        assert release.wait(timeout=5)
        return {}

    monkeypatch.setattr(plugin, "_library", delayed_library)
    pending = asyncio.create_task(plugin.playlist("replay", name="Missing", _ctx=_ctx()))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await plugin.playlist("stop", _ctx=_ctx())
    finally:
        release.set()
    result = await pending

    assert result["finish"]["data"]["status"] == "superseded"
    assert [_action(push)["type"] for push in pushes] == ["ui_action"]


@pytest.mark.asyncio
async def test_curated_revise_preserves_current_and_rejects_mismatched_artist():
    rows = [_song(song_id=i, name=str(i), artist="Band") for i in (1, 2, 3)]
    provider = _Provider(candidates=rows)
    plugin, pushes, _ = _make_plugin(provider)
    plugin._queues = {}
    await plugin.playlist(
        "curate", tracks=["1 | Band", "2 | Band", "3 | Wrong"], _ctx=_ctx()
    )
    ticket = _action(pushes[-1])["queue_ticket"]
    await plugin.playlist("revise", tracks=["3 | Band", "1 | Band"], _ctx=_ctx())
    queue = plugin._queues["lanlan:Mika"]
    assert [t.song_id for t in queue.tracks] == [1, 3]
    assert queue.ticket == ticket
    await plugin.playlist_terminal(ticket, "error", _ctx=_ctx())
    assert provider.resolve_calls == [1, 3]


@pytest.mark.asyncio
async def test_collect_current_song_preserves_library_and_reports_result(tmp_path):
    from unittest.mock import AsyncMock

    plugin, pushes, _ = _make_plugin(_Provider())
    plugin._queues = {}
    plugin.data_path = lambda: tmp_path
    song = {"song_id": 7, "name": "Current", "artist": "Band"}
    plugin._current_track = AsyncMock(side_effect=[song, song, None])
    library = {"dreampop": [[4, "Existing", "Artist"]], "other": [[9, "Other", "Band"]]}
    plugin._write_library("Mika", library)
    for status in ("added", "already_saved", "not_saved"):
        result = await plugin.playlist("save_current", name="dreampop", _ctx={**_ctx(), "latest_user_request": "保存到本地歌单"})
        assert result["finish"]["data"]["status"] == status
        assert pushes[-1]["ai_behavior"] == "respond"
    library["dreampop"].append([7, "Current", "Band"])
    assert plugin._library("Mika") == library


@pytest.mark.asyncio
async def test_saved_queue_plays_through_once_despite_duplicate_end_reports(tmp_path):
    songs = [_song(song_id=i, name=f"Song {i}") for i in range(1, 5)]
    provider = _Provider(candidates=songs)
    player, pushes, _ = _make_plugin(provider)
    player._queues = {}
    player.data_path = lambda: tmp_path
    player._write_library("Mika", {"Night": [[s.song_id, s.name, s.artist] for s in songs]})

    await player.playlist("replay", name="Night", _ctx=_ctx(language="en"))
    for song in songs:
        action = _action(pushes[-1])
        assert action["netease_id"] == song.song_id
        ticket = action["queue_ticket"]
        await player.playlist_terminal(ticket, "ended", _ctx=_ctx())
        duplicate = await player.playlist_terminal(ticket, "ended", _ctx=_ctx())
        assert duplicate["finish"]["data"]["status"] == "stale"
    assert provider.resolve_calls == [1, 2, 3, 4]
    assert pushes[-1]["parts"][0]["text"] == "Reached the end of Night."


@pytest.mark.asyncio
async def test_concurrent_additions_keep_both_tracks(tmp_path):
    player, _, _ = _make_plugin(_Provider())
    player.data_path = lambda: tmp_path
    rows = [[1, "First", "Artist"], [2, "Second", "Artist"]]
    await asyncio.gather(*(
        asyncio.to_thread(player._store_tracks, "Mika", "Mix", [row], append_current=True)
        for row in [*rows, rows[0]]
    ))
    assert sorted(player._library("Mika")["Mix"]) == rows


@pytest.mark.asyncio
async def test_close_after_end_cancels_pending_advance_and_next():
    from unittest.mock import AsyncMock
    from plugin.plugins.netease_music.playlists import Queue
    provider = _Provider()
    started = asyncio.Event()
    release = asyncio.Event()
    async def resolve(song_id):
        started.set()
        await release.wait()
        return provider.media
    provider.resolve_media = resolve
    plugin, pushes, _ = _make_plugin(provider)
    q = Queue('mix', [_song(1), _song(2)], cursor=1, ticket='a'*32)
    plugin._queues = {'lanlan:Mika': q}
    plugin._begin_request('lanlan:Mika')
    pending = asyncio.create_task(plugin.playlist_terminal(q.ticket, 'ended', _ctx=_ctx()))
    await started.wait()
    await plugin.playlist_terminal('a'*32, 'closed', _ctx=_ctx())
    release.set()
    await pending
    assert not any(p['parts'][0].get('action') == 'media_play_url' for p in pushes)
    assert q.cursor == len(q.tracks)
    await plugin.playlist('next', _ctx=_ctx())
    assert not any(p['parts'][0].get('action') == 'media_play_url' for p in pushes)


@pytest.mark.asyncio
async def test_cancelled_advance_retries_unplayed_track():
    from plugin.plugins.netease_music.playlists import Queue
    provider = _Provider(media_error=asyncio.CancelledError())
    plugin, _, _ = _make_plugin(provider)
    q = Queue('mix', [_song(1), _song(2)])
    plugin._queues = {'lanlan:Mika': q}
    generation = plugin._begin_request('lanlan:Mika')
    with pytest.raises(asyncio.CancelledError):
        await plugin._advance(provider, 'Mika', 'lanlan:Mika', q)
    assert q.cursor == 0
    provider.media_error = None
    await plugin.playlist('next', _ctx=_ctx())
    assert provider.resolve_calls == [1, 1]


@pytest.mark.asyncio
async def test_failed_single_request_preserves_playlist():
    from plugin.plugins.netease_music.playlists import Queue
    provider = _Provider(candidates=[])
    plugin, pushes, _ = _make_plugin(provider)
    q = Queue('mix', [_song(1), _song(2)], cursor=1, ticket='a'*32)
    plugin._queues = {'lanlan:Mika': q}
    plugin._begin_request('lanlan:Mika')
    await plugin.play_netease_music(PlayRequest(query='Missing'), _ctx())
    assert plugin._queues['lanlan:Mika'] is q
    await plugin.playlist_terminal('a'*32, 'ended', _ctx=_ctx())
    assert provider.resolve_calls == [2]


@pytest.mark.asyncio
async def test_collection_reports_failed_confirmation_without_losing_saved_track(tmp_path):
    from unittest.mock import AsyncMock
    plugin, _, _ = _make_plugin(_Provider(), receipts=[{'submitted': False, 'reason':'backpressure'}])
    plugin._queues = {}
    plugin.data_path = lambda: tmp_path
    plugin._current_track = AsyncMock(return_value={'song_id':1, 'name':'晴天', 'artist':'周杰伦'})
    result = await plugin.playlist('save_current', name='夜读', _ctx={**_ctx(), "latest_user_request": "保存到本地歌单"})
    assert result['finish']['data']['confirmation_submitted'] is False
    assert plugin._library('Mika')['夜读'] == [[1, '晴天', '周杰伦']]


def test_library_reads_utf8_even_when_platform_default_is_not_utf8(tmp_path, monkeypatch):
    plugin, _, _ = _make_plugin(_Provider())
    plugin.data_path = lambda: tmp_path
    plugin._write_library('Mika', {'夜读': [[1, '晴天', '周杰伦']]})
    original = Path.read_text
    def read_text(path, encoding=None, **kwargs):
        return original(path, encoding=encoding or 'ascii', **kwargs)
    monkeypatch.setattr(Path, 'read_text', read_text)
    assert plugin._library('Mika')['夜读'][0][1] == '晴天'


@pytest.mark.asyncio
async def test_failed_single_during_transition_does_not_abandon_next_track():
    from plugin.plugins.netease_music.playlists import Queue
    started, release = asyncio.Event(), asyncio.Event()
    provider = _Provider(candidates=[])
    async def resolve(song_id):
        started.set()
        await release.wait()
        return provider.media
    provider.resolve_media = resolve
    plugin, pushes, _ = _make_plugin(provider)
    q = Queue('mix', [_song(1), _song(2)], cursor=1, ticket='a'*32)
    plugin._queues = {'lanlan:Mika': q}
    plugin._begin_request('lanlan:Mika')
    pending = asyncio.create_task(plugin.playlist_terminal(q.ticket, 'ended', _ctx=_ctx()))
    await started.wait()
    await plugin.play_netease_music(PlayRequest(query='Missing'), _ctx())
    release.set()
    await pending
    actions = [p['parts'][0] for p in pushes if p['parts'][0].get('action') == 'media_play_url']
    assert [a['netease_id'] for a in actions] == [2]


@pytest.mark.asyncio
async def test_credential_change_invalidates_pending_queue_resolution():
    from plugin.plugins.netease_music.playlists import Queue
    started, release = asyncio.Event(), asyncio.Event()
    provider = _Provider()
    async def resolve(song_id):
        started.set()
        await release.wait()
        return provider.media
    provider.resolve_media = resolve
    plugin, pushes, _ = _make_plugin(provider)
    q = Queue('mix', [_song(1)])
    plugin._queues = {'lanlan:Mika': q}
    plugin._begin_request('lanlan:Mika')
    pending = asyncio.create_task(plugin._advance(provider,'Mika','lanlan:Mika',q))
    await started.wait()
    await plugin._invalidate_credential_requests()
    assert q.cursor == 0
    release.set()
    result = await pending
    assert result['finish']['data']['status'] == 'superseded'
    assert pushes == []


@pytest.mark.asyncio
async def test_old_resolution_cannot_rewind_a_new_advance_after_credential_change():
    from plugin.plugins.netease_music.playlists import Queue
    started, release = asyncio.Event(), asyncio.Event()
    provider = _Provider()
    calls = []
    async def resolve(song_id):
        calls.append(song_id)
        if len(calls) == 1:
            started.set()
            await release.wait()
        return provider.media
    provider.resolve_media = resolve
    plugin, pushes, _ = _make_plugin(provider)
    q = Queue('mix', [_song(1), _song(2)])
    plugin._queues = {'lanlan:Mika': q}
    plugin._begin_request('lanlan:Mika')
    pending = asyncio.create_task(plugin._advance(provider, 'Mika', 'lanlan:Mika', q))
    await started.wait()
    await plugin._invalidate_credential_requests()
    await plugin.playlist('next', _ctx=_ctx())
    ticket = q.ticket
    assert calls == [1, 1]
    assert q.cursor == 1
    release.set()
    assert (await pending)['finish']['data']['status'] == 'superseded'
    assert q.cursor == 1 and q.ticket == ticket
    await plugin.playlist('next', _ctx=_ctx())
    assert calls == [1, 1, 2]


@pytest.mark.asyncio
async def test_failed_next_delivery_can_retry_same_terminal_ticket_once():
    from plugin.plugins.netease_music.playlists import Queue
    provider = _Provider()
    player, pushes, _ = _make_plugin(provider, receipts=[{"submitted": False}, {"submitted": True}])
    q = Queue('mix', [_song(1), _song(2)], cursor=1, ticket='a'*32)
    player._queues = {'lanlan:Mika': q}
    player._begin_request('lanlan:Mika')
    await player.playlist_terminal('a'*32, 'ended', _ctx=_ctx())
    assert q.cursor == 1 and q.retry_ticket == 'a'*32
    await player.playlist_terminal('a'*32, 'ended', _ctx=_ctx())
    assert q.cursor == 2 and q.ticket and not q.retry_ticket
    duplicate = await player.playlist_terminal('a'*32, 'ended', _ctx=_ctx())
    assert duplicate['finish']['data']['status'] == 'stale'
    assert provider.resolve_calls == [2, 2]


@pytest.mark.asyncio
async def test_exhausted_queue_rejects_old_close_ticket():
    from plugin.plugins.netease_music.playlists import Queue
    player, _, _ = _make_plugin(_Provider())
    q = Queue('mix', [_song(1)], cursor=1, ticket='a'*32)
    player._queues = {'lanlan:Mika': q}
    player._begin_request('lanlan:Mika')
    await player.playlist_terminal('a'*32, 'ended', _ctx=_ctx())
    generation = player._session_generations['lanlan:Mika']
    assert not q.advancing_ticket
    await player.playlist_terminal('a'*32, 'closed', _ctx=_ctx())
    assert player._session_generations['lanlan:Mika'] == generation


@pytest.mark.asyncio
async def test_selected_name_uses_original_offered_group(tmp_path):
    provider = _Provider()
    player, _, _ = _make_plugin(provider)
    player._queues = {}
    player.data_path = lambda: tmp_path
    player._write_library('Mika', {n: [[i, n, 'Artist']] for i,n in enumerate(['夜读','夜读歌单','夜读歌单歌单'],1)})
    await player.playlist('replay', name='夜读', _ctx=_ctx())
    await player.playlist('replay', name='夜读歌单', name_confirmed=True, _ctx=_ctx())
    assert provider.resolve_calls == [2]


def test_exact_query_accepts_complete_collaborative_credit():
    from plugin.plugins.netease_music.provider import select_first_exact_match
    song = _song(1, name='Song', artist='A / B')
    assert select_first_exact_match('Song A / B', [song]) is song


@pytest.mark.asyncio
async def test_failed_stop_submission_is_an_error():
    player, _, _ = _make_plugin(_Provider(), receipts=[{'submitted': False}])
    player._queues = {}
    result = await player.playlist('stop', _ctx=_ctx())
    from plugin.sdk.plugin import Err
    assert isinstance(result, Err)
