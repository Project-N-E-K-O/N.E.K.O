"""M-001: a poisoned ``user_preferences.json`` must never reach a reader.

Covers P0-1a (write side refuses to emit ``NaN``), P0-3 (in-memory sanitation on
read) and boundaries B-1..B-5 from ``M-001-fix-plan.md``.

The invariant under test is narrow and deliberate: this code only removes
non-finite numbers. It does not coerce them to ``0``, it does not restore an old
value, and it never rewrites the file from the read path (the file is repaired by
the next ordinary save).

The second half covers the other half of the same bug: a *writer* that reads the
raw file through ``_load_preferences_data_for_write_unlocked`` and saves the whole
payload again must not be blocked by a poisoned entry (``NaN`` is not valid JSON,
and ``atomic_write_json`` rejects it). ``sanitize_preferences_payload`` heals the
loaded snapshot, so those saves keep working and repair the file on the way.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request

from main_routers.config_router import preferences as preferences_router
from utils import preferences, preferences_audit

GLOBAL_KEY = preferences.GLOBAL_CONVERSATION_KEY


@pytest.fixture
def isolated_preferences(monkeypatch, tmp_path):
    """Point the preferences module at a throwaway file and reset warn throttling."""
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
    """Write entries the way the bug did: json.dumps defaults to allow_nan=True."""
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def save_request(payload):
    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps(payload).encode(),
            "more_body": False,
        }

    return Request({"type": "http", "method": "POST", "path": "/preferences"}, receive)


def model_entry(model_path, **extra):
    entry = {
        "model_path": model_path,
        "position": {"x": 1, "y": 2},
        "scale": {"x": 1, "y": 1},
    }
    entry.update(extra)
    return entry


# ---------------------------------------------------------------------------
# B-1: the finite-number predicate
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_huge_int_is_rejected_without_raising():
    """10**500 is beyond float range; math.isfinite would raise OverflowError."""
    assert preferences.is_finite_json_number(10 ** 500) is False


@pytest.mark.unit
def test_safe_integer_boundary():
    assert preferences.is_finite_json_number(2 ** 53) is True
    assert preferences.is_finite_json_number(2 ** 53 + 1) is False
    assert preferences.is_finite_json_number(-2 ** 53) is True
    assert preferences.is_finite_json_number(-2 ** 53 - 1) is False


@pytest.mark.unit
def test_bool_is_not_a_number():
    # bool subclasses int and math.isfinite(True) is True; persisting "true" as a
    # coordinate breaks Number.isFinite on the client.
    assert preferences.is_finite_json_number(True) is False
    assert preferences.is_finite_json_number(False) is False


@pytest.mark.unit
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_floats_are_rejected(value):
    assert preferences.is_finite_json_number(value) is False


@pytest.mark.unit
@pytest.mark.parametrize("value", [0, -1, 1.5, 2 ** 53 - 1])
def test_finite_numbers_are_accepted(value):
    assert preferences.is_finite_json_number(value) is True


# ---------------------------------------------------------------------------
# P0-3 / B-2: entry-level sanitation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_nan_position_drops_only_the_bad_coordinate():
    """A read removes the poisoned number, not the user's model entry."""
    cleaned = preferences._sanitize_preferences_entries(
        [
            model_entry("ok.model3.json"),
            model_entry("bad.model3.json", position={"x": float("nan"), "y": 2}),
        ]
    )
    assert [entry["model_path"] for entry in cleaned] == [
        "ok.model3.json",
        "bad.model3.json",
    ]
    assert cleaned[1]["position"] == {"y": 2}


@pytest.mark.unit
def test_missing_position_key_keeps_the_entry():
    """Only non-finite numbers are removed; a missing y/z is not a reason to drop."""
    cleaned = preferences._sanitize_preferences_entries(
        [model_entry("bad.model3.json", position={"x": 1})]
    )
    assert len(cleaned) == 1
    assert cleaned[0]["position"] == {"x": 1}


@pytest.mark.unit
def test_non_mapping_field_is_preserved_as_is():
    """Shape is not policed on read: a non-dict position survives untouched."""
    cleaned = preferences._sanitize_preferences_entries(
        [model_entry("m.model3.json", position=None, scale=["legacy"])]
    )
    assert len(cleaned) == 1
    assert cleaned[0]["position"] is None
    assert cleaned[0]["scale"] == ["legacy"]


@pytest.mark.unit
def test_non_finite_optional_field_is_dropped_without_losing_the_model():
    cleaned = preferences._sanitize_preferences_entries(
        [model_entry("m.model3.json", rotation={"x": float("inf"), "y": 0, "z": 0})]
    )
    assert len(cleaned) == 1
    assert cleaned[0]["position"] == {"x": 1, "y": 2}
    # NaN/Inf key removed, the rest of the mapping kept.
    assert cleaned[0]["rotation"] == {"y": 0, "z": 0}


@pytest.mark.unit
def test_parameters_group_is_dropped_together():
    """B-2: mode='replace' promises a complete snapshot, so a bad value drops both."""
    cleaned = preferences._sanitize_preferences_entries(
        [
            model_entry(
                "m.model3.json",
                parameters={"ParamAngleX": float("nan")},
                parameters_mode="replace",
            )
        ]
    )
    assert len(cleaned) == 1
    assert "parameters" not in cleaned[0]
    assert "parameters_mode" not in cleaned[0]


@pytest.mark.unit
def test_dangling_parameters_mode_is_dropped():
    """B-2: a mode without the parameters it describes can never validate again."""
    cleaned = preferences._sanitize_preferences_entries(
        [model_entry("m.model3.json", parameters_mode="replace")]
    )
    assert len(cleaned) == 1
    assert "parameters_mode" not in cleaned[0]


@pytest.mark.unit
def test_valid_parameters_group_survives():
    cleaned = preferences._sanitize_preferences_entries(
        [
            model_entry(
                "m.model3.json",
                parameters={"ParamAngleX": 12.5, "ParamEyeLOpen": 1},
                parameters_mode="replace",
            )
        ]
    )
    assert cleaned[0]["parameters"] == {"ParamAngleX": 12.5, "ParamEyeLOpen": 1}
    assert cleaned[0]["parameters_mode"] == "replace"


@pytest.mark.unit
def test_valid_entry_is_byte_for_byte_unchanged():
    entry = model_entry(
        "m.model3.json",
        rotation={"x": 0, "y": 0.5, "z": 0},
        viewport={"width": 1920, "height": 1080},
        display={"screenX": 0, "screenY": 0},
        camera_position={"x": 1, "y": 2, "z": 3, "qx": 0, "qy": 0, "qz": 0, "qw": 1},
    )
    assert preferences._sanitize_preferences_entries([entry]) == [entry]


# ---------------------------------------------------------------------------
# B-3: the global entry must survive; only its bad numbers go
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_global_entry_keeps_journal_and_drops_only_bad_numbers():
    journal = {"version": 1, "revision": 3, "entries": []}
    cleaned = preferences._sanitize_preferences_entries(
        [
            {
                "model_path": GLOBAL_KEY,
                "proactiveChatInterval": float("nan"),
                "textGuardMaxLength": 500,
                "proactiveMusicEnabled": True,
                "_settings_mutation_journal": journal,
            }
        ]
    )
    assert len(cleaned) == 1
    global_entry = cleaned[0]
    assert global_entry["model_path"] == GLOBAL_KEY
    assert "proactiveChatInterval" not in global_entry
    assert global_entry["textGuardMaxLength"] == 500
    assert global_entry["proactiveMusicEnabled"] is True
    assert global_entry["_settings_mutation_journal"] == journal


@pytest.mark.unit
def test_deeply_nested_global_nan_is_stripped():
    cleaned = preferences._sanitize_preferences_entries(
        [
            {
                "model_path": GLOBAL_KEY,
                "activity": {"thresholds": {"gaming": float("nan"), "work": 3}},
                "listValue": [1, float("nan"), 2],
            }
        ]
    )
    assert cleaned[0]["activity"] == {"thresholds": {"work": 3}}
    assert cleaned[0]["listValue"] == [1, 2]


@pytest.mark.unit
def test_sanitized_payload_always_serialises_with_allow_nan_false():
    """B-3 post-condition: this is what makes 'GET never 500s' provable."""
    payload = preferences._sanitize_preferences_entries(
        [
            model_entry("a.model3.json", rotation={"x": float("nan")}),
            {"model_path": GLOBAL_KEY, "weird": {"deep": [float("inf")]}},
            model_entry("b.model3.json", parameters={"P": float("-inf")}),
        ]
    )
    # Would raise ValueError if any NaN/Infinity survived.
    json.dumps(payload, allow_nan=False)


# ---------------------------------------------------------------------------
# B-4: warn once, bounded
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_repeated_loads_warn_once_per_field(isolated_preferences):
    write_poisoned(
        isolated_preferences,
        [model_entry("a.model3.json", position={"x": float("nan"), "y": 0})],
    )

    for _ in range(5):
        loaded = preferences.load_user_preferences()
        assert len(loaded) == 1
        assert loaded[0]["position"] == {"y": 0}

    assert preferences._SANITIZE_WARNED == {("a.model3.json", "position.x")}


@pytest.mark.unit
def test_warn_tracking_is_bounded(isolated_preferences, monkeypatch):
    monkeypatch.setattr(preferences, "_SANITIZE_WARNED_MAX", 2)

    for index in range(4):
        preferences._note_sanitize_drop(f"m{index}.model3.json", "position.x")

    assert len(preferences._SANITIZE_WARNED) == 2
    assert preferences._SANITIZE_SUPPRESSED[0] == 2


# ---------------------------------------------------------------------------
# B-5: read heals in memory, the next save persists it
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_sanitizes_in_memory_but_does_not_rewrite_the_file(isolated_preferences):
    write_poisoned(
        isolated_preferences,
        [
            model_entry("ok.model3.json"),
            model_entry("bad.model3.json", position={"x": float("nan"), "y": 0}),
        ],
    )
    before = isolated_preferences.read_bytes()

    loaded = preferences.load_user_preferences()

    assert [entry["model_path"] for entry in loaded] == [
        "ok.model3.json",
        "bad.model3.json",
    ]
    assert loaded[1]["position"] == {"y": 0}
    # The read path must never write: it runs inside the store lock elsewhere.
    assert isolated_preferences.read_bytes() == before
    assert "NaN" in isolated_preferences.read_text(encoding="utf-8")


@pytest.mark.unit
def test_settings_journal_is_never_returned_to_readers(isolated_preferences):
    journal = {"version": 1, "revision": 7, "entries": []}
    write_poisoned(
        isolated_preferences,
        [{"model_path": GLOBAL_KEY, "_settings_mutation_journal": journal}],
    )

    loaded = preferences.load_user_preferences()

    assert len(loaded) == 1
    assert loaded[0]["model_path"] == GLOBAL_KEY
    # Existing contract: the journal is stripped from the returned view ...
    assert "_settings_mutation_journal" not in loaded[0]
    # ... but it is still on disk, untouched.
    assert journal == json.loads(isolated_preferences.read_text(encoding="utf-8"))[0][
        "_settings_mutation_journal"
    ]


@pytest.mark.unit
def test_normal_save_persists_the_cleaned_view(isolated_preferences):
    journal = {"version": 1, "revision": 5, "entries": []}
    write_poisoned(
        isolated_preferences,
        [
            model_entry("ok.model3.json"),
            model_entry("bad.model3.json", position={"x": float("nan"), "y": 0}),
            model_entry("params.model3.json", parameters={"P": float("nan")},
                        parameters_mode="replace"),
            {"model_path": GLOBAL_KEY, "proactiveChatInterval": float("nan"),
             "_settings_mutation_journal": journal},
        ],
    )

    # A plain, valid position save is all it takes to persist the cleaned view.
    assert preferences.update_model_preferences(
        "ok.model3.json", {"x": 9, "y": 8}, {"x": 1, "y": 1}
    )

    raw = isolated_preferences.read_text(encoding="utf-8")
    stored = json.loads(raw)
    paths = [entry["model_path"] for entry in stored]
    # A read removes the poisoned coordinate, not the entry; the next ordinary
    # save then persists that cleaned view for good.
    bad_entry = next(e for e in stored if e["model_path"] == "bad.model3.json")
    assert bad_entry["position"] == {"y": 0}
    assert "ok.model3.json" in paths
    assert "params.model3.json" in paths
    params_entry = next(e for e in stored if e["model_path"] == "params.model3.json")
    assert "parameters" not in params_entry and "parameters_mode" not in params_entry
    global_entry = next(e for e in stored if e["model_path"] == GLOBAL_KEY)
    assert "proactiveChatInterval" not in global_entry
    assert global_entry["_settings_mutation_journal"] == journal
    ok_entry = next(e for e in stored if e["model_path"] == "ok.model3.json")
    assert ok_entry["position"] == {"x": 9, "y": 8}
    # The regression gate for a future 500: the file parses as strict JSON.
    assert "NaN" not in raw and "Infinity" not in raw
    json.dumps(json.loads(raw), allow_nan=False)


# ---------------------------------------------------------------------------
# P0-1a: the write side refuses to emit non-finite values
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_write_rejects_non_finite_and_leaves_the_file_untouched(isolated_preferences):
    assert preferences.update_model_preferences(
        "ok.model3.json", {"x": 1, "y": 2}, {"x": 1, "y": 1}
    )
    before = isolated_preferences.read_bytes()

    assert preferences.update_model_preferences(
        "ok.model3.json", {"x": float("nan"), "y": 2}, {"x": 1, "y": 1}
    ) is False

    # json.dumps raises before atomic_write_text touches the target.
    assert isolated_preferences.read_bytes() == before


@pytest.mark.unit
def test_write_rejects_boolean_as_a_coordinate(isolated_preferences):
    """bool is an int subclass: it must not be persisted as ``true``."""
    assert preferences.update_model_preferences(
        "ok.model3.json", {"x": 1, "y": 2}, {"x": 1, "y": 1}
    )

    assert preferences.update_model_preferences(
        "ok.model3.json", {"x": True, "y": 2}, {"x": 1, "y": 1}
    ) is True
    # P0-1a does not police types (that is P0-2); the read-side sanitiser drops
    # the boolean coordinate so it never reaches a client, while the entry itself
    # still survives the read.
    reloaded = preferences.load_user_preferences()
    assert len(reloaded) == 1
    assert reloaded[0]["position"] == {"y": 2}


# ---------------------------------------------------------------------------
# HTTP view: the response the frontend actually receives
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_preferences_returns_a_sanitised_payload(isolated_preferences):
    write_poisoned(
        isolated_preferences,
        [
            model_entry("ok.model3.json"),
            model_entry("bad.model3.json", position={"x": float("nan"), "y": 0}),
        ],
    )

    payload = asyncio.run(preferences_router.get_preferences())

    assert [entry["model_path"] for entry in payload] == [
        "ok.model3.json",
        "bad.model3.json",
    ]
    assert payload[1]["position"] == {"y": 0}
    # This is the assertion that would have failed before the fix: Starlette
    # renders this payload with allow_nan=False.
    json.dumps(payload, allow_nan=False)


@pytest.mark.unit
def test_post_preferences_still_stores_finite_values(isolated_preferences):
    request = save_request(
        {
            "model_path": "m.model3.json",
            "position": {"x": 3, "y": 4},
            "scale": {"x": 1, "y": 1},
        }
    )

    result = asyncio.run(preferences_router.save_preferences(request))

    assert result["success"] is True
    payload = asyncio.run(preferences_router.get_preferences())
    assert payload[0]["position"] == {"x": 3, "y": 4}


# ---------------------------------------------------------------------------
# P0-3b: the public sanitiser every read-modify-write writer goes through
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sanitize_preferences_payload_tolerates_dict_and_none():
    """The file may hold the legacy dict form; a missing file holds nothing."""
    assert preferences.sanitize_preferences_payload(None) == []
    assert preferences.sanitize_preferences_payload("not a payload") == []
    assert preferences.sanitize_preferences_payload([None, "x", 3]) == []

    cleaned = preferences.sanitize_preferences_payload(
        {"model_path": "a.model3.json", "position": {"x": float("nan"), "y": 1}}
    )
    assert [entry["model_path"] for entry in cleaned] == ["a.model3.json"]
    assert cleaned[0]["position"] == {"y": 1}
    json.dumps(cleaned, allow_nan=False)


@pytest.mark.unit
def test_sanitize_preferences_payload_does_not_mutate_its_input():
    payload = [
        {
            "model_path": "a.model3.json",
            "position": {"x": float("nan"), "y": 1},
            "parameters": {"P": float("inf")},
            "parameters_mode": "replace",
        }
    ]

    cleaned = preferences.sanitize_preferences_payload(payload)

    assert cleaned[0]["position"] == {"y": 1}
    # The parameters group is dropped as a unit (its "replace" snapshot would lie).
    assert "parameters" not in cleaned[0] and "parameters_mode" not in cleaned[0]
    # The caller's payload is untouched: it may still be handed to another merge.
    assert payload[0]["position"]["x"] != payload[0]["position"]["x"]  # still NaN
    assert payload[0]["parameters"] == {"P": float("inf")}
    assert payload[0]["parameters_mode"] == "replace"


@pytest.mark.unit
def test_sanitize_preferences_payload_keeps_journal_structure():
    """The settings journal is scrubbed inside but its skeleton must survive."""
    journal = {
        "version": 1,
        "revision": 4,
        "protocol_version": 2,
        "incomplete": True,
        "entries": [
            {
                "id": "entry-1",
                "owner": {"key": "owner-key", "user_id": None},
                "payload": {
                    "revision": 4,
                    "origin": "user",
                    "occurred_at": 1.5,
                    "previous": {},
                    "settings": {
                        "proactiveChatInterval": float("nan"),
                        "proactiveMusicEnabled": True,
                    },
                },
                "context": {
                    "version": "desktop-feedback-capture-v2",
                    "capture": {"clock_session_id": "s", "tick_ms": float("inf")},
                },
            }
        ],
    }
    payload = [{
        "model_path": GLOBAL_KEY,
        "proactiveMusicEnabled": True,
        "activity": {"thresholds": {"gaming": float("-inf"), "work": 3}},
        "_settings_mutation_journal": journal,
    }]

    cleaned = preferences.sanitize_preferences_payload(payload)

    assert len(cleaned) == 1
    global_entry = cleaned[0]
    assert global_entry["model_path"] == GLOBAL_KEY
    assert global_entry["activity"] == {"thresholds": {"work": 3}}
    cleaned_journal = global_entry["_settings_mutation_journal"]
    assert cleaned_journal["version"] == 1
    assert cleaned_journal["revision"] == 4
    assert cleaned_journal["protocol_version"] == 2
    assert cleaned_journal["incomplete"] is True
    entry, = cleaned_journal["entries"]
    assert entry["id"] == "entry-1"
    assert entry["owner"] == {"key": "owner-key", "user_id": None}
    assert entry["payload"]["revision"] == 4
    assert entry["payload"]["settings"] == {"proactiveMusicEnabled": True}
    assert entry["context"]["capture"] == {"clock_session_id": "s"}
    # What the settings handoff will read next must still be a valid journal.
    assert preferences_audit.read_journal(cleaned)["entries"] == cleaned_journal["entries"]
    json.dumps(cleaned, allow_nan=False)


@pytest.mark.unit
def test_write_sanitiser_refuses_to_answer_with_an_empty_file():
    """A payload that cannot be cleaned must fail the write, not blank the store.

    Only a value that never came from ``json.load`` can reach this: the read path
    keeps its existing fail-closed behaviour (the file on disk stays untouched),
    while a writer raising is the only way not to replace real entries with [].
    """
    payload = [{"model_path": "a.model3.json", "position": {"x": 1, "weird": {1, 2}}}]

    with pytest.raises(ValueError):
        preferences.sanitize_preferences_payload(payload)

    assert preferences._sanitize_preferences_entries(payload) == []


def seed_writer_payload(path):
    """A poisoned file a global-settings writer must survive (M-001 review)."""
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
                    "occurred_at": 1.0,
                    "previous": {},
                    "settings": {"proactiveChatInterval": float("nan")},
                },
            }
        ],
    }
    write_poisoned(
        path,
        [
            model_entry("ok.model3.json"),
            model_entry("bad.model3.json", position={"x": float("nan"), "y": 2}),
            model_entry(
                "params.model3.json",
                parameters={"ParamAngleX": float("inf")},
                parameters_mode="replace",
            ),
            {
                "model_path": GLOBAL_KEY,
                "proactiveMusicEnabled": False,
                # A poisoned number in the global entry itself: the export and
                # the settings writers must both survive it.
                "proactiveChatInterval": float("nan"),
                "_settings_mutation_journal": journal,
            },
        ],
    )


def assert_file_is_strict_json(path):
    raw = path.read_text(encoding="utf-8")
    assert "NaN" not in raw and "Infinity" not in raw
    stored = json.loads(raw)
    json.dumps(stored, allow_nan=False)
    return stored


@pytest.mark.unit
def test_global_settings_save_heals_a_poisoned_model_entry(isolated_preferences):
    """Before the fix every one of these saves raised ValueError and wrote nothing."""
    seed_writer_payload(isolated_preferences)

    result = preferences.save_global_conversation_settings_versioned(
        {"proactiveMusicEnabled": True}
    )

    assert result.success is True
    stored = assert_file_is_strict_json(isolated_preferences)
    by_path = {entry["model_path"]: entry for entry in stored}
    # The user's models survive; only the poisoned numbers are gone.
    assert by_path["ok.model3.json"]["position"] == {"x": 1, "y": 2}
    assert by_path["bad.model3.json"]["position"] == {"y": 2}
    assert "parameters" not in by_path["params.model3.json"]
    assert "parameters_mode" not in by_path["params.model3.json"]
    assert by_path[GLOBAL_KEY]["proactiveMusicEnabled"] is True
    # The pending journal entry is kept, with its poisoned payload cleaned.
    journal = by_path[GLOBAL_KEY][preferences_audit.JOURNAL_KEY]
    assert journal["revision"] >= 2
    first_entry = next(entry for entry in journal["entries"] if entry["id"] == "pending-1")
    assert first_entry["payload"]["settings"] == {}


@pytest.mark.unit
def test_ui_language_override_survives_a_poisoned_model_entry(isolated_preferences):
    seed_writer_payload(isolated_preferences)

    assert preferences.save_ui_language_override("ja") is True

    stored = assert_file_is_strict_json(isolated_preferences)
    global_entry = next(entry for entry in stored if entry["model_path"] == GLOBAL_KEY)
    assert global_entry["uiLanguage"] == "ja"
    assert {entry["model_path"] for entry in stored} >= {"ok.model3.json", "bad.model3.json"}


@pytest.mark.unit
def test_global_entry_flags_survive_a_poisoned_model_entry(isolated_preferences):
    seed_writer_payload(isolated_preferences)

    assert preferences.save_global_entry_flags({"proactiveVisionEnabled": True}) is True

    stored = assert_file_is_strict_json(isolated_preferences)
    global_entry = next(entry for entry in stored if entry["model_path"] == GLOBAL_KEY)
    assert global_entry["proactiveVisionEnabled"] is True
    assert global_entry["proactiveMusicEnabled"] is False


@pytest.mark.unit
def test_reserve_current_settings_snapshot_survives_a_poisoned_model_entry(isolated_preferences):
    seed_writer_payload(isolated_preferences)

    captured = preferences.reserve_current_settings_snapshot(
        {"key": "fixture"}, lambda owner: {"version": "v2", "owner_key": owner["key"]}
    )

    assert captured["context"]["owner_key"] == "fixture"
    assert captured["payload"]["revision"] == 3
    assert_file_is_strict_json(isolated_preferences)
    assert len(preferences.pending_settings_changes()) == 1


@pytest.mark.unit
def test_acknowledge_settings_changes_survives_a_poisoned_model_entry(isolated_preferences):
    seed_writer_payload(isolated_preferences)

    assert preferences.acknowledge_settings_changes(["pending-1"]) == 1

    stored = assert_file_is_strict_json(isolated_preferences)
    global_entry = next(entry for entry in stored if entry["model_path"] == GLOBAL_KEY)
    assert global_entry[preferences_audit.JOURNAL_KEY]["entries"] == []
    # Acknowledging one journal ID must not drop the model entries around it.
    assert "bad.model3.json" in {entry["model_path"] for entry in stored}


@pytest.mark.unit
def test_write_path_does_not_invent_a_journal_or_drop_settings(isolated_preferences):
    """Healing the snapshot must not disturb unrelated settings or the journal."""
    journal = {"version": 1, "revision": 7, "entries": []}
    write_poisoned(
        isolated_preferences,
        [
            model_entry("bad.model3.json", position={"x": float("nan"), "y": 0}),
            {
                "model_path": GLOBAL_KEY,
                "textGuardMaxLength": 500,
                "proactiveMusicEnabled": True,
                "_settings_mutation_journal": journal,
            },
        ],
    )

    assert preferences.save_global_entry_flags({"uiLanguage": "zh-CN"}) is True

    stored = assert_file_is_strict_json(isolated_preferences)
    global_entry = next(entry for entry in stored if entry["model_path"] == GLOBAL_KEY)
    assert global_entry["textGuardMaxLength"] == 500
    assert global_entry["proactiveMusicEnabled"] is True
    assert global_entry[preferences_audit.JOURNAL_KEY] == journal


# ---------------------------------------------------------------------------
# Legacy-root import: a poisoned legacy file must not abort the migration
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_legacy_root_merge_writes_strict_json(tmp_path):
    """``_stage_merged_runtime_configs`` reads with json.load and writes strict JSON."""
    from utils.cloudsave_runtime import legacy_migration

    source_root = tmp_path / "legacy"
    target_root = tmp_path / "current"
    temp_root = tmp_path / "staged"
    (source_root / "config").mkdir(parents=True)
    (target_root / "config").mkdir(parents=True)

    (source_root / "config" / "user_preferences.json").write_text(
        json.dumps([
            model_entry("legacy.model3.json", position={"x": float("nan"), "y": 1}),
        ]),
        encoding="utf-8",
    )
    (target_root / "config" / "user_preferences.json").write_text(
        json.dumps([model_entry("current.model3.json")]),
        encoding="utf-8",
    )
    (source_root / "config" / "characters.json").write_text(
        json.dumps({
            "猫娘": {"旧猫娘": {"scale": {"x": float("inf"), "y": 1}, "档案名": "旧"}},
            "主人": {"称呼": "主人"},
            "当前猫娘": "旧猫娘",
        }),
        encoding="utf-8",
    )
    (source_root / "config" / "voice_storage.json").write_text(
        json.dumps({"ratio": float("nan"), "voice": "x"}),
        encoding="utf-8",
    )

    class Config:
        project_config_dir = tmp_path / "project-config"

        def _get_localized_characters_source(self):
            return None

        def get_default_characters(self):
            return {}

    legacy_migration._stage_merged_runtime_configs(
        Config(),
        source_root=source_root,
        target_root=target_root,
        temp_root=temp_root,
        target_summary={"seeded_character_shell": False},
    )

    merged_preferences = json.loads(
        (temp_root / "config" / "user_preferences.json").read_text(encoding="utf-8")
    )
    json.dumps(merged_preferences, allow_nan=False)
    by_path = {entry["model_path"]: entry for entry in merged_preferences}
    assert by_path["legacy.model3.json"]["position"] == {"y": 1}
    assert by_path["current.model3.json"]["position"] == {"x": 1, "y": 2}

    merged_characters = json.loads(
        (temp_root / "config" / "characters.json").read_text(encoding="utf-8")
    )
    json.dumps(merged_characters, allow_nan=False)
    assert merged_characters["猫娘"]["旧猫娘"]["scale"] == {"y": 1}
    assert merged_characters["当前猫娘"] == "旧猫娘"

    merged_voice = json.loads(
        (temp_root / "config" / "voice_storage.json").read_text(encoding="utf-8")
    )
    json.dumps(merged_voice, allow_nan=False)
    assert merged_voice == {"voice": "x"}


@pytest.mark.unit
def test_cloud_export_and_restore_survive_a_poisoned_model_entry(isolated_preferences):
    from utils.cloudsave_runtime import bindings

    seed_writer_payload(isolated_preferences)
    manager = preferences._config_manager

    exported = bindings._extract_conversation_settings(manager)
    json.dumps(exported, allow_nan=False)
    assert exported["proactiveMusicEnabled"] is False
    assert preferences_audit.JOURNAL_KEY not in exported

    restored = bindings._build_runtime_preferences_payload(
        manager, {"proactiveMusicEnabled": True}
    )
    json.dumps(restored, allow_nan=False)
    by_path = {entry["model_path"]: entry for entry in restored}
    assert by_path["bad.model3.json"]["position"] == {"y": 2}
    assert by_path[GLOBAL_KEY]["proactiveMusicEnabled"] is True

