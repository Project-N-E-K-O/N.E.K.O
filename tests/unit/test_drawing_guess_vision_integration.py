"""Existing canvas images enter the shared vision service without screen capture."""
import asyncio
import base64
import json
import sys
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from main_routers import game_router
from main_routers.game_router import drawing_guess as game


def canvas_image():
    with Image.new("RGB", (480, 360), "white") as image, BytesIO() as output:
        image.save(output, "JPEG")
        return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


@pytest.mark.parametrize("field", ["session_id", "round_id", "client_round_token", "phase",
                                    "lanlan_name", "_sdk_route_instance_id"])
def test_each_round_identity_change_retires_vision_scope(field):
    session = {"session_id": "s", "round_id": "r", "lanlan_name": "YUI"}
    current = game._drawing_vision_scope(session)
    assert current()
    session[field] = "changed"
    assert not current()


@pytest.fixture
def shared_service(monkeypatch):
    calls = []
    from utils import config_manager, llm_client

    async def config(_slot):
        return {}

    async def forbid_direct_model(**kwargs):
        raise AssertionError("integration tests must not call a remote model")

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: SimpleNamespace(aget_model_api_config=config))
    monkeypatch.setattr(llm_client, "create_chat_llm_async", forbid_direct_model)

    async def analyze(**kwargs):
        calls.append(kwargs)
        return json.dumps({"guess_id": "banana", "confidence": .9, "short_line": "A banana?", "issues": []})

    monkeypatch.setitem(sys.modules, "utils.game_vision", SimpleNamespace(analyze_game_vision=analyze))
    monkeypatch.setattr(game_router, "_get_character_info", lambda name: {
        "lanlan_name": name, "master_name": "player", "lanlan_prompt": "A warm companion.",
    })
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("review", [False, True])
async def test_existing_canvas_and_game_prompts_use_one_shared_request(shared_service, review):
    session = {"session_id": "vision-integration", "round_id": "round-1", "ai_word_id": "banana",
               "user_word_id": "apple", "ai_guess_attempts": 1, "game_chat_history": []}
    request = dict(session=session, locale="en", lanlan_name="YUI", image_data_url=canvas_image())
    result = await (game._review_ai_drawing(**request) if review else
                    game._generate_vision_guess(**request, user_hint="yellow"))
    assert len(shared_service) == 1, "game bypassed the shared image channel"
    call = shared_service[0]
    assert len(call["attachments"]) == 1
    assert call["attachments"][0]["type"] == "image"
    assert call["attachments"][0]["image_data_url"].startswith("data:image/jpeg;base64,")
    assert "region" not in call and "source" not in call
    text = call["text"]
    if not review:
        text = text.removeprefix(game.DRAWING_GUESS_CONTEXT_BEGIN).removesuffix(game.DRAWING_GUESS_CONTEXT_END).strip()
    payload = json.loads(text)
    assert len(payload["candidates"]) == game.VISION_GUESS_MAX_CANDIDATES
    assert "answer_id" not in payload and "answer_label" not in payload
    assert call["max_completion_tokens"] == (260 if review else 420)
    assert call["timeout"] == (24 if review else 300)
    assert call["is_current"]()
    session["round_id"] = "round-2"
    assert not call["is_current"]()
    assert result["accepted"] if review else result["word"].id == "banana"


@pytest.mark.asyncio
@pytest.mark.parametrize("review", [False, True])
async def test_cancel_from_shared_channel_is_not_swallowed(monkeypatch, shared_service, review):
    async def cancel(**kwargs):
        raise asyncio.CancelledError

    sys.modules["utils.game_vision"].analyze_game_vision = cancel
    request = dict(session={"session_id": "cancel", "round_id": "r"}, locale="en",
                   lanlan_name="YUI", image_data_url=canvas_image())
    with pytest.raises(asyncio.CancelledError):
        await (game._review_ai_drawing(**request) if review else
               game._generate_vision_guess(**request, user_hint=""))


@pytest.mark.asyncio
@pytest.mark.parametrize("review", [False, True])
async def test_stale_shared_channel_does_not_become_a_game_result(monkeypatch, shared_service, review):
    async def stale(**kwargs):
        raise ValueError("route_inactive")

    sys.modules["utils.game_vision"].analyze_game_vision = stale
    request = dict(session={"session_id": "stale", "round_id": "r"}, locale="en",
                   lanlan_name="YUI", image_data_url=canvas_image())
    with pytest.raises(asyncio.CancelledError):
        await (game._review_ai_drawing(**request) if review else
               game._generate_vision_guess(**request, user_hint=""))


@pytest.mark.asyncio
async def test_guess_snapshot_keeps_original_round_fence(monkeypatch, shared_service):
    session = {"session_id": "snapshot", "round_id": "r1", "phase": "ai_guessing",
               "user_word_id": "apple", "game_chat_history": []}

    async def retire(**kwargs):
        assert kwargs["is_current"]()
        session["round_id"] = "r2"
        assert not kwargs["is_current"](), "copied prompt state hid the retired original round"
        raise ValueError("route_inactive")

    async def forbid_fallback(**kwargs):
        pytest.fail("retired vision must not start a text-model fallback")

    sys.modules["utils.game_vision"].analyze_game_vision = retire
    monkeypatch.setattr(game, "_generate_text_context_guess", forbid_fallback)
    with pytest.raises(asyncio.CancelledError):
        await game._run_drawing_guess_vision_turn(session=session, locale="en", lanlan_name="YUI",
                                                 image_data_url=canvas_image(), user_hint="")
    assert "ai_score" not in session and "ai_guess_attempts" not in session


@pytest.mark.asyncio
@pytest.mark.parametrize("review", [False, True])
@pytest.mark.parametrize("action", ["cancel", "replace"])
async def test_real_service_cancellation_keeps_slot_until_provider_settles(monkeypatch, review, action):
    from utils import game_vision as service

    started, finish, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    session = {"lanlan_name": "YUI", "session_id": "owned", "round_id": "r1",
               "ai_word_id": "banana", "phase": "user_guessing" if review else "ai_guessing"}
    monkeypatch.setattr(game, "_drawing_guess_sessions", {game._session_key("YUI", "owned"): session})
    monkeypatch.setattr(game_router, "_get_character_info", lambda name: {"lanlan_name": name})

    async def config(slot):
        assert slot == "vision"
        return {"model": "test-vision"}

    class LLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            started.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await finish.wait()
            return SimpleNamespace(content='{"guess_id":"banana","confidence":1}')

    async def create(**kwargs):
        assert kwargs["max_retries"] == 0
        return LLM()

    monkeypatch.setattr(service, "get_config_manager", lambda: SimpleNamespace(aget_model_api_config=config))
    monkeypatch.setattr(service, "create_chat_llm_async", create)
    assert not service._active_analyses
    request = dict(session=session, locale="en", lanlan_name="YUI", image_data_url=canvas_image())
    task = asyncio.create_task(game._review_ai_drawing(**request) if review else
                               game._generate_vision_guess(**request, user_hint=""))
    try:
        await asyncio.wait_for(started.wait(), 2)
        if action == "cancel":
            task.cancel()
        else:
            game._drawing_guess_sessions[game._session_key("YUI", "owned")] = dict(session)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        await asyncio.wait_for(cancelled.wait(), 2)
        assert len(service._active_analyses) == 1
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*list(service._active_analyses), return_exceptions=True)
        await asyncio.sleep(0)
    assert not service._active_analyses
