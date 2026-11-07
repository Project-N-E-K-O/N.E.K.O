"""Recommendation policy admission through the real proactive service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.proactive_chat import generation, mini_game_invite, service, sources
from main_logic.proactive_chat.contracts import ProactiveChatCommand
from main_logic.session_state import SessionStateMachine
from utils import internal_http_client, preferences


class Policy:
    def __init__(self, blocked=(), *, reason="PASS_USER_COOLDOWN"):
        self.blocked = set(blocked)
        self.reason = reason
        self.current = True
        self.categories = set()
        self.filter_calls = 0

    def filter_modes(self, modes):
        self.filter_calls += 1
        return [mode for mode in modes if mode not in self.blocked], self.reason

    def guard(self, categories):
        self.categories = set(categories)
        return lambda: self.current


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    manager = SimpleNamespace(
        is_active=False, state=SessionStateMachine("PolicyTest"),
        _proactive_screenshot_request_id=None, user_language="en",
        _activity_tracker=SimpleNamespace(kickoff_open_threads_compute=MagicMock()),
    )
    cfg = SimpleNamespace(
        memory_dir=tmp_path,
        aget_core_config=AsyncMock(return_value={}),
        aget_model_api_config=AsyncMock(return_value={
            "model": "synthetic-model", "api_key": "synthetic-no-network",
            "base_url": "http://127.0.0.1:9/v1",
        }),
    )
    response = SimpleNamespace(
        status_code=200, text="", raise_for_status=lambda: None,
        json=lambda: {"topics": []},
    )
    monkeypatch.setattr(preferences, "ais_privacy_mode_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(internal_http_client, "get_internal_http_client", lambda: SimpleNamespace(
        get=AsyncMock(return_value=response),
    ))
    fetched = []

    async def fetch(mode, **_kwargs):
        fetched.append(mode)
        return mode, {"formatted_content": "Synthetic news", "links": [{
            "title": "Synthetic news", "url": "https://example.invalid/news",
            "source": "Synthetic source",
        }]}

    monkeypatch.setattr(sources, "_fetch_source", fetch)
    model = AsyncMock(return_value="[WEB] PASS\n[MUSIC] PASS\n[MEME] PASS")
    monkeypatch.setattr(generation, "_llm_call_with_retry", model)

    async def run(policy, modes, *, invite=None):
        return await service.handle_proactive_chat(
            ProactiveChatCommand(
                lanlan_name="PolicyTest", enabled_modes=modes, enabled_modes_provided=True,
                mini_game_invite_enabled=False, language="en",
            ),
            config_manager=cfg, session_manager={"PolicyTest": manager},
            character_data=("User", "PolicyTest", None, None, None, {}, None, None, None),
            game_route_active_for=lambda _name: False,
            break_config_manager_provider=lambda: cfg,
            run_mini_game_invite_short_circuit=invite or mini_game_invite._run_mini_game_invite_short_circuit,
            push_mini_game_invite_options=AsyncMock(), push_mini_game_invite_resolved=AsyncMock(),
            recommendation_policy=policy,
        )

    return SimpleNamespace(run=run, manager=manager, fetched=fetched, model=model)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["PASS_USER_COOLDOWN", "PASS_USER_PREFERENCE"])
async def test_all_suppressed_sources_skip_fetch_and_model_and_release_round(runtime, reason):
    result = await runtime.run(Policy(["news"], reason=reason), ["news"])
    assert result.body["reason_code"] == reason
    assert result.body["attempt_consumed"] is False
    assert runtime.fetched == []
    runtime.model.assert_not_awaited()
    assert runtime.manager.state.phase.value == "idle"
    assert runtime.manager._proactive_screenshot_request_id is None


@pytest.mark.asyncio
async def test_partial_filter_keeps_other_sources_and_actual_model_decisions(runtime):
    policy = Policy(["news"])
    result = await runtime.run(policy, ["news", "home"])
    assert runtime.fetched == ["home"]
    assert policy.categories == {"web"}
    runtime.model.assert_awaited_once()
    assert result.body["reason_code"] == "PASS_MODEL_PASS"
    assert "attempt_consumed" not in result.body


@pytest.mark.asyncio
async def test_refusal_during_phase1_stops_before_phase2_and_keeps_done(runtime):
    policy = Policy()

    async def refuse(**_kwargs):
        policy.current = False
        return "[WEB] PASS"

    runtime.model.side_effect = refuse
    result = await runtime.run(policy, ["news"])
    assert policy.categories == {"news"}
    assert runtime.fetched == ["news"]
    runtime.model.assert_awaited_once()
    assert result.body["reason_code"] == "PASS_USER_COOLDOWN"
    assert result.body["attempt_consumed"] is False
    assert runtime.manager.state.phase.value == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [1, 2])
async def test_committed_refusal_blocks_actual_service_after_store_reopen(runtime, tmp_path, protocol):
    from main_logic.recommendation_feedback import RecommendationStore
    from services.proactive_feedback.policy import PolicyRegistry

    path = tmp_path / "recommendations.sqlite3"
    owner = {"key": "anonymous-test", "user_id": None, "community": None}
    uid = "a" * 32
    store = RecommendationStore(path, protocol_version=protocol)
    record = store.create(owner, "PolicyTest", "old-turn", {
        "name": "Synthetic news", "artist": "Synthetic source",
        "url": "https://example.invalid/news", "cover": "",
    }, {}, category="news", character_uid=uid)
    saved = store.record(owner, record["id"], {"event_id": "explicit-no", "kind": "skipped"})
    assert saved["state"] == "skipped" and saved["applied"]
    # Consume the actual durable projection through the real request view,
    # rather than mocking the source-selection decision under test.
    registry = PolicyRegistry()
    view = registry.publish(path, owner, uid, RecommendationStore(path).get_policy(owner, uid))
    result = await runtime.run(view, ["news"])
    assert result.body["reason_code"] == "PASS_USER_COOLDOWN"
    assert result.body["attempt_consumed"] is False
    assert runtime.fetched == []
    runtime.model.assert_not_awaited()
    assert runtime.manager.state.phase.value == "idle"
    # The same refusal must not suppress the independent home/web source.
    result = await runtime.run(view, ["news", "home"])
    assert runtime.fetched == ["home"]
    runtime.model.assert_awaited_once()
    assert result.body["reason_code"] == "PASS_MODEL_PASS"


@pytest.mark.asyncio
async def test_empty_explicit_sources_never_fall_back_to_home(runtime):
    policy = Policy()
    result = await runtime.run(policy, [])
    assert policy.filter_calls == 0
    assert result.body["reason_code"] == "PASS_SOURCE_EMPTY"
    assert runtime.fetched == []


@pytest.mark.asyncio
async def test_invite_short_circuit_does_not_apply_recommendation_filter(runtime):
    from main_logic.proactive_chat.contracts import ProactiveChatResult
    invite = AsyncMock(return_value=mini_game_invite.MiniGameInviteShortCircuit(
        result=ProactiveChatResult(body={"success": True, "action": "chat", "channel": "mini_game"}),
    ))
    policy = Policy(["news"])
    result = await runtime.run(policy, ["news"], invite=invite)
    assert result.body["channel"] == "mini_game"
    assert policy.filter_calls == 0
    assert runtime.fetched == []


@pytest.mark.asyncio
async def test_voice_fast_path_does_not_apply_category_policy(runtime, monkeypatch):
    class VoiceSession:
        pass

    monkeypatch.setattr(service, "OmniRealtimeClient", VoiceSession)
    runtime.manager.is_active = True
    runtime.manager.session = VoiceSession()
    runtime.manager.trigger_voice_proactive_nudge = AsyncMock(return_value=False)
    # Keep the normal command path but request the voice route explicitly.
    policy = Policy(["news"])
    result = await service.handle_proactive_chat(
        ProactiveChatCommand(lanlan_name="PolicyTest", voice_mode=True, language="en"),
        config_manager=SimpleNamespace(), session_manager={"PolicyTest": runtime.manager},
        character_data=("User", "PolicyTest", None, None, None, {}, None, None, None),
        game_route_active_for=lambda _name: False,
        break_config_manager_provider=lambda: None,
        run_mini_game_invite_short_circuit=AsyncMock(),
        push_mini_game_invite_options=AsyncMock(), push_mini_game_invite_resolved=AsyncMock(),
        recommendation_policy=policy,
    )
    runtime.manager.trigger_voice_proactive_nudge.assert_awaited_once()
    assert policy.filter_calls == 0
    assert result.body["reason_code"] == "PASS_BUSY"
