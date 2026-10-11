"""Server-side theater backstop for proactive chat and ordinary voice start.

The frontend already suppresses proactive chat and blocks the ordinary
microphone while a theater performance runs. These tests pin the server-side
backstop: a TTL-bounded in-memory activity signal fed by successful theater
session requests, consulted by the proactive-chat router and by the WebSocket
voice start and PCM frames, failing open once the signal expires.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main_routers.websocket_router as websocket_router
from main_logic.proactive_chat import contracts
from main_routers import numeric_theater_router
from main_routers.system_router import proactive_chat_flow
from tests.unit.test_proactive_service_boundary import _wire_router_dependencies
from tests.unit.test_theater_numeric_v2_router import _client
from tests.unit.test_websocket_binary_audio import (
    _EventWebSocket,
    _install_protocol_endpoint,
    _ProtocolManager,
)
from utils import theater_activity

ROOT = Path(__file__).resolve().parents[2]


def _ok(status: str, name: str = "Lan") -> dict:
    return {"ok": True, "session": {"status": status}, "participants": {"catgirl_name": name}}


def test_activity_signal_expires_and_only_successful_payloads_count():
    """Mark on active payloads, clear on ended ones, ignore failures, and expire after the TTL."""
    ttl = theater_activity.THEATER_ACTIVITY_TTL_SECONDS
    assert theater_activity.is_theater_active("Lan") is False

    theater_activity.mark_theater_activity("Lan", now=1000.0)
    assert theater_activity.is_theater_active("Lan", now=1000.0 + ttl - 1) is True
    assert theater_activity.is_theater_active("Other", now=1000.0) is False
    # A stale signal never outlives its TTL, and expiry drops the entry.
    assert theater_activity.is_theater_active("Lan", now=1000.0 + ttl) is False
    assert "Lan" not in theater_activity._last_activity

    theater_activity.note_theater_session_response(_ok("active"))
    assert theater_activity.is_theater_active("Lan") is True
    for ignored in (
        {"ok": False, "reason": "numeric_session_not_found"},
        {**_ok("ended"), "ok": False},
        {"ok": True},
        _ok("ended", name=""),
        numeric_theater_router._error("numeric_session_not_found", 404),
    ):
        theater_activity.note_theater_session_response(ignored)
        assert theater_activity.is_theater_active("Lan") is True
    theater_activity.note_theater_session_response(_ok("ended"))
    assert theater_activity.is_theater_active("Lan") is False


def test_owner_release_preserves_peer_and_fences_late_responses():
    theater_activity.clear_all_theater_activity()
    assert theater_activity.note_theater_session_response(_ok('active'), activity_claim_id='owner-a')
    assert theater_activity.note_theater_session_response(_ok('active'), activity_claim_id='owner-b')
    theater_activity.clear_theater_activity('Lan', activity_claim_id='owner-a')
    assert theater_activity.is_theater_active('Lan')
    assert not theater_activity.note_theater_session_response(_ok('active'), activity_claim_id='owner-a')
    theater_activity.clear_theater_activity('Lan', activity_claim_id='owner-b')
    assert not theater_activity.is_theater_active('Lan')
    # Release before the delayed GET has registered anything at all.
    theater_activity.clear_theater_activity('', activity_claim_id='pending')
    assert not theater_activity.note_theater_session_response(_ok('active'), activity_claim_id='pending')
    assert not theater_activity.is_theater_active('Lan')


@pytest.mark.asyncio
async def test_release_overtakes_an_in_flight_activity_response():
    ready, finish = asyncio.Event(), asyncio.Event()

    async def handler(request):
        ready.set()
        await finish.wait()
        return _ok('active')

    tracked = numeric_theater_router._track_theater_activity(handler)
    task = asyncio.create_task(tracked(request=SimpleNamespace(headers={'X-Neko-Theater-Activity': 'pending'})))
    await ready.wait()
    theater_activity.clear_theater_activity('', activity_claim_id='pending')
    finish.set()
    result = await task
    assert result['activity_claimed'] is False
    assert not theater_activity.is_theater_active('Lan')


def test_route_activity_claims_are_isolated_and_release_is_a_fence(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    name = numeric_theater_router._current_catgirl_binding(numeric_theater_router.get_config_manager())['catgirl_name']
    with client:
        assert client.post('/api/theater-numeric/session/start', headers={'X-Neko-Theater-Activity': 'a'},
                           json={'story_id': 'numeric_v2_contract', 'session_id': 'leases'}).status_code == 200
        url = '/api/theater-numeric/session/leases?story_id=numeric_v2_contract'
        assert client.get(url, headers={'X-Neko-Theater-Activity': 'b'}).json()['activity_claimed']
        assert client.post('/api/theater-numeric/session/release', json={'activity_claim_id': 'a'}).status_code == 200
        assert theater_activity.is_theater_active(name)
        assert not client.get(url, headers={'X-Neko-Theater-Activity': 'a'}).json()['activity_claimed']
        assert client.post('/api/theater-numeric/session/release', json={'activity_claim_id': 'b'}).status_code == 200
        assert not theater_activity.is_theater_active(name)


@pytest.mark.parametrize('ending_headers', [{}, {'X-Neko-Theater-Activity': 'restored-window'}])
def test_end_releases_orphaned_and_legacy_owners_of_the_session(tmp_path, monkeypatch, ending_headers):
    """Selector end and post-reload capsule end both unblock ordinary chat."""
    client = _client(tmp_path, monkeypatch)
    name = numeric_theater_router._current_catgirl_binding(numeric_theater_router.get_config_manager())['catgirl_name']
    with client:
        # Standalone selector start does not own the stage or create a legacy guard.
        start = client.post('/api/theater-numeric/session/start?claim_activity=false',
                            json={'story_id': 'numeric_v2_contract', 'session_id': 'reload',
                                  'character_id': 'character_' + '1' * 32})
        assert start.status_code == 200, start.json()
        assert not theater_activity.is_theater_active(name)
        url = '/api/theater-numeric/session/reload?story_id=numeric_v2_contract'
        for owner in ('old-window', 'restored-window'):
            assert client.get(url, headers={'X-Neko-Theater-Activity': owner}).json()['activity_claimed']
        # Compatibility clients are scoped too, so end can clear their signal.
        assert client.get(url).status_code == 200
        assert theater_activity.is_theater_active(name)
        ended = client.post('/api/theater-numeric/session/end', headers=ending_headers, json={
            'story_id': 'numeric_v2_contract', 'session_id': 'reload',
            'base_revision': 0, 'base_lifecycle_revision': 0,
        })
        assert ended.status_code == 200
        assert not theater_activity.is_theater_active(name)
        assert not theater_activity._activity_claims.get(name)
        assert name not in theater_activity._last_activity
        # A resumed lifecycle can acquire a fresh owner despite the ending fence.
        resumed = client.post('/api/theater-numeric/session/resume?claim_activity=false', json={
            'story_id': 'numeric_v2_contract', 'session_id': 'reload',
            'base_revision': 0, 'base_lifecycle_revision': 1,
        })
        assert resumed.status_code == 200
        assert client.get(url, headers={'X-Neko-Theater-Activity': 'resumed-window'}).json()['activity_claimed']
        assert theater_activity.is_theater_active(name)


def _scoped_response(session_id, lifecycle=0, status='active', story_id='story'):
    response = _ok(status)
    response['session'].update(session_id=session_id, story_package_id=story_id, lifecycle_revision=lifecycle)
    return response


def test_session_end_preserves_other_sessions_and_newer_resumed_lifecycle():
    for owner, session_id in [('a', 'one'), ('orphan', 'one'), ('peer', 'two')]:
        assert theater_activity.note_theater_session_response(_scoped_response(session_id), activity_claim_id=owner)
    # A legacy compatibility request in another Session must also survive.
    assert theater_activity.note_theater_session_response(_scoped_response('two'))
    ended = _scoped_response('one', 1, 'ended')
    assert theater_activity.note_theater_session_response(ended)
    assert set(theater_activity._activity_claims['Lan']) == {'peer'}
    assert 'Lan' in theater_activity._last_activity
    assert not theater_activity.note_theater_session_response(_scoped_response('one'), activity_claim_id='late')
    assert theater_activity.note_theater_session_response(_scoped_response('one', 2), activity_claim_id='resumed')
    # A delayed ended snapshot for the old lifecycle cannot clear the resumed owner.
    assert theater_activity.note_theater_session_response(ended, activity_claim_id='resumed')
    assert set(theater_activity._activity_claims['Lan']) == {'peer', 'resumed'}
    theater_activity.clear_theater_activity('Lan', activity_claim_id='peer')
    theater_activity.clear_theater_activity('Lan')
    assert theater_activity.is_theater_active('Lan')


@pytest.mark.asyncio
async def test_session_end_fences_active_response_that_arrives_after_end():
    ready, finish = asyncio.Event(), asyncio.Event()

    async def handler(request):
        ready.set()
        await finish.wait()
        return _scoped_response('one')

    tracked = numeric_theater_router._track_theater_activity(handler)
    task = asyncio.create_task(tracked(request=SimpleNamespace(headers={'X-Neko-Theater-Activity': 'late'})))
    await ready.wait()
    theater_activity.note_theater_session_response(_scoped_response('one', 1, 'ended'))
    finish.set()
    result = await task
    assert result['activity_claimed'] is False
    assert not theater_activity.is_theater_active('Lan')


def test_theater_session_requests_drive_the_activity_signal(tmp_path, monkeypatch):
    """Launch, input and resume mark the character; end and release clear it; browsing does not mark."""
    client = _client(tmp_path, monkeypatch)
    name = numeric_theater_router._current_catgirl_binding(
        numeric_theater_router.get_config_manager()
    )["catgirl_name"]
    story = "numeric_v2_contract"

    def active() -> bool:
        return theater_activity.is_theater_active(name)

    with client:
        started = client.post("/api/theater-numeric/session/start",
                              json={"story_id": story, "session_id": "activity"})
        assert started.status_code == 200
        assert active() is True

        # The selector only browses progress; that must not claim the character.
        theater_activity.clear_theater_activity(name)
        assert client.get(f"/api/theater-numeric/session/active?story_id={story}").status_code == 200
        assert active() is False
        # A failed request neither marks nor clears.
        assert client.get(f"/api/theater-numeric/session/missing?story_id={story}").status_code == 404
        assert active() is False

        assert client.get(f"/api/theater-numeric/session/activity?story_id={story}&claim_activity=false").status_code == 200
        assert active() is False

        assert client.get(f"/api/theater-numeric/session/activity?story_id={story}").status_code == 200
        assert active() is True

        # A selector preview must not clear another window's existing claim.
        assert client.get(f"/api/theater-numeric/session/activity?story_id={story}&claim_activity=false").status_code == 200
        assert active() is True

        theater_activity.clear_theater_activity(name)
        submitted = client.post("/api/theater-numeric/session/input", json={
            "story_id": story, "session_id": "activity", "client_turn_id": "t1",
            "base_revision": 0, "message": "我先把信收好。",
        })
        assert submitted.status_code == 200
        assert active() is True

        ended = client.post("/api/theater-numeric/session/end", json={
            "story_id": story, "session_id": "activity", "base_revision": 1, "base_lifecycle_revision": 0,
        })
        assert ended.status_code == 200
        assert active() is False

        resumed = client.post("/api/theater-numeric/session/resume?claim_activity=false", json={
            "story_id": story, "session_id": "activity", "base_revision": 1, "base_lifecycle_revision": 1,
        })
        assert resumed.status_code == 200
        assert active() is False
        # Only the body runtime's read after handoff claims ordinary-chat blocking.
        assert client.get(f"/api/theater-numeric/session/activity?story_id={story}").status_code == 200
        assert active() is True

        # Another window performing with a different character keeps its guard:
        # release names only the character this window performed with.
        theater_activity.mark_theater_activity("另一只猫娘")
        unnamed = client.post("/api/theater-numeric/session/release", json={})
        assert unnamed.status_code == 400
        assert active() is True
        assert theater_activity.is_theater_active("另一只猫娘") is True

        released = client.post("/api/theater-numeric/session/release", json={"catgirl_name": name})
        assert released.status_code == 200
        assert released.json() == {"ok": True}
        assert active() is False
        assert theater_activity.is_theater_active("另一只猫娘") is True


@pytest.mark.asyncio
async def test_proactive_chat_passes_while_theater_is_active(monkeypatch):
    """The proactive router declines with a PASS body for the performing character and delegates otherwise."""
    handle = AsyncMock(return_value=contracts.ProactiveChatResult(body={"delegated": True}))
    _wire_router_dependencies(monkeypatch, handle)

    theater_activity.mark_theater_activity("Yui")
    request = SimpleNamespace(json=AsyncMock(return_value={}))
    response = await proactive_chat_flow.proactive_chat(request)
    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["reason_code"] == contracts.PROACTIVE_REASON_PASS_ROUTE_ACTIVE
    assert "theater" in body["message"]
    handle.assert_not_awaited()

    # Another character, and the same one once released, are untouched.
    request = SimpleNamespace(json=AsyncMock(return_value={"lanlan_name": "Other"}))
    assert json.loads((await proactive_chat_flow.proactive_chat(request)).body) == {"delegated": True}
    theater_activity.clear_all_theater_activity()
    request = SimpleNamespace(json=AsyncMock(return_value={}))
    assert json.loads((await proactive_chat_flow.proactive_chat(request)).body) == {"delegated": True}
    assert handle.await_count == 2


@pytest.mark.asyncio
async def test_ordinary_voice_start_is_declined_while_theater_is_active(monkeypatch):
    """Decline audio start before claiming the voice lease; text start and expired signals are unaffected."""
    theater_activity.mark_theater_activity("Lan")
    manager = _ProtocolManager()
    websocket = _EventWebSocket([
        {"action": "start_session", "input_type": "audio"},
        {"action": "start_session", "input_type": "text"},
    ])
    _install_protocol_endpoint(monkeypatch, manager=manager, websocket=websocket)

    await websocket_router.websocket_endpoint(websocket, "Lan")

    started_modes = [args for name, args in manager.calls if name == "start_session"]
    assert len(started_modes) == 1, "only the text start may proceed"
    assert not [name for name, _ in manager.calls if name in {"begin", "authorize"}]
    sent = [json.loads(payload) for payload in websocket.sent_text]
    assert {"type": "session_failed", "input_mode": "audio"} in sent
    statuses = [json.loads(item["message"]) for item in sent if item.get("type") == "status"]
    assert statuses == [{"code": "THEATER_SESSION_ACTIVE", "details": {"reason": "theater_session_active"}}]

    # Once the signal is gone the ordinary voice start proceeds unchanged.
    theater_activity.clear_all_theater_activity()
    manager = _ProtocolManager()
    websocket = _EventWebSocket([{"action": "start_session", "input_type": "audio"}])
    _install_protocol_endpoint(monkeypatch, manager=manager, websocket=websocket)
    await websocket_router.websocket_endpoint(websocket, "Lan")
    assert [name for name, _ in manager.calls if name == "start_session"] == ["start_session"]
    assert websocket.sent_text == []


class _LiveVoiceManager(_ProtocolManager):
    """An ordinary audio session that is live until the server ends it."""

    def __init__(self) -> None:
        super().__init__()
        self.is_active = True
        self.release_end = asyncio.Event()

    async def send_session_ended_by_server(self) -> None:
        self.calls.append(("session_ended_by_server", None))

    async def end_session(self, *_args, **kwargs) -> None:
        self.calls.append(("end_session", kwargs))
        await self.release_end.wait()
        self.is_active = False


_AUDIO_FRAME = {"action": "stream_data", "input_type": "audio", "data": [0, 1, -1, 0]}


@pytest.mark.asyncio
async def test_ordinary_audio_frames_are_dropped_and_live_voice_ended_while_theater_is_active(monkeypatch):
    """A mic opened before the theater (e.g. the Electron Pet window) cannot keep feeding ordinary turns."""
    theater_activity.mark_theater_activity("Lan")
    manager = _LiveVoiceManager()
    websocket = _EventWebSocket([dict(_AUDIO_FRAME) for _ in range(4)])
    _install_protocol_endpoint(monkeypatch, manager=manager, websocket=websocket)

    await websocket_router.websocket_endpoint(websocket, "Lan")
    for _ in range(5):
        await asyncio.sleep(0)

    names = [name for name, _ in manager.calls]
    assert "stream_data" not in names, "no ordinary PCM may reach the manager mid-theater"
    assert "begin" not in names, "a dropped frame must not claim the voice connection"
    # The burst ends the live session exactly once, the way other server-side ends do.
    assert names.count("session_ended_by_server") == 1
    ends = [kwargs for name, kwargs in manager.calls if name == "end_session"]
    assert ends == [{"by_server": True, "expected_session": manager.session}]
    manager.release_end.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert manager.is_active is False
    sent = [json.loads(payload) for payload in websocket.sent_text]
    statuses = [json.loads(item["message"]) for item in sent if item.get("type") == "status"]
    assert statuses == [
        {"code": "THEATER_SESSION_ACTIVE", "details": {"reason": "theater_session_active", "input_type": "audio"}}
    ]
    assert websocket_router._theater_voice_end_tasks == {}

    # Frames after the session is gone are dropped without another teardown.
    manager.calls.clear()
    websocket = _EventWebSocket([dict(_AUDIO_FRAME)])
    _install_protocol_endpoint(monkeypatch, manager=manager, websocket=websocket)
    await websocket_router.websocket_endpoint(websocket, "Lan")
    assert [name for name, _ in manager.calls if name in {"stream_data", "end_session", "begin"}] == []

    # The game route keeps its own voice path while a theater signal lingers.
    manager = _ProtocolManager()
    websocket = _EventWebSocket([dict(_AUDIO_FRAME)])
    _install_protocol_endpoint(monkeypatch, manager=manager, websocket=websocket, game_active=True)
    await websocket_router.websocket_endpoint(websocket, "Lan")
    assert [name for name, _ in manager.calls if name == "stream_data"] == ["stream_data"]


@pytest.mark.asyncio
async def test_external_route_keeps_its_input_while_theater_signal_lingers(monkeypatch):
    from utils import external_route_registry
    theater_activity.mark_theater_activity('Lan')
    manager = _ProtocolManager()
    websocket = _EventWebSocket([
        {'action': 'stream_data', 'input_type': 'text', 'data': 'hello'},
        dict(_AUDIO_FRAME),
    ])
    _install_protocol_endpoint(monkeypatch, manager=manager, websocket=websocket)
    route = AsyncMock(return_value=True)
    external_route_registry.register_external_route_kind(
        external_route_registry.ExternalRouteKind(
            kind='visit-test', is_active=lambda name: name == 'Lan',
            route_stream_message=route, on_start_session=AsyncMock(return_value=True),
            finalize_for_character=AsyncMock(return_value=0),
            current_instance=lambda name: 'visit-session',
        )
    )
    await websocket_router.websocket_endpoint(websocket, 'Lan')
    assert [call.args[1]['input_type'] for call in route.await_args_list] == ['text', 'audio']
    assert not [name for name, _ in manager.calls if name in {'stream_data', 'end_session'}]
    assert not any('THEATER_SESSION_ACTIVE' in message for message in websocket.sent_text)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["replaced", "ended", "text_mode"])
async def test_theater_voice_teardown_leaves_a_session_that_changed_before_it_ran(monkeypatch, change):
    """session_ended_by_server is not session-scoped: a session changed meanwhile gets neither it nor an end."""
    theater_activity.mark_theater_activity("Lan")
    manager = _LiveVoiceManager()
    websocket = _EventWebSocket([])
    seen_session = manager.session

    websocket_router._drop_ordinary_audio_for_theater(websocket, manager, "Lan")
    # The teardown is scheduled; before it runs another start installs a new
    # session (or the old one ends / turns into a text session).
    if change == "replaced":
        manager.session = object()
    elif change == "ended":
        manager.is_active = False
    else:
        manager.input_mode = "text"
    for _ in range(5):
        await asyncio.sleep(0)

    names = [name for name, _ in manager.calls]
    assert "session_ended_by_server" not in names, "the current session's client must not be told it ended"
    assert "end_session" not in names
    assert websocket_router._theater_voice_end_tasks == {}
    if change == "replaced":
        assert manager.session is not seen_session and manager.is_active is True


@pytest.mark.asyncio
@pytest.mark.parametrize("release", ["cleared", "expired"])
async def test_ordinary_audio_frames_flow_once_the_theater_signal_is_gone(monkeypatch, release):
    """Ended or TTL-expired theater activity leaves ordinary voice untouched."""
    if release == "expired":
        stale = time.monotonic() - theater_activity.THEATER_ACTIVITY_TTL_SECONDS - 1
        theater_activity.mark_theater_activity("Lan", now=stale)
    else:
        theater_activity.mark_theater_activity("Lan")
        theater_activity.clear_theater_activity("Lan")
    manager = _LiveVoiceManager()
    websocket = _EventWebSocket([dict(_AUDIO_FRAME), dict(_AUDIO_FRAME)])
    _install_protocol_endpoint(monkeypatch, manager=manager, websocket=websocket)

    await websocket_router.websocket_endpoint(websocket, "Lan")

    names = [name for name, _ in manager.calls]
    assert names.count("stream_data") == 2
    assert "begin" in names
    assert "end_session" not in names and "session_ended_by_server" not in names
    assert websocket.sent_text == []


class _AvatarProtocolManager(_ProtocolManager):
    """Record avatar interactions that reach the ordinary manager."""

    def note_avatar_interaction_ingress(self, message) -> bool:
        self.calls.append(("avatar_ingress", message.get("interaction_id")))
        return True

    async def handle_avatar_interaction(self, message) -> None:
        self.calls.append(("avatar_interaction", message.get("interaction_id")))


_ORDINARY_INPUTS = [
    {"action": "stream_data", "input_type": "text", "data": "你好"},
    {"action": "stream_data", "input_type": "avatar_drop_image", "data": "data:image/png;base64,AA=="},
    {"action": "stream_data", "input_type": "user_image", "data": "data:image/png;base64,AA=="},
    {"action": "avatar_interaction", "interaction_id": "tap-1", "tool_id": "fist", "action_id": "poke"},
]


@pytest.mark.asyncio
async def test_ordinary_text_and_avatar_turns_are_declined_while_theater_is_active(monkeypatch):
    """Another window cannot start an ordinary turn mid-performance; users without a theater are untouched."""
    theater_activity.mark_theater_activity("Lan")
    manager = _AvatarProtocolManager()
    websocket = _EventWebSocket(list(_ORDINARY_INPUTS))
    _install_protocol_endpoint(monkeypatch, manager=manager, websocket=websocket)

    await websocket_router.websocket_endpoint(websocket, "Lan")

    reached = [name for name, _ in manager.calls if name in {"stream_data", "avatar_ingress", "avatar_interaction"}]
    assert reached == [], "no ordinary text/image/avatar turn may reach the manager"
    sent = [json.loads(payload) for payload in websocket.sent_text]
    statuses = [json.loads(item["message"]) for item in sent if item.get("type") == "status"]
    assert statuses == [
        {"code": "THEATER_SESSION_ACTIVE", "details": {"reason": "theater_session_active", "input_type": kind}}
        for kind in ("text", "avatar_drop_image", "user_image", "avatar_interaction")
    ]

    # A character that is not performing, and the same one once released, proceed unchanged.
    theater_activity.clear_all_theater_activity()
    theater_activity.mark_theater_activity("另一只猫娘")
    manager = _AvatarProtocolManager()
    websocket = _EventWebSocket(list(_ORDINARY_INPUTS))
    _install_protocol_endpoint(monkeypatch, manager=manager, websocket=websocket)
    await websocket_router.websocket_endpoint(websocket, "Lan")
    for _ in range(5):
        await asyncio.sleep(0)
    assert [name for name, _ in manager.calls if name == "stream_data"] == ["stream_data"] * 3
    assert ("avatar_ingress", "tap-1") in manager.calls
    assert ("avatar_interaction", "tap-1") in manager.calls
    assert not [
        payload for payload in websocket.sent_text
        if "THEATER_SESSION_ACTIVE" in payload
    ]


def test_frontend_maps_the_decline_status_to_the_theater_voice_notice():
    """The client shows the theater notice matching the declined input instead of a raw error token."""
    source = (ROOT / "static" / "app" / "app-websocket.js").read_text(encoding="utf-8")
    branch = source.index("statusCode === 'THEATER_SESSION_ACTIVE'")
    block = source[branch:source.index("var isGoodbyeActive", branch)]
    assert "theater.voiceUnavailable" in block
    assert "theater.chatUnavailable" in block
    assert "statusDetails.input_type" in block
    assert branch < source.index("var translatedMessage = window.translateStatusMessage")
