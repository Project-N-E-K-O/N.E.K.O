"""Normal turns must not enact the target act's opening or bridge-exclusive times as the present (issue 2.141 B3)."""  # noqa: DOCSTRING_CJK

from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_context as context
from services.theater.numeric_v2_context import (
    premature_target_markers,
    premature_target_scene_facts,
    transition_bridge_leak_markers,
)

TARGET_OPENING = "19:47，信标预热还剩三分钟，隔壁控制台突然报警。"
SOURCE_BEAT = {
    "summary": "19:12，她将眼前真实回应写入私人日志；六小时预热将在19:50完成。",
    "character_state": {"environment_state": "信标继续在隔壁独立预热，19:50完成。"},
}


def _engine():
    return SimpleNamespace(
        nodes={
            "source": {"id": "source", "story_beat": SOURCE_BEAT, "route_gates": [{"target_node_id": "target"}]},
            "target": {"id": "target", "story_beat": {"opening_scene": TARGET_OPENING, "summary": "她冲进控制台。"}},
        },
        preview_route=lambda node_id, metrics, *, facts=None: {
            "target_node_id": "target",
            "transition_contract": {"bridge_scene_narration": "按玩家的等待略去平静时段，来到19:47、预热剩三分钟时。"},
        },
    )


def _session():
    return SimpleNamespace(current_node_id="source", metrics={}, opening_performance={}, performance_history=())


def _outcome(source="source", target="source"):
    return SimpleNamespace(ledger_event={"from_node_id": source, "to_node_id": target})


@pytest.fixture(autouse=True)
def _no_history(monkeypatch):
    monkeypatch.setattr(context, "performance_history_records", lambda session: [])


def test_next_scene_time_marker_in_narration_is_flagged():
    leaked = premature_target_markers(
        _engine(), _session(), _outcome(),
        {"scene_narration": "时间快速流转至19:47，隔壁控制室突然传来警报。"},
    )
    assert leaked == ("19:47",)


def test_source_scene_own_time_marker_is_allowed():
    # 19:12/19:50 are author facts for the current act; appearing in next-act text does not make them premature enactment.
    leaked = premature_target_markers(
        _engine(), _session(), _outcome(),
        {"scene_narration": "她说19:12会把眼前的回应写进日志，预热19:50完成。"},
    )
    assert leaked == ()


def test_player_input_and_dialogue_may_reference_the_exit():
    # A time mentioned by the player, or only in a spoken invitation, does not count as premature narration delivery.
    leaked = premature_target_markers(
        _engine(), _session(), _outcome(),
        {"performance": "（看向控制台）19:47我们再出发。"},
        player_input="我们19:47再出发吧。",
    )
    assert leaked == ()


def test_player_claim_cannot_waive_target_guards_while_departure_is_blocked():
    engine, session, outcome = _engine(), _session(), _outcome()
    outcome.departure_blocker = "completion_pending"
    player_input = TARGET_OPENING
    assert premature_target_markers(engine, session, outcome,
        {"scene_narration": TARGET_OPENING}, player_input) == ("19:47",)
    assert premature_target_scene_facts(engine, session, outcome,
        {"scene_narration": TARGET_OPENING}, player_input) == ("信标预热还剩三分钟", "隔壁控制台突然报警")
    assert not premature_target_markers(engine, session, outcome,
        {"performance": "等条件齐了，我们19:47再出发。"}, player_input)


def test_transition_turns_may_deliver_the_target_opening():
    # Formal transitions must deliver the target act's opening, so allow its narration segments.
    leaked = premature_target_markers(
        _engine(), _session(), _outcome(source="source", target="target"),
        {"segments": [{"phase": "target_opening", "scene_narration": TARGET_OPENING}]},
    )
    assert leaked == ()


def test_marker_in_segment_narration_is_still_flagged():
    leaked = premature_target_markers(
        _engine(), _session(), _outcome(),
        {"segments": [{"phase": "source_response", "scene_narration": "警报在19:47响起。"}]},
    )
    assert leaked == ("19:47",)


def test_exact_target_opening_fact_in_narration_is_flagged():
    leaked = premature_target_scene_facts(
        _engine(), _session(), _outcome(),
        {"scene_narration": "信标预热还剩三分钟，隔壁控制台突然报警。"},
    )
    assert leaked == ("信标预热还剩三分钟", "隔壁控制台突然报警")


def test_exact_target_opening_fact_in_dialogue_is_allowed():
    # Dialogue may propose visiting the target act later; only visible narration proves the target facts have occurred.
    leaked = premature_target_scene_facts(
        _engine(), _session(), _outcome(),
        {"performance": "信标预热还剩三分钟，隔壁控制台突然报警。"},
    )
    assert leaked == ()


def _unresolved_routes():
    engine = _engine()
    engine.preview_route = lambda *args, **kwargs: None
    engine.nodes["source"] = {
        "id": "source",
        "story_beat": {"opening_scene": "镖局兵器房内，桌上放着文书。小葵说：“官道先到驿站。”"},
        "route_gates": [
            {"target_node_id": "target"},
            {"target_node_id": "station", "transition_contract": {
                "bridge_scene_narration": "半日后，你们抵达了官道旁的驿站，推门而入。",
            }},
        ],
    }
    engine.nodes["station"] = {
        "id": "station",
        "type": "scene",
        "story_beat": {"opening_scene": "驿站大堂人来人往，炉火正旺。管事请两人出示通行文书。"},
    }
    return engine


_ARRIVAL_DRAFT = {
    "scene_narration": "两匹马在风雪中缓缓靠近驿站入口，厚重的木门半掩，门内透出昏黄的灯火与暖意。",
    "performance": "（勒住缰绳停在门外，呼出一口白气）到了，先把马牵到檐下避风处。",
}
_ENTRANCE_DRAFT = {"performance": "（推开半掩的木门，侧身让你先进去）里头暖和些，文书我带着，你只管护好匣子。"}


def test_unresolved_routes_still_guard_exact_target_opening_and_time():
    engine = _unresolved_routes()
    assert premature_target_scene_facts(engine, _session(), _outcome(), {
        "scene_narration": "信标预热还剩三分钟，隔壁控制台突然报警。",
    }) == ("信标预热还剩三分钟", "隔壁控制台突然报警")
    assert premature_target_markers(engine, _session(), _outcome(), {
        "scene_narration": "时间来到19:47。",
    }) == ("19:47",)
    assert not premature_target_markers(engine, _session(), _outcome(), {
        "scene_narration": "她打算等到19:47再出发。",
    })


def test_unresolved_route_catches_actual_arrival_and_continuation_in_old_save():
    engine = _unresolved_routes()
    session = _session()
    assert premature_target_scene_facts(engine, session, _outcome(), _ARRIVAL_DRAFT,
        "（勒住缰绳放慢速度）收到，我小心靠近。") == ("驿站入口",)
    # A generic door in a draft proves nothing on its own. In the old save,
    # the immediately previous performance established the same blocked destination.
    assert not premature_target_scene_facts(engine, session, _outcome(), _ENTRANCE_DRAFT)
    session.performance_history = ({**_ARRIVAL_DRAFT, "from_node_id": "source", "to_node_id": "source"},)
    assert premature_target_scene_facts(engine, session, _outcome(), _ENTRANCE_DRAFT,
        "（翻身下马，把木匣抱在怀里）咱们这就进去办手续吧。") == ("驿站入口",)
    assert not premature_target_scene_facts(engine, session, _outcome(), _ENTRANCE_DRAFT,
        "（推开木门并走进去）")


@pytest.mark.parametrize("draft,player_input", [
    ({"scene_narration": "她打算等会儿到达驿站。"}, ""),
    ({"scene_narration": "她问：‘前面是驿站入口吗？’"}, ""),
    ({"performance": "到了驿站入口，我们再下来歇脚。"}, ""),
    ({"scene_narration": "前方已经能看见驿站入口。"}, ""),
    ({"scene_narration": "两匹马缓缓靠近驿站入口。", "performance": "（勒住缰绳停在门外）到了再下马。"}, ""),
    (_ARRIVAL_DRAFT, "（抵达驿站入口后勒马）"),
    (_ARRIVAL_DRAFT, "（走到驿站入口后勒马）"),
])
def test_unresolved_route_does_not_confuse_destination_references_with_arrival(draft, player_input):
    assert not premature_target_scene_facts(_unresolved_routes(), _session(), _outcome(), draft, player_input)


def test_unresolved_route_does_not_guard_same_location_or_prior_scene_entry():
    engine = _unresolved_routes()
    session = _session()
    session.story_state = {"revision": 1, "facts": {"event:scene.entered:station:r1": {"visibility": "public"}}}
    assert not premature_target_scene_facts(engine, session, _outcome(), _ARRIVAL_DRAFT)

    engine.nodes["source"]["story_beat"]["opening_scene"] = "驿站门外，两匹马已经饮过水。"
    assert not premature_target_scene_facts(engine, _session(), _outcome(), _ARRIVAL_DRAFT)


@pytest.mark.parametrize('later_events', [0, 32])
def test_entered_target_scene_fact_allows_rementioning_opening(later_events):
    session = _session()
    session.story_state = {
        "revision": 1,
        "facts": {"event:scene.entered:target:r1": {"visibility": "public"}},
    }
    session.story_state['facts'].update({f'event:scene.entered:other_{index}:r{index + 2}':
                                       {'visibility': 'public', 'updated_revision': index + 2}
                                       for index in range(later_events)})
    leaked = premature_target_scene_facts(
        _engine(), session, _outcome(),
        {"scene_narration": "信标预热还剩三分钟，隔壁控制台突然报警。"},
    )
    assert leaked == ()


def test_transition_bridge_cannot_copy_target_opening_clause():
    leaked = transition_bridge_leak_markers(
        target_opening="视野豁然开朗，小葵快步走向平台边缘。晚霞铺满山谷。",
        bridge_text="沿着石阶向上，视野豁然开朗，小葵快步走向平台边缘。",
    )
    # Very short common phrases are not structural evidence; more specific target actions still match.
    assert leaked == ("小葵快步走向平台边缘",)


def test_authored_bridge_and_common_short_phrases_are_allowed():
    assert transition_bridge_leak_markers(
        target_opening="视野豁然开朗，小葵快步走向平台边缘。",
        bridge_text="沿着石阶向上，视野豁然开朗，小葵快步走向平台边缘。",
        authored_bridge="视野豁然开朗，小葵快步走向平台边缘。",
    ) == ()
    assert transition_bridge_leak_markers(
        target_opening="她看向窗外。",
        bridge_text="她看向窗外。",
    ) == ()


def test_transition_bridge_time_marker_is_flagged_after_rephrasing():
    leaked = transition_bridge_leak_markers(
        target_opening="时间来到19:47，控制台响起警报。",
        bridge_text="等候片刻后，钟表指向19:47。",
    )
    assert leaked == ("19:47",)
