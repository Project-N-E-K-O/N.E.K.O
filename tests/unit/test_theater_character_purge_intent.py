"""Character purge intents: exact, root-confined, and never applied to a live character."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from services.theater import numeric_v2_maintenance as maintenance


def _seed(theater: Path, relative: str) -> Path:
    path = theater / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


def _raw_intent(theater: Path, targets: list[str], *, character_id: str = "character_lan") -> Path:
    intent = maintenance.character_purge_intent_path(theater, "character_lan", "Lan")
    intent.parent.mkdir(parents=True, exist_ok=True)
    intent.write_text(json.dumps({
        "schema": maintenance.CHARACTER_PURGE_INTENT_SCHEMA,
        "character_id": character_id,
        "legacy_catgirl_name": "Lan",
        "targets": targets,
    }), encoding="utf-8")
    return intent


@pytest.mark.unit
def test_intent_deletes_only_listed_files_and_tolerates_missing_ones(tmp_path):
    listed = _seed(tmp_path, "numeric_v2/sessions/lan_session.json")
    missing = tmp_path / "numeric_v2/public_archives/gone.json"
    unlisted = _seed(tmp_path, "numeric_v2/sessions/other_session.json")
    intent = maintenance.write_character_purge_intent(
        tmp_path, character_id="character_lan", legacy_catgirl_name="Lan",
        targets=[listed, missing],
    )

    result = maintenance.recover_character_purge_intents(tmp_path, {"Other": "character_other"})

    assert result == {"purge_intents_applied": 1, "purge_intents_discarded": 0}
    assert not listed.exists()
    assert unlisted.is_file()
    assert not intent.exists()


@pytest.mark.unit
def test_intent_for_a_still_configured_character_is_discarded_without_deleting(tmp_path):
    listed = _seed(tmp_path, "numeric_v2/sessions/lan_session.json")
    intent = maintenance.write_character_purge_intent(
        tmp_path, character_id="character_lan", legacy_catgirl_name="Lan", targets=[listed],
    )

    result = maintenance.recover_character_purge_intents(tmp_path, {"Lan": "character_lan"})

    assert result == {"purge_intents_applied": 0, "purge_intents_discarded": 1}
    assert listed.is_file()
    assert not intent.exists()


@pytest.mark.unit
@pytest.mark.parametrize("target", [
    "../outside.json",
    "numeric_v2/../../outside.json",
    "numeric_v2/packages/story.json",
    "numeric_v2/sessions/nested/deep.json",
    "/etc/passwd",
    "numeric_v2/sessions/not_json.txt",
])
def test_intent_naming_a_path_outside_the_purge_dirs_is_never_acted_on(tmp_path, target):
    victim = _seed(tmp_path, "numeric_v2/packages/story.json")
    listed = _seed(tmp_path, "numeric_v2/sessions/lan_session.json")
    intent = _raw_intent(tmp_path, ["numeric_v2/sessions/lan_session.json", target])

    result = maintenance.recover_character_purge_intents(tmp_path, {})

    assert result == {"purge_intents_applied": 0, "purge_intents_discarded": 0}
    assert victim.is_file()
    # The whole intent is refused, including its otherwise valid entries.
    assert listed.is_file()
    assert intent.is_file()


@pytest.mark.unit
def test_intent_with_mismatched_identity_is_kept_untouched(tmp_path):
    listed = _seed(tmp_path, "numeric_v2/sessions/lan_session.json")
    intent = _raw_intent(
        tmp_path, ["numeric_v2/sessions/lan_session.json"], character_id="character_somebody_else",
    )

    maintenance.recover_character_purge_intents(tmp_path, {})

    assert listed.is_file()
    assert intent.is_file()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_intent_does_not_follow_a_symlinked_purge_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    theater = tmp_path / "theater"
    (theater / "numeric_v2").mkdir(parents=True)
    (theater / "numeric_v2" / "sessions").symlink_to(outside, target_is_directory=True)
    _raw_intent(theater, ["numeric_v2/sessions/victim.json"])

    maintenance.recover_character_purge_intents(theater, {})

    assert victim.is_file()


@pytest.mark.unit
def test_writing_an_intent_rejects_targets_outside_the_theater_root(tmp_path):
    with pytest.raises(maintenance.NumericV2PurgeIntentError):
        maintenance.write_character_purge_intent(
            tmp_path / "theater", character_id="character_lan", legacy_catgirl_name="Lan",
            targets=[tmp_path / "elsewhere.json"],
        )
    assert not (tmp_path / "theater" / "numeric_v2" / "purge_intents").exists()


@pytest.mark.unit
def test_startup_maintenance_retries_leftover_purge_intents(tmp_path, monkeypatch):
    from services.theater.numeric_v2_registry import NumericV2PackageRegistry

    listed = _seed(tmp_path, "numeric_v2/public_archives/" + "a" * 64 + ".json")
    listed.write_text(json.dumps({
        "schema": "neko.theater.numeric.v2.public-archive",
        "session_id": "lan_session", "story_id": "story", "character_id": "character_lan",
    }), encoding="utf-8")
    intent = maintenance.write_character_purge_intent(
        tmp_path, character_id="character_lan", legacy_catgirl_name="Lan", targets=[listed],
    )
    monkeypatch.setattr(maintenance, "_MAINTAINED_ROOTS", set())

    result = maintenance.maintain_numeric_v2_storage_once(
        tmp_path,
        NumericV2PackageRegistry(tmp_path / "numeric_v2" / "packages"),
        character_ids_by_name={},
    )

    assert result["purge_intents_applied"] == 1
    assert not listed.exists()
    assert not intent.exists()
