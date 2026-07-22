import json

import pytest

from utils.seven_day_tutorial_state import (
    SevenDayTutorialStateConflict,
    get_seven_day_tutorial_state_response,
    replace_seven_day_tutorial_state,
)


class _ConfigManager:
    def __init__(self, config_dir):
        self.config_dir = config_dir

    def get_config_path(self, filename):
        return str(self.config_dir / filename)


@pytest.mark.unit
def test_seven_day_state_is_uninitialized_before_first_browser_migration(tmp_path):
    response = get_seven_day_tutorial_state_response(
        config_manager=_ConfigManager(tmp_path)
    )

    assert response["ok"] is True
    assert response["initialized"] is False
    assert response["revision"] == 0
    assert response["state"] is None


@pytest.mark.unit
def test_completed_legacy_home_tutorial_is_migrated_once(tmp_path):
    manager = _ConfigManager(tmp_path)
    (tmp_path / "tutorial_prompt.json").write_text(json.dumps({
        "home_tutorial_completed": True,
        "first_seen_at": 1782864000000,
    }))

    response = get_seven_day_tutorial_state_response(config_manager=manager)

    assert response["initialized"] is True
    assert response["revision"] == 1
    assert response["state"]["completedRounds"] == [1]
    assert response["state"]["firstSeenDate"] == "2026-07-01"

    (tmp_path / "tutorial_prompt.json").write_text(json.dumps({
        "home_tutorial_completed": False,
    }))
    reloaded = get_seven_day_tutorial_state_response(config_manager=manager)
    assert reloaded == response


@pytest.mark.unit
def test_legacy_never_remind_is_not_migrated_as_tutorial_completion(tmp_path):
    manager = _ConfigManager(tmp_path)
    (tmp_path / "tutorial_prompt.json").write_text(json.dumps({
        "never_remind": True,
        "home_tutorial_completed": False,
    }))

    response = get_seven_day_tutorial_state_response(config_manager=manager)

    assert response["initialized"] is False
    assert response["state"] is None


@pytest.mark.unit
def test_first_browser_state_becomes_authoritative_and_survives_new_origin(tmp_path):
    manager = _ConfigManager(tmp_path)
    saved = replace_seven_day_tutorial_state(
        {
            "version": 2,
            "firstSeenDate": "2026-07-01",
            "completedRounds": [1, 2],
            "skippedRounds": [3],
        },
        expected_revision=0,
        config_manager=manager,
    )

    assert saved["initialized"] is True
    assert saved["revision"] == 1
    assert saved["state"]["completedRounds"] == [1, 2]
    assert saved["state"]["skippedRounds"] == [3]

    reloaded = get_seven_day_tutorial_state_response(config_manager=manager)
    assert reloaded == {"ok": True, **saved}


@pytest.mark.unit
def test_replacement_is_normalized_and_revision_is_monotonic(tmp_path):
    manager = _ConfigManager(tmp_path)
    first = replace_seven_day_tutorial_state(
        {
            "firstSeenDate": "not-a-date",
            "completedRounds": [1, 1, 8, "2"],
            "skippedRounds": [1, 3],
            "lastAutoShownRound": 99,
        },
        expected_revision=0,
        config_manager=manager,
    )
    second = replace_seven_day_tutorial_state(
        first["state"],
        expected_revision=first["revision"],
        config_manager=manager,
    )

    assert first["state"]["completedRounds"] == [1, 2]
    assert first["state"]["skippedRounds"] == [3]
    assert first["state"]["lastAutoShownRound"] is None
    assert second["revision"] == 2

    raw = json.loads((tmp_path / "seven_day_tutorial_state.json").read_text())
    assert raw["revision"] == 2


@pytest.mark.unit
def test_replacement_rejects_non_object_state(tmp_path):
    with pytest.raises(ValueError, match="state must be an object"):
        replace_seven_day_tutorial_state(
            [],
            expected_revision=0,
            config_manager=_ConfigManager(tmp_path),
        )


@pytest.mark.unit
def test_replacement_rejects_non_integer_revision(tmp_path):
    with pytest.raises(ValueError, match="expectedRevision must be a non-negative integer"):
        replace_seven_day_tutorial_state(
            {},
            expected_revision=0.5,
            config_manager=_ConfigManager(tmp_path),
        )


@pytest.mark.unit
def test_replacement_rejects_a_stale_revision_without_overwriting(tmp_path):
    manager = _ConfigManager(tmp_path)
    first = replace_seven_day_tutorial_state(
        {"completedRounds": [1], "updatedAt": "2026-07-01T00:00:00.000Z"},
        expected_revision=0,
        config_manager=manager,
    )

    with pytest.raises(SevenDayTutorialStateConflict) as conflict:
        replace_seven_day_tutorial_state(
            {"completedRounds": [], "updatedAt": "2026-06-30T00:00:00.000Z"},
            expected_revision=0,
            config_manager=manager,
        )

    assert conflict.value.current_store == first
    assert get_seven_day_tutorial_state_response(config_manager=manager) == {
        "ok": True,
        **first,
    }
