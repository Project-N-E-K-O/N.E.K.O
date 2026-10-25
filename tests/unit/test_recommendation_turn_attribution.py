"""Keep committed recommendation ownership through real post-delivery disk I/O."""
import asyncio
import json
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from main_logic.proactive_chat import delivery, state
from main_logic.session_state import ProactivePhase, SessionEvent, TurnOwner
from main_routers import recommendation_router
from main_routers.system_router import _shared, proactive_chat_flow
from tests.unit.test_proactive_action_note import _make_mgr
from utils import file_utils


@pytest.fixture
def delivery_http(tmp_path, monkeypatch):
    """Replace generation and external transports, retaining commit/state/storage."""
    mgr = _make_mgr()
    mgr.use_tts = False
    mgr.current_speech_id = "committed-proactive-turn"
    mgr.last_user_engagement_time = None
    mgr._proactive_screenshot_request_id = "fixture-round"
    mgr.session = SimpleNamespace(_conversation_history=[])
    mgr._takeover_active = False
    mgr.audio_resampler = MagicMock()
    mgr._clear_tts_pipeline = AsyncMock()
    mgr._reset_ai_turn_buffer = MagicMock()
    mgr.send_user_activity = AsyncMock()
    mgr.send_lanlan_response.return_value = True
    totals_path = tmp_path / "proactive_chat_totals.json"
    database = tmp_path / "recommendation_feedback.sqlite3"
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_write = file_utils.atomic_write_json

    def blocked_write(path, data, **kwargs):
        if Path(path) != totals_path:
            return original_write(path, data, **kwargs)
        entered.set()
        try:
            if not release.wait(timeout=10):
                raise TimeoutError("test did not release accounting write")
            return original_write(path, data, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(file_utils, "atomic_write_json", blocked_write)
    monkeypatch.setattr(state, "_proactive_chat_totals", {})
    monkeypatch.setattr(state, "_invite_ever_delivered", {})
    monkeypatch.setattr(state, "_proactive_chat_totals_loaded", False)
    monkeypatch.setattr(state, "_proactive_chat_totals_loaded_path", None)
    monkeypatch.setattr(state, "_proactive_chat_totals_lock", asyncio.Lock())
    for name in ("_record_proactive_chat", "_record_proactive_material", "_mini_game_invite_count_post_response_chat"):
        monkeypatch.setattr(delivery, name, MagicMock())
    monkeypatch.setattr(delivery, "_get_internal_http_client", MagicMock())

    snapshot = {"local_user_id": "fixture-user", "access_token": "fixture-token", "base_url": "https://community.example"}
    credential_lock = threading.RLock()

    @contextmanager
    def credential_fence():
        with credential_lock:
            yield

    monkeypatch.setattr(recommendation_router.host, "saved_session_snapshot", lambda: dict(snapshot))
    monkeypatch.setattr(recommendation_router.host, "saved_session_writer_fence", credential_fence)
    monkeypatch.setattr(recommendation_router, "get_config_manager", lambda: SimpleNamespace(get_runtime_config_path=lambda _: database))
    monkeypatch.setattr(recommendation_router, "load_global_conversation_settings", lambda: {"proactiveMusicEnabled": True})
    monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", "fixture-csrf")
    monkeypatch.setattr(proactive_chat_flow, "get_config_manager", lambda: SimpleNamespace(
        aget_character_data=AsyncMock(return_value=("Master", "Test", None, None, None, {}, None, None, None))))
    monkeypatch.setattr(proactive_chat_flow, "get_session_manager", lambda: {"Test": mgr})
    monkeypatch.setattr(proactive_chat_flow, "is_theater_active", lambda _: False)
    link = {"title": "Fixture song", "artist": "Fixture artist", "source": "音乐推荐", "url": "https://media.example/song"}

    async def generated_delivery(command, **kwargs):
        lifecycle = delivery.ProactiveLifecycle(mgr, SessionEvent.PROACTIVE_DONE, "Test", screenshot_request_id="fixture-round")
        try:
            committed = await delivery._commit_proactive_delivery(
                mgr=mgr, proactive_sid="committed-proactive-turn", lanlan_name="Test",
                response_text="A fixture recommendation", source_tag="MUSIC", active_channels=["music"],
                selected_web_link=None, selected_music_link=link, selected_meme_link=None,
                music_content=None, is_music_used=True, is_playing_music=False, music_cooldown=False,
                vision_content=None, phase2_use_vision=False, screenshot_b64=None,
                proactive_lang="en", master_name="Master", round_id="fixture-round")
            if committed.result is not None:
                return await lifecycle.finalize(committed.result)
            result = await delivery._record_committed_delivery(
                mgr=mgr, delivery=committed.delivery, lanlan_name="Test",
                response_text="A fixture recommendation", source_tag="MUSIC", active_channels=["music"],
                has_unfinished_thread=False, surfaced_reflection_ids=[], selected_web_link=None,
                selected_web_topic_key=None, web_parsed=None, selected_music_link=link,
                selected_music_topic_key=None, selected_meme_link=None, selected_meme_topic_key=None,
                meme_content=None, memory_dir=tmp_path)
            return await lifecycle.finalize(result)
        finally:
            await lifecycle.safe_done()

    monkeypatch.setattr(proactive_chat_flow.proactive_service, "handle_proactive_chat", generated_delivery)
    app = FastAPI()
    app.add_api_route("/api/proactive_chat", proactive_chat_flow.proactive_chat, methods=["POST"])
    app.include_router(recommendation_router.router, prefix="/api/proactive")
    fixture = SimpleNamespace(app=app, mgr=mgr, entered=entered, release=release, finished=finished,
                              database=database, totals_path=totals_path)
    yield fixture
    release.set()
    if entered.is_set():
        assert finished.wait(timeout=5), "accounting worker was not drained"


async def start_round(mgr):
    assert await mgr.state.try_start_proactive(round_id="fixture-round")
    await mgr.state.fire(SessionEvent.PROACTIVE_CLAIM, sid="committed-proactive-turn", round_id="fixture-round")
    await mgr.state.fire(SessionEvent.PROACTIVE_PHASE2, round_id="fixture-round")


def http_client(fixture):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture.app), base_url="http://127.0.0.1:48911",
                             headers={"Origin": "http://127.0.0.1:48911", "X-CSRF-Token": "fixture-csrf"})


@pytest.mark.asyncio
@pytest.mark.parametrize("user_input", [False, True])
async def test_committed_turn_survives_post_delivery_write_and_user_input(delivery_http, user_input):
    f = delivery_http
    await start_round(f.mgr)
    async with http_client(f) as client:
        request = asyncio.create_task(client.post("/api/proactive_chat", json={}))
        try:
            assert await asyncio.to_thread(f.entered.wait, 5)
            assert f.mgr.state.phase is ProactivePhase.COMMITTING
            assert f.mgr.session._conversation_history[0].additional_kwargs["anti_repeat_response_id"] == "committed-proactive-turn"
            if user_input:
                # Real TurnMixin rotates the SID and preempts the real COMMITTING state.
                await f.mgr.handle_new_message()
                assert f.mgr.current_speech_id != "committed-proactive-turn"
                assert f.mgr.state.owner is TurnOwner.USER
                assert f.mgr.state.is_proactive_preempted()
            f.release.set()
            response = await asyncio.wait_for(request, 5)
            assert response.status_code == 200
            body = response.json()
            assert body["turn_id"] == "committed-proactive-turn"
            recommendation_id = body["music_recommendation"]["id"]
            with closing(sqlite3.connect(f.database)) as db:
                assert db.execute("SELECT turn_id FROM recommendations WHERE id=?", (recommendation_id,)).fetchone()[0] == "committed-proactive-turn"
            event = await client.post(f"/api/proactive/recommendations/{recommendation_id}/events", json={"event_id": "fixture-shown", "kind": "shown"})
            assert event.status_code == 200
            exported = await client.get("/api/proactive/recommendations/events")
            assert exported.status_code == 200
            assert exported.json()["events"][0]["turn_id"] == "committed-proactive-turn"
            assert json.loads(f.totals_path.read_text(encoding="utf-8"))["totals"] == {"Test": 1}
            assert f.mgr.state.phase is ProactivePhase.IDLE
            if user_input:
                assert f.mgr.state.owner is TurnOwner.USER
        finally:
            f.release.set()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_post_delivery_write_cannot_register_a_later_recommendation(delivery_http):
    f = delivery_http
    await start_round(f.mgr)
    async with http_client(f) as client:
        request = asyncio.create_task(client.post("/api/proactive_chat", json={}))
        try:
            assert await asyncio.to_thread(f.entered.wait, 5)
            assert f.mgr.state.phase is ProactivePhase.COMMITTING
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            await f.mgr.handle_new_message()
            f.release.set()
            assert await asyncio.to_thread(f.finished.wait, 5)
            assert json.loads(f.totals_path.read_text(encoding="utf-8"))["totals"] == {"Test": 1}
            assert not f.database.exists()
            assert f.mgr.state.phase is ProactivePhase.IDLE
            assert f.mgr.state.owner is TurnOwner.USER
        finally:
            f.release.set()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
async def test_user_takeover_before_commit_has_no_recommendation_or_accounting(delivery_http):
    f = delivery_http
    await start_round(f.mgr)
    await f.mgr.handle_new_message()
    async with http_client(f) as client:
        response = await client.post("/api/proactive_chat", json={})
    assert response.status_code == 200
    assert response.json()["action"] == "pass"
    assert "music_recommendation" not in response.json()
    assert not f.entered.is_set()
    assert not f.database.exists()
    assert not f.totals_path.exists()
    f.mgr.send_lanlan_response.assert_not_awaited()
    assert f.mgr.state.phase is ProactivePhase.IDLE
    assert f.mgr.state.owner is TurnOwner.USER
