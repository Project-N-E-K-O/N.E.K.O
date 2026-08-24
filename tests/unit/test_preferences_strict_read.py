"""The strict/lenient split on the global conversation-settings read.

Codex P2 on PR #2345: ``load_global_conversation_settings`` swallowed every
IO/JSON error and returned the SAME empty dict as a file that legitimately has
no settings yet. Callers whose default is *weaker* than the persisted choice --
the microphone route decision, which falls back to the native Omni path -- then
silently overrode the user's stored preference on a transient read failure,
which is the opposite of fail-closed.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from utils import preferences


@pytest.fixture
def preferences_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "user_preferences.json"
    monkeypatch.setattr(
        preferences,
        "_get_active_preferences_path",
        lambda: str(path),
    )
    return path


@pytest.mark.unit
def test_malformed_file_is_swallowed_by_default(preferences_file: Path) -> None:
    preferences_file.write_text("{ not json", encoding="utf-8")

    assert preferences.load_global_conversation_settings() == {}


@pytest.mark.unit
def test_malformed_file_raises_under_strict(preferences_file: Path) -> None:
    preferences_file.write_text("{ not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        preferences.load_global_conversation_settings(strict=True)


@pytest.mark.unit
@pytest.mark.parametrize("strict", [False, True])
def test_absent_file_is_not_a_failure(preferences_file: Path, strict: bool) -> None:
    # The half that keeps a genuine first run defaulting normally: no file is
    # "no settings yet", not "the settings could not be read".
    assert not os.path.exists(preferences_file)

    assert preferences.load_global_conversation_settings(strict=strict) == {}


@pytest.mark.unit
def test_async_read_forwards_strict(preferences_file: Path) -> None:
    preferences_file.write_text("{ not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        asyncio.run(preferences.aload_global_conversation_settings(strict=True))

    assert asyncio.run(preferences.aload_global_conversation_settings()) == {}


@pytest.mark.unit
def test_readable_settings_are_returned_under_strict(preferences_file: Path) -> None:
    # Non-vacuity: strict must not change the happy path.
    preferences_file.write_text(
        json.dumps(
            [
                {
                    "model_path": preferences.GLOBAL_CONVERSATION_KEY,
                    "independentAsrEnabled": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    settings = preferences.load_global_conversation_settings(strict=True)

    assert settings.get("independentAsrEnabled") is True
