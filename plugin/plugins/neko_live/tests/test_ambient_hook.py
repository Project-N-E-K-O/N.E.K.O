from plugin.plugins.neko_live.modules.live_events.ambient_hook import (
    select_ambient_hook,
)


def _row(
    seq: int,
    uid: str,
    text: str,
    *,
    selected: bool = False,
) -> dict[str, object]:
    return {
        "seq": seq,
        "uid": uid,
        "nickname": f"viewer-{uid}",
        "text": text,
        "selected": selected,
    }


def test_ambient_hook_keeps_substantive_distinct_viewer_chorus_as_one_hook():
    chorus = select_ambient_hook(
        [
            _row(4, "a", "猫猫钻纸箱真的太好笑了"),
            _row(3, "b", "猫猫钻纸箱真的太好笑了"),
            _row(2, "c", "🌟🌟🌟"),
            _row(1, "d", "然后呢"),
        ]
    )

    assert chorus.row is not None
    assert chorus.row["seq"] == 4
    assert chorus.reason == "selected.chorus"


def test_ambient_hook_rejects_same_viewer_and_anonymous_floods():
    same_viewer_repeat = select_ambient_hook(
        [
            _row(2, "same", "猫猫钻纸箱真的太好笑了"),
            _row(1, "same", "猫猫钻纸箱真的太好笑了"),
        ]
    )

    assert same_viewer_repeat.row is None
    assert same_viewer_repeat.reason == "duplicate_or_flood"

    flooded = select_ambient_hook(
        [
            _row(3, "same", "第一段完整但来自同一人"),
            _row(2, "same", "第二段完整但来自同一人"),
            _row(1, "same", "第三段完整但来自同一人"),
        ]
    )

    assert flooded.row is None
    assert flooded.reason == "duplicate_or_flood"

    anonymous_flood = select_ambient_hook(
        [
            _row(3, "", "第一段匿名刷屏内容"),
            _row(2, "", "第二段匿名刷屏内容"),
            _row(1, "", "第三段匿名刷屏内容"),
        ]
    )

    assert anonymous_flood.row is None
    assert anonymous_flood.reason == "duplicate_or_flood"


def test_ambient_hook_rejects_emoji_reactions_and_contextless_fragments():
    selection = select_ambient_hook(
        [
            _row(4, "a", "😂😂😂"),
            _row(3, "b", "哈哈哈"),
            _row(2, "c", "刚才那个"),
            _row(1, "d", "然后呢"),
        ]
    )

    assert selection.row is None
    assert selection.reason in {"low_value", "fragment"}
    assert selection.score == 0


def test_ambient_hook_prefers_a_continuing_topic_from_distinct_viewers():
    selection = select_ambient_hook(
        [
            _row(3, "a", "猫猫钻纸箱的时候把自己卡住了"),
            _row(2, "b", "刚才那个纸箱像猫猫的新房子"),
            _row(1, "c", "今晚窗外的雨声听起来很舒服"),
        ]
    )

    assert selection.row is not None
    assert selection.row["seq"] == 3
    assert selection.reason == "selected.continuity"
    assert selection.candidate_count == 3


def test_ambient_hook_labels_direct_question_for_answer_first_intent():
    selection = select_ambient_hook(
        [_row(1, "a", "为什么猫猫钻纸箱以后突然安静了？")]
    )

    assert selection.row is not None
    assert selection.reason == "selected.question"


def test_ambient_hook_labels_clear_emotion_for_empathy_intent():
    selection = select_ambient_hook(
        [_row(1, "a", "今天有点难过，想听猫猫说句话")]
    )

    assert selection.row is not None
    assert selection.reason == "selected.mood"


def test_ambient_hook_keeps_replied_fact_out_of_ordinary_pickup():
    selection = select_ambient_hook(
        [
            _row(2, "a", "为什么猫猫刚才突然躲起来了？", selected=True),
            _row(1, "b", "猫猫钻纸箱的样子像在搬新家"),
        ]
    )

    assert selection.row is not None
    assert selection.row["seq"] == 1
    assert selection.reason == "selected.complete"


def test_ambient_hook_returns_no_candidate_when_every_complete_row_was_replied():
    selection = select_ambient_hook(
        [
            _row(2, "a", "为什么猫猫刚才突然躲起来了？", selected=True),
            _row(1, "b", "猫猫钻纸箱的样子像在搬新家", selected=True),
        ]
    )

    assert selection.row is None
    assert selection.reason == "already_replied"
