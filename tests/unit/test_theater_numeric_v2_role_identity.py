"""Application names bind story slots without assigning genders or inventing names."""

from copy import deepcopy
from dataclasses import replace

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_cast import NumericV2CastProjection
from tests.unit.test_theater_numeric_v2_entry_trigger_order import CASES, _case
from tests.utils.theater_prompt import prompt_payload


class Manager:
    def __init__(self, characters):
        self.characters, self.reads = characters, 0

    def load_characters(self):
        self.reads += 1
        if isinstance(self.characters, Exception): raise self.characters
        return deepcopy(self.characters)


@pytest.mark.parametrize("name,gender", [("维恩", "男性"), ("澄", "非二元性别")])
def test_opening_identity_snapshot_retains_explicit_story_gender_and_unknown_user(name, gender):
    engine, session, _, _ = _case(CASES[0])
    engine.story["intro"].update(catgirl_name="作者角色", player_name="作者玩家",
        catgirl_identity=f"作者角色，{gender}的档案管理员。", player_identity="作者玩家，前来查阅资料的人，性别未说明。")
    cast = NumericV2CastProjection.from_story(engine.story, player_name="玩家", catgirl_name=name)
    messages = actor._opening_messages(engine, "性格: 沉着、喜欢解释原因。", name, "你", False)
    data = prompt_payload(messages[1].content)
    snapshot = {"name": data["character_name"], "story_identity": data["acting_context"]["story_identity"],
        "player_identity": data["story_context"]["player_identity"], "role_pair": cast.text("男女主人公")}
    assert snapshot == {"name": name, "story_identity": f"{name}，{gender}的档案管理员。",
        "player_identity": "玩家，前来查阅资料的人，性别未说明。", "role_pair": f"玩家和{name}"}
    assert "留下男主可以自然回应的话头" not in messages[1].content
    assert "不能从catgirl或player槽位推断性别" in messages[0].content


def test_missing_cast_name_is_only_a_role_reference_and_keeps_legacy_alias_projection():
    story = {"intro": {"catgirl_identity": "女主，观测员。", "player_identity": "男主，访客。"}}
    cast = NumericV2CastProjection.from_story(story, catgirl_name="", player_name="")
    assert cast.catgirl_name == "演出角色" and cast.player_name == "你"
    assert cast.text("女主与男主一起查看观测屏。") == "演出角色与你一起查看观测屏。"
    assert cast.text("男女主保留各自决定。") == "你和演出角色保留各自决定。"


@pytest.mark.parametrize("characters", [OSError("unavailable"), {}, {"当前猫娘": "失效名字", "猫娘": {}},
    {"当前猫娘": "", "猫娘": {"维恩": {"性别": "男"}}}, {"当前猫娘": "维恩", "猫娘": {"维恩": None}}])
@pytest.mark.asyncio
async def test_missing_actual_character_stops_opening_before_model_call(monkeypatch, characters):
    engine, _, _, _ = _case(CASES[0])
    writer = actor.NumericV2Actor(Manager(characters))
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(kwargs)
        raise AssertionError("invalid identity must not reach the Actor provider")

    monkeypatch.setattr(writer, "_invoke", forbidden)
    with pytest.raises(actor.NumericV2ActorUnavailableError):
        await writer.generate_opening(engine=engine)
    assert calls == [] and writer.provider_call_count == 0


@pytest.mark.parametrize("name", ["维恩", "澄", "演出角色"])
def test_real_card_name_is_used_verbatim_without_gender_or_label_blacklists(name):
    manager = Manager({"当前猫娘": name, "猫娘": {name: {"性别": "非二元", "性格": "安静"}},
        "主人": {"昵称": "访客"}})
    before = deepcopy(manager.characters)
    assert actor.NumericV2Actor(manager)._current_catgirl_name() == name
    assert manager.reads == 1 and manager.characters == before


@pytest.mark.asyncio
async def test_valid_session_binding_does_not_read_card_again_for_the_name(monkeypatch):
    engine, session, outcome, _ = _case(CASES[0])
    manager = Manager(OSError("must use bound identity"))
    writer = actor.NumericV2Actor(manager)
    calls = []

    async def invoke(messages, **kwargs):
        calls.append(messages)
        assert prompt_payload(messages[1].content)["character_name"] == session.catgirl_binding["catgirl_name"]
        return {"source_performance": "", "bridge_scene_narration": "门外的灯亮着。",
            "target_scene_narration": CASES[0][3], "target_performance": CASES[0][4], "suggested_inputs": []}

    monkeypatch.setattr(writer, "_invoke", invoke)
    await writer.generate_turn(engine=engine, session=session, outcome=outcome,
        player_input="好，我们继续。", character_profile="性格: 安静。")
    assert manager.reads == 0 and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_name", ["", "   "])
async def test_blank_session_name_requires_a_real_card_before_generation(monkeypatch, missing_name):
    engine, session, outcome, _ = _case(CASES[0])
    session = replace(session, catgirl_binding={**session.catgirl_binding, "catgirl_name": missing_name})
    writer = actor.NumericV2Actor(Manager({}))

    async def forbidden(*args, **kwargs):
        raise AssertionError("blank session identity cannot be sent to Actor")

    monkeypatch.setattr(writer, "_invoke", forbidden)
    with pytest.raises(actor.NumericV2ActorUnavailableError):
        await writer.generate_turn(engine=engine, session=session, outcome=outcome,
            player_input="继续。", character_profile="性格: 安静。")
    assert writer.provider_call_count == 0
