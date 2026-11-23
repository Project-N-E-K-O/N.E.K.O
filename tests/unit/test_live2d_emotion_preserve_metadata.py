"""Backend tests for atomic metadata preservation in Live2D update_emotion_mapping.

Covers F0063 Option A: preserving Cubism entry metadata (FadeInTime, FadeOutTime,
Sound, custom Name), unmanaged legacy motion/expression groups, underscore-containing
group names across repeated saves, multi-file persistent groups, empty legacy
expression groups, and avoiding 400 failures on untouched legacy references.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from main_routers import live2d_router


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class _FakeRequest:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


@pytest.fixture(autouse=True)
def _clear_model_config_write_locks():
    live2d_router._MODEL_CONFIG_WRITE_LOCKS.clear()
    yield
    live2d_router._MODEL_CONFIG_WRITE_LOCKS.clear()


@pytest.fixture
def model_dir(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "user_live2d" / "m"
    path.mkdir(parents=True)
    monkeypatch.setattr(
        live2d_router, "find_model_directory", lambda _name: (str(path), "/user_live2d")
    )
    return path


@pytest.mark.unit
def test_preserves_motion_and_expression_metadata_atomically(model_dir: Path) -> None:
    model_json = model_dir / "m.model3.json"
    _write(
        model_json,
        {
            "Version": 3,
            "EmotionMapping": {
                "motions": {
                    "happy": ["motions/h1.motion3.json"],
                    "TapBody": ["motions/tap.motion3.json"],
                },
                "expressions": {
                    "happy": ["exp/smile.exp3.json"],
                    "常驻": ["exp/blush.exp3.json"],
                },
            },
            "FileReferences": {
                "Motions": {
                    "happy": [
                        {
                            "File": "motions/h1.motion3.json",
                            "FadeInTime": 0.4,
                            "FadeOutTime": 0.6,
                            "Sound": "sounds/giggle.wav",
                        }
                    ],
                    "TapBody": [
                        {
                            "File": "motions/tap.motion3.json",
                            "FadeInTime": 0.2,
                            "Sound": "sounds/tap.wav",
                        }
                    ],
                },
                "Expressions": [
                    {
                        "Name": "unprefixed_custom",
                        "File": "exp/plain.exp3.json",
                        "FadeInTime": 0.9,
                    },
                    {
                        "Name": "happy_alias",
                        "File": "exp/smile.exp3.json",
                        "FadeInTime": 0.35,
                        "FadeOutTime": 0.45,
                    },
                    {
                        "Name": "常驻_alias",
                        "File": "exp/blush.exp3.json",
                        "FadeInTime": 0.5,
                    },
                ],
            },
        },
    )

    payload = {
        "motions": {
            "TapBody": ["motions/tap.motion3.json"],
            "happy": ["motions/h1.motion3.json", "motions/h2.motion3.json"],
        },
        "expressions": {
            "常驻": ["exp/blush.exp3.json", "exp/glasses.exp3.json"],
            "happy": ["exp/smile.exp3.json"],
        },
    }
    res = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert res["success"] is True

    saved = json.loads(model_json.read_text(encoding="utf-8"))
    assert saved["EmotionMapping"] == payload
    assert saved["FileReferences"]["Motions"] == {
        "TapBody": [
            {
                "File": "motions/tap.motion3.json",
                "FadeInTime": 0.2,
                "Sound": "sounds/tap.wav",
            }
        ],
        "happy": [
            {
                "File": "motions/h1.motion3.json",
                "FadeInTime": 0.4,
                "FadeOutTime": 0.6,
                "Sound": "sounds/giggle.wav",
            },
            {"File": "motions/h2.motion3.json"},
        ],
    }
    assert saved["FileReferences"]["Expressions"] == [
        {"Name": "unprefixed_custom", "File": "exp/plain.exp3.json", "FadeInTime": 0.9},
        {"Name": "常驻_alias", "File": "exp/blush.exp3.json", "FadeInTime": 0.5},
        {"Name": "常驻_glasses", "File": "exp/glasses.exp3.json"},
        {
            "Name": "happy_alias",
            "File": "exp/smile.exp3.json",
            "FadeInTime": 0.35,
            "FadeOutTime": 0.45,
        },
    ]


@pytest.mark.unit
def test_underscore_group_names_are_idempotent_across_multiple_saves(model_dir: Path) -> None:
    model_json = model_dir / "m.model3.json"
    _write(
        model_json,
        {
            "Version": 3,
            "EmotionMapping": {
                "motions": {},
                "expressions": {
                    "custom": ["exp/c.exp3.json"],
                    "custom_group": ["exp/cg.exp3.json"],
                },
            },
            "FileReferences": {
                "Motions": {},
                "Expressions": [
                    {"Name": "custom_entry", "File": "exp/c.exp3.json", "FadeInTime": 0.1},
                    {"Name": "custom_group_entry", "File": "exp/cg.exp3.json", "FadeInTime": 0.75},
                ],
            },
        },
    )

    payload = {
        "motions": {},
        "expressions": {
            "custom": ["exp/c.exp3.json"],
            "custom_group": ["exp/cg.exp3.json"],
            "happy": ["exp/h.exp3.json"],
        },
    }

    for _ in range(3):
        res = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
        assert res["success"] is True

    saved = json.loads(model_json.read_text(encoding="utf-8"))
    assert saved["FileReferences"]["Expressions"] == [
        {"Name": "custom_entry", "File": "exp/c.exp3.json", "FadeInTime": 0.1},
        {"Name": "custom_group_entry", "File": "exp/cg.exp3.json", "FadeInTime": 0.75},
        {"Name": "happy_h", "File": "exp/h.exp3.json"},
    ]


@pytest.mark.unit
def test_clearing_group_removes_entries_while_initial_derived_clear_does_not_resurrect(model_dir: Path) -> None:
    model_json = model_dir / "m.model3.json"
    # Case 1: Existing EmotionMapping has non-empty happy; next save omits happy -> removed.
    _write(
        model_json,
        {
            "Version": 3,
            "EmotionMapping": {
                "motions": {"happy": ["motions/h1.motion3.json"]},
                "expressions": {"happy": ["exp/h1.exp3.json"], "常驻": ["exp/p1.exp3.json"]},
            },
            "FileReferences": {
                "Motions": {
                    "happy": [{"File": "motions/h1.motion3.json"}],
                    "unmanaged_tap": [{"File": "motions/tap.motion3.json", "Sound": "s.wav"}],
                },
                "Expressions": [
                    {"Name": "happy_h1", "File": "exp/h1.exp3.json"},
                    {"Name": "常驻_p1", "File": "exp/p1.exp3.json", "FadeInTime": 0.5},
                ],
            },
        },
    )
    res = asyncio.run(
        live2d_router.update_emotion_mapping(
            "m",
            _FakeRequest({"motions": {}, "expressions": {"常驻": ["exp/p1.exp3.json"]}}),
        )
    )
    assert res["success"] is True
    saved = json.loads(model_json.read_text(encoding="utf-8"))
    assert saved["FileReferences"]["Expressions"] == [
        {"Name": "常驻_p1", "File": "exp/p1.exp3.json", "FadeInTime": 0.5}
    ]
    assert saved["FileReferences"]["Motions"] == {
        "unmanaged_tap": [{"File": "motions/tap.motion3.json", "Sound": "s.wav"}]
    }

    # Case 2: No prior EmotionMapping; caller clears Idle derived by GET -> must NOT resurrect Idle.
    _write(
        model_json,
        {
            "Version": 3,
            "FileReferences": {
                "Motions": {"Idle": [{"File": "motions/idle.motion3.json"}]},
                "Expressions": [],
            },
        },
    )
    res2 = asyncio.run(
        live2d_router.update_emotion_mapping(
            "m",
            _FakeRequest({"motions": {"happy": ["motions/h2.motion3.json"]}, "expressions": {}}),
        )
    )
    assert res2["success"] is True
    saved2 = json.loads(model_json.read_text(encoding="utf-8"))
    assert saved2["FileReferences"]["Motions"] == {"happy": [{"File": "motions/h2.motion3.json"}]}


@pytest.mark.unit
def test_empty_legacy_expression_group_does_not_wipe_existing_entries_and_untouched_bad_refs_do_not_400(
    model_dir: Path,
) -> None:
    model_json = model_dir / "m.model3.json"
    _write(
        model_json,
        {
            "Version": 3,
            "EmotionMapping": {
                "motions": {"happy": ["motions/h.motion3.json"]},
                "expressions": {"常驻": [], "legacy_empty": []},
            },
            "FileReferences": {
                "Motions": {
                    "TapBody": [{"File": "motions/tap.motion3.json", "Sound": "sounds/tap.wav"}],
                    "happy": [{"File": "motions/h.motion3.json"}],
                },
                "Expressions": [
                    {"Name": "常驻_stay", "File": "exp/stay.exp3.json", "FadeInTime": 0.4},
                    {"Name": "legacy_empty_keep", "File": "exp/keep.exp3.json", "FadeInTime": 0.3},
                    {"Name": "broken_untouched", "File": "../outside.exp3.json"},
                    {"Name": "noUnderscore", "File": "exp/plain.exp3.json"},
                ],
            },
        },
    )

    payload = {
        "motions": {"happy": ["motions/h.motion3.json"]},
        "expressions": {"happy": ["exp/new_happy.exp3.json"], "legacy_empty": []},
    }
    res = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert res["success"] is True

    saved = json.loads(model_json.read_text(encoding="utf-8"))
    assert saved["FileReferences"]["Motions"] == {
        "happy": [{"File": "motions/h.motion3.json"}],
        "TapBody": [{"File": "motions/tap.motion3.json", "Sound": "sounds/tap.wav"}],
    }
    assert saved["FileReferences"]["Expressions"] == [
        {"Name": "常驻_stay", "File": "exp/stay.exp3.json", "FadeInTime": 0.4},
        {"Name": "legacy_empty_keep", "File": "exp/keep.exp3.json", "FadeInTime": 0.3},
        {"Name": "broken_untouched", "File": "../outside.exp3.json"},
        {"Name": "noUnderscore", "File": "exp/plain.exp3.json"},
        {"Name": "happy_new_happy", "File": "exp/new_happy.exp3.json"},
    ]


@pytest.mark.unit
def test_empty_known_group_prefix_protects_entries_from_shorter_managed_prefix(model_dir: Path) -> None:
    model_json = model_dir / "m.model3.json"
    _write(
        model_json,
        {
            "Version": 3,
            "EmotionMapping": {
                "motions": {},
                "expressions": {
                    "custom": ["exp/a.exp3.json"],
                    "custom_alt": [],
                },
            },
            "FileReferences": {
                "Motions": {},
                "Expressions": [
                    {"Name": "custom_a", "File": "exp/a.exp3.json", "FadeInTime": 0.2},
                    {"Name": "custom_alt_keep", "File": "exp/keep.exp3.json", "FadeInTime": 0.4},
                ],
            },
        },
    )

    payload = {
        "motions": {},
        "expressions": {"custom": ["exp/a.exp3.json"]},
    }
    res = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert res["success"] is True

    saved = json.loads(model_json.read_text(encoding="utf-8"))
    assert saved["FileReferences"]["Expressions"] == [
        {"Name": "custom_alt_keep", "File": "exp/keep.exp3.json", "FadeInTime": 0.4},
        {"Name": "custom_a", "File": "exp/a.exp3.json", "FadeInTime": 0.2},
    ]

    res2 = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert res2["success"] is True

    saved_second = json.loads(model_json.read_text(encoding="utf-8"))
    assert saved_second["FileReferences"]["Expressions"] == [
        {"Name": "custom_alt_keep", "File": "exp/keep.exp3.json", "FadeInTime": 0.4},
        {"Name": "custom_a", "File": "exp/a.exp3.json", "FadeInTime": 0.2},
    ]


@pytest.mark.unit
def test_newly_added_longer_prefix_does_not_hijack_existing_shorter_group_entry(model_dir: Path) -> None:
    model_json = model_dir / "m.model3.json"
    _write(
        model_json,
        {
            "Version": 3,
            "EmotionMapping": {
                "motions": {},
                "expressions": {
                    "custom": ["exp/group_smile.exp3.json"],
                },
            },
            "FileReferences": {
                "Motions": {},
                "Expressions": [
                    {"Name": "custom_group_smile", "File": "exp/group_smile.exp3.json", "FadeInTime": 0.7},
                ],
            },
        },
    )

    payload = {
        "motions": {},
        "expressions": {
            "custom": ["exp/group_smile.exp3.json"],
            "custom_group": ["exp/new.exp3.json"],
        },
    }
    res = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert res["success"] is True

    saved = json.loads(model_json.read_text(encoding="utf-8"))
    assert saved["FileReferences"]["Expressions"] == [
        {"Name": "custom_group_smile", "File": "exp/group_smile.exp3.json", "FadeInTime": 0.7},
        {"Name": "custom_group_new", "File": "exp/new.exp3.json"},
    ]


@pytest.mark.unit
def test_initial_save_without_prior_mapping_allows_explicit_empty_group_to_clear(model_dir: Path) -> None:
    model_json = model_dir / "m.model3.json"
    _write(
        model_json,
        {
            "Version": 3,
            "FileReferences": {
                "Motions": {},
                "Expressions": [
                    {"Name": "常驻_blush", "File": "exp/blush.exp3.json"},
                    {"Name": "happy_smile", "File": "exp/smile.exp3.json"},
                ],
            },
        },
    )

    payload = {
        "motions": {},
        "expressions": {
            "常驻": [],
            "happy": ["exp/smile.exp3.json"],
        },
    }
    res = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert res["success"] is True

    saved = json.loads(model_json.read_text(encoding="utf-8"))
    assert saved["FileReferences"]["Expressions"] == [
        {"Name": "happy_smile", "File": "exp/smile.exp3.json"},
    ]


@pytest.mark.unit
def test_unmatched_raw_entries_in_existing_group_survive_unrelated_save(model_dir: Path) -> None:
    """Keep untracked raw expressions under an existing group prefix untouched."""
    model_json = model_dir / "m.model3.json"
    _write(
        model_json,
        {
            "Version": 3,
            "EmotionMapping": {
                "motions": {},
                "expressions": {
                    "custom": ["exp/a.exp3.json"],
                },
            },
            "FileReferences": {
                "Motions": {},
                "Expressions": [
                    {"Name": "custom_a", "File": "exp/a.exp3.json", "FadeInTime": 0.2},
                    {"Name": "custom_extra", "File": "exp/extra.exp3.json", "FadeInTime": 0.6},
                ],
            },
        },
    )

    payload = {
        "motions": {},
        "expressions": {
            "custom": ["exp/a.exp3.json"],
            "happy": ["exp/h.exp3.json"],
        },
    }
    res = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert res["success"] is True

    saved = json.loads(model_json.read_text(encoding="utf-8"))
    assert saved["FileReferences"]["Expressions"] == [
        {"Name": "custom_extra", "File": "exp/extra.exp3.json", "FadeInTime": 0.6},
        {"Name": "custom_a", "File": "exp/a.exp3.json", "FadeInTime": 0.2},
        {"Name": "happy_h", "File": "exp/h.exp3.json"},
    ]


@pytest.mark.unit
def test_newly_generated_expression_names_avoid_preserved_and_duplicate_collisions(model_dir: Path) -> None:
    """Disambiguate newly generated expression names against preserved entries and duplicate basenames."""
    model_json = model_dir / "m.model3.json"
    _write(
        model_json,
        {
            "Version": 3,
            "EmotionMapping": {
                "motions": {},
                "expressions": {
                    "custom": ["exp/a.exp3.json"],
                },
            },
            "FileReferences": {
                "Motions": {},
                "Expressions": [
                    {"Name": "custom_a", "File": "exp/a.exp3.json", "FadeInTime": 0.2},
                    {"Name": "custom_smile", "File": "exp/legacy_smile.exp3.json", "FadeInTime": 0.6},
                ],
            },
        },
    )

    payload = {
        "motions": {},
        "expressions": {
            "custom": [
                "exp/a.exp3.json",
                "dir1/smile.exp3.json",
                "dir2/smile.exp3.json",
            ],
        },
    }
    res1 = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert res1["success"] is True

    expected = [
        {"Name": "custom_smile", "File": "exp/legacy_smile.exp3.json", "FadeInTime": 0.6},
        {"Name": "custom_a", "File": "exp/a.exp3.json", "FadeInTime": 0.2},
        {"Name": "custom_smile_2", "File": "dir1/smile.exp3.json"},
        {"Name": "custom_smile_3", "File": "dir2/smile.exp3.json"},
    ]
    saved1 = json.loads(model_json.read_text(encoding="utf-8"))
    assert saved1["FileReferences"]["Expressions"] == expected
    names1 = [entry["Name"] for entry in saved1["FileReferences"]["Expressions"]]
    assert len(names1) == len(set(names1)) == 4

    res2 = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert res2["success"] is True

    saved2 = json.loads(model_json.read_text(encoding="utf-8"))
    assert saved2["FileReferences"]["Expressions"] == expected
