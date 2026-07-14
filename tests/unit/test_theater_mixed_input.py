"""按七成自由输入、三成作者选项压力测试当前正式剧本。"""  # noqa: DOCSTRING_CJK

from copy import deepcopy

import pytest

from services.theater import runtime, session_store
from services.theater.turn_service import MAX_IDEMPOTENT_RESULTS, MAX_RECENT_TURN_MESSAGES


# 同时覆盖正常互动、关系试探和明显越界请求，验证自由文本不会改写作者权威状态。
FREE_INPUT_SAMPLES = (
    "你现在最担心的是什么？",
    "我会尊重你的决定，我们可以慢一点。",
    "刚才那句话让你不舒服了吗？",
    "我们不管这里了，立刻坐飞船去未来战争吧。",
    "其实这个世界是修仙宗门，我们去渡劫怎么样？",
    "先别推进，我想听听你此刻真实的心情。",
    "如果需要空间，我可以先陪你安静一会儿。",
)

# 这些字段共同决定静态图、事实、道具、线索和结局；scene_notes 被允许作为非权威短期记忆变化。
AUTHORITATIVE_STATE_KEYS = (
    "current_node_id",
    "completed_node_ids",
    "narrative_facts",
    "available_prop_ids",
    "used_prop_ids",
    "clue_ids",
    "flags",
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("story_id", "choice_turns", "ending_id"),
    (
        ("date_list_last_item_story", 15, "ending_last_item_is_tomorrow"),
    ),
)
async def test_seventy_thirty_mixed_inputs_keep_story_controllable(
    tmp_path,
    story_id: str,
    choice_turns: int,
    ending_id: str,
):
    """七成自由输入不得推进或污染剧情，三成作者选项最终必须正常通关。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / story_id
    result = await runtime.start_session(root, lanlan_name="测试猫娘", story_id=story_id)
    session_id = result["session_id"]
    revision = 0
    free_turns = round(choice_turns * 7 / 3)
    submitted_free_turns = 0

    for choice_index in range(choice_turns):
        # 按累计目标均匀穿插自由输入，避免只在开场集中聊天而漏测后续场景。
        target_free_turns = round((choice_index + 1) * free_turns / choice_turns)
        while submitted_free_turns < target_free_turns:
            before = await session_store.load_session(root, session_id)
            assert before is not None
            before_state = {
                key: deepcopy(before["story_state"].get(key))
                for key in AUTHORITATIVE_STATE_KEYS
            }
            before_choices = [item["choice_id"] for item in result["suggestion_options"]]
            before_scene = result["scene"]["scene_id"]
            message = FREE_INPUT_SAMPLES[submitted_free_turns % len(FREE_INPUT_SAMPLES)]

            result = await runtime.submit_input(
                root,
                session_id=session_id,
                input_kind="free_input",
                message=message,
                client_turn_id=f"mixed_free_{submitted_free_turns}",
                base_revision=revision,
            )
            revision += 1
            submitted_free_turns += 1

            assert result["ok"] is True
            assert result["scenario_trace"]["progress_kind"] == "roleplay_response"
            assert result["scene"]["scene_id"] == before_scene
            assert [item["choice_id"] for item in result["suggestion_options"]] == before_choices
            assert result["ending"]["should_end_session"] is False
            assert "GM" not in result["dialogue"]["text"]
            assert "回到剧本选项" not in result["dialogue"]["text"]

            after = await session_store.load_session(root, session_id)
            assert after is not None
            after_state = {
                key: deepcopy(after["story_state"].get(key))
                for key in AUTHORITATIVE_STATE_KEYS
            }
            assert after_state == before_state

        # 推荐选项是唯一权威推进入口；每次选择后允许进入下一个作者节点。
        assert result["suggestion_options"], (story_id, choice_index)
        result = await runtime.submit_input(
            root,
            session_id=session_id,
            input_kind="choice",
            choice_id=result["suggestion_options"][0]["choice_id"],
            client_turn_id=f"mixed_choice_{choice_index}",
            base_revision=revision,
        )
        revision += 1
        assert result["ok"] is True
        assert result["scenario_trace"]["progress_kind"] == "graph_progress"

    assert submitted_free_turns == free_turns
    assert submitted_free_turns / revision == pytest.approx(0.7, abs=0.012)
    assert result["ending"]["ending_id"] == ending_id
    assert result["can_resume"] is False

    # 压力测试结束后，持久化上下文和重试缓存必须保持固定上限。
    saved = await session_store.load_session(root, session_id)
    assert saved is not None
    assert len(saved["turns"]) <= MAX_RECENT_TURN_MESSAGES
    assert len(saved["turn_results_by_client_id"]) <= MAX_IDEMPOTENT_RESULTS
    assert len(saved["story_state"]["scene_notes"]) <= 6
