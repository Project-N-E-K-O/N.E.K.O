import json
from pathlib import Path

import pytest

from utils.tutorial_prompt_state import (
    LATER_COOLDOWN_MS,
    MIN_PROMPT_FOREGROUND_MS,
    get_tutorial_prompt_state_response,
    load_tutorial_prompt_state,
    load_tutorial_prompt_runtime_config,
    process_tutorial_prompt_heartbeat,
    record_tutorial_prompt_shown,
    record_tutorial_prompt_decision,
    record_tutorial_started,
    record_tutorial_completed,
    save_tutorial_prompt_state,
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
def test_prompt_triggers_after_idle_home_usage(tmp_path):
    config = DummyConfig(tmp_path)
    response = process_tutorial_prompt_heartbeat(
        {
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
            "chat_turns_delta": 0,
            "voice_sessions_delta": 0,
            "home_tutorial_completed": False,
        },
        config_manager=config,
        now_ms=1_000,
    )

    assert response["should_prompt"] is True
    assert response["prompt_reason"] == "idle_timeout"
    assert response["prompt_mode"] == "tutorial"
    assert response["prompt_token"]
    assert response["state"]["user_cohort"] == "new"

    state = load_tutorial_prompt_state(config)
    assert state["status"] == "observing"
    assert state["shown_count"] == 0
    assert state["active_prompt_token"] == response["prompt_token"]
    assert state["last_shown_at"] == 0


@pytest.mark.unit
def test_shown_ack_increments_count_once(tmp_path):
    config = DummyConfig(tmp_path)
    response = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    shown = record_tutorial_prompt_shown(
        {"prompt_token": response["prompt_token"]},
        config_manager=config,
        now_ms=1_200,
    )

    assert shown["already_acknowledged"] is False
    state = load_tutorial_prompt_state(config)
    assert state["status"] == "prompted"
    assert state["shown_count"] == 1
    assert state["last_shown_at"] == 1_200
    assert state["active_prompt_token"] == ""

    shown_again = record_tutorial_prompt_shown(
        {"prompt_token": response["prompt_token"]},
        config_manager=config,
        now_ms=1_300,
    )

    assert shown_again["already_acknowledged"] is True
    state = load_tutorial_prompt_state(config)
    assert state["shown_count"] == 1


@pytest.mark.unit
def test_meaningful_action_blocks_prompt(tmp_path):
    config = DummyConfig(tmp_path)
    response = process_tutorial_prompt_heartbeat(
        {
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
            "chat_turns_delta": 1,
        },
        config_manager=config,
        now_ms=1_500,
    )

    assert response["should_prompt"] is False
    assert response["prompt_reason"] == "meaningful_action_taken"

    state = load_tutorial_prompt_state(config)
    assert state["chat_turns"] == 1
    assert state["shown_count"] == 0


@pytest.mark.unit
def test_home_interaction_resets_idle_timer_without_permanent_block(tmp_path):
    config = DummyConfig(tmp_path)
    response = process_tutorial_prompt_heartbeat(
        {
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
            "home_interactions_delta": 1,
        },
        config_manager=config,
        now_ms=1_600,
    )

    assert response["should_prompt"] is False
    assert response["prompt_reason"] == "foreground_insufficient"

    state = load_tutorial_prompt_state(config)
    assert state["home_interactions"] == 1
    assert state["foreground_ms"] == 0
    assert state["shown_count"] == 0

    follow_up = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=2_000,
    )

    assert follow_up["should_prompt"] is True
    assert follow_up["prompt_reason"] == "idle_timeout"


@pytest.mark.unit
def test_manual_home_tutorial_view_blocks_prompt(tmp_path):
    config = DummyConfig(tmp_path)
    response = process_tutorial_prompt_heartbeat(
        {
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
            "manual_home_tutorial_viewed": True,
        },
        config_manager=config,
        now_ms=2_000,
    )

    assert response["should_prompt"] is False
    assert response["prompt_reason"] == "tutorial_started"
    assert response["state"]["manual_home_tutorial_viewed"] is True
    assert response["state"]["manual_home_tutorial_viewed_at"] == 2_000


@pytest.mark.unit
def test_manual_home_tutorial_view_heartbeat_clears_stale_prompt_start_flag(tmp_path):
    config = DummyConfig(tmp_path)
    stale_state = load_tutorial_prompt_state(config)
    stale_state["started_via_prompt"] = True
    stale_state["accepted_at"] = 1_000
    stale_state["started_at"] = 1_000
    stale_state["status"] = "started"
    save_tutorial_prompt_state(stale_state, config)

    response = process_tutorial_prompt_heartbeat(
        {
            "manual_home_tutorial_viewed": True,
        },
        config_manager=config,
        now_ms=2_000,
    )

    assert response["state"]["started_via_prompt"] is False
    assert response["state"]["manual_home_tutorial_viewed"] is True


@pytest.mark.unit
def test_later_decision_sets_cooldown(tmp_path):
    config = DummyConfig(tmp_path)
    heartbeat = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    decision = record_tutorial_prompt_decision(
        {"decision": "later", "prompt_token": heartbeat["prompt_token"]},
        config_manager=config,
        now_ms=2_000,
    )

    assert decision["state"]["status"] == "deferred"
    assert decision["state"]["deferred_until"] == 2_000 + LATER_COOLDOWN_MS
    assert decision["state"]["shown_count"] == 1

    blocked = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": 0},
        config_manager=config,
        now_ms=2_000 + 60_000,
    )

    assert blocked["should_prompt"] is False
    assert blocked["prompt_reason"] == "cooldown_active"


@pytest.mark.unit
def test_accept_started_marks_state_started(tmp_path):
    config = DummyConfig(tmp_path)
    heartbeat = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    decision = record_tutorial_prompt_decision(
        {
            "decision": "accept",
            "result": "started",
            "prompt_token": heartbeat["prompt_token"],
        },
        config_manager=config,
        now_ms=5_000,
    )

    assert decision["state"]["status"] == "started"
    assert decision["state"]["started_at"] == 5_000
    assert decision["state"]["shown_count"] == 1

    follow_up = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=6_000,
    )

    assert follow_up["should_prompt"] is False
    assert follow_up["prompt_reason"] == "tutorial_started"


@pytest.mark.unit
def test_accept_accepted_marks_started_as_fallback(tmp_path):
    config = DummyConfig(tmp_path)
    heartbeat = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    decision = record_tutorial_prompt_decision(
        {
            "decision": "accept",
            "result": "accepted",
            "prompt_token": heartbeat["prompt_token"],
        },
        config_manager=config,
        now_ms=1_500,
    )

    assert decision["state"]["accepted_at"] == 1_500
    assert decision["state"]["started_at"] == 1_500

    state = load_tutorial_prompt_state(config)
    assert state["accepted_at"] == 1_500
    assert state["started_at"] == 1_500
    assert state["started_via_prompt"] is True
    assert state["funnel_counts"]["accept"] == 1
    assert state["funnel_counts"]["started"] == 1


@pytest.mark.unit
def test_accept_decision_requires_prompt_token(tmp_path):
    config = DummyConfig(tmp_path)
    process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    with pytest.raises(ValueError, match="invalid prompt_token"):
        record_tutorial_prompt_decision(
            {"decision": "accept", "result": "accepted"},
            config_manager=config,
            now_ms=2_000,
        )


@pytest.mark.unit
def test_manual_started_event_persists_started_state_immediately(tmp_path):
    config = DummyConfig(tmp_path)

    started = record_tutorial_started(
        {"page": "home", "source": "manual"},
        config_manager=config,
        now_ms=2_500,
    )

    assert started["ignored"] is False
    assert started["state"]["status"] == "started"
    assert started["state"]["started_at"] == 2_500
    assert started["tutorial_run_token"]

    state = load_tutorial_prompt_state(config)
    assert state["manual_home_tutorial_viewed"] is True
    assert state["manual_home_tutorial_viewed_at"] == 2_500
    assert state["started_at"] == 2_500
    assert state["started_via_prompt"] is False
    assert state["active_tutorial_run_token"] == started["tutorial_run_token"]
    assert state["active_tutorial_run_source"] == "manual"


@pytest.mark.unit
def test_prompt_started_event_backfills_accept_and_started_once(tmp_path):
    config = DummyConfig(tmp_path)
    heartbeat = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    started = record_tutorial_started(
        {
            "page": "home",
            "source": "idle_prompt",
            "prompt_token": heartbeat["prompt_token"],
        },
        config_manager=config,
        now_ms=2_000,
    )

    assert started["ignored"] is False
    assert started["state"]["status"] == "started"
    assert started["tutorial_run_token"]

    state = load_tutorial_prompt_state(config)
    assert state["accepted_at"] == 2_000
    assert state["started_at"] == 2_000
    assert state["started_via_prompt"] is True
    assert state["funnel_counts"]["accept"] == 1
    assert state["funnel_counts"]["started"] == 1
    assert state["active_tutorial_run_token"] == started["tutorial_run_token"]
    assert state["active_tutorial_run_source"] == "idle_prompt"


@pytest.mark.unit
def test_manual_started_event_clears_stale_prompt_start_flag(tmp_path):
    config = DummyConfig(tmp_path)
    heartbeat = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    record_tutorial_started(
        {
            "page": "home",
            "source": "idle_prompt",
            "prompt_token": heartbeat["prompt_token"],
        },
        config_manager=config,
        now_ms=2_000,
    )

    started = record_tutorial_started(
        {"page": "home", "source": "manual"},
        config_manager=config,
        now_ms=3_000,
    )

    state = load_tutorial_prompt_state(config)
    assert state["started_via_prompt"] is False
    assert state["manual_home_tutorial_viewed"] is True
    assert state["active_tutorial_run_token"] == started["tutorial_run_token"]
    assert state["active_tutorial_run_source"] == "manual"


@pytest.mark.unit
def test_completed_event_persists_completion_with_valid_run_token(tmp_path):
    config = DummyConfig(tmp_path)
    started = record_tutorial_started(
        {"page": "home", "source": "manual"},
        config_manager=config,
        now_ms=3_000,
    )

    completed = record_tutorial_completed(
        {
            "page": "home",
            "source": "manual",
            "tutorial_run_token": started["tutorial_run_token"],
        },
        config_manager=config,
        now_ms=4_000,
    )

    assert completed["ignored"] is False
    assert completed["state"]["status"] == "completed"
    assert completed["state"]["completed_at"] == 4_000

    state = load_tutorial_prompt_state(config)
    assert state["started_at"] == 3_000
    assert state["completed_at"] == 4_000
    assert state["home_tutorial_completed"] is True
    assert state["active_tutorial_run_token"] == ""


@pytest.mark.unit
def test_manual_completed_event_clears_stale_prompt_start_flag(tmp_path):
    config = DummyConfig(tmp_path)
    started = record_tutorial_started(
        {"page": "home", "source": "manual"},
        config_manager=config,
        now_ms=3_000,
    )

    stale_state = load_tutorial_prompt_state(config)
    stale_state["started_via_prompt"] = True
    stale_state["accepted_at"] = 2_000
    save_tutorial_prompt_state(stale_state, config)

    record_tutorial_completed(
        {
            "page": "home",
            "source": "manual",
            "tutorial_run_token": started["tutorial_run_token"],
        },
        config_manager=config,
        now_ms=4_000,
    )

    state = load_tutorial_prompt_state(config)
    assert state["started_via_prompt"] is False
    assert state["home_tutorial_completed"] is True
    assert state["funnel_counts"]["completed"] == 0


@pytest.mark.unit
def test_completed_event_requires_valid_tutorial_run_token(tmp_path):
    config = DummyConfig(tmp_path)
    started = record_tutorial_started(
        {"page": "home", "source": "manual"},
        config_manager=config,
        now_ms=2_500,
    )

    with pytest.raises(ValueError, match="invalid tutorial_run_token"):
        record_tutorial_completed(
            {
                "page": "home",
                "source": "manual",
                "tutorial_run_token": started["tutorial_run_token"] + "-bad",
            },
            config_manager=config,
            now_ms=3_000,
        )


@pytest.mark.unit
def test_lifecycle_events_require_valid_source(tmp_path):
    config = DummyConfig(tmp_path)

    with pytest.raises(ValueError, match="invalid source"):
        record_tutorial_started(
            {"page": "home", "source": "unexpected"},
            config_manager=config,
            now_ms=1_000,
        )

    started = record_tutorial_started(
        {"page": "home", "source": "manual"},
        config_manager=config,
        now_ms=2_000,
    )

    with pytest.raises(ValueError, match="invalid source"):
        record_tutorial_completed(
            {
                "page": "home",
                "source": "unexpected",
                "tutorial_run_token": started["tutorial_run_token"],
            },
            config_manager=config,
            now_ms=2_500,
        )


@pytest.mark.unit
def test_completed_home_tutorial_suppresses_future_prompts(tmp_path):
    config = DummyConfig(tmp_path)
    response = process_tutorial_prompt_heartbeat(
        {
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
            "home_tutorial_completed": True,
        },
        config_manager=config,
        now_ms=8_000,
    )

    assert response["should_prompt"] is False
    assert response["prompt_reason"] == "tutorial_completed"

    state = load_tutorial_prompt_state(config)
    assert state["status"] == "completed"
    assert state["completed_at"] == 8_000


@pytest.mark.unit
def test_manual_completion_heartbeat_clears_stale_prompt_start_flag(tmp_path):
    config = DummyConfig(tmp_path)
    stale_state = load_tutorial_prompt_state(config)
    stale_state["started_via_prompt"] = True
    stale_state["accepted_at"] = 1_000
    stale_state["started_at"] = 1_000
    stale_state["status"] = "started"
    stale_state["manual_home_tutorial_viewed"] = True
    stale_state["manual_home_tutorial_viewed_at"] = 1_000
    save_tutorial_prompt_state(stale_state, config)

    response = process_tutorial_prompt_heartbeat(
        {
            "home_tutorial_completed": True,
            "manual_home_tutorial_viewed": True,
        },
        config_manager=config,
        now_ms=8_000,
    )

    assert response["state"]["started_via_prompt"] is False
    assert response["state"]["funnel_counts"]["completed"] == 0
    assert response["state"]["home_tutorial_completed"] is True


@pytest.mark.unit
def test_funnel_counts_track_accept_start_and_completion(tmp_path):
    config = DummyConfig(tmp_path)
    heartbeat = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    assert heartbeat["state"]["funnel_counts"]["issued"] == 1

    shown = record_tutorial_prompt_shown(
        {"prompt_token": heartbeat["prompt_token"]},
        config_manager=config,
        now_ms=1_200,
    )
    assert shown["state"]["funnel_counts"]["shown"] == 1

    started = record_tutorial_prompt_decision(
        {
            "decision": "accept",
            "result": "started",
            "prompt_token": heartbeat["prompt_token"],
        },
        config_manager=config,
        now_ms=2_000,
    )
    assert started["state"]["funnel_counts"]["accept"] == 1
    assert started["state"]["funnel_counts"]["started"] == 1

    completed = process_tutorial_prompt_heartbeat(
        {"home_tutorial_completed": True},
        config_manager=config,
        now_ms=3_000,
    )
    assert completed["state"]["funnel_counts"]["completed"] == 1


@pytest.mark.unit
def test_funnel_counts_track_later_never_and_failed(tmp_path):
    later_config = DummyConfig(tmp_path / "later")
    later_heartbeat = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=later_config,
        now_ms=1_000,
    )
    later_decision = record_tutorial_prompt_decision(
        {"decision": "later", "prompt_token": later_heartbeat["prompt_token"]},
        config_manager=later_config,
        now_ms=2_000,
    )
    assert later_decision["state"]["funnel_counts"]["later"] == 1

    never_config = DummyConfig(tmp_path / "never")
    never_heartbeat = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=never_config,
        now_ms=1_000,
    )
    never_decision = record_tutorial_prompt_decision(
        {"decision": "never", "prompt_token": never_heartbeat["prompt_token"]},
        config_manager=never_config,
        now_ms=2_000,
    )
    assert never_decision["state"]["funnel_counts"]["never"] == 1

    failed_config = DummyConfig(tmp_path / "failed")
    failed_heartbeat = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=failed_config,
        now_ms=1_000,
    )
    failed_decision = record_tutorial_prompt_decision(
        {
            "decision": "accept",
            "result": "failed",
            "error": "boom",
            "prompt_token": failed_heartbeat["prompt_token"],
        },
        config_manager=failed_config,
        now_ms=2_000,
    )
    assert failed_decision["state"]["funnel_counts"]["accept"] == 1
    assert failed_decision["state"]["funnel_counts"]["failed"] == 1


@pytest.mark.unit
def test_existing_user_with_memory_history_is_never_prompted(tmp_path):
    config = DummyConfig(tmp_path)
    memory_file = config.memory_dir / "LanLan" / "recent.json"
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text("[]", encoding="utf-8")

    response = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    assert response["should_prompt"] is False
    assert response["prompt_reason"] == "existing_user"
    assert response["state"]["user_cohort"] == "existing"
    assert response["state"]["cohort_reason"] == "memory_history"


@pytest.mark.unit
def test_legacy_autostart_state_is_ignored_for_new_tutorial_prompt(tmp_path):
    config = DummyConfig(tmp_path)
    legacy_state_path = config.config_dir / "autostart_prompt.json"
    legacy_state_path.write_text(json.dumps({
        "schema_version": 1,
        "status": "guided",
        "shown_count": 2,
        "never_remind": True,
        "foreground_ms": 0,
        "chat_turns": 0,
        "voice_sessions": 0,
        "home_tutorial_completed": False,
        "manual_home_tutorial_viewed": False,
    }), encoding="utf-8")

    response = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=3_000,
    )

    assert response["should_prompt"] is True
    assert response["prompt_reason"] == "idle_timeout"
    assert response["state"]["shown_count"] == 0
    assert response["state"]["never_remind"] is False

    tutorial_state_path = config.config_dir / "tutorial_prompt.json"
    assert tutorial_state_path.exists()


@pytest.mark.unit
def test_decision_acknowledges_prompt_if_shown_ack_is_missing(tmp_path):
    config = DummyConfig(tmp_path)
    heartbeat = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    decision = record_tutorial_prompt_decision(
        {"decision": "later", "prompt_token": heartbeat["prompt_token"]},
        config_manager=config,
        now_ms=1_500,
    )

    assert decision["state"]["shown_count"] == 1
    assert decision["state"]["active_prompt_token"] == ""


@pytest.mark.unit
def test_public_state_response_hides_internal_prompt_tokens(tmp_path):
    config = DummyConfig(tmp_path)
    heartbeat = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    assert heartbeat["prompt_token"]

    response = get_tutorial_prompt_state_response(config_manager=config)
    state = response["state"]

    assert state["status"] == "observing"
    assert state["shown_count"] == 0
    assert "active_prompt_token" not in state
    assert "active_prompt_issued_at" not in state
    assert "last_acknowledged_prompt_token" not in state


@pytest.mark.unit
def test_missing_prompt_threshold_config_uses_default_values(tmp_path):
    config = DummyConfig(tmp_path)

    runtime_config = load_tutorial_prompt_runtime_config(config)

    assert MIN_PROMPT_FOREGROUND_MS == 15_000
    assert runtime_config["min_prompt_foreground_ms"] == 15_000
    assert runtime_config["later_cooldown_ms"] == LATER_COOLDOWN_MS
    assert runtime_config["failure_cooldown_ms"] == 2 * 60 * 60 * 1000
    assert runtime_config["max_prompt_shows"] == 2


@pytest.mark.unit
def test_malformed_prompt_threshold_config_uses_default_values(tmp_path):
    config = DummyConfig(tmp_path)
    (config.config_dir / "tutorial_prompt_config.json").write_text("{", encoding="utf-8")

    runtime_config = load_tutorial_prompt_runtime_config(config)

    assert runtime_config["min_prompt_foreground_ms"] == 15_000
    assert runtime_config["later_cooldown_ms"] == LATER_COOLDOWN_MS
    assert runtime_config["failure_cooldown_ms"] == 2 * 60 * 60 * 1000
    assert runtime_config["max_prompt_shows"] == 2


@pytest.mark.unit
def test_prompt_threshold_config_overrides_idle_and_later_cooldown(tmp_path):
    config = DummyConfig(tmp_path)
    (config.config_dir / "tutorial_prompt_config.json").write_text(json.dumps({
        "min_prompt_foreground_ms": 30_000,
        "later_cooldown_ms": 600_000,
        "failure_cooldown_ms": 120_000,
        "max_prompt_shows": 3,
    }), encoding="utf-8")

    blocked = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": 29_000},
        config_manager=config,
        now_ms=1_000,
    )

    assert blocked["should_prompt"] is False
    assert blocked["prompt_reason"] == "foreground_insufficient"

    prompt = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": 1_000},
        config_manager=config,
        now_ms=2_000,
    )

    assert prompt["should_prompt"] is True

    decision = record_tutorial_prompt_decision(
        {"decision": "later", "prompt_token": prompt["prompt_token"]},
        config_manager=config,
        now_ms=5_000,
    )

    assert decision["state"]["deferred_until"] == 5_000 + 600_000


@pytest.mark.unit
def test_prompt_threshold_config_overrides_failure_cooldown_and_show_limit(tmp_path):
    config = DummyConfig(tmp_path)
    (config.config_dir / "tutorial_prompt_config.json").write_text(json.dumps({
        "min_prompt_foreground_ms": MIN_PROMPT_FOREGROUND_MS,
        "later_cooldown_ms": LATER_COOLDOWN_MS,
        "failure_cooldown_ms": 120_000,
        "max_prompt_shows": 1,
    }), encoding="utf-8")

    prompt = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=1_000,
    )

    decision = record_tutorial_prompt_decision(
        {
            "decision": "accept",
            "result": "failed",
            "error": "boom",
            "prompt_token": prompt["prompt_token"],
        },
        config_manager=config,
        now_ms=3_000,
    )

    assert decision["state"]["status"] == "error"
    assert decision["state"]["deferred_until"] == 3_000 + 120_000
    assert decision["state"]["shown_count"] == 1

    follow_up = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=200_000,
    )

    assert follow_up["should_prompt"] is False
    assert follow_up["prompt_reason"] == "show_limit_reached"


@pytest.mark.unit
def test_invalid_prompt_threshold_config_is_clamped(tmp_path):
    config = DummyConfig(tmp_path)
    (config.config_dir / "tutorial_prompt_config.json").write_text(json.dumps({
        "min_prompt_foreground_ms": -1,
        "later_cooldown_ms": 0,
        "failure_cooldown_ms": "bad",
        "max_prompt_shows": 999,
    }), encoding="utf-8")

    runtime_config = load_tutorial_prompt_runtime_config(config)

    assert runtime_config["min_prompt_foreground_ms"] == 15_000
    assert runtime_config["later_cooldown_ms"] == 5 * 60 * 1000
    assert runtime_config["failure_cooldown_ms"] == 2 * 60 * 60 * 1000
    assert runtime_config["max_prompt_shows"] == 10

    response = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": 0},
        config_manager=config,
        now_ms=1_000,
    )

    assert response["should_prompt"] is False
    assert response["prompt_reason"] == "foreground_insufficient"


@pytest.mark.unit
def test_app_start_only_token_usage_does_not_mark_existing_user(tmp_path):
    config = DummyConfig(tmp_path)
    token_usage_path = config.config_dir / "token_usage.json"
    token_usage_path.write_text(json.dumps({
        "version": 1,
        "daily_stats": {
            "2026-04-03": {
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "cached_tokens": 0,
                "call_count": 1,
                "error_count": 0,
                "by_model": {
                    "app_start": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "cached_tokens": 0,
                        "call_count": 1,
                    }
                },
                "by_call_type": {
                    "app_start": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "cached_tokens": 0,
                        "call_count": 1,
                    }
                },
            }
        },
        "recent_records": [
            {
                "ts": 1.0,
                "model": "app_start",
                "pt": 0,
                "ct": 0,
                "tt": 0,
                "cch": 0,
                "type": "app_start",
                "src": "",
                "ok": True,
            }
        ],
        "last_saved": "",
    }), encoding="utf-8")

    response = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=2_000,
    )

    assert response["should_prompt"] is True
    assert response["state"]["user_cohort"] == "new"
    assert response["state"]["cohort_reason"] == "no_prior_usage"


@pytest.mark.unit
def test_malformed_token_usage_collections_do_not_crash_or_mark_existing_user(tmp_path):
    config = DummyConfig(tmp_path)
    token_usage_path = config.config_dir / "token_usage.json"
    token_usage_path.write_text(json.dumps({
        "version": 1,
        "daily_stats": "bad",
        "recent_records": {"unexpected": True},
    }), encoding="utf-8")

    response = process_tutorial_prompt_heartbeat(
        {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS},
        config_manager=config,
        now_ms=2_000,
    )

    assert response["should_prompt"] is True
    assert response["state"]["user_cohort"] == "new"
    assert response["state"]["cohort_reason"] == "no_prior_usage"
