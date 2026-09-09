"""验证来源、访问边界和真实计分解析，不把 Prompt 文案断言当作模型质量通过。"""

import json
from dataclasses import replace

from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_context import history_evidence
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_prompt_contract import _session
from utils.tokenize import count_tokens


def _long_session():
    # 学校仅作跨幕对象之一，加入工具归还和隐私否定，避免只测单一剧本。
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    old = {"revision": 1, "from_node_id": "old", "to_node_id": "old",
           "input_text": "我读本地综合大学；观察日记只是讨论过展示，今天没有同意公开。",
           "performance": "你会读本地综合大学；日记继续保密。缝衣针已经归还木盒。",
           "suggested_inputs": ["我把观察日记公开了。"]}
    crossing = {"revision": 2, "from_node_id": "old", "to_node_id": "start",
                "input_text": "去花店。", "segments": [
                    {"phase": "source_response", "performance": "旧码头可以乘船。"},
                    {"phase": "target_opening", "scene_narration": "花店后面的小路通往档案馆。"}]}
    later = tuple({"revision": i, "from_node_id": "start", "to_node_id": "start",
                   "input_text": f"聊聊第{i}盆花。", "performance": "叶子刚刚擦干。"} for i in range(3, 16))
    return engine, replace(_session(engine), revision=15, node_turn_count=13,
                           performance_history=(old, crossing, *later))


@pytest.mark.parametrize("query,expected", [
    ("我的综合大学在哪里？", "本地综合大学"),
    ("观察日记同意公开了吗？", "没有同意公开"),
    ("缝衣针归还到哪里？", "归还木盒"),
])
def test_retrieval_keeps_complete_sourced_old_facts(query, expected):
    _, session = _long_session()
    rows = history_evidence(session, query)
    assert expected in str(rows)
    assert all(not row["current_visit"] for row in rows if row["revision"] == 1)
    assert all(row["text"] != "我把观察日记公开了。" for row in rows)
    assert count_tokens(json.dumps(rows, ensure_ascii=False, separators=(",", ":"))) <= 600


def test_public_destination_keeps_visit_boundary_and_source():
    _, session = _long_session()
    rows = history_evidence(session, "旧码头乘船，或者小路通往档案馆？")
    assert any(row["current_visit"] and "档案馆" in row["text"] for row in rows)
    assert all(not row["current_visit"] for row in rows if "旧码头" in row["text"])
    assert evaluator._has_public_transition_quote("花店后面的小路通往档案馆。", session)
    assert not evaluator._has_public_transition_quote("旧码头可以乘船。", session)


def test_three_consumers_receive_old_fact_without_changing_actor_six_fields():
    engine, session = _long_session()
    query = "我的综合大学在哪里？"
    outcome = engine.resolve_turn(session, TurnRequestV2("recall", 15, query), (), scene_complete=False)
    actor = _turn_messages(engine, session, outcome, query, "安静克制。", "测试猫娘", "哥哥")
    judge = evaluator._build_transition_judge_messages(
        engine, session, player_input=query,
        actor_performance={"performance": "本地综合大学。", "suggested_inputs": []})
    for messages in (actor, evaluator._build_messages(engine, session, query), judge):
        assert "本地综合大学" in messages[1].content
        assert "失忆或认知边界" in messages[0].content
    assert len(json.loads(actor[1].content.split("：\n", 1)[1])) == 6


def test_optional_retrieval_fits_guard_budget_without_cutting_candidate(monkeypatch):
    engine, session = _long_session()
    candidate = {"performance": "我没有把观察日记公开，仍然保密。", "suggested_inputs": []}
    # 先量出同份固定合同的成本，再给少量检索空间；不靠扩大容量让断言通过。
    baseline = evaluator._build_transition_judge_messages(engine, session, player_input="", actor_performance=candidate)
    budget = sum(count_tokens(x.content) for x in baseline) + 40
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES["balanced"], "judge_input_max_tokens", budget)
    messages = evaluator._build_transition_judge_messages(
        engine, session, player_input="观察日记同意公开了吗？", actor_performance=candidate)
    assert sum(count_tokens(x.content) for x in messages) <= budget
    assert candidate["performance"] in messages[1].content
    assert "观察日记同意公开了吗？" in messages[1].content


@pytest.mark.parametrize("same_wording", [False, True])
def test_new_event_is_not_blocked_by_criterion_or_wording(same_wording):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    original = "（把答应归还的雨伞交还给你）雨伞还给你。"
    message = original if same_wording else "（把答应修好的台灯带来，插电亮起）台灯也修好了。"
    ledger = ({"result_revision": 1, "input_text": original, "metric_changes": [
        {"metric_id": "trust", "delta": 2, "criterion": "玩家兑现承诺"}]},)
    response = json.dumps({"scene_complete": False, "metric_changes": {
        "trust": {"strength": "normal", "criterion_id": "trust.increase.1"}}})
    result = evaluator._parse_output(response, engine, message, _session(engine), ledger)
    # 此处假定模型已依据不同对象确认新事件；重复事件能否识别须另跑真实模型正反例。
    assert len(result.metric_changes) == 1 and result.metric_changes[0].delta == 2
    # 模型必须看见旧行为原文，才能把换句话复述与另一件新行为区分开。
    assert original in evaluator._build_messages(engine, _session(engine), message, recent_ledger_events=ledger)[1].content


@pytest.mark.parametrize("fact,query", [
    ("录取已确定：我留在本地综合大学，你去外地读艺术学院。", "我们录取的学校确定了吗？"),
    ("工具已经归还：铜钥匙现在由档案管理员保管。", "工具归还以后由谁保管？"),
    ("日记授权已撤回，今天继续保密，不能公开展示。", "日记授权撤回之后还能公开吗？"),
])
def test_current_question_precedes_unrelated_route_vocabulary(fact, query):
    # 长历史中的路线词不能挤掉玩家正在询问的事实，三个题材共用同一检索规则。
    engine, session = _long_session()
    focus = "手账的未来约定，香樟树下碰面，展厅开放安排，明天结束整理。"
    rows = tuple({"revision": i, "from_node_id": "old", "to_node_id": "old",
                  "performance": focus + f"这是第{i}回合已经讨论的安排。"} for i in range(1, 22))
    fact_row = {"revision": 22, "from_node_id": "old", "to_node_id": "old", "input_text": fact}
    crossing = {"revision": 23, "from_node_id": "old", "to_node_id": "start",
                "segments": [{"phase": "target_opening", "performance": "现在开始新的话题。"}]}
    session = replace(session, revision=23, performance_history=(*rows, fact_row, crossing))
    selected = history_evidence(session, query, focus=focus)
    assert any(row["text"] == fact and not row["current_visit"] for row in selected)


def test_question_retrieval_keeps_later_revocation_with_original_permission():
    # 不能只召回较早的许可；后续撤回也是同一对象的完整原话。
    _, session = _long_session()
    rows = tuple({"revision": i, "from_node_id": "old", "to_node_id": "old", "performance": text}
                 for i, text in enumerate(["观察日记可以公开展示。", "观察日记的公开授权已撤回，继续保密。"], 1))
    session = replace(session, performance_history=rows)
    selected = history_evidence(session, "观察日记可以公开吗？", focus="花店里的花盆与浇水安排。")
    assert [row["text"] for row in selected if row["revision"] in {1, 2}] == [row["performance"] for row in rows]


@pytest.mark.parametrize("facts,claim", [
    (["我确定留在本地综合大学，你去外地艺术学院。"], "你读本地综合大学，我去外地艺术学院。"),
    (["观察日记可以公开展示。", "观察日记的公开授权已撤回，今天继续保密。"], "你同意公开展示观察日记了。"),
    (["铜钥匙已经归还木盒。", "铜钥匙后来交给档案管理员保管，不在木盒了。"], "铜钥匙还在木盒。"),
])
def test_guard_uses_claim_to_find_original_and_later_changes(facts, claim):
    # 模糊问题不含物件名；待审断言只能用于找原话，不能进入已提交历史。
    engine, session = _long_session()
    rows = tuple({"revision": i, "from_node_id": "old", "to_node_id": "old", "input_text": text}
                 for i, text in enumerate(facts, 1))
    crossing = {"revision": 3, "from_node_id": "old", "to_node_id": "start",
                "segments": [{"phase": "target_opening", "performance": "我们坐下来休息。"}]}
    session = replace(session, revision=3, performance_history=(*rows, crossing))
    messages = evaluator._build_transition_judge_messages(engine, session,
        player_input="还记得之前怎么决定的吗？", actor_performance={"performance": claim})
    data = json.loads(messages[1].content.split("：", 1)[1])
    evidence = data.get("history_evidence", [])
    assert all(any(row["text"] == fact for row in evidence) for fact in facts)
    assert all(row["text"] != claim for row in evidence)
    assert all(not row["current_visit"] for row in evidence)


@pytest.mark.parametrize('object_name', ['徽章', '唱片', '手账'])
def test_short_object_query_is_not_displaced_by_question_filler(monkeypatch, object_name):
    """对象只占一个中文二元片段时，常用问句不应优先于实际持有记录。"""
    _, session = _long_session()
    fact = f'{object_name}在莉莉的布袋里。'
    records = ({'revision': 1, 'from_node_id': 'old', 'to_node_id': 'old', 'performance': fact},
               *({'revision': i, 'from_node_id': 'old', 'to_node_id': 'old',
                  'input_text': '我们现在已经到了哪里？接下来呢？', 'performance': '先在长椅坐一会。'}
                 for i in range(2, 12)))
    session = replace(session, revision=11, performance_history=records)
    # 极小条数预算让测试直接观察排序，不能用提高容量掩盖无关匹配。
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES['balanced'], 'evidence_max_units', 1)
    rows = history_evidence(session, f'我们现在已经拿到了哪些{object_name}？分别是谁保管的？')
    assert [row['text'] for row in rows] == [fact]


def test_query_filler_alone_does_not_recall_unrelated_old_questions():
    """过滤只影响检索排序，不把常用代词当成某个道具或已发生事件的证据。"""
    _, session = _long_session()
    session = replace(session, performance_history=({'revision': 1, 'from_node_id': 'old',
        'to_node_id': 'old', 'input_text': '我们现在已经到了哪里？'},))
    assert history_evidence(session, '我们现在已经？') == []


@pytest.mark.parametrize('object_name,place', [
    ('青瓷茶杯', '旧书店'), ('银纹胸针', '修鞋铺'), ('儿童画册', '社区图书馆'),
])
def test_followup_retrieves_source_of_previous_visible_object(object_name, place):
    """追问省略全名时，上一轮公开的对象可以辅助找回跨幕来源；不预制剧本别名。"""
    _, session = _long_session()
    fact = f'{object_name}是从{place}借来的。'
    records = ({'revision': 1, 'from_node_id': 'old', 'to_node_id': 'old', 'performance': fact},
               *({'revision': i, 'from_node_id': 'start', 'to_node_id': 'start',
                  'performance': '先坐下休息一下。'} for i in range(2, 12)),
               {'revision': 12, 'from_node_id': 'start', 'to_node_id': 'start',
                'performance': f'{object_name}由管理员保管。',
                'suggested_inputs': ['那本未曾取得的故事书是从海边捡来的。']})
    session = replace(session, revision=12, node_turn_count=11, performance_history=records)
    rows = history_evidence(session, '是在哪里借的？帮我回忆一下。')
    assert any(row['text'] == fact for row in rows)
    assert all('故事书' not in row['text'] for row in rows)
