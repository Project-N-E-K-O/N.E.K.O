from __future__ import annotations

import ast
import asyncio
import json
import tomllib
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
    assert plugin_meta["name"] == "网易云匿名点歌"
    assert "匿名搜索网易云单曲" in plugin_meta["description"]
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
        "messages.fallback",
        "errors.missing_target",
        "errors.search_failed",
        "errors.media_failed",
        "errors.delivery_failed",
        "errors.message_delivery_failed",
    } == expected_keys


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
async def test_first_result_wins_even_when_later_result_is_exact() -> None:
    first = _song(song_id=1, name="纸短情长 (翻唱版)", artist="其他歌手")
    provider = _Provider(
        candidates=[
            first,
            _song(song_id=2, name="纸短情长", artist="烟把儿"),
        ]
    )
    plugin, pushes, finish_calls = _make_plugin(provider)

    result = await plugin.play_netease_music(PlayRequest(query="纸短情长"), _ctx())

    assert result["finish"]["delivery"] == "silent"
    assert provider.resolve_calls == [first.song_id]
    assert _action(pushes[1])["name"] == "纸短情长 (翻唱版)"
    assert _action(pushes[2])["text"].startswith("我不太确定")
    assert finish_calls[-1]["data"]["used_fallback"] is True


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_fuzzy_query_plays_first_result_and_pushes_gentle_notice() -> None:
    provider = _Provider(
        candidates=[
            _song(song_id=1, artist="周杰伦"),
            _song(song_id=2, artist="其他歌手", album="翻唱合集"),
        ]
    )
    plugin, pushes, finish_calls = _make_plugin(provider)

    result = await plugin.play_netease_music(PlayRequest(query="晴"), _ctx())

    assert result["finish"]["delivery"] == "silent"
    assert provider.resolve_calls == [1]
    assert len(pushes) == 3
    assert _action(pushes[1])["action"] == "media_play_url"
    assert _action(pushes[1])["artist"] == "周杰伦"
    assert pushes[2]["visibility"] == ["chat"]
    assert pushes[2]["ai_behavior"] == "blind"
    assert pushes[2]["target_lanlan"] == "Mika"
    assert _action(pushes[2])["text"] == (
        "我不太确定这是不是你想听的版本，先按搜索结果给你放第一首啦。"
    )
    assert finish_calls[-1]["data"]["status"] == "submitted"
    assert finish_calls[-1]["data"]["used_fallback"] is True
    assert finish_calls[-1]["data"]["notice_submitted"] is True


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_fallback_notice_rejection_does_not_turn_submitted_play_into_error() -> None:
    provider = _Provider(
        candidates=[
            _song(song_id=1, artist="周杰伦"),
            _song(song_id=2, artist="其他歌手"),
        ]
    )
    plugin, pushes, finish_calls = _make_plugin(
        provider,
        receipts=[
            {"submitted": True},
            {"submitted": True},
            {"submitted": False, "reason": "transport_unavailable"},
        ],
    )

    result = await plugin.play_netease_music(PlayRequest(query="晴"), _ctx())

    assert result["finish"]["delivery"] == "silent"
    assert len(pushes) == 3
    assert finish_calls[-1]["data"]["status"] == "submitted"
    assert finish_calls[-1]["data"]["notice_submitted"] is False


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
