"""The unknown-address leak check only fires on the catgirl directly using the configured nickname as a standalone word."""

import pytest

from services.theater.numeric_v2_actor import (
    NumericV2ActorOutputError,
    _assert_no_unknown_player_address_leak,
)


def _check(performance, *, player_input=""):
    _assert_no_unknown_player_address_leak(
        performance,
        player_address="哥哥",
        player_address_known=False,
        player_input=player_input,
    )


@pytest.mark.parametrize("performance", [
    {"performance": "（打量着门口的陌生人）这位小哥哥，你找谁？"},
    {"performance": "我哥哥以前也这么说。"},
    {"performance": "（低头）……", "scene_narration": "她的哥哥三年前离开了小镇。"},
    {
        "performance": "（摇头）他没回来。",
        "suggested_inputs": ["（追问）你哥哥去哪了？", "哥哥，我陪你去找。"],
    },
    {"accept_input": "哥哥，我们走吧。", "alternative_inputs": ["再等等。"]},
])
def test_compound_words_npc_narration_and_player_suggestions_do_not_leak(performance):
    _check(performance)


@pytest.mark.parametrize("performance", [
    {"performance": "哥哥，你来了。"},
    {"performance": "（抬头）哥哥！你终于来了。"},
    {"performance": "嗯。", "scene_narration": "她小声喊了一句：哥哥。"},
    {"source_performance": "好。", "target_performance": "（笑）哥哥，走吧。"},
])
def test_catgirl_direct_address_still_leaks(performance):
    with pytest.raises(NumericV2ActorOutputError, match="numeric_v2_actor_player_address_leak"):
        _check(performance)


def test_nickname_disclosed_this_turn_may_be_repeated():
    _check({"performance": "哥哥，你来了。"}, player_input="你可以叫我哥哥。")
