from copy import deepcopy

import pytest

from services.theater.name_projection import replace_names
from services.theater.numeric_v2 import NumericV2CompileError, NumericV2Compiler
from services.theater.numeric_v2_cast import NumericV2CastProjection
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


def named_story():
    story = numeric_v2_story(player_address_known=False)
    story["intro"].update({
        "player_name": "小明，二号",
        "catgirl_name": "小岚",
        "player_identity": "小明，二号，回乡的故人。",
        "catgirl_identity": "小岚，经营花店。",
        "background": "小岚把信交给小明，二号。",
    })
    return story


def test_explicit_names_preserve_full_nickname_and_undisclosed_state():
    story = named_story()
    original = deepcopy(story)
    NumericV2Compiler().compile_v2_2(story)
    cast = NumericV2CastProjection.from_story(story, player_name="你", catgirl_name="霜月")
    assert cast.intro(story)["background"] == "霜月把信交给你。"
    assert story == original
    assert story["initial_state"]["player_address_known"] is False


def test_explicit_names_rebind_to_new_runtime_names_after_disclosure():
    cast = NumericV2CastProjection.from_story(named_story(), player_name="阿晨", catgirl_name="霜月")
    assert cast.text("小岚向小明，二号道谢。") == "霜月向阿晨道谢。"


@pytest.mark.parametrize("change", [
    {"player_name": ""},
    {"player_name": 123},
    {"player_name": "另一个人"},
    {"player_name": "小岚", "player_identity": "小岚，回乡的故人。"},
])
def test_invalid_explicit_names_are_rejected(change):
    story = named_story()
    story["intro"].update(change)
    with pytest.raises(NumericV2CompileError):
        NumericV2Compiler().compile_v2_2(story)


def test_incomplete_explicit_name_pair_is_rejected():
    story = named_story()
    del story["intro"]["catgirl_name"]
    with pytest.raises(NumericV2CompileError):
        NumericV2Compiler().compile_v2_2(story)


def test_unchanged_longer_name_protects_itself_from_shorter_name_replacement():
    assert replace_names("小明向小明月道谢。", [("小明", "阿晨"), ("小明月", "小明月")]) == "阿晨向小明月道谢。"
    assert replace_names("林舟向小岚道谢。", [("林舟", "小岚"), ("小岚", "霜月")]) == "小岚向霜月道谢。"


@pytest.mark.parametrize("name", ["player", "semantic", "turn", "required"])
def test_cast_replaces_prose_without_rewriting_protocol_values(name):
    story = named_story()
    story["intro"].update(player_name=name, player_identity=f"{name}，故人。")
    cast = NumericV2CastProjection.from_story(story, player_name="阿晨", catgirl_name="霜月")
    payload = {"id": name, "owner": "player", "evidence": {"mode": "semantic"},
               "acting_contract": {"dialogue_policy": "required"}, "delivery": {
        "source_ids": [name], "timing": "turn", "description": f"{name}向小岚道谢。",
    }}
    result = cast.value(payload)
    assert result == {"id": name, "owner": "player", "evidence": {"mode": "semantic"},
                      "acting_contract": {"dialogue_policy": "required"}, "delivery": {
        "source_ids": [name], "timing": "turn", "description": "阿晨向霜月道谢。",
    }}


@pytest.mark.parametrize("known", [False, True])
def test_actor_receives_current_names_with_disclosure_gate(known):
    from main_routers.numeric_theater_router import _surface_player_name
    from services.theater.numeric_v2_actor import _turn_messages
    from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2

    story = named_story()
    story["nodes"][0]["story_beat"]["summary"] = "小岚向小明，二号出示旧信。"
    story["nodes"][0]["story_beat"]["opening_scene"] = "小岚向小明，二号出示旧信。"
    engine = NumericV2Engine.from_mapping(story)
    binding = {"catgirl_id": "catgirl:test", "catgirl_name": "霜月", "player_address": "阿晨"}
    session = engine.create_session(session_id="name_test", catgirl_binding=binding,
        opening_performance={"performance": "（抬眼）你好。", "suggested_inputs": []})
    outcome = engine.resolve_turn(session, TurnRequestV2("names", 0, "我想看那封信。"), (), scene_complete=False)
    messages = _turn_messages(engine, session, outcome, "我想看那封信。", "安静克制。",
        "霜月", _surface_player_name(binding, known=known), player_address_known=known)
    combined = "\n".join(message.content for message in messages)
    assert "小明，二号" not in combined
    assert "小岚" not in combined
    assert "霜月" in combined
    assert ("阿晨" in combined) is known


class _NameConfig:
    def load_characters(self):
        return {"当前猫娘": "霜月", "主人": {"昵称": "小明，二号", "档案名": "档案旧名"},
                "猫娘": {"霜月": {"人格": "测试人格", "_reserved": {"character_id": "character_" + "1" * 32}}}}


def test_authoring_names_use_current_card_and_master_nickname_only():
    from services.theater.numeric_v2_identity import numeric_v2_authoring_names
    assert numeric_v2_authoring_names(_NameConfig()) == {"player_name": "小明，二号", "catgirl_name": "霜月"}


def test_authoring_names_cli_returns_only_names(monkeypatch, capsys):
    import json
    from scripts import validate_numeric_v2_story as cli
    from utils import config_manager

    monkeypatch.setattr(config_manager, "ConfigManager", _NameConfig)
    monkeypatch.setattr(cli.sys, "argv", ["validate_numeric_v2_story.py", "--authoring-names"])
    monkeypatch.setattr(cli, "_package_root", lambda: pytest.fail("name lookup must not install a package"))
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result == {"success": True, "data": {"player_name": "小明，二号", "catgirl_name": "霜月"}}


def test_explicit_state_subjects_follow_role_binding():
    story = named_story()
    state = {"catgirl_state": "小岚坐在桌旁。", "player_state": "小明，二号站在门旁。",
             "environment_state": "环境安静。", "continuity_from_previous": [], "scene_boundaries": []}
    story["nodes"][0]["story_beat"]["character_state"] = state
    NumericV2Compiler().compile_v2_2(story)
    state["player_state"] = "小岚站在门旁。"
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert any(issue.code == "character_state_subject_invalid" for issue in caught.value.issues)
