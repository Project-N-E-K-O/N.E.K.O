from unittest.mock import AsyncMock
import pytest
from plugin.plugins.netease_music.cloud import CloudLibrary, CloudError, page
from plugin.plugins.netease_music import cloud as api
from plugin.plugins.netease_music.cloud_entry import CloudMusicMixin


@pytest.mark.asyncio
async def test_credentials_required():
    with pytest.raises(CloudError, match="login_required"):
        async with CloudLibrary({}):
            pass


@pytest.mark.asyncio
async def test_account_is_sanitized_and_private_session_closed(monkeypatch):
    mock = AsyncMock(
        return_value={
            "code": 200,
            "profile": {
                "userId": 7,
                "nickname": "Name",
                "vipType": 11,
                "phone": "must-not-expose",
            },
        }
    )
    monkeypatch.setattr(api, "GetCurrentLoginStatus", mock)
    async with CloudLibrary({"MUSIC_U": "test-secret"}) as library:
        session = library.session
        assert await library.account() == {
            "user_id": 7,
            "nickname": "Name",
            "vip_type": 11,
        }
        assert mock.call_args.kwargs["session"] is session
    assert session.is_closed


@pytest.mark.asyncio
async def test_auth_errors_do_not_leak_upstream_values():
    library = CloudLibrary({})
    with pytest.raises(CloudError, match="login_expired"):
        await library.call(AsyncMock(return_value={"code": 301, "cookie": "secret"}))
    with pytest.raises(CloudError, match="upstream_unavailable") as caught:
        await library.call(AsyncMock(side_effect=RuntimeError("cookie=secret")))
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_playlist_pagination_and_ownership(monkeypatch):
    library = CloudLibrary({})
    library.account = AsyncMock(return_value={"user_id": 7})
    library.call = AsyncMock(
        side_effect=[
            {
                "more": True,
                "playlist": [
                    {
                        "id": 1,
                        "name": "Mine",
                        "creator": {"userId": 7},
                        "specialType": 5,
                    }
                ],
            },
            {
                "more": False,
                "playlist": [{"id": 2, "name": "Saved", "creator": {"userId": 8}}],
            },
        ]
    )
    rows = await library.playlists()
    assert [r["kind"] for r in rows] == ["created", "subscribed"]
    assert rows[0]["liked"] is True
    assert library.call.call_args_list[1].kwargs["offset"] == 200
    assert page(rows, 0, 1)["next_offset"] == 1


@pytest.mark.asyncio
async def test_ambiguous_names_and_other_owners_are_not_modified():
    library = CloudLibrary({})
    library.playlists = AsyncMock(
        return_value=[
            {"id": 1, "name": "Rock", "kind": "created", "liked": False},
            {"id": 2, "name": "Rock", "kind": "subscribed", "liked": False},
        ]
    )
    with pytest.raises(CloudError, match="ambiguous_playlist"):
        await library.resolve_playlist("Rock")
    with pytest.raises(CloudError, match="playlist_not_owned"):
        await library.change_tracks("2", [10], add=True)


@pytest.mark.asyncio
async def test_liked_playlist_belongs_to_current_account():
    library = CloudLibrary({})
    library.playlists = AsyncMock(
        return_value=[
            {"id": 1, "name": "My likes", "kind": "created", "liked": True},
            {
                "id": 2,
                "name": "Someone else's likes",
                "kind": "subscribed",
                "liked": True,
            },
        ]
    )
    assert (await library.resolve_playlist("liked"))["id"] == 1


@pytest.mark.asyncio
async def test_create_private_playlist_verified_and_duplicate_is_idempotent():
    library = CloudLibrary({})
    library.account = AsyncMock(return_value={"user_id": 7})
    created = {"id": 8, "name": "Night", "kind": "created"}
    library.playlists = AsyncMock(side_effect=[[], [created], [created]])
    library.call = AsyncMock(return_value={"code": 200, "playlist": {"id": 8}})
    assert (await library.create("Night"))["status"] == "created"
    assert library.call.call_args.kwargs == {"privacy": True}
    assert (await library.create("Night"))["status"] == "already_exists"
    assert library.call.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "add,before,after,status",
    [(True, [1], [1, 2], "added"), (False, [1, 2], [1], "removed")],
)
async def test_cloud_collection_writes_are_read_back(add, before, after, status):
    library = CloudLibrary({})
    library.resolve_playlist = AsyncMock(return_value={"id": 8, "name": "Rock"})
    library.track_ids = AsyncMock(side_effect=[before, after])
    library.call = AsyncMock(return_value={"code": 200})
    result = await library.change_tracks("Rock", [2], add=add)
    assert result["status"] == status
    assert library.call.call_args.kwargs["op"] == ("add" if add else "del")


@pytest.mark.asyncio
async def test_unconfirmed_write_is_not_reported_successful():
    library = CloudLibrary({})
    library.resolve_playlist = AsyncMock(return_value={"id": 8})
    library.track_ids = AsyncMock(return_value=[1])
    library.call = AsyncMock(return_value={"code": 200})
    with pytest.raises(CloudError, match="write_unconfirmed"):
        await library.change_tracks("Rock", [2], add=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("liked,before,after", [(True, [], [2]), (False, [2], [])])
async def test_like_and_unlike_use_account_and_verify(liked, before, after):
    library = CloudLibrary({})
    library.account = AsyncMock(return_value={"user_id": 7})
    library.resolve_playlist = AsyncMock(return_value={"id": 8})
    library.track_ids = AsyncMock(side_effect=[before, after])
    library.call = AsyncMock(return_value={"code": 200})
    assert (await library.like(2, liked=liked))["liked"] is liked
    assert library.call.call_args.kwargs == {"like": liked, "userid": 7}


@pytest.mark.asyncio
async def test_playlist_track_order_preserved():
    library = CloudLibrary({})
    library.track_ids = AsyncMock(return_value=[3, 1, 2])
    library.call = AsyncMock(
        return_value={
            "songs": [
                {"id": 1, "name": "one", "ar": [{"name": "a"}]},
                {"id": 3, "name": "three", "ar": [{"name": "a"}]},
            ]
        }
    )
    tracks, total = await library.tracks(8, 0, 2)
    assert [t.song_id for t in tracks] == [3, 1] and total == 3


@pytest.mark.asyncio
async def test_write_entry_without_host_plan_is_rejected():
    plugin = CloudMusicMixin()
    plugin._target_lanlan = lambda ctx: "YUI"
    plugin.finish = AsyncMock(side_effect=lambda **kw: kw)
    for text in ["网易云如何创建歌单", "不要创建歌单", "看看我的歌单"]:
        result = await plugin.cloud_music(
            "create", playlist="Rock", _ctx={"latest_user_request": text}
        )
        assert result["data"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_cloud_playlist_requires_exact_names():
    library = CloudLibrary({})
    library.playlists = AsyncMock(
        return_value=[{"id": 1, "name": "Bgm", "kind": "created", "liked": False}]
    )
    with pytest.raises(CloudError, match="playlist_not_found"):
        await library.resolve_playlist("Bgm歌单")
    library.playlists.return_value.append(
        {"id": 2, "name": "Bgm歌单", "kind": "created", "liked": False}
    )
    assert (await library.resolve_playlist("Bgm歌单"))["id"] == 2


@pytest.mark.parametrize("source", ["proactive", "", None])
@pytest.mark.parametrize("action,utterance", [("like", "我帮你把这首歌加入喜欢的音乐吧"),
    ("add", "把这首加入夜读歌单"), ("remove", "从夜读歌单移除这首"),
    ("create", "创建网易云歌单夜读"), ("unlike", "取消这首歌的红心")])
async def test_non_user_source_cannot_mutate_cloud(source, action, utterance):
    plugin = CloudMusicMixin()
    plugin._target_lanlan = lambda ctx: "YUI"
    plugin.finish = AsyncMock(side_effect=lambda **kw: kw)
    plugin._new_cloud_library = lambda: pytest.fail("Must not open a cloud session")
    result = await plugin.cloud_music(action, _ctx={"request_source": source, "latest_user_request": utterance})
    assert result["data"]["status"] == "blocked"


@pytest.mark.parametrize("action,utterance", [("like", "我不想收藏这首"), ("unlike", "别取消这首歌的红心"),
    ("like", "Don't save this song"), ("unlike", "Do not unlike this track")])
async def test_user_source_without_authorized_plan_cannot_mutate_cloud(action, utterance):
    plugin = CloudMusicMixin()
    plugin._target_lanlan = lambda ctx: "YUI"
    plugin.finish = AsyncMock(side_effect=lambda **kw: kw)
    result = await plugin.cloud_music(action, _ctx={"request_source": "user", "latest_user_request": utterance})
    assert result["data"]["status"] == "blocked"


@pytest.mark.parametrize("utterance", ["收藏這首", "Save this song", "Like the current song"])
async def test_multilingual_explicit_user_save_reaches_write_and_readback(utterance):
    from plugin.tests.unit.plugins.netease_music.test_plugin import _make_plugin, _Provider, _ctx
    plugin, _, _ = _make_plugin(_Provider())
    library = CloudLibrary({})
    library.call = AsyncMock(return_value={"code": 200})
    library.account = AsyncMock(return_value={"user_id": 7})
    library.resolve_playlist = AsyncMock(return_value={"id": 9})
    library.track_ids = AsyncMock(side_effect=[[], [42]])
    class Session:
        async def __aenter__(self): return library
        async def __aexit__(self, *args): pass
    plugin._new_cloud_library = Session
    result = await plugin.cloud_music("like", song="42", _ctx={**_ctx(), "latest_user_request": utterance, "authorized_action": "like"})
    assert result["finish"]["data"]["status"] == "liked"
    assert library.call.await_count == 1
    assert library.track_ids.await_count == 2


async def test_cloud_play_waiting_on_lock_cannot_restart_after_stop():
    import asyncio
    from plugin.tests.unit.plugins.netease_music.test_plugin import _make_plugin, _Provider, _ctx
    plugin, pushes, _ = _make_plugin(_Provider())
    plugin._queues = {}
    plugin._cloud_lock = asyncio.Lock()
    await plugin._cloud_lock.acquire()
    plugin._new_cloud_library = lambda: pytest.fail("Superseded utterance must not query cloud")
    pending = asyncio.create_task(plugin.cloud_music("play", playlist="liked", _ctx=_ctx()))
    try:
        await asyncio.sleep(0)
        await plugin.playlist("stop", _ctx=_ctx())
    finally:
        plugin._cloud_lock.release()
    result = await pending
    assert result["finish"]["data"]["status"] == "superseded"
    assert not any(part.get("action") == "media_play_url" for push in pushes for part in push["parts"])
