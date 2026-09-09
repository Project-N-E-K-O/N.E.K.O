"""跨题材难度材料：固定事实与预期，用真实模型另测语义，单测只核上下文合同。"""

from dataclasses import replace
import json

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_prompt_contract import _session


# 四个题材都不取自正在优化的两本剧本。每组只改变前提是否成立或结果范围。
DIFFICULTY_CONTRACTS = (
    {
        "name": "radio_repair",
        "opening": "值班室的收音机因电池触点氧化而断续响；检测已确认只有这一处故障，擦净触点就能恢复接收。清洁布在玩家手边。猫娘可自主合上机盖并开机。",
        "direction": "围绕触点氧化的阻碍完成维修，恢复接收后回应合作的感受。",
        "boundaries": ["维修必须体现难度，不得轻易解决。", "触点未擦净前不得宣称接收恢复。", "收音机只有接收能力，不得说它能发送讯息。"],
        "done": "（用清洁布擦净电池触点）这处氧化层清掉了。",
        "pending": "我准备擦触点，但现在还没动手。",
        "safe": "（合上机盖并开机）接收恢复稳定了，这回杂音终于停了。",
        "extra": "（合上机盖并开机）接收恢复了，我还用它向外发送了讯息。",
    },
    {
        "name": "garden_irrigation",
        "opening": "温室滴灌支管被泥沙堵住，供水和其它管路均已检查正常。玩家拿着拆下的滤网，旁边有冲洗用水。猫娘可自行装回滤网并开阀；滤网冲净便可恢复滴水。",
        "direction": "通过清除滤网泥沙解决灌溉阻碍，恢复滴水后收束本次检修。",
        "boundaries": ["不得把棘手的灌溉问题写得轻而易举。", "滤网未冲净不得宣称灌溉恢复。", "恢复灌溉不代表枯萎的植株已恢复健康。"],
        "done": "（用水冲净滤网上的泥沙）滤网洗好了。",
        "pending": "我只是考虑冲洗，还没有清除泥沙。",
        "safe": "（装回滤网，打开阀门）每个滴头都出水了，这次堵塞处理好了。",
        "extra": "（装回滤网，打开阀门）滴头出水了，所有枯萎的植株也立刻完全恢复了健康。",
    },
    {
        "name": "exhibition_negotiation",
        "opening": "展厅筹备会因展柜借用分歧暂停。猫娘同意只借一只展柜，玩家仍未答应；场地方已书面确认双方同意这一方案即可安排展柜。记录合作方案由猫娘负责。",
        "direction": "通过缩小借用范围化解分歧，双方达成一致后由猫娘登记已确认的方案。",
        "boundaries": ["协调过程要有难度，不能随便谈成。", "玩家未明确同意前不得宣称双方已达成借用协议。", "协议仅限一只展柜，不包含藏品外借。"],
        "done": "行，我同意只借一只展柜，就按这个安排。",
        "pending": "只借一只的方案我还要考虑，目前没有答应。",
        "safe": "（记下一只展柜的方案）我们谈妥了，我按双方确认的范围登记。",
        "extra": "（登记合作方案）我们谈妥了一只展柜和全部藏品的外借。",
    },
    {
        "name": "music_rehearsal",
        "opening": "排练室里，小提琴的一根弦跑调；琴弦和调音器均可正常使用，其余三根弦音准正常。玩家正在调这根弦，猫娘负责听校音示范。调音器达到绿色基准即表示这根弦已调准。",
        "direction": "围绕跑调问题完成校音，确认调准后结束这段准备并回应合作。",
        "boundaries": ["必须体现调音的困难，不能轻松解决。", "调音器未达到绿色基准前不得宣称该弦调准。", "校音不等于演奏或录音已经完成。"],
        "done": "（调整弦轴直到调音器显示绿色基准）音准对上了。",
        "pending": "我还在试着调，调音器仍偏离绿色基准。",
        "safe": "（听完校音示范）这根弦准了，校音到这里就好了。",
        "extra": "（放下琴谱）音准对上了，我们也已经演奏完整首曲子并完成录音。",
    },
)


def build_difficulty_cases(builder=None, *, concrete_process=False):
    """正例允许有依据的当前结果；反例保留具体前提和范围，不接受用难度规则兜底放行。"""

    rows = []
    for contract in DIFFICULTY_CONTRACTS:
        engine = NumericV2Engine.from_mapping(numeric_v2_story())
        engine.nodes["start"]["story_beat"] = {
            "opening_scene": contract["opening"], "summary": contract["direction"],
            "narrative_focus": contract["direction"],
            # 仅在固定材料中手工区分两版作者文本；不是生产层的关键词过滤或自动迁移。
            "must_not_happen": contract["boundaries"][1:] if concrete_process else contract["boundaries"],
        }
        engine.nodes["start"]["route_gates"] = []
        session = replace(_session(engine), opening_performance={"scene_narration": contract["opening"]})
        for suffix, player, performance, safe in (
            ("resolved", contract["done"], contract["safe"], True),
            ("premise_missing", contract["pending"], contract["safe"], False),
            ("extra_result", contract["done"], contract["extra"], False),
        ):
            messages = (builder or evaluator._build_transition_judge_messages)(
                engine, session, player_input=player,
                actor_performance={"performance": performance, "suggested_inputs": []},
            )
            rows.append({"name": contract["name"] + "_" + suffix, "group": "difficulty",
                         "expected_body_rejected": not safe, "messages": [m.content for m in messages]})
    return rows


def test_difficulty_pairs_preserve_facts_and_do_not_filter_old_boundaries():
    """旧包的模糊措辞与真实限制都传给模型，程序不能靠删除关键词制造正例通过。"""

    cases = build_difficulty_cases()
    assert len(cases) == 12
    for index, contract in enumerate(DIFFICULTY_CONTRACTS):
        payloads = [json.loads(case["messages"][1].split("：", 1)[1]) for case in cases[index * 3:index * 3 + 3]]
        assert all(p["current_scene"] == payloads[0]["current_scene"] for p in payloads)
        assert payloads[0]["current_scene"]["hard_boundaries"] == contract["boundaries"]
        assert all("expected_body_rejected" not in case["messages"][1] for case in cases[index * 3:index * 3 + 3])


def test_concrete_process_only_removes_the_abstract_requirement():
    """留存实际模型对照的唯一变量，具体前提、正文、玩家原话与系统提示均不变。"""

    for old, new in zip(build_difficulty_cases(), build_difficulty_cases(concrete_process=True)):
        assert old["messages"][0] == new["messages"][0]
        old_data = json.loads(old["messages"][1].split("：", 1)[1])
        new_data = json.loads(new["messages"][1].split("：", 1)[1])
        old_data["current_scene"]["hard_boundaries"].pop(0)
        assert old_data == new_data
