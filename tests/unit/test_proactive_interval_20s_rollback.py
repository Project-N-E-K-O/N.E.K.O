"""One-time preference rollback for retired proactive_interval_20s experiment.

The experiment changed overseas first-launch proactiveChatInterval defaults from 15s to 20s; retirement requires
restoring persisted 20s to 15s. Rollback attaches to `_get_telemetry_branch`'s retired-branch-invalid/redraw choke
point:

- Only installations with .telemetry_branch exactly proactive_interval_20s (definitive experiment signal)
  roll back, never ordinary manual 20s choices/domestic users;
- Roll back only preferences still at overridden 20s; preserve user changes;
- Redraw inherently marks idempotency across restarts, without blocking later manual 20s selection.
"""
from unittest.mock import patch

import utils.token_tracker as tt


def _seed_branch(config_dir, value):
    (config_dir / tt._TELEMETRY_BRANCH_FILE).write_text(value, encoding="utf-8")


def _read_branch(config_dir):
    return (config_dir / tt._TELEMETRY_BRANCH_FILE).read_text(encoding="utf-8").strip()


def test_retired_interval_branch_rolls_back_overwritten_value(tmp_path):
    tt._telemetry_branch_cache.clear()
    _seed_branch(tmp_path, "proactive_interval_20s")

    with patch(
        "utils.preferences.load_global_conversation_settings",
        return_value={"proactiveChatInterval": 20},
    ), patch(
        "utils.preferences.save_global_conversation_settings", return_value=True
    ) as mock_save:
        branch = tt._get_telemetry_branch(tmp_path)

    # Retired value redrawn into current pool.
    assert branch in tt._TELEMETRY_BRANCHES
    assert branch != "proactive_interval_20s"
    # Preference restored to control group's 15s.
    mock_save.assert_called_once_with({"proactiveChatInterval": 15})
    # Branch file overwritten by redraw (inherently idempotent marker).
    assert _read_branch(tmp_path) == branch


def test_user_changed_interval_is_preserved(tmp_path):
    """Retain experiment users' manually changed values (!=20) after first launch; no rollback."""
    tt._telemetry_branch_cache.clear()
    _seed_branch(tmp_path, "proactive_interval_20s")

    with patch(
        "utils.preferences.load_global_conversation_settings",
        return_value={"proactiveChatInterval": 30},
    ), patch(
        "utils.preferences.save_global_conversation_settings"
    ) as mock_save:
        tt._get_telemetry_branch(tmp_path)

    mock_save.assert_not_called()


def test_non_experiment_branch_value_20_is_not_touched(tmp_path):
    """Do not roll back ordinary manually selected 20s (branch outside retired experiment), protecting manual preferences."""
    tt._telemetry_branch_cache.clear()
    _seed_branch(tmp_path, "corrupted_or_unknown_value")

    with patch(
        "utils.preferences.load_global_conversation_settings",
        return_value={"proactiveChatInterval": 20},
    ), patch(
        "utils.preferences.save_global_conversation_settings"
    ) as mock_save:
        branch = tt._get_telemetry_branch(tmp_path)

    assert branch in tt._TELEMETRY_BRANCHES
    mock_save.assert_not_called()


def test_valid_main_branch_does_not_trigger_rollback(tmp_path):
    """Valid persisted branch takes fast path, never redraw choke point or rollback."""
    tt._telemetry_branch_cache.clear()
    _seed_branch(tmp_path, "main")

    with patch(
        "utils.preferences.load_global_conversation_settings",
        return_value={"proactiveChatInterval": 20},
    ), patch(
        "utils.preferences.save_global_conversation_settings"
    ) as mock_save:
        branch = tt._get_telemetry_branch(tmp_path)

    assert branch == "main"
    mock_save.assert_not_called()


def test_rollback_is_idempotent_across_restarts(tmp_path):
    tt._telemetry_branch_cache.clear()
    _seed_branch(tmp_path, "proactive_interval_20s")

    with patch(
        "utils.preferences.load_global_conversation_settings",
        return_value={"proactiveChatInterval": 20},
    ), patch(
        "utils.preferences.save_global_conversation_settings", return_value=True
    ) as mock_save:
        tt._get_telemetry_branch(tmp_path)
    assert mock_save.call_count == 1

    # Simulate next startup (new process, clear process cache). Branch already redrawn valid, hitting fast path.
    tt._telemetry_branch_cache.clear()
    with patch(
        "utils.preferences.load_global_conversation_settings",
        return_value={"proactiveChatInterval": 20},
    ), patch(
        "utils.preferences.save_global_conversation_settings"
    ) as mock_save_2:
        tt._get_telemetry_branch(tmp_path)
    mock_save_2.assert_not_called()
