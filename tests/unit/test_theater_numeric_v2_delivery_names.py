"""Author prop names follow casting; actual played evidence stays verbatim."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.theater.numeric_v2_context import contract_required_names, missing_contract_names


STORY = {"intro": {"player_name": "林舟", "catgirl_name": "沈青"}}


def _session(known=False, history=()):
    return SimpleNamespace(
        current_node_id="source", revision=len(history), opening_performance={},
        performance_history=history, player_address_known=known,
        catgirl_binding={"player_address": "哥哥", "catgirl_name": "Lan"},
    )


def _node(requirement):
    return {"route_gates": [{"target_node_id": "target", "transition_contract": {
        "must_deliver": [requirement], "must_preserve": [],
    }}]}


def _check(requirement, performance, expected, *, session=None, outcome=None, story=STORY):
    node, session = _node(requirement), session or _session()
    before = deepcopy((node, session, outcome, performance, story))
    assert missing_contract_names(
        node, "target", performance, session, story=story, delivery_session=outcome,
    ) == expected
    assert (node, session, outcome, performance, story) == before


@pytest.mark.parametrize("known,text", [
    (False, "你的登记册放在这里。"),
    (True, "哥哥的登记册放在这里。"),
])
def test_player_prop_uses_current_cast(known, text):
    _check('关键道具“林舟的登记册”[register_book]：须展示。',
           {"performance": text}, (), session=_session(known))


@pytest.mark.parametrize("channel", ["performance"])
def test_catgirl_prop_uses_runtime_name_in_actual_visible_channels(channel):
    # 姓名投影留一个代表；各可见通道由 delivery_visibility 的配对样本覆盖。
    _check('关键道具“沈青的登记册”[register_book]：须展示。',
           {channel: "Lan的登记册放在这里。"}, ())


@pytest.mark.parametrize("text", ["你的登记册放在这里。", "哥哥的登记册放在这里。"])
def test_disclosure_turn_accepts_current_and_delivery_cast_without_recasting_body(text):
    _check('关键道具“林舟的登记册”[register_book]：须展示。',
           {"performance": text}, (), session=_session(), outcome=_session(True))


def test_future_nickname_is_not_a_current_cast_without_disclosure():
    _check('关键道具“林舟的登记册”[register_book]：须展示。',
           {"performance": "哥哥的登记册放在这里。"}, ("你的登记册",))


@pytest.mark.parametrize("old_text", ["你的登记册放在这里。", "哥哥的登记册放在这里。", "林舟的登记册放在这里。"])
def test_known_player_keeps_actual_historical_name_variants(old_text):
    history = ({"revision": 1, "from_node_id": "source", "to_node_id": "source",
                "input_text": "请展示。", "performance": old_text},)
    _check('关键道具“林舟的登记册”[register_book]：须展示。',
           {"performance": "我们接着看。"}, (), session=_session(True, history))


def test_current_disclosure_does_not_retroactively_introduce_a_historical_nickname():
    history = ({"revision": 1, "from_node_id": "source", "to_node_id": "source",
                "input_text": "请展示。", "performance": "哥哥的登记册。"},)
    _check('关键道具“林舟的登记册”[register_book]：须展示。',
           {"performance": "我们接着看。"}, ("你的登记册",),
           session=_session(False, history), outcome=_session(True))


def test_cast_name_collision_does_not_transform_actual_evidence_again():
    session = _session(True)
    session.catgirl_binding["catgirl_name"] = "林舟"
    _check('关键道具“沈青的登记册”[register_book]：须展示。',
           {"performance": "林舟的登记册放在这里。"}, (), session=session)


def test_internal_id_fallback_is_never_cast_even_when_it_equals_an_author_name():
    story = {"intro": {"player_name": "player_prop", "catgirl_name": "cat_prop"}}
    _check("关键道具[cat_prop]：须展示。", {"performance": "cat_prop在这里。"}, (), story=story)
    _check("关键道具[cat_prop]：须展示。", {"performance": "Lan在这里。"}, ("cat_prop",), story=story)


@pytest.mark.parametrize("reverse,text,expected", [
    pytest.param(False, "哥哥", ("player_prop",), id="哥哥-expected0-False"),
    pytest.param(True, "哥哥", ("player_prop",), id="哥哥-expected0-True"),
    pytest.param(False, "player_prop", (), id="player_prop-expected1-False"),
])
def test_same_spelling_display_name_cannot_hide_a_separate_protocol_id(reverse, text, expected):
    node = _node("")
    requirements = ['关键道具“player_prop”：须展示。', '关键道具[player_prop]：须展示。']
    node["route_gates"][0]["transition_contract"]["must_deliver"] = requirements[::-1] if reverse else requirements
    story = {"intro": {"player_name": "player_prop", "catgirl_name": "沈青"}}
    # Public extraction keeps its old de-duplicated result. The internal check
    # still knows that the second declaration requires an uncast protocol ID.
    assert contract_required_names(node, "target") == ("player_prop",)
    assert missing_contract_names(node, "target", {"performance": text}, _session(True), story=story) == expected


def test_quoted_human_name_still_takes_precedence_over_internal_id():
    _check('关键道具“沈青的登记册”[register_book]：须展示。',
           {"performance": "register_book放在这里。"}, ("Lan的登记册",))


def test_multiple_required_props_keep_order_and_deduplicate_after_cast():
    _check('关键道具“沈青的登记册”；关键道具“Lan的登记册”；关键道具“林舟的钥匙”[register_book]：须展示。',
           {"performance": "我们先等等。"}, ("Lan的登记册", "你的钥匙"))


def test_literal_author_name_in_legacy_fixed_original_still_counts():
    _check('关键道具“沈青的登记册”[register_book]：须展示。', {
        "performance": "先看这里。", "fixed_narrations": [{
            "id": "legacy", "position": "before", "text": "沈青的登记册放在这里。",
        }],
    }, ())


def test_legacy_identity_name_fallback_uses_the_existing_cast_projection():
    story = {"intro": {"player_identity": "林舟，来访者", "catgirl_identity": "沈青，记录员"}}
    _check('关键道具“沈青的登记册”[register_book]：须展示。',
           {"performance": "Lan的登记册放在这里。"}, (), story=story)


def test_missing_story_keeps_legacy_helper_and_extraction_contract():
    node = _node('关键道具“沈青的登记册”[register_book]：须展示。')
    assert contract_required_names(node, "target") == ("沈青的登记册",)
    assert missing_contract_names(node, "target", {"performance": "沈青的登记册。"}, _session()) == ()
    assert missing_contract_names(node, "other", {"performance": ""}, _session()) == ()
