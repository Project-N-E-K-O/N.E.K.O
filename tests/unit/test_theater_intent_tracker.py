"""验证 v2.6 通用自由意图线程的确定性累计、休眠与清理规则。"""  # noqa: DOCSTRING_CJK

import pytest

from services.theater import intent_tracker, rules


def test_new_then_refine_preserves_server_identity_and_reaches_threshold():
    """同一节点内的细化表达保留服务端 ID，并在第二次证据后达到规划阈值。"""  # noqa: DOCSTRING_CJK
    first = intent_tracker.update_dynamic_intent(
        {},
        new_intent_key="intent_server_1",
        summary="整理桌上的旧明信片",
        relation="new",
        evidence_message="先看看桌上那张旧明信片",
        origin_node_id="node_fixture_origin",
    )
    refined = intent_tracker.update_dynamic_intent(
        first,
        new_intent_key="intent_server_2",
        summary="收好带蓝色邮戳的旧明信片",
        relation="refine",
        evidence_message="蓝色邮戳那张，就收这个",
        origin_node_id="node_fixture_origin",
    )

    assert first["streak"] == 1
    assert (
        intent_tracker.should_plan_branch(
            first,
            current_node_id="node_fixture_origin",
        )
        is False
    )
    assert refined == {
        "intent_key": "intent_server_1",
        "intent_summary": "收好带蓝色邮戳的旧明信片",
        "origin_node_id": "node_fixture_origin",
        "streak": 2,
        "evidence_messages": ["先看看桌上那张旧明信片", "蓝色邮戳那张，就收这个"],
        "relation": "refine",
        "thread_state": "active",
    }
    assert (
        intent_tracker.should_plan_branch(
            refined,
            current_node_id="node_fixture_origin",
        )
        is True
    )


def test_should_plan_rejects_intent_from_another_author_node():
    """即使次数和证据完整，旧节点意图也不能在当前节点触发 Planner。"""  # noqa: DOCSTRING_CJK
    intent = {
        "intent_key": "intent_server_wrong_node",
        "intent_summary": "继续整理旧明信片",
        "origin_node_id": "node_fixture_origin",
        "streak": 2,
        "evidence_messages": ["先看看旧明信片", "继续整理这张明信片"],
        "relation": "continue",
        "thread_state": "active",
    }

    assert (
        intent_tracker.should_plan_branch(
            intent,
            current_node_id="node_fixture_target",
        )
        is False
    )


def test_should_plan_rejects_forged_threshold_with_one_evidence():
    """一条玩家证据不能借伪造 streak=2 跳过连续坚持门槛。"""  # noqa: DOCSTRING_CJK
    intent = {
        "intent_key": "intent_server_short_evidence",
        "intent_summary": "继续整理旧明信片",
        "origin_node_id": "node_fixture_origin",
        "streak": 2,
        "evidence_messages": ["只说过一次整理明信片"],
        "relation": "continue",
        "thread_state": "active",
    }

    assert (
        intent_tracker.should_plan_branch(
            intent,
            current_node_id="node_fixture_origin",
        )
        is False
    )


def test_should_plan_rejects_streak_above_server_threshold():
    """服务端计数被篡改到阈值以上时必须失效，不能按“大于等于”继续规划。"""  # noqa: DOCSTRING_CJK
    intent = {
        "intent_key": "intent_server_overflow",
        "intent_summary": "继续整理旧明信片",
        "origin_node_id": "node_fixture_origin",
        "streak": 999,
        "evidence_messages": ["先看看旧明信片", "继续整理", "还是继续整理"],
        "relation": "continue",
        "thread_state": "active",
    }

    assert (
        intent_tracker.should_plan_branch(
            intent,
            current_node_id="node_fixture_origin",
        )
        is False
    )


def test_replace_resets_streak_and_uses_new_server_identity():
    """换成另一个图外目标时必须重置连续次数，不能借旧证据直接规划。"""  # noqa: DOCSTRING_CJK
    current = {
        "intent_key": "intent_server_1",
        "intent_summary": "收好带蓝色邮戳的旧明信片",
        "origin_node_id": "node_fixture_origin",
        "streak": 2,
        "evidence_messages": ["看看旧明信片", "蓝色邮戳那张"],
        "relation": "refine",
    }
    replaced = intent_tracker.update_dynamic_intent(
        current,
        new_intent_key="intent_server_2",
        summary="打开窗边的收音机",
        relation="replace",
        evidence_message="还是先听收音机吧",
        origin_node_id="node_fixture_origin",
    )

    assert replaced["intent_key"] == "intent_server_2"
    assert replaced["streak"] == 1
    assert replaced["evidence_messages"] == ["还是先听收音机吧"]
    assert (
        intent_tracker.should_plan_branch(
            replaced,
            current_node_id="node_fixture_origin",
        )
        is False
    )


def test_continue_from_another_node_is_treated_as_new_intent():
    """作者节点变化会切断连续证据，即使模型误报 continue 也只能从一次重新累计。"""  # noqa: DOCSTRING_CJK
    current = {
        "intent_key": "intent_server_1",
        "intent_summary": "收好带蓝色邮戳的旧明信片",
        "origin_node_id": "node_fixture_origin",
        "streak": 1,
        "evidence_messages": ["看看旧明信片"],
        "relation": "new",
    }
    moved = intent_tracker.update_dynamic_intent(
        current,
        new_intent_key="intent_server_2",
        summary="继续收好蓝色邮戳的旧明信片",
        relation="continue",
        evidence_message="还是蓝色邮戳那张",
        origin_node_id="node_fixture_target",
    )

    assert moved["intent_key"] == "intent_server_2"
    assert moved["origin_node_id"] == "node_fixture_target"
    assert moved["streak"] == 1
    assert moved["relation"] == "new"


def test_idle_dormancy_preserves_identity_but_cannot_plan_until_resumed():
    """一次 idle 只休眠短期线程；休眠态不规划，明确细化后恢复原身份并累计。"""  # noqa: DOCSTRING_CJK
    state = {
        "dynamic_intent": {
            "intent_key": "intent_server_1",
            "intent_summary": "收好旧明信片",
            "origin_node_id": "node_fixture_origin",
            "streak": 1,
            "evidence_messages": ["先看看旧明信片"],
            "relation": "new",
        }
    }

    intent_tracker.mark_dynamic_intent_idle(
        state, current_node_id="node_fixture_origin"
    )

    dormant = state["dynamic_intent"]
    assert dormant["intent_key"] == "intent_server_1"
    assert dormant["streak"] == 1
    assert dormant["thread_state"] == "dormant"
    assert (
        intent_tracker.should_plan_branch(
            dormant,
            current_node_id="node_fixture_origin",
        )
        is False
    )

    resumed = intent_tracker.update_dynamic_intent(
        dormant,
        new_intent_key="intent_server_2",
        summary="收好蓝色邮戳的旧明信片",
        relation="refine",
        evidence_message="还是蓝色邮戳那张",
        origin_node_id="node_fixture_origin",
    )
    assert resumed["intent_key"] == "intent_server_1"
    assert resumed["streak"] == 2
    assert resumed["thread_state"] == "active"
    assert (
        intent_tracker.should_plan_branch(
            resumed,
            current_node_id="node_fixture_origin",
        )
        is True
    )


def test_second_consecutive_idle_clears_dormant_intent():
    """连续第二次 idle 超出首批宽限，必须清理旧线程以防换话题后误激活。"""  # noqa: DOCSTRING_CJK
    state = {
        "dynamic_intent": {
            "intent_key": "intent_server_1",
            "intent_summary": "收好旧明信片",
            "origin_node_id": "node_fixture_origin",
            "streak": 1,
            "evidence_messages": ["先看看旧明信片"],
            "relation": "new",
        }
    }
    intent_tracker.mark_dynamic_intent_idle(
        state, current_node_id="node_fixture_origin"
    )
    intent_tracker.mark_dynamic_intent_idle(
        state, current_node_id="node_fixture_origin"
    )
    assert state["dynamic_intent"] == {}


def test_confirmed_pending_contributes_evidence_only_for_explicit_continuation():
    """Pending 只有在本轮明确 continue/refine 时才与当前原话组成两条证据。"""  # noqa: DOCSTRING_CJK
    confirmed = intent_tracker.update_dynamic_intent(
        {},
        new_intent_key="intent_server_confirmed",
        summary="进入验证区后检查记录板",
        relation="continue",
        evidence_message="对，进去后检查记录板",
        origin_node_id="node_target",
        confirmed_pending_evidence="然后检查记录板",
    )
    replaced = intent_tracker.update_dynamic_intent(
        {},
        new_intent_key="intent_server_replaced",
        summary="先去看灯展",
        relation="replace",
        evidence_message="还是先看灯展吧",
        origin_node_id="node_target",
        confirmed_pending_evidence="然后检查记录板",
    )

    assert confirmed["streak"] == 2
    assert confirmed["evidence_messages"] == [
        "然后检查记录板",
        "对，进去后检查记录板",
    ]
    assert (
        intent_tracker.should_plan_branch(
            confirmed,
            current_node_id="node_target",
        )
        is True
    )
    assert replaced["streak"] == 1
    assert replaced["evidence_messages"] == ["还是先看灯展吧"]


def test_legacy_intent_without_thread_state_is_read_as_active():
    """v2.5 存档缺少线程状态时按 active 读取，并在首次更新时懒归一化。"""  # noqa: DOCSTRING_CJK
    legacy = {
        "intent_key": "intent_legacy",
        "intent_summary": "继续整理明信片",
        "origin_node_id": "node_fixture_origin",
        "streak": 2,
        "evidence_messages": ["看看明信片", "继续整理"],
        "relation": "continue",
    }
    assert (
        intent_tracker.should_plan_branch(
            legacy,
            current_node_id="node_fixture_origin",
        )
        is True
    )
    state = {"dynamic_intent": legacy}
    intent_tracker.mark_dynamic_intent_idle(
        state,
        current_node_id="node_fixture_origin",
    )
    assert state["dynamic_intent"]["thread_state"] == "dormant"


def test_author_node_progress_clears_dynamic_intent():
    """提交新的作者节点后清除原节点意图，避免动态证据跨主线推进继续累计。"""  # noqa: DOCSTRING_CJK
    state = rules.initial_state({}, initial_node_id="node_origin")
    state["dynamic_intent"] = {
        "intent_key": "intent_server_1",
        "intent_summary": "收好带蓝色邮戳的旧明信片",
        "origin_node_id": "node_origin",
        "streak": 1,
        "evidence_messages": ["看看旧明信片"],
        "relation": "new",
    }
    state["pending_intent"] = {
        "summary": "到下一个节点继续整理明信片",
        "evidence_excerpt": "等会儿继续",
    }

    rules.apply_node({}, state, {"node_id": "node_target"})

    assert state["current_node_id"] == "node_target"
    assert state["dynamic_intent"] == {}
    assert state["pending_intent"] == {}


def test_evidence_is_bounded_without_hiding_repeated_insistence():
    """证据只保留最近上限，但玩家用新回合重复原话仍属于有效连续证据。"""  # noqa: DOCSTRING_CJK
    current = {}
    for index in range(intent_tracker.MAX_DYNAMIC_INTENT_EVIDENCE + 2):
        current = intent_tracker.update_dynamic_intent(
            current,
            new_intent_key=f"intent_server_{index}",
            summary="收好带蓝色邮戳的旧明信片",
            relation="new" if index == 0 else "continue",
            evidence_message="我还是想收好这张明信片",
            origin_node_id="node_fixture_origin",
        )

    assert current["streak"] == intent_tracker.DYNAMIC_INTENT_PLANNING_THRESHOLD
    assert (
        current["evidence_messages"]
        == ["我还是想收好这张明信片"] * intent_tracker.MAX_DYNAMIC_INTENT_EVIDENCE
    )
    assert (
        intent_tracker.should_plan_branch(
            current,
            current_node_id="node_fixture_origin",
        )
        is True
    )


@pytest.mark.parametrize("relation", ["", "same", "stronger"])
def test_unknown_relation_is_rejected(relation):
    """服务端只接受协议声明的四种关系，避免模型任意文本改变累计规则。"""  # noqa: DOCSTRING_CJK
    with pytest.raises(ValueError, match="relation"):
        intent_tracker.update_dynamic_intent(
            {},
            new_intent_key="intent_server_1",
            summary="收好带蓝色邮戳的旧明信片",
            relation=relation,
            evidence_message="看看旧明信片",
            origin_node_id="node_fixture_origin",
        )


def test_dynamic_intent_rejects_evidence_that_would_lose_sentence_tail():
    """意图线程不得静默截掉长证据句尾，否则否定和转折会被保存成相反语义。"""  # noqa: DOCSTRING_CJK
    evidence = "继续检查当前记录" * 80 + "但不要提交"

    assert intent_tracker.evidence_message_fits(evidence) is False
    with pytest.raises(ValueError, match="would be truncated"):
        intent_tracker.update_dynamic_intent(
            {},
            new_intent_key="intent_server_long",
            summary="检查当前记录",
            relation="new",
            evidence_message=evidence,
            origin_node_id="node_fixture_origin",
        )


def test_legacy_long_evidence_cannot_plan_or_continue_from_truncated_prefix():
    """旧存档里的超长证据必须失效，不能截掉句尾否定后继续累计或规划。"""  # noqa: DOCSTRING_CJK
    legacy = {
        "intent_key": "intent_legacy_long",
        "intent_summary": "继续核对记录",
        "origin_node_id": "node_fixture_origin",
        "streak": 2,
        "evidence_messages": [
            "continue checking the record " * 20 + "but do not submit"
        ],
        "relation": "continue",
        "thread_state": "active",
    }

    assert (
        intent_tracker.should_plan_branch(
            legacy,
            current_node_id="node_fixture_origin",
        )
        is False
    )
    updated = intent_tracker.update_dynamic_intent(
        legacy,
        new_intent_key="intent_new_after_invalid_legacy",
        summary="继续核对记录",
        relation="continue",
        evidence_message="先重新确认记录",
        origin_node_id="node_fixture_origin",
    )

    assert updated["streak"] == 1
    assert updated["evidence_messages"] == ["先重新确认记录"]


def test_long_pending_evidence_is_not_truncated_into_planning_support():
    """超界 Pending 不能贡献第一条证据，当前回合只能作为新意图重新起算。"""  # noqa: DOCSTRING_CJK
    updated = intent_tracker.update_dynamic_intent(
        {},
        new_intent_key="intent_pending_boundary",
        summary="继续核对记录",
        relation="continue",
        evidence_message="现在重新核对",
        origin_node_id="node_fixture_origin",
        confirmed_pending_evidence="继续核对" * 100 + "但不要提交",
    )

    assert updated["streak"] == 1
    assert updated["evidence_messages"] == ["现在重新核对"]
