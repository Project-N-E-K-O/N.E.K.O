import asyncio
import hashlib
import json
import threading
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from utils.proactive_preferences.personalization import (
    LocalPreferences,
    RecommendationContext,
)
from utils.proactive_preferences.preference_model import DAY, PreferenceEvent, Profile
from utils.proactive_preferences.search_policy import SearchPermission
from main_logic import recommendation_feedback
from main_logic.recommendation_feedback import RecommendationStore
from main_logic.proactive_chat import preference_capture
from services.proactive_feedback import host as feedback_host
from services.proactive_feedback.host import owner_from_snapshot
from services.proactive_feedback.runtime import ProactiveFeedbackRuntime
from utils import preferences
from utils.web_scraper import trending_content


@pytest.fixture
def privacy_settings_path(tmp_path, monkeypatch):
    path = tmp_path / "user_preferences.json"
    monkeypatch.setattr(preferences, "PREFERENCES_FILE", str(path))
    monkeypatch.setattr(preferences, "_get_preferences_write_path", lambda: str(path))
    monkeypatch.setattr(preferences, "_get_preferences_read_path", lambda: str(path))
    return path


def test_chat_preferences_are_owner_scoped_and_rank_across_sources(tmp_path: Path):
    now = 1_750_000_000
    positive = [
        PreferenceEvent("user-message-1", "独立解谜", "all", "explicit", 1, now)
    ]
    negative = [
        PreferenceEvent("user-message-2", "竞技游戏", "video", "exclude", -1, now)
    ]
    inferred = [PreferenceEvent("user-message-3", "解谜", "all", "inferred", 1, now)]
    profile = Profile.from_events(positive + negative + inferred, now)
    assert profile.score("news", {"解谜"}) == 0.5
    assert Profile.from_events(inferred, now + 7 * DAY).score("news", {"解谜"}) == 0.25
    exception = Profile.from_events(
        [PreferenceEvent("no-puzzles", "独立解谜", "all", "exclude", -1, now)], now
    )
    assert exception.rank("video", [{"title": "独立解谜"}]) == []
    assert exception.rank("video", [{"title": "博物馆的密室机关"}])

    candidates = [
        {"title": "另一个话题"},
        {"title": "独立解谜的机关如何设计"},
        {"title": "竞技游戏决赛"},
    ]
    for category in (
        "news",
        "video",
        "community",
        "personal",
        "home",
        "meme",
        "mini_game",
    ):
        ranked = profile.rank(category, candidates)
        assert ranked[0] == candidates[1]
        assert (candidates[2] in ranked) is (category != "video")

    store = RecommendationStore(tmp_path / "recommendations.sqlite3")
    store.save_preferences("guest", positive + negative)
    store.save_preferences("guest", positive)
    preferences, events = store.preference_context({"key": "guest"})
    assert len(events) == 2
    assert preferences.profile.rank("news", candidates)[0] == candidates[1]
    assert not store.local_preferences({"key": "account"}).profile.weights


@pytest.mark.asyncio
@pytest.mark.parametrize("switch_account", [False, True])
async def test_chat_source_worker_keeps_admitted_owner(tmp_path, monkeypatch, switch_account):
    origin = "https://preferences-community.example.test"
    original = {"base_url": origin, "local_user_id": "account-a", "access_token": "synthetic-a"}
    replacement = {**original, "local_user_id": "account-b", "access_token": "synthetic-b"}
    active = original
    loop = asyncio.get_running_loop()
    worker_started = asyncio.Event()
    release_worker = threading.Event()

    @contextmanager
    def attribution():
        loop.call_soon_threadsafe(worker_started.set)
        assert release_worker.wait(3)
        yield owner_from_snapshot(active)

    monkeypatch.setattr(feedback_host.credentials, "saved_session_snapshot", lambda: active)
    monkeypatch.setattr(feedback_host.credentials, "signed_in_account_state", lambda: ("signed_in", active["local_user_id"]))
    monkeypatch.setattr(feedback_host, "attribution_context", attribution)
    owner_key = preference_capture.capture_chat_source_owner()
    assert owner_key == owner_from_snapshot(original)["key"]
    config_manager = SimpleNamespace(get_runtime_config_path=lambda name: tmp_path / name)
    source_id = "1" * 32
    transcript = "I am learning swimming"
    task = asyncio.create_task(preference_capture.remember_chat_source(
        "NEKO", source_id, transcript, owner_key=owner_key, config_manager=config_manager,
    ))
    try:
        await asyncio.wait_for(worker_started.wait(), 3)
        if switch_account:
            active = replacement
    finally:
        release_worker.set()
        await task
    store = RecommendationStore(tmp_path / "recommendation_feedback.sqlite3")
    assert store.chat_source_digests(owner_from_snapshot(replacement)["key"], "NEKO") == {}
    assert store.chat_source_digests(owner_key, "NEKO") == (
        {} if switch_account else {source_id: hashlib.sha256(transcript.encode()).hexdigest()}
    )


def test_durable_exclusions_survive_inferred_event_trimming(tmp_path):
    now = int(time())
    store = RecommendationStore(tmp_path / "preferences.sqlite3")
    owner = {"key": "test-account"}
    blocked = PreferenceEvent("blocked", "竞技游戏", "video", "exclude", -1, now - DAY)
    old_affinity = PreferenceEvent("old-affinity", "摄影", "all", "explicit", 1, now - DAY)
    new_affinity = PreferenceEvent("new-affinity", "摄影", "all", "explicit", -1, now)
    inferred = [
        PreferenceEvent(f"chat-{index}", "游泳", "all", "inferred", 1, now)
        for index in range(2002)
    ]
    store.save_preferences(owner["key"], [blocked, old_affinity, new_affinity, *inferred])
    preferences, events = store.preference_context(owner)
    assert len(events) == 2002
    assert preferences.profile.score("video", {blocked.feature}) is None
    assert preferences.profile.score("video", {new_affinity.feature}) == -3
    store.save_preferences(owner["key"], [
        PreferenceEvent("unblock", blocked.feature, blocked.scope, "allow", 1, now + 1),
    ])
    assert store.local_preferences(owner).profile.score("video", {blocked.feature}) == 0


def test_interest_upload_cannot_reuse_an_older_game_permission(tmp_path):
    now = int(time())
    store = RecommendationStore(tmp_path / "preferences.sqlite3")
    owner = {"key": "test-account"}
    older = PreferenceEvent("a" * 64, "独立解谜", "all", "inferred", 1, now - 1)
    newer = PreferenceEvent("b" * 64, "独立解谜", "all", "inferred", 1, now)
    store.save_preferences(owner["key"], [older, newer], permissions={
        older.id: SearchPermission(True, True), newer.id: SearchPermission(True, False),
    })
    pending = store.pending_chat_interests(owner)
    assert [interest.event for interest in pending] == [older, newer]
    assert not any(interest.game for interest in pending)


@pytest.mark.parametrize("age", [DAY, 31 * DAY])
def test_preference_pruning_cannot_restore_superseded_public_permission(tmp_path, age):
    now = int(time())
    store = RecommendationStore(tmp_path / "preferences.sqlite3")
    owner = {"key": "test-account"}
    old_public = PreferenceEvent("a" * 64, "蛙泳换气", "all", "explicit", 1, now - 32 * DAY)
    private = PreferenceEvent("b" * 64, old_public.feature, "all", "inferred", 1, now - age)
    public = PreferenceEvent("c" * 64, "夜景摄影", "all", "explicit", 1, now)
    unrelated = [
        PreferenceEvent(f"chat-{index}", "游泳", "all", "inferred", 1, now)
        for index in range(2002)
    ]
    store.save_preferences(owner["key"], [old_public, private, public, *unrelated], permissions={
        old_public.id: SearchPermission(True), private.id: SearchPermission(False),
        public.id: SearchPermission(True),
    })
    assert store.local_preferences(owner).search_permission("news", old_public.feature) == SearchPermission(False)
    assert [item.event for item in store.pending_chat_interests(owner)] == [public]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_settings", ["malformed", "unreadable"])
async def test_chat_interest_sync_skips_unreadable_privacy_settings(tmp_path, monkeypatch, privacy_settings_path, bad_settings):
    origin = "https://preferences-community.example.test"
    saved = {"base_url": origin, "local_user_id": "account", "access_token": "synthetic"}
    owner = owner_from_snapshot(saved)
    store = RecommendationStore(tmp_path / "preferences.sqlite3")
    event = PreferenceEvent("a" * 64, "蛙泳换气", "all", "inferred", 1, int(time()))
    store.save_preferences(owner["key"], [event], permissions={event.id: SearchPermission(True)})
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"version": 1, "revision": "e" * 64, "events": []})

    @contextmanager
    def attribution():
        yield owner

    if bad_settings == "malformed":
        privacy_settings_path.write_text("{", encoding="utf-8")
    else:
        privacy_settings_path.mkdir()
    monkeypatch.setattr("services.proactive_feedback.runtime.fetch_public_search_words", AsyncMock(return_value=frozenset()))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        runtime = ProactiveFeedbackRuntime(
            store, client=client, chat_interest_origin=origin, snapshot_provider=lambda: saved,
            fenced_snapshot_provider=lambda: saved, attribution_context=attribution,
        )
        await runtime._sync_chat_interests()
        assert not requests
        assert [item.event for item in store.pending_chat_interests(owner)] == [event]
        if bad_settings == "malformed":
            privacy_settings_path.unlink()
        else:
            privacy_settings_path.rmdir()
        await runtime._sync_chat_interests()
    assert [upload["events"] for upload in requests] == [[{**asdict(event), "game": False}]]


@pytest.mark.asyncio
async def test_chat_interest_sync_pauses_when_privacy_changes_during_batch_read(tmp_path, monkeypatch, privacy_settings_path):
    origin = "https://preferences-community.example.test"
    saved = {"base_url": origin, "local_user_id": "account", "access_token": "synthetic"}
    owner = owner_from_snapshot(saved)
    store = RecommendationStore(tmp_path / "preferences.sqlite3")
    event = PreferenceEvent("a" * 64, "蛙泳换气", "all", "inferred", 1, int(time()))
    store.save_preferences(owner["key"], [event], permissions={event.id: SearchPermission(True)})
    pending = store.pending_chat_interests
    privacy_on = False
    enable_on_read = True
    requests = []

    def read_batch(*args):
        nonlocal privacy_on
        batch = pending(*args)
        if enable_on_read:
            privacy_on = True
        return batch

    async def privacy_mode(*, strict=False):
        assert strict
        return privacy_on

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"version": 1, "revision": "e" * 64, "events": []})

    @contextmanager
    def attribution():
        yield owner

    monkeypatch.setattr(store, "pending_chat_interests", read_batch)
    monkeypatch.setattr(preferences, "ais_privacy_mode_enabled", privacy_mode)
    monkeypatch.setattr("services.proactive_feedback.runtime.fetch_public_search_words", AsyncMock(return_value=frozenset()))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        runtime = ProactiveFeedbackRuntime(
            store, client=client, chat_interest_origin=origin, snapshot_provider=lambda: saved,
            fenced_snapshot_provider=lambda: saved, attribution_context=attribution,
        )
        await runtime._sync_chat_interests()
        assert privacy_on and not requests
        assert [item.event for item in pending(owner)] == [event]
        privacy_on = enable_on_read = False
        await runtime._sync_chat_interests()
    assert [upload["events"] for upload in requests] == [[{**asdict(event), "game": False}]]
    assert pending(owner) == []


@pytest.mark.parametrize("new_public_age", [29, 31])
def test_expiring_public_inference_cannot_erase_underlying_private_permission(tmp_path, monkeypatch, new_public_age):
    now = int(time())
    clock = [now]
    monkeypatch.setattr(recommendation_feedback, "time", SimpleNamespace(time=lambda: clock[0]))
    store = RecommendationStore(tmp_path / "preferences.sqlite3")
    owner = {"key": "test-account"}
    topic = "蛙泳换气"
    explicit = PreferenceEvent("a" * 64, topic, "all", "explicit", 1, now - 33 * DAY)
    private = PreferenceEvent("b" * 64, topic, "all", "inferred", 1, now - 32 * DAY)
    temporary_public = PreferenceEvent("c" * 64, topic, "all", "inferred", 1, now - new_public_age * DAY)
    store.save_preferences(owner["key"], [explicit, private, temporary_public], permissions={
        explicit.id: SearchPermission(True), private.id: SearchPermission(False),
        temporary_public.id: SearchPermission(True),
    })
    assert bool(store.pending_chat_interests(owner)) == (new_public_age < 30)
    clock[0] += 2 * DAY
    store.save_preferences(owner["key"], [])
    assert store.local_preferences(owner).search_permission("news", topic) == SearchPermission(False)
    assert store.pending_chat_interests(owner) == []


@pytest.mark.asyncio
async def test_chat_interest_snapshot_is_not_committed_if_privacy_turns_on(tmp_path, monkeypatch, privacy_settings_path):
    origin = "https://preferences-community.example.test"
    saved = {"base_url": origin, "local_user_id": "account", "access_token": "synthetic"}
    owner = owner_from_snapshot(saved)
    store = RecommendationStore(tmp_path / "preferences.sqlite3")
    remote = PreferenceEvent("e" * 64, "夜景摄影", "all", "explicit", 1, int(time()))
    revision = "f" * 64
    ready_to_commit = asyncio.Event()
    release_commit = threading.Event()
    loop = asyncio.get_running_loop()

    def handler(_request):
        return httpx.Response(200, json={"version": 1, "revision": revision, "events": [asdict(remote)]})

    @contextmanager
    def attribution():
        loop.call_soon_threadsafe(ready_to_commit.set)
        assert release_commit.wait(3)
        yield owner

    def set_privacy(enabled):
        with preferences._locked_preferences_store(attribute=False):
            privacy_settings_path.write_text(json.dumps([{
                "model_path": preferences.GLOBAL_CONVERSATION_KEY,
                "proactiveVisionEnabled": not enabled,
            }]))

    monkeypatch.setattr("services.proactive_feedback.runtime.fetch_public_search_words", AsyncMock(return_value=frozenset()))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProactiveFeedbackRuntime(
            store, client=client, chat_interest_origin=origin, snapshot_provider=lambda: saved,
            fenced_snapshot_provider=lambda: saved, attribution_context=attribution,
        )
        task = asyncio.create_task(runtime._sync_chat_interests())
        try:
            await asyncio.wait_for(ready_to_commit.wait(), 3)
            await asyncio.to_thread(set_privacy, True)
        finally:
            release_commit.set()
            await task
        assert "夜景摄影" not in store.local_preferences(owner).profile.features("news")
        assert runtime._chat_interest_revision is None
        await asyncio.to_thread(set_privacy, False)
        await runtime._sync_chat_interests()
    assert "夜景摄影" in store.local_preferences(owner).profile.features("news")
    assert runtime._chat_interest_revision == (owner["key"], revision)


@pytest.mark.asyncio
async def test_account_chat_interests_sync_without_requesting_community_candidates(
    tmp_path, monkeypatch, privacy_settings_path
):
    now = int(time())
    origin = "https://preferences-community.example.test"
    saved = {
        "base_url": origin,
        "local_user_id": "account",
        "access_token": "synthetic-token",
    }
    other = {**saved, "local_user_id": "other", "access_token": "other-token"}
    active = saved
    owner = owner_from_snapshot(saved)
    store = RecommendationStore(tmp_path / "recommendations.sqlite3")
    events = [
        PreferenceEvent("a" * 64, "蛙泳换气", "all", "inferred", 1, now),
        PreferenceEvent("b" * 64, "此处仅本机自定义主题", "all", "explicit", 1, now),
        PreferenceEvent("c" * 64, "独立解谜", "personal", "explicit", 1, now),
        PreferenceEvent("d" * 64, "病历记录", "all", "explicit", 1, now),
        PreferenceEvent("9" * 64, "蓝鸥苑六号楼", "all", "explicit", 1, now),
    ]
    store.save_preferences(
        owner["key"],
        events,
        permissions={
            event.id: SearchPermission(event != events[1]) for event in events
        },
    )
    store.save_preferences(
        owner_from_snapshot(None)["key"],
        [
            PreferenceEvent("f" * 64, "离线桌游", "all", "explicit", 1, now),
        ],
        permissions={"f" * 64: SearchPermission(True)},
    )
    remote = PreferenceEvent("e" * 64, "竞技游戏", "all", "exclude", -1, now)
    remote_game = PreferenceEvent("8" * 64, "独立解谜游戏", "all", "inferred", 1, now)
    revision = "7" * 64
    requests = []

    def handle(request):
        nonlocal active
        assert str(request.url) == f"{origin}/api/users/me/chat-interests/sync"
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(503)
        if len(requests) == 2:
            active = other
        incoming = (
            []
            if requests[-1].get("known_revision")
            else [
                asdict(remote),
                {**asdict(remote_game), "game": True},
            ]
        )
        return httpx.Response(
            200, json={"version": 1, "revision": revision, "events": incoming}
        )

    @contextmanager
    def attribution():
        yield owner_from_snapshot(active)

    privacy = AsyncMock(return_value=True)
    monkeypatch.setattr(preferences, "ais_privacy_mode_enabled", privacy)
    monkeypatch.setattr(
        "services.proactive_feedback.runtime.fetch_public_search_words",
        AsyncMock(return_value=frozenset({"蓝鸥", "六号楼"})),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        runtime = ProactiveFeedbackRuntime(
            store,
            client=client,
            chat_interest_origin=origin,
            snapshot_provider=lambda: active,
            fenced_snapshot_provider=lambda: active,
            attribution_context=attribution,
        )
        await runtime._sync_chat_interests()
        assert requests == []
        privacy.return_value = False
        await runtime._sync_chat_interests()
        assert [item.event for item in store.pending_chat_interests(owner)] == events[
            :1
        ]
        await runtime._sync_chat_interests()
        assert [item.event for item in store.pending_chat_interests(owner)] == events[
            :1
        ]
        assert not store.local_preferences(owner_from_snapshot(other)).profile.weights
        active = saved
        await runtime._sync_chat_interests()
        assert store.pending_chat_interests(owner) == []
        await runtime._sync_chat_interests()
    expected = {
        "version": 1,
        "account_id": "account",
        "events": [{**asdict(events[0]), "game": False}],
    }
    assert requests == [
        expected,
        expected,
        expected,
        {**expected, "events": [], "known_revision": revision},
    ]
    prefs = store.local_preferences(owner)
    assert prefs.profile.score("news", {"竞技游戏"}) is None
    assert prefs.search_permission("news", "独立解谜游戏") == SearchPermission(
        True, True
    )
    from main_logic.proactive_chat.interest_retrieval import public_interest_topics

    assert "独立解谜游戏" in public_interest_topics(prefs, "news")
    assert "竞技游戏" not in store.local_preferences(
        owner_from_snapshot(None)
    ).profile.features("news")


@pytest.mark.asyncio
async def test_sync_drains_upload_backlog_before_merging_account_snapshot(
    tmp_path,
    monkeypatch,
    privacy_settings_path,
):
    from utils.proactive_preferences.preference_model import SYNC_BATCH_LIMIT

    now = int(time())
    origin = "https://preferences-community.example.test"
    saved = {
        "base_url": origin,
        "local_user_id": "account",
        "access_token": "synthetic",
    }
    owner = owner_from_snapshot(saved)
    store = RecommendationStore(tmp_path / "recommendations.sqlite3")
    pending = [
        PreferenceEvent(f"{index:064x}", "蛙泳换气", "all", "explicit", 1, now)
        for index in range(SYNC_BATCH_LIMIT + 1)
    ]
    store.save_preferences(
        owner["key"],
        pending,
        permissions={event.id: SearchPermission(True) for event in pending},
    )
    remote = PreferenceEvent("e" * 64, "夜景摄影", "all", "explicit", 1, now)
    batches = []

    def handle(request):
        batches.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "version": 1,
                "revision": "f" * 64,
                "events": [asdict(remote)],
            },
        )

    @contextmanager
    def attribution():
        yield owner

    monkeypatch.setattr(
        preferences, "ais_privacy_mode_enabled", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        "services.proactive_feedback.runtime.fetch_public_search_words",
        AsyncMock(return_value=frozenset()),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        runtime = ProactiveFeedbackRuntime(
            store,
            client=client,
            chat_interest_origin=origin,
            snapshot_provider=lambda: saved,
            fenced_snapshot_provider=lambda: saved,
            attribution_context=attribution,
        )
        await runtime._sync_chat_interests()
        assert "夜景摄影" not in store.local_preferences(owner).profile.features("news")
        await runtime._sync_chat_interests()
    assert [len(batch["events"]) for batch in batches] == [SYNC_BATCH_LIMIT, 1]
    assert "known_revision" not in batches[1]
    assert store.local_preferences(owner).profile.features("news") == {
        "蛙泳换气",
        "夜景摄影",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_server", [False, True])
async def test_account_candidates_use_verse_without_sending_interests(
    monkeypatch, legacy_server
):
    origin = "https://preferences-community.example.test"
    now = int(time())
    local = [PreferenceEvent("e" * 64, "竞技游戏", "all", "exclude", -1, now)]
    requests = []

    def handle(request):
        requests.append(request)
        if legacy_server and json.loads(request.content).get("surface") == "proactive":
            return httpx.Response(422)
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "blocked", "title": "竞技游戏赛况"},
                    {"id": "allowed", "title": "一份机关设计手稿"},
                ],
            },
        )

    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(trending_content, "social_base_url", lambda: origin)
    monkeypatch.setattr(
        trending_content.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(transport=transport, **kwargs),
    )
    context = RecommendationContext(
        preferences=LocalPreferences(profile=Profile.from_events(local, int(time()))),
        community=origin,
        bearer="synthetic-token",
    )
    result = await trending_content.fetch_neko_community_feed(
        limit=2, recommendations=context
    )
    assert len(requests) == (2 if legacy_server else 1)
    assert json.loads(requests[0].content)["surface"] == "proactive"
    for request in requests:
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        assert (
            not {"preference_events", "preference_version"}
            & json.loads(request.content).keys()
        )
    assert [item["id"] for item in result["posts"]] == ["allowed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["community", "all"])
async def test_guest_community_feed_succeeds_when_all_cards_are_excluded(monkeypatch, scope):
    origin = "https://guest-community.example.test"
    monkeypatch.setattr(trending_content, "social_base_url", lambda: origin)
    monkeypatch.setattr(trending_content, "_public_community_pools", OrderedDict())

    def handle(request):
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"items": [{"id": "blocked", "title": "竞技游戏赛况"}]})

    context = RecommendationContext(
        preferences=LocalPreferences(profile=Profile(exclusions={(scope, "竞技游戏")})),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(trending_content, "get_external_http_client", lambda: client)
        result = await trending_content.fetch_neko_community_feed(recommendations=context)

    assert result["success"] is True
    assert result["authenticated"] is False
    assert result["posts"] == []


@pytest.mark.asyncio
async def test_chat_provenance_survives_store_reopen_and_uses_a_worker(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace
    from main_logic.proactive_chat import preference_capture
    from services.proactive_feedback import host

    now = int(time())
    active = "guest"
    event_loop_thread = threading.get_ident()
    path = tmp_path / "recommendation_feedback.sqlite3"
    manager = SimpleNamespace(get_runtime_config_path=lambda name: path)

    @contextmanager
    def attribution():
        assert threading.get_ident() != event_loop_thread
        yield {"key": active}

    monkeypatch.setattr(host, "attribution_context", attribution)
    text = "memo\nUser | old\nUser | first\nUser | current\nUser | other"
    references = [
        {
            "id": source,
            "at": now,
            "offset": text.index("User | " + line),
            "digest": hashlib.sha256(line.encode()).hexdigest(),
        }
        for source, line in (
            ("old", "old"),
            ("first", "first"),
            ("current", "current"),
            ("other", "other"),
        )
    ]
    for label in ("old", "first", "current"):
        await preference_capture.remember_chat_source(
            "Neko", label, label, owner_key=active, config_manager=manager
        )
    active = "other-account"
    await preference_capture.remember_chat_source(
        "Neko", "other", "other", owner_key=active, config_manager=manager
    )
    owned = RecommendationStore(path).chat_source_digests("guest", "Neko")
    assert set(owned) == {"old", "first", "current"}
    annotated, refs = preference_capture.annotate_chat_sources(
        text,
        references,
        owned=owned,
        analyzed=frozenset({"first"}),
        start=text.index("User | first"),
    )
    assert annotated == text.replace("User | current", "[P1] User | current")
    assert refs == {1: ("current", now)}


def test_model_evidence_commits_once_per_message_and_preserves_privacy(tmp_path):
    from main_logic.proactive_chat.preference_capture import build_chat_evidence
    from utils.proactive_preferences.search_policy import SearchPermission

    store = RecommendationStore(tmp_path / "recommendations.sqlite3")
    now = int(time())
    refs = {1: ("original-turn", now), 2: ("no-evidence-turn", now)}
    item = {
        "source": 1,
        "scope": "all",
        "kind": "inferred",
        "direction": 1,
        "confidence": 0.95,
        "public": True,
        "game": False,
    }
    assert build_chat_evidence(None, refs) == {}
    assert build_chat_evidence([{**item, "source": 3}], refs) == {}
    evidence = build_chat_evidence(
        [
            {**item, "feature": "蛙泳"},
            {**item, "feature": "心理咨询"},
            {**item, "feature": "电竞", "confidence": 0.5},
        ],
        refs,
    )
    store.save_chat_preferences("guest", evidence)
    duplicate = build_chat_evidence([{**item, "feature": "蛙泳换气"}], refs)
    store.save_chat_preferences("guest", duplicate)
    local = store.local_preferences({"key": "guest"})
    assert local.analyzed_chat_sources == {"original-turn", "no-evidence-turn"}
    assert local.search_permissions == {
        ("all", "蛙泳"): SearchPermission(True),
        ("all", "心理咨询"): SearchPermission(False),
    }
    assert local.profile.score("video", {"蛙泳"}) == 0.5
    assert not store.local_preferences({"key": "other-account"}).analyzed_chat_sources
    assert "蛙泳换气" not in local.profile.features("video")


def test_recent_focus_fades_and_cannot_override_an_exclusion():
    now = int(time())
    hobby = [PreferenceEvent("hobby", "机械键盘", "all", "explicit", 1, now - 20 * DAY)]
    focus = [PreferenceEvent("focus", "游泳", "all", "inferred", 1, now)]
    old = {"title": "机械键盘测试"}
    new = {"title": "游泳入门"}
    assert Profile.from_events(hobby + focus, now).rank("video", [old, new]) == [
        new,
        old,
    ]
    faded = Profile.from_events(hobby + focus, now + 7 * DAY)
    assert not faded.recent_focus
    assert faded.rank("video", [new, old]) == [old, new]
    exclude = [PreferenceEvent("exclude", "游泳", "video", "exclude", -1, now)]
    assert Profile.from_events(hobby + focus + exclude, now).rank(
        "video", [new, old]
    ) == [old]
