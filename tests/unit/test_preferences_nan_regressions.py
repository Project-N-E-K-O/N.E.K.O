"""M-001 review: the four legacy-``NaN`` regression doors, pinned end to end.

``test_preferences_nan_sanitize.py`` covers the sanitiser itself; this file pins
the concrete failure modes that were reported against it, so each one fails on
its own if a future change reintroduces it:

a) a body that is not JSON at all is a client error (400), not a 500;
b) an old ``user_preferences.json`` carrying ``NaN`` no longer blocks
   ``save_ui_language_override`` -- and the repaired file is strict JSON;
c) the legacy-root import merges a poisoned payload (including a poisoned
   settings journal) and still writes a clean file;
d) a poisoned ``_settings_mutation_journal`` keeps a normal read and a normal
   per-model save working, and the journal's structure survives the write.

None of these may pass by declaring ``allow_nan=True``: every assertion that the
file is clean is made with ``json.dumps(..., allow_nan=False)``.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request
from starlette.responses import Response

from main_routers.config_router import preferences as preferences_router
from utils import preferences, preferences_audit

GLOBAL_KEY = preferences.GLOBAL_CONVERSATION_KEY
JOURNAL_KEY = preferences_audit.JOURNAL_KEY


@pytest.fixture
def isolated_preferences(monkeypatch, tmp_path):
    """Point the preferences module at a throwaway file, never the real config dir."""
    path = tmp_path / "user_preferences.json"

    class Config:
        def get_config_path(self, _name):
            return path

        get_runtime_config_path = get_config_path

        def ensure_config_directory(self):
            path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(preferences, "_config_manager", Config())
    monkeypatch.setattr(preferences, "assert_cloudsave_writable", lambda *a, **k: None)
    monkeypatch.setattr(preferences, "_SANITIZE_WARNED", set())
    monkeypatch.setattr(preferences, "_SANITIZE_SUPPRESSED", [0])
    # Journal behaviour must not depend on which test file ran before this one.
    monkeypatch.setattr(preferences_audit, "_attribution_factory", None)
    monkeypatch.setattr(preferences_audit, "_capture_protocol", 1)
    monkeypatch.setattr(preferences_audit, "_capture_provider", None)
    return path


def write_poisoned(path, entries):
    """Write entries the way the bug did: ``json.dumps`` defaults to ``allow_nan=True``."""
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def model_entry(model_path, **extra):
    entry = {
        "model_path": model_path,
        "position": {"x": 1, "y": 2},
        "scale": {"x": 1, "y": 1},
    }
    entry.update(extra)
    return entry


def raw_request(body: bytes) -> Request:
    """A POST request with a literal body, so it can be malformed on purpose."""
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/config/preferences",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


def post_raw(body: bytes):
    """Invoke the handler the way FastAPI does, with an injected response."""
    response = Response()
    result = asyncio.run(
        preferences_router.save_preferences(raw_request(body), response)
    )
    return response.status_code, result


def assert_strict_json_file(path):
    """The regression gate: the file on disk must be strict JSON, not just parsable."""
    raw = path.read_text(encoding="utf-8")
    assert "NaN" not in raw and "Infinity" not in raw
    stored = json.loads(raw)
    json.dumps(stored, allow_nan=False)
    return stored


# ---------------------------------------------------------------------------
# a) A malformed body is a client error, not a server fault
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [
        b"{ not json",
        b'{"model_path": "m.model3.json",}',
        b"\xff\xfe\x00",
        b"",
    ],
    ids=["garbage", "trailing-comma", "invalid-utf8", "empty-body"],
)
def test_post_invalid_json_returns_400_and_writes_nothing(
    isolated_preferences, body
):
    status, result = post_raw(body)

    assert status == 400
    assert result == {"success": False, "error": "无效的数据"}
    # Nothing was read as preferences, so nothing was created or rewritten.
    assert not isolated_preferences.exists()


# ---------------------------------------------------------------------------
# b) save_ui_language_override must repair, not fail, on a poisoned old file
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_save_ui_language_override_heals_a_poisoned_legacy_file(
    isolated_preferences,
):
    write_poisoned(
        isolated_preferences,
        [
            model_entry(
                "broken.model3.json",
                position={"x": float("nan"), "y": 5},
                scale={"x": float("inf"), "y": 1},
            ),
            {
                "model_path": GLOBAL_KEY,
                "proactiveChatInterval": float("nan"),
                "textGuardMaxLength": 600,
            },
        ],
    )

    # Before the fix this returned False and wrote nothing: the poisoned model
    # entry failed serialisation of the whole payload.
    assert preferences.save_ui_language_override("ja") is True

    stored = assert_strict_json_file(isolated_preferences)
    by_path = {entry["model_path"]: entry for entry in stored}
    # Only the poisoned numbers were removed; the entries and their good keys stay.
    assert by_path["broken.model3.json"]["position"] == {"y": 5}
    assert by_path["broken.model3.json"]["scale"] == {"y": 1}
    global_entry = by_path[GLOBAL_KEY]
    assert global_entry["uiLanguage"] == "ja"
    assert global_entry["textGuardMaxLength"] == 600
    assert "proactiveChatInterval" not in global_entry


# ---------------------------------------------------------------------------
# c) The legacy-root merge writes a clean file even with a poisoned journal
# ---------------------------------------------------------------------------


def poisoned_legacy_journal():
    return {
        "version": 1,
        "revision": 3,
        "entries": [
            {
                "id": "legacy-pending",
                "owner": {"key": "owner-a", "user_id": None},
                "payload": {
                    "revision": 3,
                    "origin": "user",
                    "occurred_at": 1.5,
                    "previous": {"proactiveChatInterval": float("nan")},
                    "settings": {"proactiveChatInterval": float("inf")},
                },
            }
        ],
    }


@pytest.mark.unit
def test_legacy_root_import_heals_a_poisoned_settings_journal(tmp_path):
    from utils.cloudsave_runtime import legacy_migration

    source_root = tmp_path / "legacy"
    target_root = tmp_path / "current"
    temp_root = tmp_path / "staged"
    (source_root / "config").mkdir(parents=True)
    (target_root / "config").mkdir(parents=True)

    (source_root / "config" / "user_preferences.json").write_text(
        json.dumps(
            [
                model_entry("legacy.model3.json"),
                {
                    "model_path": GLOBAL_KEY,
                    "proactiveMusicEnabled": True,
                    "_settings_mutation_journal": poisoned_legacy_journal(),
                },
            ]
        ),
        encoding="utf-8",
    )
    # The current root is poisoned too: merging both must still produce clean JSON.
    (target_root / "config" / "user_preferences.json").write_text(
        json.dumps(
            [model_entry("current.model3.json", position={"x": float("inf"), "y": 3})]
        ),
        encoding="utf-8",
    )

    class Config:
        project_config_dir = tmp_path / "project-config"

        def _get_localized_characters_source(self):
            return None

        def get_default_characters(self):
            return {}

    # Before the fix this raised ValueError from atomic_write_json and aborted the
    # whole import, leaving the user on the old runtime root.
    legacy_migration._stage_merged_runtime_configs(
        Config(),
        source_root=source_root,
        target_root=target_root,
        temp_root=temp_root,
        target_summary={"seeded_character_shell": False},
    )

    merged_path = temp_root / "config" / "user_preferences.json"
    merged = assert_strict_json_file(merged_path)
    by_path = {entry["model_path"]: entry for entry in merged}
    assert set(by_path) >= {"legacy.model3.json", "current.model3.json", GLOBAL_KEY}
    # Only the non-finite numbers went away; both roots' entries are still there.
    assert by_path["legacy.model3.json"]["position"] == {"x": 1, "y": 2}
    assert by_path["current.model3.json"]["position"] == {"y": 3}
    assert by_path[GLOBAL_KEY]["proactiveMusicEnabled"] is True

    # The journal survives as a journal -- same version/revision/entry ids --
    # with only its poisoned payload numbers scrubbed.
    journal = by_path[GLOBAL_KEY][JOURNAL_KEY]
    assert journal["version"] == 1
    assert journal["revision"] == 3
    entry, = journal["entries"]
    assert entry["id"] == "legacy-pending"
    assert entry["owner"] == {"key": "owner-a", "user_id": None}
    assert entry["payload"]["revision"] == 3
    assert entry["payload"]["origin"] == "user"
    assert entry["payload"]["occurred_at"] == 1.5
    assert entry["payload"]["previous"] == {}
    assert entry["payload"]["settings"] == {}
    # The handoff must still be able to read what the import wrote.
    assert preferences_audit.read_journal(merged)["entries"] == journal["entries"]


# ---------------------------------------------------------------------------
# d) A poisoned settings journal must not break a read or a normal model save
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_poisoned_settings_journal_survives_a_read_and_a_model_save(
    isolated_preferences,
):
    journal = {
        "version": 1,
        "revision": 2,
        "entries": [
            {
                "id": "pending-1",
                "owner": {"key": "anonymous"},
                "payload": {
                    "revision": 2,
                    "origin": "user",
                    "occurred_at": 1.5,
                    "previous": {"proactiveChatInterval": float("nan")},
                    "settings": {"proactiveChatInterval": float("-inf")},
                },
                "context": {
                    "version": "desktop-feedback-capture-v2",
                    "capture": {"tick_ms": float("nan")},
                },
            }
        ],
    }
    write_poisoned(
        isolated_preferences,
        [
            model_entry("ok.model3.json"),
            model_entry("bad.model3.json", position={"x": float("nan"), "y": 2}),
            {
                "model_path": GLOBAL_KEY,
                "proactiveMusicEnabled": False,
                "_settings_mutation_journal": journal,
            },
        ],
    )

    # A read strips the journal from the returned view and drops only the bad
    # numbers; it never raises and never touches the file.
    before = isolated_preferences.read_bytes()
    loaded = preferences.load_user_preferences()
    assert [entry["model_path"] for entry in loaded] == [
        "ok.model3.json",
        "bad.model3.json",
        GLOBAL_KEY,
    ]
    assert loaded[1]["position"] == {"y": 2}
    assert JOURNAL_KEY not in loaded[2]
    assert isolated_preferences.read_bytes() == before

    # A normal per-model save loads the poisoned journal on the write path and
    # must still commit: before the fix this returned False and wrote nothing.
    assert preferences.update_model_preferences(
        "ok.model3.json", {"x": 7, "y": 8}, {"x": 1, "y": 1}
    ) is True

    stored = assert_strict_json_file(isolated_preferences)
    by_path = {entry["model_path"]: entry for entry in stored}
    assert by_path["ok.model3.json"]["position"] == {"x": 7, "y": 8}
    assert by_path["bad.model3.json"]["position"] == {"y": 2}

    global_entry = by_path[GLOBAL_KEY]
    assert global_entry["proactiveMusicEnabled"] is False
    saved_journal = global_entry[JOURNAL_KEY]
    # The journal's skeleton is preserved for the settings handoff ...
    assert saved_journal["version"] == 1
    assert saved_journal["revision"] == 2
    saved_entry, = saved_journal["entries"]
    assert saved_entry["id"] == "pending-1"
    assert saved_entry["owner"] == {"key": "anonymous"}
    assert saved_entry["payload"]["revision"] == 2
    assert saved_entry["payload"]["origin"] == "user"
    assert saved_entry["payload"]["occurred_at"] == 1.5
    # ... while only its non-finite numbers are gone.
    assert saved_entry["payload"]["previous"] == {}
    assert saved_entry["payload"]["settings"] == {}
    assert saved_entry["context"]["capture"] == {}
    assert preferences_audit.read_journal(stored)["entries"] == saved_journal["entries"]
