"""M-001: a poisoned ``user_preferences.json`` must never reach a reader.

Covers P0-1a (write side refuses to emit ``NaN``), P0-3 (in-memory sanitation on
read) and boundaries B-1..B-5 from ``M-001-fix-plan.md``.

The invariant under test is narrow and deliberate: this code only removes
non-finite numbers. It does not coerce them to ``0``, it does not restore an old
value, and it never rewrites the file from the read path (the file is repaired by
the next ordinary save).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request

from main_routers.config_router import preferences as preferences_router
from utils import preferences

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
def test_nan_position_drops_the_whole_entry():
    cleaned = preferences._sanitize_preferences_entries(
        [
            model_entry("ok.model3.json"),
            model_entry("bad.model3.json", position={"x": float("nan"), "y": 2}),
        ]
    )
    assert [entry["model_path"] for entry in cleaned] == ["ok.model3.json"]


@pytest.mark.unit
def test_missing_required_position_key_drops_the_whole_entry():
    cleaned = preferences._sanitize_preferences_entries(
        [model_entry("bad.model3.json", position={"x": 1})]
    )
    assert cleaned == []


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
        assert preferences.load_user_preferences() == []

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

    assert [entry["model_path"] for entry in loaded] == ["ok.model3.json"]
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
    assert "bad.model3.json" not in paths
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
    # P0-1a does not police types (that is P0-2); the read-side sanitiser does,
    # so the bad coordinate must not survive a reload.
    reloaded = preferences.load_user_preferences()
    assert reloaded == []


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

    assert [entry["model_path"] for entry in payload] == ["ok.model3.json"]
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
