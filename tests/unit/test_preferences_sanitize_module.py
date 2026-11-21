"""M-001 follow-ups: the sanitiser must be a leaf, and cloud restore must order clean→check.

Two regressions drove these tests:

- ``legacy_migration`` imported ``utils.preferences`` for
  ``sanitize_preferences_payload``. Importing that module runs
  ``get_config_manager()`` with ``migrate=True`` at module scope, which settles the
  runtime root and marks the manager migrated -- during launcher phase-0, before
  the final root is known, so the real root migration is skipped.
- ``_build_runtime_preferences_payload`` judged "is the archive complete?" before
  removing non-finite values, so a poisoned setting counted as restored: the reset
  tombstone was dropped and the value was then removed, leaving a missing setting
  that an open client would silently write back.
"""

from __future__ import annotations

import math
import subprocess
import sys

import pytest

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_importing_the_sanitizer_leaf_does_not_touch_the_config_manager():
    """Import the leaf in a clean interpreter and prove nothing else was pulled in."""
    code = (
        "import sys\n"
        "import utils.preferences_sanitize\n"
        "assert 'utils.preferences' not in sys.modules, sorted(m for m in sys.modules if m.startswith('utils.pref'))\n"
        "assert 'utils.config_manager' not in sys.modules, 'config manager imported as a side effect'\n"
        "import utils.config_manager as cm\n"
        "assert cm._config_manager_migrated is False, 'leaf import marked the manager migrated'\n"
        "print('LEAF_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "LEAF_OK" in result.stdout


@pytest.mark.unit
def test_cloud_restore_of_a_nan_setting_keeps_the_reset_marker(monkeypatch):
    """A poisoned setting must count as *not restored*, so the tombstone survives."""
    from utils.cloudsave_runtime import bindings

    monkeypatch.setattr(bindings, "_load_user_preferences_entries", lambda *a, **k: [])

    settings = {key: True for key in bindings._ALLOWED_CONVERSATION_SETTINGS}
    settings["proactiveChatInterval"] = float("nan")
    settings["proactiveVisionInterval"] = float("inf")

    payload = bindings._build_runtime_preferences_payload(object(), settings)

    global_entry = next(
        entry
        for entry in payload
        if entry.get("model_path") == bindings.GLOBAL_CONVERSATION_KEY
    )
    # The poisoned values are gone ...
    assert "proactiveChatInterval" not in global_entry
    assert "proactiveVisionInterval" not in global_entry
    # ... and the archive is treated as incomplete, so clients are told to reset.
    assert global_entry[bindings._CONVERSATION_SETTINGS_RESET_KEY] is True


@pytest.mark.unit
def test_cloud_restore_of_an_unsafe_integer_keeps_the_reset_marker(monkeypatch):
    """The pre-check uses the same number rule as the trailing sanitation."""
    from utils.cloudsave_runtime import bindings

    monkeypatch.setattr(bindings, "_load_user_preferences_entries", lambda *a, **k: [])

    settings = {key: True for key in bindings._ALLOWED_CONVERSATION_SETTINGS}
    settings["textGuardMaxLength"] = 2 ** 53 + 1

    payload = bindings._build_runtime_preferences_payload(object(), settings)

    global_entry = next(
        entry
        for entry in payload
        if entry.get("model_path") == bindings.GLOBAL_CONVERSATION_KEY
    )
    assert "textGuardMaxLength" not in global_entry
    assert global_entry[bindings._CONVERSATION_SETTINGS_RESET_KEY] is True


@pytest.mark.unit
def test_cloud_restore_of_a_complete_archive_still_clears_the_reset_marker(monkeypatch):
    """The fix must not turn every restore into a reset."""
    from utils.cloudsave_runtime import bindings

    monkeypatch.setattr(bindings, "_load_user_preferences_entries", lambda *a, **k: [])

    settings = {key: True for key in bindings._ALLOWED_CONVERSATION_SETTINGS}
    settings[bindings._CONVERSATION_SETTINGS_RESET_KEY] = True

    payload = bindings._build_runtime_preferences_payload(object(), settings)

    global_entry = next(
        entry
        for entry in payload
        if entry.get("model_path") == bindings.GLOBAL_CONVERSATION_KEY
    )
    assert bindings._CONVERSATION_SETTINGS_RESET_KEY not in global_entry
    assert all(key in global_entry for key in bindings._ALLOWED_CONVERSATION_SETTINGS)


@pytest.mark.unit
def test_sanitizer_leaf_does_not_import_preferences_when_python_is_isolated():
    """Cheap sanity net for the module graph (no subprocess): no stale back-import."""
    import utils.preferences_sanitize as leaf

    source = __import__("pathlib").Path(leaf.__file__).read_text(encoding="utf-8")
    assert "from utils.preferences import" not in source
    assert "import utils.preferences\n" not in source
    assert math  # keep the import used, documents that only stdlib maths is needed
