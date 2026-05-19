from __future__ import annotations

import json
from pathlib import Path

import pytest

from _galgame_character_data import (
    CHARACTER_DATA_DIR,
    MURASAME_PROFILE,
    SENREN_BANKA_DATA,
)
from plugin.plugins.galgame_plugin.character_profile import (
    L0_MAX_TOKENS,
    L1_MAX_TOKENS,
    L2_MAX_TOKENS,
    MAX_PROFILE_SIZE_BYTES,
    CharacterProfileManager,
)
from plugin.plugins.galgame_plugin.context_tokens import count_tokens_heuristic


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


VALID_PRESET: dict[str, object] = {
    "game_id": "demo_game",
    "last_updated": "2026-05-18",
    "characters": {
        "雪乃": {
            "identity": "学生会副会长，头脑冷静的眼镜女孩",
            "appearance": "长直黑发，眼镜，校服整齐",
            "character_voice": {
                "core_traits": [
                    {
                        "trait": "理性冷静",
                        "speech_effect": "用敬语和数据说话，少用感叹词",
                    },
                    {
                        "trait": "偶尔流露温柔",
                        "speech_effect": "语气放缓，停顿变多",
                    },
                ],
                "verbal_tics": ["以…为基准"],
                "first_person_pronoun": "私",
            },
            "relationships": {
                "主角": "学生会同事，关系正在变化",
                "结衣": "好友兼对手",
            },
            "background": ["学生会副会长", "成绩第一"],
            "key_events": [
                {"event": "主角邀请放学一起回家", "significance": "关系起点"}
            ],
        },
        "结衣": {
            "identity": "活泼开朗的同班同学",
            "character_voice": {
                "core_traits": [
                    {"trait": "外向热情", "speech_effect": "句尾常带！，语速快"}
                ],
                "verbal_tics": ["やっはろー"],
                "first_person_pronoun": "あたし",
            },
        },
    },
}


@pytest.fixture
def manager(tmp_path: Path) -> CharacterProfileManager:
    return CharacterProfileManager(data_dir=tmp_path)


def _write(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Preset JSON sanity (kept from scaffold)
# ---------------------------------------------------------------------------


def test_senren_banka_character_data_has_required_shape() -> None:
    payload = SENREN_BANKA_DATA

    assert payload["game_id"] == "senren_banka"
    assert payload["last_updated"] == "2026-05-18"
    assert "叢雨" in payload["characters"]
    murasame = payload["characters"]["叢雨"]
    assert murasame["identity"]
    assert murasame["character_voice"]["core_traits"]
    assert murasame["character_voice"]["first_person_pronoun"] == "わらわ"


# ---------------------------------------------------------------------------
# Loading & merging
# ---------------------------------------------------------------------------


def test_load_game_profiles_missing_files_returns_empty(
    manager: CharacterProfileManager,
) -> None:
    result = manager.load_game_profiles("nonexistent_game")
    assert result["profiles"] == {}
    assert result["preset_loaded"] is False
    assert result["user_loaded"] is False
    assert any("file not found" in err for err in result["errors"])


def test_load_game_profiles_preset_only(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    _write(tmp_path / "demo_game.json", VALID_PRESET)
    result = manager.load_game_profiles("demo_game")

    assert result["preset_loaded"] is True
    assert result["user_loaded"] is False
    assert set(result["profiles"].keys()) == {"雪乃", "结衣"}
    assert result["version"] == "2026-05-18"
    assert result["errors"] == []


def test_load_game_profiles_user_overrides_preset(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    _write(tmp_path / "demo_game.json", VALID_PRESET)
    user_payload = {
        "game_id": "demo_game",
        "last_updated": "2026-05-20",
        "characters": {
            "雪乃": {
                "identity": "USER VERSION 雪乃",
                "character_voice": {
                    "core_traits": [
                        {"trait": "改造性格", "speech_effect": "改造说话方式"}
                    ],
                    "verbal_tics": ["新口癖"],
                    "first_person_pronoun": "わたし",
                },
            },
            "新角色": {
                "identity": "用户添加的角色",
                "character_voice": {
                    "core_traits": [
                        {"trait": "用户性格", "speech_effect": "用户语调"}
                    ]
                },
            },
        },
    }
    _write(tmp_path / "demo_game.user.json", user_payload)

    result = manager.load_game_profiles("demo_game")

    assert result["user_loaded"] is True
    assert result["preset_loaded"] is True
    assert set(result["profiles"].keys()) == {"雪乃", "结衣", "新角色"}
    assert result["profiles"]["雪乃"]["identity"] == "USER VERSION 雪乃"
    assert result["profiles"]["结衣"]["identity"] == "活泼开朗的同班同学"
    assert result["version"] == "2026-05-20"


def test_load_game_profiles_user_only_without_preset(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    user_payload = {
        "game_id": "demo_game",
        "last_updated": "2026-05-20",
        "characters": {
            "雪乃": {
                "identity": "user-only profile",
                "character_voice": {
                    "core_traits": [
                        {"trait": "自定义性格", "speech_effect": "自定义语调"}
                    ]
                },
            }
        },
    }
    _write(tmp_path / "demo_game.user.json", user_payload)

    result = manager.load_game_profiles("demo_game")

    assert result["preset_loaded"] is False
    assert result["user_loaded"] is True
    assert result["errors"] == []
    assert set(result["profiles"].keys()) == {"雪乃"}
    assert result["profiles"]["雪乃"]["identity"] == "user-only profile"
    assert result["version"] == "2026-05-20"


def test_load_game_profiles_broken_user_falls_back_to_preset(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    _write(tmp_path / "demo_game.json", VALID_PRESET)
    (tmp_path / "demo_game.user.json").write_text("{not json", encoding="utf-8")

    result = manager.load_game_profiles("demo_game")

    assert result["preset_loaded"] is True
    assert result["user_loaded"] is False
    assert set(result["profiles"].keys()) == {"雪乃", "结衣"}
    assert any("user JSON ignored" in w for w in result["warnings"])


def test_load_game_profiles_user_only_with_empty_preset_placeholder(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    _write(tmp_path / "demo_game.json", {"game_id": "demo_game", "characters": {}})
    user_payload = {
        "game_id": "demo_game",
        "last_updated": "2026-05-20",
        "characters": {
            "自定义角色": {
                "identity": "用户导入的自定义角色",
                "character_voice": {
                    "core_traits": [
                        {"trait": "谨慎", "speech_effect": "先确认事实再回答"}
                    ],
                },
            },
        },
    }
    _write(tmp_path / "demo_game.user.json", user_payload)

    result = manager.load_game_profiles("demo_game")

    assert result["preset_loaded"] is False
    assert result["user_loaded"] is True
    assert result["errors"] == []
    assert set(result["profiles"].keys()) == {"自定义角色"}
    assert result["version"] == "2026-05-20"
    assert any("preset JSON ignored" in w for w in result["warnings"])


def test_load_game_profiles_oversize_rejected(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    bloated = dict(VALID_PRESET)
    bloated["padding"] = "x" * (MAX_PROFILE_SIZE_BYTES + 10)
    _write(tmp_path / "demo_game.json", bloated)

    result = manager.load_game_profiles("demo_game")

    assert result["profiles"] == {}
    assert any("超出大小上限" in err for err in result["errors"])


def test_load_game_profiles_cached_on_second_call(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    _write(tmp_path / "demo_game.json", VALID_PRESET)
    first = manager.load_game_profiles("demo_game")
    # Mutate file; cache should ignore until invalidate
    _write(tmp_path / "demo_game.json", {**VALID_PRESET, "characters": {}})
    second = manager.load_game_profiles("demo_game")
    assert second["profiles"].keys() == first["profiles"].keys()

    manager.invalidate("demo_game")
    third = manager.load_game_profiles("demo_game")
    # After mutation+invalidate the new empty payload surfaces as an error
    assert third["preset_loaded"] is False


def test_resolve_profile_match_prefers_exact_game_id(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    _write(tmp_path / "demo_game.json", VALID_PRESET)

    match = manager.resolve_profile_match([{"game_id": "demo_game"}])

    assert match is not None
    assert match.game_id == "demo_game"
    assert match.reason == "exact_game_id"


def test_resolve_profile_match_uses_alias_and_window_title(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    payload = {
        **VALID_PRESET,
        "aliases": ["千恋万花"],
        "match": {
            "process_names": ["SenrenBanka.exe"],
            "window_title_contains": ["千恋＊万花"],
        },
    }
    _write(tmp_path / "senren_banka.json", payload)

    alias_match = manager.resolve_profile_match([{"game_id": "千恋万花"}])
    window_match = manager.resolve_profile_match(
        [{"window_title": "千恋＊万花 - Steam"}]
    )
    process_match = manager.resolve_profile_match(
        [{"process_name": "SenrenBanka.exe"}]
    )

    assert alias_match is not None
    assert alias_match.game_id == "senren_banka"
    assert alias_match.reason == "alias"
    assert window_match is not None
    assert window_match.game_id == "senren_banka"
    assert window_match.reason == "window_title_contains"
    assert process_match is not None
    assert process_match.game_id == "senren_banka"
    assert process_match.reason == "process_name"


def test_resolve_profile_match_includes_user_only_profile(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    _write(
        tmp_path / "demo_game.user.json",
        {
            **VALID_PRESET,
            "game_id": "demo_game",
            "aliases": ["用户导入游戏"],
        },
    )

    exact_match = manager.resolve_profile_match([{"game_id": "demo_game"}])
    alias_match = manager.resolve_profile_match([{"game_title": "用户导入游戏"}])

    assert exact_match is not None
    assert exact_match.game_id == "demo_game"
    assert exact_match.reason == "exact_game_id"
    assert alias_match is not None
    assert alias_match.game_id == "demo_game"
    assert alias_match.reason == "alias"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_missing_required_field_is_error(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    payload = {
        "game_id": "demo",
        "characters": {
            "破角色": {
                # missing "identity"
                "character_voice": {
                    "core_traits": [{"trait": "x", "speech_effect": "y"}]
                }
            }
        },
    }
    _write(tmp_path / "demo.json", payload)
    result = manager.load_game_profiles("demo")
    assert result["preset_loaded"] is False
    assert any("missing required" in err for err in result["errors"])


def test_core_trait_missing_speech_effect_is_error(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    payload = {
        "game_id": "demo",
        "characters": {
            "角色": {
                "identity": "test",
                "character_voice": {"core_traits": [{"trait": "孤高"}]},
            }
        },
    }
    _write(tmp_path / "demo.json", payload)
    result = manager.load_game_profiles("demo")
    assert any(
        "speech_effect" in err and "不能为空" in err for err in result["errors"]
    )


def test_user_json_soft_consistency_warning(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    _write(tmp_path / "demo.json", VALID_PRESET)
    user_payload = {
        "game_id": "demo",
        "characters": {
            "雪乃": {
                "identity": "user",
                "character_voice": {
                    "core_traits": [
                        {"trait": "暴躁", "speech_effect": "轻声细语"}
                    ]
                },
            }
        },
    }
    _write(tmp_path / "demo.user.json", user_payload)
    result = manager.load_game_profiles("demo")
    assert result["user_loaded"] is True
    assert any("请确认" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Scene queries
# ---------------------------------------------------------------------------


def test_get_profiles_for_scene_filters_to_named_characters() -> None:
    all_profiles = {
        "雪乃": {"identity": "x", "character_voice": {"core_traits": []}},
        "结衣": {"identity": "y", "character_voice": {"core_traits": []}},
        "未出场": {"identity": "z", "character_voice": {"core_traits": []}},
    }
    out = CharacterProfileManager.get_profiles_for_scene(
        all_profiles, ["雪乃", "雪乃", "", "结衣", "缺失角色"]
    )
    assert list(out.keys()) == ["雪乃", "结衣"]


def test_get_profiles_for_scene_empty_inputs() -> None:
    assert CharacterProfileManager.get_profiles_for_scene({}, ["x"]) == {}
    assert (
        CharacterProfileManager.get_profiles_for_scene({"x": {}}, []) == {}
    )


# ---------------------------------------------------------------------------
# Layered rendering
# ---------------------------------------------------------------------------


def _murasame_profile() -> dict[str, object]:
    return MURASAME_PROFILE


def test_l0_within_budget_and_includes_name() -> None:
    manager = CharacterProfileManager(data_dir=CHARACTER_DATA_DIR)
    text = manager.build_character_context_l0("叢雨", _murasame_profile())
    assert "叢雨" in text
    assert count_tokens_heuristic(text) <= L0_MAX_TOKENS


def test_l1_within_budget_and_includes_speech_effect() -> None:
    manager = CharacterProfileManager(data_dir=CHARACTER_DATA_DIR)
    text = manager.build_character_context_l1(
        "叢雨",
        _murasame_profile(),
        runtime_state={"current_emotion": "慌乱嘴硬"},
    )
    assert "叢雨" in text
    assert "→" in text  # trait → speech mapping
    assert "慌乱嘴硬" in text
    assert count_tokens_heuristic(text) <= L1_MAX_TOKENS


def test_l2_within_budget_and_richer_than_l1() -> None:
    manager = CharacterProfileManager(data_dir=CHARACTER_DATA_DIR)
    profile = _murasame_profile()
    l1 = manager.build_character_context_l1("叢雨", profile)
    l2 = manager.build_character_context_l2("叢雨", profile)
    assert len(l2) >= len(l1)
    assert count_tokens_heuristic(l2) <= L2_MAX_TOKENS


def test_build_character_push_payload_orders_and_skips_unknown() -> None:
    manager = CharacterProfileManager(data_dir=CHARACTER_DATA_DIR)
    profiles = {"叢雨": _murasame_profile()}
    payload = manager.build_character_push_payload(
        ["叢雨", "未出场", "叢雨"],
        profiles,
        runtime_states={"叢雨": {"current_emotion": "试探"}},
        level="L0",
        fixed_character="叢雨",
    )
    assert payload["format"] == "character_payload"
    assert payload["level"] == "L0"
    assert payload["fixed_character"] == "叢雨"
    assert list(payload["characters"].keys()) == ["叢雨"]


def test_build_character_push_payload_unknown_level_defaults_to_l1() -> None:
    manager = CharacterProfileManager(data_dir=CHARACTER_DATA_DIR)
    profiles = {"叢雨": _murasame_profile()}
    payload = manager.build_character_push_payload(
        ["叢雨"], profiles, runtime_states={}, level="weird"
    )
    assert payload["level"] == "WEIRD"
    # L1 default has speech-effect arrows
    assert "→" in payload["characters"]["叢雨"]


# ---------------------------------------------------------------------------
# Runtime state derivation
# ---------------------------------------------------------------------------


def test_init_runtime_state_from_profile_picks_first_trait_as_emotion() -> None:
    manager = CharacterProfileManager(data_dir=CHARACTER_DATA_DIR)
    state = manager.init_runtime_state_from_profile("叢雨", _murasame_profile())
    assert state["arc_stage"] == "初始"
    assert state["plot_involvement"] == "主要角色"
    assert state["current_emotion"]
    assert state["updated_at_seq"] == 0


def test_init_runtime_state_minor_character() -> None:
    manager = CharacterProfileManager(data_dir=CHARACTER_DATA_DIR)
    minor = {
        "identity": "minor",
        "character_voice": {
            "core_traits": [{"trait": "background", "speech_effect": "few lines"}]
        },
    }
    state = manager.init_runtime_state_from_profile("路人", minor)
    assert state["plot_involvement"] == "次要角色"
    assert state["relationship_status"] == ""


# ---------------------------------------------------------------------------
# Importing user JSON
# ---------------------------------------------------------------------------


def test_import_user_profiles_writes_user_json(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    source = tmp_path / "incoming.json"
    user_payload = {
        "game_id": "demo_game",
        "last_updated": "2026-05-20",
        "characters": {
            "雪乃": {
                "identity": "imported",
                "character_voice": {
                    "core_traits": [
                        {"trait": "import 性格", "speech_effect": "import 语调"}
                    ]
                },
            }
        },
    }
    _write(source, user_payload)

    result = manager.import_user_profiles("demo_game", source)
    assert result.ok is True
    assert result.profile_count == 1

    target = tmp_path / "demo_game.user.json"
    assert target.exists()
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["characters"]["雪乃"]["identity"] == "imported"


def test_import_user_profiles_invalidates_metadata_for_user_only_match(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    assert manager.resolve_profile_match([{"game_id": "demo_game"}]) is None
    source = tmp_path / "incoming.json"
    _write(
        source,
        {
            "game_id": "demo_game",
            "last_updated": "2026-05-20",
            "characters": {
                "雪乃": {
                    "identity": "imported",
                    "character_voice": {
                        "core_traits": [
                            {"trait": "import 性格", "speech_effect": "import 语调"}
                        ]
                    },
                }
            },
        },
    )

    result = manager.import_user_profiles("demo_game", source)
    match = manager.resolve_profile_match([{"game_id": "demo_game"}])

    assert result.ok is True
    assert match is not None
    assert match.game_id == "demo_game"


def test_import_user_profiles_rejects_invalid_payload(
    manager: CharacterProfileManager, tmp_path: Path
) -> None:
    source = tmp_path / "broken.json"
    _write(source, {"game_id": "demo", "characters": {"x": {}}})

    result = manager.import_user_profiles("demo", source)
    assert result.ok is False
    assert not (tmp_path / "demo.user.json").exists()
    assert result.errors


@pytest.mark.parametrize("game_id", ["../evil", "a/b"])
def test_profile_paths_reject_nested_game_ids(
    manager: CharacterProfileManager,
    tmp_path: Path,
    game_id: str,
) -> None:
    source = tmp_path / "incoming.json"
    _write(source, VALID_PRESET)

    load_result = manager.load_game_profiles(game_id)
    import_result = manager.import_user_profiles(game_id, source)

    assert load_result["profiles"] == {}
    assert load_result["errors"]
    assert import_result.ok is False
    assert import_result.errors
    assert not (tmp_path / "evil.user.json").exists()
    assert not (tmp_path / "a/b.user.json").exists()
