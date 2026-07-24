import json
from pathlib import Path

import pytest

from utils.autostart_prompt_state import (
    AUTOSTART_LATER_COOLDOWN_MS,
    AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
    get_autostart_prompt_state_response,
    get_autostart_prompt_state_path,
    load_autostart_prompt_runtime_config,
    load_autostart_prompt_state,
    process_autostart_prompt_heartbeat,
    record_autostart_prompt_decision,
    save_autostart_prompt_state,
)


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture: these pure state tests do not need it."""
    yield


class DummyConfig:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.config_dir = self.root / "config"
        self.memory_dir = self.root / "memory"
        self.chara_dir = self.root / "character_cards"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.chara_dir.mkdir(parents=True, exist_ok=True)

    def get_config_path(self, filename):
        return self.config_dir / filename


@pytest.mark.unit
def test_legacy_tutorial_state_does_not_mark_autostart_as_enabled(tmp_path):
    config = DummyConfig(tmp_path)
    legacy_state_path = config.config_dir / "autostart_prompt.json"
    legacy_state_path.write_text(json.dumps({
        "schema_version": 1,
        "home_tutorial_completed": True,
        "completed_at": 1_234,
        "status": "completed",
    }), encoding="utf-8")

    state = load_autostart_prompt_state(config)

    assert state["autostart_enabled"] is False
    assert state["enabled_at"] == 0
    assert state["completed_at"] == 0
    assert state["status"] == "observing"
    assert not get_autostart_prompt_state_path(config).exists()


@pytest.mark.unit
def test_legacy_autostart_state_is_migrated_to_dedicated_state_file(tmp_path):
    config = DummyConfig(tmp_path)
    legacy_state_path = config.config_dir / "autostart_prompt.json"
    legacy_state_path.write_text(json.dumps({
        "autostart_enabled": True,
        "enabled_at": 4_000,
        "completed_at": 4_000,
        "status": "completed",
    }), encoding="utf-8")

    state = load_autostart_prompt_state(config)

    assert state["autostart_enabled"] is True
    assert state["enabled_at"] == 4_000
    assert state["completed_at"] == 4_000
    assert state["status"] == "completed"

    migrated_state_path = get_autostart_prompt_state_path(config)
    assert migrated_state_path.exists()
    assert legacy_state_path.exists()

    persisted = json.loads(migrated_state_path.read_text(encoding="utf-8"))
    assert persisted["prompt_kind"] == "autostart_prompt"
    assert persisted["autostart_enabled"] is True


@pytest.mark.unit
def test_autostart_prompt_uses_30_min_threshold_and_3_day_later_cooldown(tmp_path):
    config = DummyConfig(tmp_path)

    runtime_config = load_autostart_prompt_runtime_config(config)

    assert AUTOSTART_MIN_PROMPT_FOREGROUND_MS == 30 * 60 * 1000
    assert AUTOSTART_LATER_COOLDOWN_MS == 3 * 24 * 60 * 60 * 1000
    assert runtime_config["min_prompt_foreground_ms"] == AUTOSTART_MIN_PROMPT_FOREGROUND_MS
    assert runtime_config["later_cooldown_ms"] == AUTOSTART_LATER_COOLDOWN_MS
    assert "never_cooldown_ms" not in runtime_config

    blocked = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS - 1},
        config_manager=config,
        now_ms=2_000,
    )
    assert blocked["should_prompt"] is False
    assert blocked["prompt_reason"] == "foreground_insufficient"

    prompt = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": 1},
        config_manager=config,
        now_ms=3_000,
    )
    assert prompt["should_prompt"] is True
    assert prompt["prompt_reason"] == "usage_timeout"

    decision = record_autostart_prompt_decision(
        {"decision": "later", "prompt_token": prompt["prompt_token"]},
        config_manager=config,
        now_ms=4_000,
    )
    assert decision["state"]["deferred_until"] == 4_000 + AUTOSTART_LATER_COOLDOWN_MS


@pytest.mark.unit
def test_autostart_never_decision_is_accepted_when_submitted(tmp_path):
    config = DummyConfig(tmp_path)

    prompt = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    response = record_autostart_prompt_decision(
        {"decision": "never", "prompt_token": prompt["prompt_token"]},
        config_manager=config,
        now_ms=2_000,
    )

    assert response["state"]["status"] == "never"
    assert response["state"]["never_remind"] is True


@pytest.mark.unit
def test_autostart_legacy_never_state_still_suppresses_prompt(tmp_path):
    config = DummyConfig(tmp_path)
    save_autostart_prompt_state(
        {
            "prompt_kind": "autostart_prompt",
            "status": "never",
            "never_remind": True,
            "foreground_ms": AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
        },
        config_manager=config,
    )

    response = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": 0},
        config_manager=config,
        now_ms=2_000,
    )

    assert response["should_prompt"] is False
    assert response["prompt_reason"] == "never_remind"
    assert response["state"]["status"] == "never"
    assert response["state"]["never_remind"] is True


@pytest.mark.unit
def test_autostart_prompt_interactions_do_not_reset_or_block_threshold(tmp_path):
    config = DummyConfig(tmp_path)

    blocked = process_autostart_prompt_heartbeat(
        {
            "foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS - 1,
            "home_interactions_delta": 1,
            "chat_turns_delta": 1,
            "voice_sessions_delta": 1,
        },
        config_manager=config,
        now_ms=2_000,
    )

    assert blocked["should_prompt"] is False
    assert blocked["prompt_reason"] == "foreground_insufficient"

    state = load_autostart_prompt_state(config)
    assert state["foreground_ms"] == AUTOSTART_MIN_PROMPT_FOREGROUND_MS - 1
    assert state["home_interactions"] == 1
    assert state["chat_turns"] == 1
    assert state["voice_sessions"] == 1

    prompt = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": 1},
        config_manager=config,
        now_ms=3_000,
    )

    assert prompt["should_prompt"] is True
    assert prompt["prompt_reason"] == "usage_timeout"


@pytest.mark.unit
def test_autostart_enabled_heartbeat_marks_autostart_flow_completed(tmp_path):
    config = DummyConfig(tmp_path)

    response = process_autostart_prompt_heartbeat(
        {
            "foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
            "autostart_enabled": True,
            "autostart_provider": "backend",
        },
        config_manager=config,
        now_ms=2_000,
    )

    assert response["should_prompt"] is False
    assert response["prompt_reason"] == "autostart_enabled"

    state = load_autostart_prompt_state(config)
    assert state["autostart_enabled"] is True
    assert state["enabled_at"] == 2_000
    assert state["enabled_provider"] == "backend"
    assert state["status"] == "completed"


@pytest.mark.unit
def test_authoritative_desktop_disabled_clears_legacy_completed_state_and_resets_prompt_history(tmp_path):
    config = DummyConfig(tmp_path)
    state = load_autostart_prompt_state(config)
    state["foreground_ms"] = AUTOSTART_MIN_PROMPT_FOREGROUND_MS
    state["shown_count"] = 2
    state["last_shown_at"] = 1_500
    state["last_acknowledged_prompt_token"] = "old-ack"
    state["last_decision_prompt_token"] = "old-decision"
    state["accepted_at"] = 2_000
    state["started_at"] = 2_000
    state["started_via_prompt"] = True
    state["autostart_enabled"] = True
    state["enabled_at"] = 2_000
    state["completed_at"] = 2_000
    state["status"] = "completed"
    save_autostart_prompt_state(state, config)

    response = process_autostart_prompt_heartbeat(
        {
            "foreground_ms_delta": 0,
            "autostart_enabled": False,
            "autostart_status_authoritative": True,
            "autostart_provider": "neko-pc",
        },
        config_manager=config,
        now_ms=5_000,
    )

    assert response["should_prompt"] is True
    assert response["prompt_reason"] == "usage_timeout"
    assert response["prompt_token"]

    state = load_autostart_prompt_state(config)
    assert state["autostart_enabled"] is False
    assert state["enabled_at"] == 0
    assert state["enabled_provider"] == ""
    assert state["accepted_at"] == 0
    assert state["started_at"] == 0
    assert state["started_via_prompt"] is False
    assert state["completed_at"] == 0
    assert state["status"] == "observing"
    assert state["shown_count"] == 0
    assert state["last_shown_at"] == 0
    assert state["last_acknowledged_prompt_token"] == ""
    assert state["last_decision_prompt_token"] == ""
    assert state["active_prompt_token"] == response["prompt_token"]


@pytest.mark.unit
def test_authoritative_same_provider_disabled_preserves_prompt_history(tmp_path):
    config = DummyConfig(tmp_path)
    state = load_autostart_prompt_state(config)
    state["foreground_ms"] = AUTOSTART_MIN_PROMPT_FOREGROUND_MS
    state["shown_count"] = 2
    state["last_shown_at"] = 1_500
    state["accepted_at"] = 2_000
    state["started_at"] = 2_000
    state["started_via_prompt"] = True
    state["autostart_enabled"] = True
    state["enabled_at"] = 2_000
    state["enabled_provider"] = "neko-pc"
    state["completed_at"] = 2_000
    state["status"] = "completed"
    save_autostart_prompt_state(state, config)

    response = process_autostart_prompt_heartbeat(
        {
            "foreground_ms_delta": 0,
            "autostart_enabled": False,
            "autostart_status_authoritative": True,
            "autostart_provider": "neko-pc",
        },
        config_manager=config,
        now_ms=5_000,
    )

    assert response["should_prompt"] is False
    assert response["prompt_reason"] == "show_limit_reached"

    state = load_autostart_prompt_state(config)
    assert state["autostart_enabled"] is False
    assert state["enabled_provider"] == ""
    assert state["completed_at"] == 0
    assert state["shown_count"] == 2
    assert state["last_shown_at"] == 1_500


@pytest.mark.unit
def test_authoritative_unsupported_autostart_clears_stale_completed_state_without_prompt(tmp_path):
    config = DummyConfig(tmp_path)
    state = load_autostart_prompt_state(config)
    state["foreground_ms"] = AUTOSTART_MIN_PROMPT_FOREGROUND_MS
    state["autostart_enabled"] = True
    state["enabled_at"] = 2_000
    state["enabled_provider"] = "backend"
    state["completed_at"] = 2_000
    state["status"] = "completed"
    save_autostart_prompt_state(state, config)

    response = process_autostart_prompt_heartbeat(
        {
            "foreground_ms_delta": 0,
            "autostart_enabled": False,
            "autostart_supported": False,
            "autostart_status_authoritative": True,
            "autostart_provider": "backend",
        },
        config_manager=config,
        now_ms=5_000,
    )

    assert response["should_prompt"] is False
    assert response["prompt_reason"] == "autostart_unsupported"
    assert response["prompt_token"] is None

    state = load_autostart_prompt_state(config)
    assert state["autostart_enabled"] is False
    assert state["enabled_at"] == 0
    assert state["completed_at"] == 0
    assert state["status"] == "observing"


@pytest.mark.unit
def test_authoritative_unsupported_autostart_suppresses_prompt_when_threshold_met(tmp_path):
    config = DummyConfig(tmp_path)

    response = process_autostart_prompt_heartbeat(
        {
            "foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
            "autostart_enabled": False,
            "autostart_supported": False,
            "autostart_status_authoritative": True,
            "autostart_provider": "backend",
        },
        config_manager=config,
        now_ms=2_000,
    )

    assert response["should_prompt"] is False
    assert response["prompt_reason"] == "autostart_unsupported"
    assert response["prompt_token"] is None


@pytest.mark.unit
def test_autostart_accept_enabled_result_is_treated_as_success(tmp_path):
    config = DummyConfig(tmp_path)
    heartbeat = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    decision = record_autostart_prompt_decision(
        {
            "decision": "accept",
            "result": "enabled",
            "autostart_provider": "neko-pc",
            "prompt_token": heartbeat["prompt_token"],
        },
        config_manager=config,
        now_ms=5_000,
    )

    assert decision["state"]["status"] == "completed"
    assert decision["state"]["started_at"] == 5_000
    assert decision["state"]["started_via_prompt"] is True
    assert decision["state"]["autostart_enabled"] is True
    assert decision["state"]["enabled_at"] == 5_000
    assert decision["state"]["funnel_counts"]["completed"] == 1

    state = load_autostart_prompt_state(config)
    assert state["enabled_provider"] == "neko-pc"


@pytest.mark.unit
def test_authoritative_autostart_enabled_heartbeat_does_not_double_count_completion(tmp_path):
    config = DummyConfig(tmp_path)
    heartbeat = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    record_autostart_prompt_decision(
        {
            "decision": "accept",
            "result": "enabled",
            "autostart_provider": "neko-pc",
            "prompt_token": heartbeat["prompt_token"],
        },
        config_manager=config,
        now_ms=2_000,
    )

    first_state = load_autostart_prompt_state(config)
    assert first_state["funnel_counts"]["completed"] == 1

    process_autostart_prompt_heartbeat(
        {
            "foreground_ms_delta": 0,
            "autostart_enabled": True,
            "autostart_status_authoritative": True,
            "autostart_provider": "neko-pc",
        },
        config_manager=config,
        now_ms=3_000,
    )

    second_state = load_autostart_prompt_state(config)
    assert second_state["funnel_counts"]["completed"] == 1
    assert second_state["status"] == "completed"
    assert second_state["autostart_enabled"] is True


@pytest.mark.unit
def test_autostart_accept_without_enable_confirmation_enters_retryable_error(tmp_path):
    config = DummyConfig(tmp_path)
    runtime_config = load_autostart_prompt_runtime_config(config)
    heartbeat = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    decision = record_autostart_prompt_decision(
        {
            "decision": "accept",
            "result": "accepted",
            "prompt_token": heartbeat["prompt_token"],
        },
        config_manager=config,
        now_ms=2_000,
    )

    assert decision["state"]["status"] == "error"
    assert decision["state"]["autostart_enabled"] is False
    assert decision["state"]["started_at"] == 0
    assert decision["state"]["started_via_prompt"] is True
    assert decision["state"]["last_error"] == "autostart_enable_unconfirmed"
    assert decision["state"]["deferred_until"] == 2_000 + runtime_config["failure_cooldown_ms"]

    blocked = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": 0},
        config_manager=config,
        now_ms=3_000,
    )
    assert blocked["should_prompt"] is False
    assert blocked["prompt_reason"] == "cooldown_active"

    retried = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": 0},
        config_manager=config,
        now_ms=2_000 + runtime_config["failure_cooldown_ms"] + 1,
    )
    assert retried["should_prompt"] is True
    assert retried["prompt_reason"] == "usage_timeout"


@pytest.mark.unit
def test_autostart_later_decision_clears_stale_prompt_attribution(tmp_path):
    config = DummyConfig(tmp_path)
    runtime_config = load_autostart_prompt_runtime_config(config)
    first_prompt = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    record_autostart_prompt_decision(
        {
            "decision": "accept",
            "result": "accepted",
            "prompt_token": first_prompt["prompt_token"],
        },
        config_manager=config,
        now_ms=2_000,
    )

    retry_prompt = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": 0},
        config_manager=config,
        now_ms=2_000 + runtime_config["failure_cooldown_ms"] + 1,
    )

    decision_response = record_autostart_prompt_decision(
        {
            "decision": "later",
            "prompt_token": retry_prompt["prompt_token"],
        },
        config_manager=config,
        now_ms=3_000 + runtime_config["failure_cooldown_ms"],
    )

    assert decision_response["state"]["started_via_prompt"] is False

    completed = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": 0, "autostart_enabled": True},
        config_manager=config,
        now_ms=4_000 + runtime_config["later_cooldown_ms"],
    )

    assert completed["state"]["status"] == "completed"
    assert completed["state"]["autostart_enabled"] is True
    assert completed["state"]["funnel_counts"]["completed"] == 0


@pytest.mark.unit
def test_autostart_decision_is_idempotent_for_repeated_token(tmp_path):
    config = DummyConfig(tmp_path)
    runtime_config = load_autostart_prompt_runtime_config(config)
    heartbeat = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    first = record_autostart_prompt_decision(
        {"decision": "later", "prompt_token": heartbeat["prompt_token"]},
        config_manager=config,
        now_ms=2_000,
    )
    second = record_autostart_prompt_decision(
        {"decision": "later", "prompt_token": heartbeat["prompt_token"]},
        config_manager=config,
        now_ms=5_000,
    )

    assert first["state"]["deferred_until"] == 2_000 + runtime_config["later_cooldown_ms"]
    assert second["state"]["deferred_until"] == 2_000 + runtime_config["later_cooldown_ms"]
    assert second["state"]["funnel_counts"]["later"] == 1


@pytest.mark.unit
def test_autostart_can_never_remind_from_first_prompt(tmp_path):
    config = DummyConfig(tmp_path)
    heartbeat = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    assert heartbeat["should_prompt"] is True
    assert heartbeat["state"]["funnel_counts"]["later"] == 0
    assert heartbeat["state"]["can_never_remind"] is True


@pytest.mark.unit
def test_autostart_can_offer_never_after_three_later_decisions(tmp_path):
    config = DummyConfig(tmp_path)
    runtime_config = load_autostart_prompt_runtime_config(config)
    now_ms = 1_000

    latest_decision = None
    for _ in range(3):
        heartbeat = process_autostart_prompt_heartbeat(
            {"foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS},
            config_manager=config,
            now_ms=now_ms,
        )
        assert heartbeat["should_prompt"] is True

        latest_decision = record_autostart_prompt_decision(
            {"decision": "later", "prompt_token": heartbeat["prompt_token"]},
            config_manager=config,
            now_ms=now_ms + 100,
        )
        now_ms = latest_decision["state"]["deferred_until"] + 1

    assert latest_decision is not None
    assert latest_decision["state"]["funnel_counts"]["later"] == 3
    assert latest_decision["state"]["can_never_remind"] is True

    next_prompt = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": 0},
        config_manager=config,
        now_ms=now_ms + runtime_config["later_cooldown_ms"],
    )

    assert next_prompt["should_prompt"] is True
    assert next_prompt["prompt_reason"] == "usage_timeout"
    assert next_prompt["state"]["can_never_remind"] is True


@pytest.mark.unit
def test_autostart_never_decision_persists_never_remind_state(tmp_path):
    config = DummyConfig(tmp_path)
    heartbeat = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    decision = record_autostart_prompt_decision(
        {"decision": "never", "prompt_token": heartbeat["prompt_token"]},
        config_manager=config,
        now_ms=2_000,
    )

    assert decision["state"]["status"] == "never"
    assert decision["state"]["never_remind"] is True
    assert decision["state"]["funnel_counts"]["never"] == 1

    blocked = process_autostart_prompt_heartbeat(
        {"foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=30 * 24 * 60 * 60 * 1000,
    )

    assert blocked["should_prompt"] is False
    assert blocked["prompt_reason"] == "never_remind"
    assert blocked["prompt_token"] is None

    state = load_autostart_prompt_state(config)
    assert state["status"] == "never"
    assert state["never_remind"] is True


@pytest.mark.unit
def test_autostart_prompt_state_response_reports_autostart_mode(tmp_path):
    config = DummyConfig(tmp_path)

    response = get_autostart_prompt_state_response(config_manager=config)

    assert response["prompt_mode"] == "autostart"
    assert response["state"]["autostart_enabled"] is False
