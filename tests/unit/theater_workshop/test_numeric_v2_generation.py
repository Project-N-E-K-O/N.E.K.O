from __future__ import annotations
from theater_workshop.host import InProcessPackageGateway

import json

import pytest

from theater_workshop.sdk.generation.numeric_v2 import (
    _BRANCH_ENDING_PROMPT,
    _BRANCH_PATH_PROMPT,
    _SCENE_PROCESS_AUTHORING_RULE,
    _MAINLINE_PROMPT,
    _MAINLINE_CONTINUATION_PROMPT,
    _NODE_ENHANCEMENT_PROMPT,
    _validate_idea_outline,
    _pacing_diagnostics_for_outline,
    NumericV2GenerationError,
    NumericV2Generator,
)
from theater_workshop.sdk.generation.quality import (
    _ASSESSMENT_PROMPT,
    _NODE_OPTIMIZATION_PROMPT,
    QUALITY_DIMENSIONS,
    NumericV2QualityAssessor,
    QualityAssessmentError,
)
from theater_workshop.sdk.generation.facts import FACT_REVIEW_PROMPT
from theater_workshop.sdk.numeric_v2 import NumericV2Compiler, scene_turn_budget
from .numeric_v2_fixture import numeric_v2_setup


@pytest.mark.parametrize("prompt", [
    _MAINLINE_PROMPT, _MAINLINE_CONTINUATION_PROMPT, _NODE_ENHANCEMENT_PROMPT,
    _BRANCH_ENDING_PROMPT, _BRANCH_PATH_PROMPT,
    _NODE_OPTIMIZATION_PROMPT,
], ids=["mainline", "continuation", "enhancement", "branch-ending", "branch-path", "optimization"])
def test_concrete_process_rule_covers_generation_and_revision_entries(prompt):
    # Short IDs keep PYTEST_CURRENT_TEST below Windows' environment-value limit.
    # 修补、完善与支线入口同样会产出边界，不能仅修首次主线生成后又被旧合同覆盖。
    assert prompt.count(_SCENE_PROCESS_AUTHORING_RULE) == 1
    assert prompt.endswith(_SCENE_PROCESS_AUTHORING_RULE)
    # 双人演绎取向须覆盖续写、支线和改稿，不能只在首份主线中生效。
    assert "主线、支线及结局的关键互动优先由男主与女主推进" in prompt
    assert "不默认把配角的连续问答、实际操作、评价或认可作为推进和收束的必要条件" in prompt
    assert "男主的选择、行动和台词仍由玩家输入决定" in prompt
    assert "配角被直接询问时仍可简短回答、拒绝或说明未知" in prompt
    # 多出口预览不冻结路线，也不要求玩家先把所有候选支线都完成。
    assert "不要求把全部候选支线的调查都完成才离幕" in prompt
    assert "玩家接受时仍按最新数值确定路线" in prompt
    assert "各目标入口先兑现这项共同行动" in prompt
    # 出口明确已知去向和参与者，不能让后续改稿重新变成无界的调查/护送任务。
    assert "谁同行及配角留在哪里" in prompt
    assert "这四处必须兑现同一去向、时段和开始的活动" in prompt
    assert "单个出口不能写‘去A或去B’却只通往C" in prompt
    assert "不能在换幕后临时编造捷径" in prompt
    assert "不以含糊标识诱导另造终端或第二次救援" in prompt
    # 出口三要素沿用既有作者字段；生成与改稿都不能追加可选素材作为离幕前提。
    assert "trigger_fact 只列离开真正必要的具体结果" in prompt
    assert "并安排在来源幕自然公开" in prompt
    assert "下一幕的操作、调查和角色选择不得反写为本幕离开的前提" in prompt
    # 剧情转折须有当下可执行选择；设备等待不能在换幕时凭空完成。
    assert "写角色此刻能公开并让玩家决定的移动、等候或协作安排" in prompt
    assert "玩家同意后再由桥段承接时间经过" in prompt
    # 所有写稿入口同步连续动作的历史适配，不能完善后又新增分幕禁令。
    assert "玩家提前明确实施后，运行时会承接结果" in prompt
    # 六个生成/评改入口共享主动转场及未授权反例的规则。
    assert "主动要求进入已公开的下一地点、下一阶段" in prompt
    assert "询问、考虑或准备不算开始" in prompt
    # 所有共用写稿入口都应区分输入前提冲突与从当前状态开始的新动作。
    assert "角色应自然提醒实际情况" in prompt
    assert "从当前状态实施的新可行动作仍正常承接" in prompt
    assert "不必再邀请、再确认一次" in prompt
    # 写作、续写和评改都要保留回应的具体对象，但不能把台词模板变成硬性任务。
    assert "必要角色反应写清她回应哪项选择或结果，以及为什么在意" in prompt
    assert "不把示例台词固定为必说原文" in prompt
    # 各生成/评改入口都应保留反应的因果和阶段差异，不能要求每次互动都升温。
    assert "女主原先在意的具体事" in prompt
    assert "不能把本幕尚待发生的反应提前写成入幕事实" in prompt
    assert "不要求每幕改变立场" in prompt
    assert "ending.summary 承接变化的原因" in prompt
    assert "不为了制造变化强行和解、彻底改观或升温" in prompt
    # 结局开场、摘要和状态线必须同一时点，不能通过状态字段偷偷新增玩家行动。
    assert "结束不等于散场，不默认男主收拾物品、离开或补签手续" in prompt
    # 完善与评分建议也不得重新为已关闭输入的结局分配普通回合任务。
    assert "结局目标只声明开场已经交付的环境事实" in prompt
    # 用户明确状态时点为开场结束，七个作者入口不能重新写成开场前或整幕完成后。
    assert "开场演完、第一条玩家输入之前" in prompt
    assert "不能把 turn 目标的预期结果提前写入状态" in prompt
    # 七个入口都须区分约定与执行，防止完善/评改重新追加未来行动作为收束前提。
    assert "本幕要交付达成约定还是实际执行" in prompt
    assert "不把未来执行列为本幕缺项" in prompt
    assert "不能只用同意方案代替完成" in prompt
    # 所有创作入口保留同一完成主体与范围，不能从示范扩大成全员体验结束。
    assert "一人示范不代替另一人的亲手操作" in prompt
    assert "出口和结局不得改写成所有参与者均已完成" in prompt
    assert "已授权且结果可确定的最后互动仍可同轮交付" in prompt
    # 新生成及评改都沿用历史适配合同，不能重新要求逐字复演来源动作。
    assert "运行时会依据实际游玩历史改写桥段与开场措辞" in prompt
    assert "写清必须保留的时间、地点、必要结果和阶段边界" in prompt
    assert "不把再等一会儿、再确认一遍或追加告别作为必经步骤" in prompt
    # 七个生成/评改入口都须保留同一主体范围，避免后续完善重新写出无主体的硬边界。
    assert "每条限制写明主体、对象及受限行为或必要前提" in prompt
    assert "只限制口头表达时，不扩大为禁止行动反馈，也不约束其他角色" in prompt
    assert "禁止项须与本幕安排的事件及角色反应相容" in prompt
    assert "不新增玩家禁令或额外前提" in prompt
    assert "scene_boundaries 写空数组" in prompt
    assert "不得用‘不得结束本幕活动’" in prompt
    assert "key_prop_state_changes 记录本幕实际规划的出幕变化" in prompt
    assert "不能只留在 world.rules 或 intervention_capacity" in prompt
    assert "用主体、操作对象、行为和可观察结果表达剧情" in prompt
    assert "没有阻碍的互动也不必强加阻碍" in prompt
    assert "必要的工具、角色协作或玩家决定必须明确" in prompt
    assert "不要把抽象评价改写为必须反复操作、额外确认或增加失败次数" in prompt
    # 各入口必须区分当前反馈与真正的跨阶段机会，防止评改时又把奖励写成转场提议。
    assert "proposal 写进入下一阶段的具体邀请或机会" in prompt
    assert "并与下一幕实际开场相容" in prompt
    assert "不能单独冒充跨阶段提议" in prompt
    assert "本幕准备任务与未来见面分开提出" in prompt
    # 所有写作和评改入口共享终局例外，防止修订阶段再次要求额外确认。
    assert "上述邀请要求适用于角色提出的普通换幕" in prompt
    assert "结局的 player_decision 可以为空" in prompt
    assert "结局仍需要玩家尚未作出的选择" in prompt
    # 续写与评改也要保留同轮收束，避免重新追加确认或把最后动作在结局重演。
    assert "可以在同一轮完成互动、角色回应并自然结束" in prompt
    assert "仅考虑或准备不算授权" in prompt
    assert "桥段与结局开场承接结果，不再重演同一动作" in prompt


def test_mainline_player_decision_example_matches_actionable_exit_rule():
    """Keep ordinary transition choices in the example; permit null only for final closure with no unresolved choice."""

    assert '"player_decision": "普通换幕保留可执行选择；最后一幕自然收束且没有未决选择时为空字符串"' in _MAINLINE_PROMPT


def _idea_outline(chapter_count: int = 4) -> dict:
    ceilings = ["guarded", "cooperative", "trusted", "intimate"]
    return {
        "world": {
            "background": "雨季小镇与保存旧信的花店。",
            "rules": ["旧信中的信息必须通过现实证据交叉确认。"],
            "core_mystery": "当年的分别是否源于一场被隐瞒的误会。",
            "core_conflict": "两人都想知道真相，却害怕再次被对方抛下。",
        },
        "story_protagonist": {
            "identity": "女主，守着花店和旧信的年轻女性。",
            "secret_or_wound": "她一直认为玩家当年不告而别。",
            "motivation": "确认玩家是否值得再次信任。",
        },
        "player_role": {
            "identity": "男主，多年后回乡处理旧屋的年轻男性。",
            "entry_reason": "整理旧物时发现与旧信有关的线索。",
            "intervention_capacity": "能够调查旧物、兑现承诺并决定是否留下。",
        },
        "relationship": "多年未联系、仍保留旧日误会的儿时邻居",
        "tone": ["克制", "温柔", "雨季怀旧"],
        "relationship_arc": {
            "opening_relationship": "多年未联系、仍保留旧日误会的儿时邻居",
            "long_term_direction": "通过核验旧信和兑现承诺逐步重建信任。",
            "stages": [
                {
                    "chapter_index": index + 1,
                    "stage_ceiling": ceilings[min(index, len(ceilings) - 1)],
                    "address_state": "introduced_in_scene" if index == 0 else "known_from_prior_scene",
                    "known_player_facts": [] if index == 0 else ["男主已经说明回乡原因"],
                    "allowed_behaviors": ["保持礼貌并核验事实"],
                    "forbidden_behaviors": ["把未来承诺当作已经兑现"],
                    "progress_opportunity": "女主展示旧信，提供核对当年事实的机会。",
                    "reset_reason": "",
                }
                for index in range(chapter_count)
            ],
        },
        "character_state_arc": {
            "stages": [
                {
                    "chapter_index": index + 1,
                    "catgirl_state": "女主身体状态正常，记得此前已经成立的调查事实。",
                    "player_state": "男主身体状态正常，尚未替他预设新的选择或行动。",
                    "environment_state": "环境保持雨季花店及其已建立的物品状态。",
                    "acting_contract": {
                        "cognition_state": "normal",
                        "memory_state": "available",
                        "self_reference_mode": "persona_allowed",
                        "persona_scope": "full",
                        "dialogue_policy": "required",
                        "assertable_self_facts": [],
                        "allowed_behaviors": ["核对已经展示的旧信"],
                        "forbidden_behaviors": ["虚构尚未发生的共同经历"],
                    },
                    "continuity_from_previous": [] if index == 0 else ["旧信仍由女主保管"],
                    "scene_boundaries": ["不得把女主展示旧信的职责转交给男主"],
                }
                for index in range(chapter_count)
            ],
            "ending_stage": {
                "catgirl_state": "女主保留已经核验的记忆，没有再次失忆或重新启动。",
                "player_state": "男主保留已经作出的选择，不替他追加结局后的承诺。",
                "environment_state": "环境延续雨停后已经整理完成的花店现场。",
                "acting_contract": {
                    "cognition_state": "normal",
                    "memory_state": "available",
                    "self_reference_mode": "persona_allowed",
                    "persona_scope": "full",
                    "dialogue_policy": "required",
                    "assertable_self_facts": [],
                    "allowed_behaviors": ["承接已经核验的旧信事实"],
                    "forbidden_behaviors": ["重新询问已经确认的身份"],
                },
                "continuity_from_previous": ["旧信的日期已经完成核验"],
                "scene_boundaries": ["不得把普通结局重新演成初次相遇"],
            },
        },
        "key_props": [{
            "id": "dated_old_letter",
            "name": "写有日期的旧信",
            "purpose": "核对当年离开时间与收信记录",
            "states": [{
                "chapter_index": 1,
                "owner": "catgirl",
                "state": "由女主保管，纸面日期可直接核验",
            }],
        }],
        "mainline_chapters": [
            {
                "title": f"第 {index + 1} 章",
                "narrative": "雨水沿着花店玻璃缓缓滑落，写有日期的旧信摊在桌面上。新的事实将改变两人对当年事件的理解，但尚未完成整章冲突。",
                "narrative_focus": "旧信上的日期正在把当年的误会引向一条可以核对的现实线索。",
                "expected_turns": 4,
                "opening_scene": "雨水沿着花店玻璃缓缓滑落，写有日期的旧信摊在桌面上。",
                "entry_bridge": "" if index == 0 else "雨声渐缓，花店阁楼的灯在下一次整点时亮起。",
                "transition_goal": "在核对当前记录后，自然引出下一份可交叉验证的证据。",
                "exit_plan": {
                    "trigger_fact": "当前旧信的日期已经完成核对。",
                    "proposal_owner": "catgirl",
                    "proposal": "继续核对下一份收信记录。",
                    "player_decision": "是否现在继续核对。",
                    "preserve_facts": ["当年离开时间仍存在待解矛盾"],
                    "carry_props": ["dated_old_letter"],
                },
                "ordered_goals": [
                    {
                        "owner": "catgirl",
                        "delivery_type": "catgirl_action",
                        "description": "女主把写有日期的旧信原件放到桌面上。",
                        "evidence_mode": "semantic",
                        "anchors": [],
                        "sources": ["opening"],
                    }
                ],
                "catgirl_situation": "女主掌握旧信原件，既想核实真相，又担心再次被男主隐瞒。",
            }
            for index in range(chapter_count)
        ],
        "ending": {
            "type": "normal",
            "title": "带着理解分别",
            "summary": "真相部分揭开。",
            "opening_scene": "雨停后，摊开的旧信留在花店桌面，当年的误会已有了可核对的答案。",
            "entry_bridge": "最后一封旧信核验完毕，窗外持续多日的雨声终于停下。",
        },
    }


def _generation_setup() -> dict:
    setup = numeric_v2_setup()
    setup["brief"] = "玩家多年后回到雨季小镇，与守着一封旧信的猫娘重逢，两人必须解开当年的误会并决定未来。"
    return setup


def test_numeric_v2_generator_normalizes_profile_part_punctuation():
    assert NumericV2Generator._join_profile_parts(
        "男主，身份说明。",
        "进入原因。",
        "介入能力。",
    ) == "男主，身份说明。进入原因。介入能力。"


def test_numeric_v2_generator_calls_model_once_for_mainline_and_one_normal_ending_only():
    generator = NumericV2Generator()
    calls: list[dict] = []

    def fake_call(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        return json.dumps(_idea_outline(), ensure_ascii=False)

    generator.call_llm = fake_call
    setup = _generation_setup()
    result = generator.generate(title="清河晚风", setup=setup)

    assert len(calls) == 1
    assert calls[0]["max_retries"] == 1
    assert calls[0]["operation"] == "numeric_v2_mainline_generation"
    assert json.loads(calls[0]["messages"][1]["content"]) == {
        "core_idea": setup["brief"],
            "length": {
                "preset": "short",
                "mainline_chapter_min": 3,
                "mainline_chapter_max": 6,
                "scene_expected_turns_target": 8,
            },
    }
    story = result["story"]
    assert story["meta"]["author"] == "NEKO_Numeric_drama"
    # 作者已显式填写 100 时必须保留，不把“默认 50”误当成强制重写。
    assert story["metric_schema"]["trust"]["max"] == 100
    assert story["initial_state"]["metrics"] == {"trust": 20}
    assert [node["type"] for node in story["nodes"]].count("ending") == 1
    assert len([node for node in story["nodes"] if node["type"] != "ending"]) == 4
    assert all(
        node["min_turns"] == 3 and node["recommended_turns"] == 3
        for node in story["nodes"]
        if node["type"] != "ending"
    )
    prompt = calls[0]["messages"][0]["content"]
    assert "关系变化必须由共同经历、信息确认、边界协商或实际选择逐步支撑" in prompt
    assert "相邻章节只能推进一小级" in prompt
    assert "温柔、甜美、傲娇等是表达风格，不代表关系已经建立" in prompt
    assert "必须生成 relationship_arc 作为作者侧关系弧规划" in prompt
    assert "必须生成 character_state_arc 作为逐幕角色状态线" in prompt
    assert "long_term_direction 只供作者规划未来" in prompt
    assert story["endings"] == [{
        "id": "ending_normal",
        "title": "带着理解分别",
        "summary": "真相部分揭开。",
        "terminal": True,
    }]
    assert story["nodes"][0]["route_gates"][0]["conditions"] == {"all": []}
    assert story["intro"]["background"] == "雨季小镇与保存旧信的花店。"
    assert "世界规则：" not in story["intro"]["background"]
    assert "核心悬念：" not in story["intro"]["background"]
    assert "核心矛盾：" not in story["intro"]["background"]
    assert result["setup_updates"]["relationship"] == "多年未联系、仍保留旧日误会的儿时邻居"
    assert result["relationship_arc"]["stages"][0]["node_id"] == "mainline_01"
    assert result["character_state_arc"]["stages"][0]["node_id"] == "mainline_01"
    assert story["nodes"][0]["story_beat"]["acting_contract"] == {
        "cognition_state": "normal",
        "memory_state": "available",
        "self_reference_mode": "persona_allowed",
        "persona_scope": "full",
        "dialogue_policy": "required",
        "allowed_behaviors": ["核对已经展示的旧信"],
        "forbidden_behaviors": ["虚构尚未发生的共同经历"],
    }
    assert story["nodes"][0]["story_beat"]["narrative_focus"] == (
        "旧信上的日期正在把当年的误会引向一条可以核对的现实线索。"
    )
    assert result["pacing_diagnostics"]["status"] == "pass"
    assert story["nodes"][0]["story_beat"]["character_state"] == {
        "catgirl_state": "女主身体状态正常，记得此前已经成立的调查事实。",
        "player_state": "男主身体状态正常，尚未替他预设新的选择或行动。",
        "environment_state": "环境保持雨季花店及其已建立的物品状态。",
        "continuity_from_previous": [],
        "scene_boundaries": ["不得把女主展示旧信的职责转交给男主"],
    }
    assert "不得把女主展示旧信的职责转交给男主" in story["nodes"][0]["story_beat"]["must_not_happen"]
    assert "男主身体状态正常" in story["nodes"][0]["story_beat"]["catgirl_situation"]
    assert story["nodes"][0]["route_gates"][0]["transition_contract"]["must_preserve"] == [
        "当年离开时间仍存在待解矛盾",
        "关键道具“写有日期的旧信”[dated_old_letter]：用途为核对当年离开时间与收信记录；当前归属为女主；状态为由女主保管，纸面日期可直接核验。",
        "旧信仍由女主保管",
    ]
    assert story["nodes"][0]["story_beat"]["transition_goal"] == (
        "在核对当前记录后，自然引出下一份可交叉验证的证据。"
    )
    assert story["nodes"][0]["route_gates"][0]["transition_contract"]["reason"] == (
        "触发事实：当前旧信的日期已经完成核对；"
        "女主提出：继续核对下一份收信记录；"
        "仍由玩家决定：是否现在继续核对。"
    )
    assert story["nodes"][-1]["story_beat"]["acting_contract"]["memory_state"] == "available"
    assert story["nodes"][-1]["story_beat"]["character_state"]["continuity_from_previous"] == [
        "旧信的日期已经完成核验"
    ]
    assert "可能重新信任" not in story["catgirl_binding"]["role_overlay"]
    assert "在试探中重建信任" not in story["catgirl_binding"]["role_overlay"]
    assert story["catgirl_binding"]["role_overlay"].startswith(
        "开场关系上限：戒备；称呼未知，介绍后方可使用；已知男主：无。"
    )
    assert "整理旧物时发现" not in story["intro"]["player_identity"]
    assert "能够调查旧物" not in story["intro"]["player_identity"]
    assert "确认玩家是否值得" not in story["intro"]["catgirl_identity"]
    prompt = calls[0]["messages"][0]["content"]
    assert "不生成支线" in prompt
    assert "不生成数值" in prompt
    assert "只生成一个 normal 结局" in prompt
    assert "剧情主角必须是女性" in prompt
    assert "玩家角色必须是男性" in prompt
    assert "story_protagonist.identity 必须以“女主，”开头" in prompt
    assert "player_role.identity 必须以“男主，”开头" in prompt
    assert "只把会影响剧情的关键道具写入 key_props" in prompt
    assert "world.background 是直接展示给玩家的“前情提要”" in prompt
    assert "凡是在这段可见正文中指代玩家，一律使用第二人称“你”" in prompt
    assert "第一章必须从玩家尚未进行任何输入的状态开始" in prompt
    assert "不能提前写入第一章才会交付或由男主决定的结果" in prompt
    assert "narrative 是作者侧梗概" in prompt
    assert "该句会直接作为换幕起始画面" not in prompt
    assert "不得替玩家说出台词、作出选择、完成关键行动" in prompt
    assert "opening_scene 和 entry_bridge 都不能新增男主的主动行为" in prompt
    assert "每章必须给出 opening_scene、entry_bridge、narrative_focus、expected_turns、transition_goal、exit_plan" in prompt
    assert "narrative_focus 只写一句当前最值得继续发展的因果或互动方向" in prompt
    assert "expected_turns 是作者对本幕从开场到自然离幕的大致普通回合数估计" in prompt
    assert "不得把 narrative、ordered_goals 或 exit_plan 中的预期内容写成已经完成" in prompt
    assert "没有这类道具时写空数组" in prompt
    assert "exit_plan.carry_props 只能引用这些 id" in prompt
    assert "每项只表达一个原子交付" in prompt
    assert "catgirl_action 的锚点必须是可直接放入括号动作块" in prompt
    assert "catgirl_dialogue、catgirl_action、environment_fact、player_action 和 shared_agreement 默认使用 semantic" in prompt
    assert "只有 core_idea 明确要求某段不可改写文本必须逐字出现时" in prompt
    assert "不要用“确立基调”“加深关系”等抽象主题充当目标" in prompt
    assert "输入的 cast_names 是生成时的真实姓名快照" in prompt
    assert "N.E.K.O 开演时仍按当时的名字适配" in prompt
    assert "显式填写 owner、delivery_type、evidence_mode、anchors、sources、timing" in prompt
    assert "fallback_player_inputs" not in prompt
    assert "dialogue_policy_after" in prompt
    assert "需要玩家决定时使用 owner=player 或 shared" in prompt
    assert "9月17日的旧信" not in prompt
    assert "可核对的实际值必须直接写进 description" in prompt
    assert "指代玩家时必须使用第二人称“你”" in prompt
    assert "不能让演绎模型临时编造" in prompt
    assert "实际可见或可听的内容" in prompt
    assert "每项只写一个原子交付" in _NODE_ENHANCEMENT_PROMPT
    assert "默认使用 semantic 且 anchors 为空" in _NODE_ENHANCEMENT_PROMPT
    assert "默认使用 semantic 且 anchors 为空" in _MAINLINE_CONTINUATION_PROMPT
    assert "opening_scene 或 entry_bridge" in _MAINLINE_CONTINUATION_PROMPT
    assert "只能把玩家行动留给普通回合" in _NODE_ENHANCEMENT_PROMPT
    assert "不得让 N.E.K.O 临时编造神秘人" in _NODE_ENHANCEMENT_PROMPT
    assert "guarded 不得出现依赖、拥抱、牵手或亲密结论" in _MAINLINE_CONTINUATION_PROMPT
    assert "不能把温柔、甜美或傲娇等表达风格写成突然建立" in _NODE_ENHANCEMENT_PROMPT
    assert "支线沿用 global.intro 中的明确姓名及角色归属" in _BRANCH_PATH_PROMPT
    assert "不把当前设备的新名字混入既有作者项目" in _BRANCH_PATH_PROMPT
    assert "不能留下让 N.E.K.O 演绎时临时编造的空白" in _BRANCH_PATH_PROMPT
    assert "默认使用 semantic 且 anchors 为空" in _BRANCH_PATH_PROMPT
    assert "exact anchors 指代玩家时必须使用最终可见的第二人称“你”" in _BRANCH_PATH_PROMPT
    assert "多个必须全部成立的条件、数量、期限或范围" in _BRANCH_PATH_PROMPT
    assert "不得写入新的玩家身体行动" in _BRANCH_PATH_PROMPT
    assert "每一幕只能推进一小级" in _BRANCH_PATH_PROMPT
    assert "不得仅因进入支线就从警惕或疏离跳到粘人" in _BRANCH_PATH_PROMPT
    assert "不得把 source 或 scene 的预期目标写成已经完成" in _BRANCH_PATH_PROMPT
    assert "不得只给抽象结论" in _BRANCH_PATH_PROMPT
    assert "expected_turns 是作者对本幕从开场到自然离幕的大致普通回合数估计" in _BRANCH_PATH_PROMPT
    # 作者已确认先事实后文学：时序核对移入事实阶段，文学阶段消费其报告。
    assert "按路线的实际 source/target" in FACT_REVIEW_PROMPT
    assert "fact_review 是完整事实报告" in _ASSESSMENT_PROMPT
    assert "路线可辨识性" in _ASSESSMENT_PROMPT
    assert "不能只靠隐藏数值暗中分流" in _ASSESSMENT_PROMPT
    assert "relationship_effect 标成 none" in _ASSESSMENT_PROMPT
    assert "证据可演性" in _ASSESSMENT_PROMPT
    assert story["meta"]["contract_version"] == "v2.2"
    assert story["initial_state"]["player_address_known"] is False
    assert story["nodes"][0]["story_beat"]["goals"][0]["delivery"] == {
        "type": "catgirl_action",
        "output_field": "performance_action",
        "source_ids": ["opening.mainline_01"],
        "timing": "turn",
    }
    assert story["nodes"][0]["story_beat"]["goals"][0]["evidence"] == {
        "mode": "semantic",
        "anchors": [],
    }
    assert story["nodes"][-1]["story_beat"]["goals"][0]["evidence"] == {
        "mode": "semantic",
        "anchors": [],
    }
    assert story["nodes"][0]["story_beat"]["catgirl_situation"] == (
        "女主身体状态正常，记得此前已经成立的调查事实。"
        "男主身体状态正常，尚未替他预设新的选择或行动。"
        "环境保持雨季花店及其已建立的物品状态。"
        "女主掌握旧信原件，既想核实真相，又担心再次被男主隐瞒。"
        "关系上限：戒备；称呼未知，介绍后方可使用；"
        # 道具只保留固定资料，生命周期中的签收等规划不再作为入幕现状拼接。
        "已知男主：无；禁止：把未来承诺当作已经兑现。\n"
        "道具资料（仅定义用途，不表示已取得或操作完成）："
        "关键道具“写有日期的旧信”[dated_old_letter]：用途为核对当年离开时间与收信记录。"
    )


def test_numeric_v2_generator_does_not_publish_planned_prop_change_as_entry_state():
    """Planned chapter-end signing or ownership changes must not overwrite the player's undecided opening state."""

    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["key_props"][0]["states"][0].update(
        owner="player", state="玩家完成签收，旧信已交给玩家保管"
    )
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    result = generator.generate(title="清河晚风", setup=_generation_setup())

    beat = result["story"]["nodes"][0]["story_beat"]
    assert "玩家完成签收" not in beat["catgirl_situation"]
    # 修复时态不能丢失不随签收变化的名称和用途，否则 Actor 会重新猜测道具能力。
    assert "写有日期的旧信" in beat["catgirl_situation"]
    assert "核对当年离开时间与收信记录" in beat["catgirl_situation"]
    assert "尚未替他预设新的选择或行动" in beat["catgirl_situation"]
    assert beat["opening_scene"] == candidate["mainline_chapters"][0]["opening_scene"]
    # 作者规划继续留在作者侧，出幕携带合同仍可承接本幕达成后的状态。
    assert result["key_props"][0]["states"][0]["state"] == "玩家完成签收，旧信已交给玩家保管"
    assert any("玩家完成签收" in item for item in
               result["story"]["nodes"][0]["route_gates"][0]["transition_contract"]["must_preserve"])


def test_numeric_v2_generator_projects_multiple_atomic_mainline_events():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][0]["ordered_goals"] = [
        {
            "owner": "catgirl", "delivery_type": "catgirl_action",
            "description": "女主把旧信原件放到桌面上。", "evidence_mode": "exact",
            "anchors": ["旧信原件", "放到桌面"], "sources": ["opening"],
        },
        {
            "owner": "catgirl", "delivery_type": "catgirl_dialogue",
            "description": "女主说明旧信是在花店阁楼找到的。", "evidence_mode": "exact",
            "anchors": ["花店阁楼"], "sources": ["previous_goal"],
        },
        {
            "owner": "environment", "delivery_type": "environment_fact",
            "description": "环境让信封背面的寄出日期显露出来。", "evidence_mode": "exact",
            "anchors": ["寄出日期"], "sources": ["previous_goal"],
        },
    ]
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    result = generator.generate(title="清河晚风", setup=_generation_setup())

    goals = result["story"]["nodes"][0]["story_beat"]["goals"]
    assert [goal["owner"] for goal in goals] == ["catgirl", "catgirl", "environment"]
    assert goals[1]["delivery"]["source_ids"] == ["goal.mainline_01_goal_01"]
    assert result["story"]["nodes"][0]["min_turns"] == 3
    assert result["story"]["nodes"][0]["recommended_turns"] == 3


@pytest.mark.parametrize(
    ("turn_goal_count", "expected"),
    [
        (0, (3, 3)),
        (1, (3, 3)),
        (2, (3, 3)),
        (3, (3, 3)),
        (4, (3, 3)),
        (6, (3, 3)),
    ],
)
def test_scene_turn_budget_uses_compact_complexity_bands(turn_goal_count, expected):
    goals = [
        {"timing": "turn", "description": f"目标 {index}"}
        for index in range(turn_goal_count)
    ]

    assert scene_turn_budget(goals) == expected


def test_scene_turn_budget_keeps_runtime_recommendation_at_three_for_long_author_estimates():
    # expected_turns 只服务作者侧节奏诊断，不能把 Runtime 的推荐回合拉长。
    assert scene_turn_budget([], 6) == (3, 3)
    assert scene_turn_budget([], 120) == (3, 3)


def test_mainline_pacing_diagnostics_warns_when_player_exit_action_is_missing():
    candidate = _idea_outline(chapter_count=4)
    candidate["mainline_chapters"][0]["exit_plan"]["player_decision"] = ""

    diagnostics = _pacing_diagnostics_for_outline(candidate)

    scene = diagnostics["scenes"][0]
    assert diagnostics["status"] == "warning"
    assert scene["natural_exit"]["available"] is False
    assert "scene_natural_exit_player_action_missing" in scene["warning_codes"]


def test_mainline_final_natural_ending_allows_no_extra_decision():
    # 最终收束不是缺少玩家动作；普通幕的空决定仍由相邻用例检查并报告。
    candidate = _idea_outline(chapter_count=4)
    candidate["mainline_chapters"][-1]["exit_plan"]["player_decision"] = ""
    diagnostics = _pacing_diagnostics_for_outline(candidate)
    assert diagnostics["status"] == "pass"
    assert diagnostics["scenes"][-1]["natural_exit"]["available"] is True
    assert not _validate_idea_outline(candidate, minimum=4, maximum=6)


def test_mainline_pacing_diagnostics_rejects_observer_only_exit_description():
    candidate = _idea_outline(chapter_count=4)
    candidate["mainline_chapters"][0]["exit_plan"]["player_decision"] = "无，仅作为观察者见证结局"

    diagnostics = _pacing_diagnostics_for_outline(candidate)

    scene = diagnostics["scenes"][0]
    assert scene["natural_exit"]["available"] is False
    assert "scene_natural_exit_player_action_not_actionable" in scene["warning_codes"]


def test_mainline_outline_validation_flags_observer_only_exit_description():
    candidate = _idea_outline(chapter_count=4)
    candidate["mainline_chapters"][0]["exit_plan"]["player_decision"] = "仅作为观察者见证结局"

    issues = _validate_idea_outline(candidate, minimum=4, maximum=6)

    assert any(
        issue["code"] == "transition_player_decision_not_actionable"
        for issue in issues
    )


def test_mainline_outline_validation_requests_short_scene_rewrite_when_over_target():
    candidate = _idea_outline(chapter_count=4)
    candidate["mainline_chapters"][0]["expected_turns"] = 10

    issues = _validate_idea_outline(
        candidate,
        minimum=4,
        maximum=6,
        scene_expected_turns_target=8,
    )

    assert any(
        issue["code"] == "scene_expected_turns_exceed_target"
        and issue["path"] == "mainline_chapters[0]"
        for issue in issues
    )


def test_mainline_pacing_diagnostics_warns_when_estimate_exceeds_eight_turns():
    candidate = _idea_outline(chapter_count=4)
    candidate["mainline_chapters"][0]["ordered_goals"] = [
        {"timing": "turn", "description": f"第 {index + 1} 个普通回合交付"}
        for index in range(8)
    ]

    diagnostics = _pacing_diagnostics_for_outline(candidate)

    scene = diagnostics["scenes"][0]
    assert scene["estimated_turns"] == 9
    assert "scene_expected_turns_exceed_8" in scene["warning_codes"]


def test_mainline_pacing_diagnostics_uses_declared_turn_estimate_and_warns_over_forty():
    candidate = _idea_outline(chapter_count=4)
    candidate["mainline_chapters"][0]["expected_turns"] = 41

    diagnostics = _pacing_diagnostics_for_outline(candidate)

    scene = diagnostics["scenes"][0]
    assert scene["expected_turns"] == 41
    assert scene["estimated_turns"] == 41
    assert "scene_expected_turns_exceed_8" in scene["warning_codes"]
    assert "scene_expected_turns_exceed_40" in scene["warning_codes"]


def test_mainline_pacing_diagnostics_marks_missing_turn_estimate_instead_of_guessing():
    candidate = _idea_outline(chapter_count=4)
    candidate["mainline_chapters"][0].pop("expected_turns")

    diagnostics = _pacing_diagnostics_for_outline(candidate)

    scene = diagnostics["scenes"][0]
    assert scene["expected_turns"] is None
    assert "scene_expected_turns_unknown" in scene["warning_codes"]


@pytest.mark.parametrize(
    ("owner", "delivery_type", "description", "sources", "output_field"),
    [
        ("catgirl", "catgirl_dialogue", "女主自然说明旧信来自阁楼。", ["opening"], "performance_dialogue"),
        ("catgirl", "catgirl_action", "女主自然地把旧信转向男主。", ["opening"], "performance_action"),
        ("environment", "environment_fact", "环境显露信封背面的寄出日期。", ["opening"], "scene_update"),
        ("player", "player_action", "男主说明愿意继续核对旧信。", ["player_input"], "player_input"),
    ],
)
def test_numeric_v2_generator_allows_semantic_evidence_for_typed_outputs(
    owner,
    delivery_type,
    description,
    sources,
    output_field,
):
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][0]["ordered_goals"][0] = {
        "owner": owner,
        "delivery_type": delivery_type,
        "description": description,
        "evidence_mode": "semantic",
        "anchors": [],
        "sources": sources,
    }
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    goal = generator.generate(title="自然语义目标", setup=_generation_setup())["story"]["nodes"][0]["story_beat"]["goals"][0]

    assert goal["evidence"] == {"mode": "semantic", "anchors": []}
    assert goal["delivery"]["type"] == delivery_type
    assert goal["delivery"]["output_field"] == output_field


def test_numeric_v2_generator_rejects_character_state_arc_with_swapped_subjects():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["character_state_arc"]["stages"][1]["player_state"] = "女主正在发热并需要服药。"
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    with pytest.raises(NumericV2GenerationError) as caught:
        generator.generate(title="状态主体不可倒置", setup=_generation_setup())

    assert "character_state_player_subject_invalid" in {
        issue["code"] for issue in caught.value.issues
    }


def test_numeric_v2_generator_does_not_match_scene_boundary_prefixes():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["character_state_arc"]["stages"][0]["scene_boundaries"] = [
        "避免把女主展示旧信的职责转交给男主"
    ]
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    story = generator.generate(title="状态边界自然措辞", setup=_generation_setup())["story"]

    assert "避免把女主展示旧信的职责转交给男主" in story["nodes"][0]["story_beat"]["must_not_happen"]


def test_numeric_v2_generator_projects_explicit_fresh_boot_state_only_where_declared():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    stage = candidate["character_state_arc"]["stages"][0]
    stage["catgirl_state"] = "女主刚刚首次启动，只知道视觉校准已经完成。"
    stage["acting_contract"].update({
        "cognition_state": "fresh_boot",
        "memory_state": "empty",
        "self_reference_mode": "system_neutral",
        "persona_scope": "style_only",
        "assertable_self_facts": ["视觉校准已经完成"],
        "forbidden_behaviors": ["使用角色卡自称", "虚构旧记忆"],
    })
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    story = generator.generate(title="首次启动", setup=_generation_setup())["story"]

    assert story["nodes"][0]["story_beat"]["acting_contract"]["cognition_state"] == "fresh_boot"
    assert story["nodes"][1]["story_beat"]["acting_contract"]["cognition_state"] == "normal"


def test_numeric_v2_generator_accepts_player_goal_without_generated_fallback_input():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][0]["ordered_goals"][0] = {
        "owner": "player",
        "delivery_type": "player_action",
        "description": "男主明确说明愿意继续核对旧信。",
        "evidence_mode": "exact",
        "anchors": ["愿意继续核对"],
        "sources": ["player_input"],
    }
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    story = generator.generate(title="玩家自由推进", setup=_generation_setup())["story"]

    delivery = story["nodes"][0]["story_beat"]["goals"][0]["delivery"]
    assert delivery["output_field"] == "player_input"
    assert "fallback_player_inputs" not in delivery


def test_numeric_v2_generator_does_not_regex_rewrite_visible_background():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["world"]["background"] = "晨雾中，女主死死盯着玩家手中的修复工具。"
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return json.dumps(candidate, ensure_ascii=False)

    generator.call_llm = fake_call
    result = generator.generate(title="清河晚风", setup=_generation_setup())

    assert result["story"]["intro"]["background"] == candidate["world"]["background"]
    assert calls == 1


def test_numeric_v2_generator_rejects_relationship_arc_that_skips_stages():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["relationship_arc"]["stages"][1]["stage_ceiling"] = "intimate"
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    with pytest.raises(NumericV2GenerationError) as caught:
        generator.generate(title="清河晚风", setup=_generation_setup())

    assert any(
        issue["code"] == "relationship_arc_stage_jump"
        for issue in caught.value.issues
    )


def test_numeric_v2_generator_rejects_only_explicit_opening_knowledge_leak():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["relationship"] = "女主醒来后绝对依赖男主，把他视为唯一安全源。"
    candidate["relationship_arc"]["opening_relationship"] = candidate["relationship"]
    candidate["relationship_arc"]["stages"][0]["known_player_facts"] = ["男主是修理工"]
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    with pytest.raises(NumericV2GenerationError) as caught:
        generator.generate(title="清河晚风", setup=_generation_setup())

    codes = {issue["code"] for issue in caught.value.issues}
    assert "relationship_arc_opening_knowledge_leak" in codes


def test_numeric_v2_generator_allows_explicit_relationship_reset_after_memory_loss():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][-1]["catgirl_situation"] = "女主记忆完全清空，不认识男主。"
    reset_stage = candidate["relationship_arc"]["stages"][-1]
    reset_stage.update({
        "stage_ceiling": "stranger",
        "address_state": "introduced_in_scene",
        "known_player_facts": [],
        "reset_reason": "女主记忆归零",
    })
    state_stage = candidate["character_state_arc"]["stages"][-1]
    state_stage["catgirl_state"] = "女主记忆已经归零，只能确认当前视觉输入。"
    state_stage["acting_contract"].update({
        "cognition_state": "fresh_boot",
        "memory_state": "empty",
        "self_reference_mode": "system_neutral",
        "persona_scope": "style_only",
        "assertable_self_facts": ["当前视觉输入可用"],
    })
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    result = generator.generate(title="清河晚风", setup=_generation_setup())

    assert "关系上限：陌生" in result["story"]["nodes"][3]["story_beat"]["catgirl_situation"]


def test_numeric_v2_generator_does_not_regex_classify_opening_relationship_wording():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["world"]["background"] = "维修台指示灯转绿，女主即将睁开电子眼。"
    candidate["relationship"] = "救助者与受助者，初建的共生关系。"
    candidate["relationship_arc"]["opening_relationship"] = candidate["relationship"]
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    result = generator.generate(title="清河晚风", setup=_generation_setup())

    assert result["setup_updates"]["relationship"] == candidate["relationship"]


def test_numeric_v2_generator_does_not_regex_classify_chapter_relationship_wording():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][1]["narrative"] += "两人的关系达到前所未有的亲密。"
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    story = generator.generate(title="清河晚风", setup=_generation_setup())["story"]

    assert story["nodes"][1]["story_beat"]["summary"].endswith("前所未有的亲密。")


def test_numeric_v2_generator_saves_checkpoint_after_three_incomplete_attempts():
    generator = NumericV2Generator()
    calls = 0

    def fake_call(_messages, **_kwargs):
        nonlocal calls
        calls += 1
        # 三幕已是合法短篇；两幕仍不足，用它验证失败候选和修订次数被完整保留。
        return json.dumps(_idea_outline(chapter_count=2), ensure_ascii=False)

    generator.call_llm = fake_call
    with pytest.raises(NumericV2GenerationError) as caught:
        generator.generate(title="清河晚风", setup=_generation_setup())

    assert calls == 3
    assert caught.value.code == "invalid_mainline_generation"
    assert "mainline_chapters" in {issue["path"] for issue in caught.value.issues}
    assert caught.value.attempts == 3
    assert caught.value.checkpoint["candidate"]["mainline_chapters"] == _idea_outline(chapter_count=2)["mainline_chapters"]


def test_numeric_v2_generator_rejects_swapped_runtime_roles_without_retry():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["story_protagonist"]["identity"] = "陆晨，木艺坊的年轻男性。"
    candidate["player_role"]["identity"] = "苏晓眠，经营甜品店的年轻女性。"
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    with pytest.raises(NumericV2GenerationError) as caught:
        generator.generate(title="角色不可交换", setup=_generation_setup())

    assert caught.value.code == "invalid_mainline_generation"
    assert {issue["code"] for issue in caught.value.issues} == {
        "catgirl_role_marker_required",
        "player_role_marker_required",
    }


def test_numeric_v2_generator_rejects_player_owned_scene_goal_without_retry():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][0]["ordered_goals"][0]["owner"] = "player"
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return json.dumps(candidate, ensure_ascii=False)

    generator.call_llm = fake_call

    with pytest.raises(NumericV2GenerationError) as caught:
        generator.generate(title="玩家行动不可预写", setup=_generation_setup())

    assert calls == 3
    assert caught.value.code == "invalid_mainline_generation"
    assert {issue["code"] for issue in caught.value.issues} == {
        "goal_delivery_owner_mismatch",
        "player_goal_source_required",
    }


def test_numeric_v2_generator_continues_only_invalid_paths_after_initial_outline():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][0]["ordered_goals"][0]["owner"] = "player"
    calls = []

    def fake_call(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        if len(calls) == 1:
            return json.dumps(candidate, ensure_ascii=False)
        model_input = json.loads(messages[1]["content"])
        assert model_input["requested_paths"] == [
            "mainline_chapters[0].ordered_goals[0].owner",
            "mainline_chapters[0].ordered_goals[0].sources",
        ]
        return json.dumps({
            "replacements": {
                "mainline_chapters[0].ordered_goals[0].owner": "catgirl",
                "mainline_chapters[0].ordered_goals[0].sources": ["opening"],
            },
        }, ensure_ascii=False)

    generator.call_llm = fake_call
    story = generator.generate(title="局部续写", setup=_generation_setup())["story"]

    assert len(calls) == 2
    assert calls[0]["operation"] == "numeric_v2_mainline_generation"
    assert calls[1]["operation"] == "numeric_v2_mainline_continuation"
    assert story["nodes"][0]["story_beat"]["goals"][0]["owner"] == "catgirl"
    assert story["nodes"][1]["story_beat"]["summary"] == _idea_outline()["mainline_chapters"][1]["narrative"]


def test_numeric_v2_generator_resumes_checkpoint_without_regenerating_valid_outline():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][0]["ordered_goals"][0]["owner"] = "player"
    calls = []

    def fake_call(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        return json.dumps({
            "replacements": {
                "mainline_chapters[0].ordered_goals[0].owner": "catgirl",
            },
        }, ensure_ascii=False)

    generator.call_llm = fake_call
    story = generator.generate(
        title="检查点续写",
        setup=_generation_setup(),
        checkpoint={
            "candidate": candidate,
            "issues": [{"path": "mainline_chapters[0].ordered_goals[0].owner"}],
        },
    )["story"]

    assert len(calls) == 1
    assert calls[0]["operation"] == "numeric_v2_mainline_continuation"
    assert story["nodes"][0]["story_beat"]["goals"][0]["owner"] == "catgirl"


def test_numeric_v2_generator_retries_invalid_json_three_times():
    generator = NumericV2Generator()
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return "not-json"

    generator.call_llm = fake_call

    with pytest.raises(NumericV2GenerationError) as caught:
        generator.generate(title="无效 JSON", setup=_generation_setup())

    assert calls == 3
    assert caught.value.code == "invalid_model_json"
    assert caught.value.attempts == 3
    assert caught.value.checkpoint is None


def test_numeric_v2_generator_allows_player_marker_as_catgirl_action_target():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][0]["ordered_goals"][0].update({
        "description": "女主把备用钥匙递给男主。",
    })
    candidate["mainline_chapters"][1]["narrative"] = (
        "女主把账单推向男主。账单上的欠费日期让两人意识到中介早有预谋。"
    )
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    story = generator.generate(title="角色槽位投影", setup=_generation_setup())["story"]

    assert story["intro"]["player_identity"].startswith("男主，")
    assert story["intro"]["catgirl_identity"].startswith("女主，")
    assert story["nodes"][0]["story_beat"]["goals"][0]["description"] == "女主把备用钥匙递给男主。"
    assert story["nodes"][1]["story_beat"]["summary"].startswith(
        "女主把账单推向男主。"
    )


def test_numeric_v2_generator_projects_long_context_delivery_controls():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    goal = candidate["mainline_chapters"][0]["ordered_goals"][0]
    goal.update({
        "owner": "player",
        "delivery_type": "semantic_state",
        "evidence_mode": "semantic",
        "anchors": [],
        "sources": ["player_input"],
        "timing": "turn",
        "dialogue_policy_after": "forbidden",
    })
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(
        candidate,
        ensure_ascii=False,
    )

    story = generator.generate(title="长程演绎合同", setup=_generation_setup())["story"]

    delivery = story["nodes"][0]["story_beat"]["goals"][0]["delivery"]
    assert delivery["timing"] == "turn"
    assert "fallback_player_inputs" not in delivery
    assert delivery["state_effects"] == {"dialogue_policy": "forbidden"}


def test_numeric_v2_generator_rejects_player_goal_completed_by_opening():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][0]["ordered_goals"][0].update({
        "owner": "player",
        "delivery_type": "player_action",
        "sources": ["opening"],
        "timing": "opening",
    })
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return json.dumps(candidate, ensure_ascii=False)

    generator.call_llm = fake_call

    with pytest.raises(NumericV2GenerationError) as caught:
        generator.generate(title="开场不能替玩家行动", setup=_generation_setup())

    assert calls == 3
    assert {issue["code"] for issue in caught.value.issues} == {
        "opening_goal_owner_invalid",
        "player_goal_source_required",
    }


def test_numeric_v2_generator_allows_shared_goal_to_follow_previous_goal():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][0]["ordered_goals"].append({
        "owner": "shared",
        "delivery_type": "shared_agreement",
        "description": "双方根据已经核验的旧信同意继续查看下一份记录。",
        "evidence_mode": "semantic",
        "anchors": [],
        "sources": ["previous_goal"],
        "timing": "turn",
        "dialogue_policy_after": "unchanged",
    })
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(
        candidate,
        ensure_ascii=False,
    )

    story = generator.generate(title="共同目标承接上一步", setup=_generation_setup())["story"]

    shared_goal = story["nodes"][0]["story_beat"]["goals"][1]
    assert shared_goal["owner"] == "shared"
    assert shared_goal["delivery"]["source_ids"] == ["goal.mainline_01_goal_01"]


@pytest.mark.parametrize("player_opening", [
    "男主推开库房门。女主随后展示木料。",
    "玩家推开库房门。女主随后展示木料。",
    "他推开库房门。女主随后展示木料。",
])
def test_numeric_v2_generator_does_not_regex_classify_chapter_opening(player_opening):
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][1]["narrative"] = player_opening
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    story = generator.generate(title="章节开场模型负责", setup=_generation_setup())["story"]

    assert story["nodes"][1]["story_beat"]["summary"] == player_opening


def test_numeric_v2_generator_does_not_regex_repair_player_action_wording():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][1]["narrative"] = (
        "为了验证日志上的线索，你提议在午夜后巡视站台。女主起初拒绝。"
    )
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return json.dumps(candidate, ensure_ascii=False)

    generator.call_llm = fake_call
    story = generator.generate(title="玩家开场归属修复", setup=_generation_setup())["story"]

    assert calls == 1
    assert story["nodes"][1]["story_beat"]["summary"] == candidate["mainline_chapters"][1]["narrative"]


def test_numeric_v2_generator_does_not_regex_repair_vague_date_wording():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][0]["narrative"] = (
        "雨水沿着伞架滴落。女主取下一把标有特定日期的旧伞。"
    )
    candidate["mainline_chapters"][0]["ordered_goals"][0].update({
        "description": "女主将旧伞挂回高处。",
    })
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return json.dumps(candidate, ensure_ascii=False)

    generator.call_llm = fake_call
    story = generator.generate(title="具体证据修复", setup=_generation_setup())["story"]

    assert calls == 1
    assert "特定日期" in story["nodes"][0]["story_beat"]["summary"]


def test_numeric_v2_generated_mainline_compiles_without_route_metric_conditions():
    generator = NumericV2Generator()
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(_idea_outline(), ensure_ascii=False)

    story = generator.generate(title="清河晚风", setup=_generation_setup())["story"]

    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(story)

    assert compiled.story["nodes"][-1]["type"] == "ending"
    assert all(
        route["conditions"] == {"all": []}
        for node in compiled.story["nodes"]
        for route in node["route_gates"]
    )
    first_transition = story["nodes"][0]["route_gates"][0]["transition_contract"]["must_deliver"]
    assert first_transition == [
        "雨声渐缓，花店阁楼的灯在下一次整点时亮起。"
    ]
    assert _idea_outline()["mainline_chapters"][1]["narrative"] not in first_transition[0]
    ending_transition = story["nodes"][-2]["route_gates"][0]["transition_contract"]["must_deliver"]
    assert ending_transition == [
        "最后一封旧信核验完毕，窗外持续多日的雨声终于停下。"
    ]


def test_numeric_v2_generator_keeps_original_idea_and_returns_only_derived_setup_fields():
    generator = NumericV2Generator()
    calls: list[dict] = []
    idea = "玩家多年后回到雨季小镇，与守着一封旧信的猫娘重逢，两人必须解开当年的误会并决定未来。"
    def fake_call(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        return json.dumps(_idea_outline(), ensure_ascii=False)

    generator.call_llm = fake_call
    setup = numeric_v2_setup()
    setup["brief"] = idea
    result = generator.generate(title="清河晚风", setup=setup)

    assert len(calls) == 1
    assert calls[0]["max_retries"] == 1
    assert calls[0]["operation"] == "numeric_v2_mainline_generation"
    assert calls[0]["max_tokens"] == 16000
    assert json.loads(calls[0]["messages"][1]["content"]) == {
        "core_idea": idea,
            "length": {
                "preset": "short",
                "mainline_chapter_min": 3,
                "mainline_chapter_max": 6,
                "scene_expected_turns_target": 8,
            },
    }
    assert "brief" not in result["setup_updates"]
    assert result["setup_updates"]["relationship"] == "多年未联系、仍保留旧日误会的儿时邻居"
    assert result["setup_updates"]["tone"] == ["克制", "温柔", "雨季怀旧"]
    assert "资深互动小说叙事架构师" in calls[0]["messages"][0]["content"]
    assert "不得参考标题、题材" not in calls[0]["messages"][0]["content"]


def _quality_payload(*, weak_dimension: str | None = None, target_node_id: str = "") -> dict:
    scores = {
        dimension: {
            "score": 82 if dimension != weak_dimension else 68,
            "summary": f"{QUALITY_DIMENSIONS[dimension]['label']}评估说明",
        }
        for dimension in QUALITY_DIMENSIONS
    }
    issues = []
    if weak_dimension:
        issues.append({
            "dimension": weak_dimension,
            # 测试替身必须像原生评分一样声明具体字段，不能只填可自动修订的标签。
            "repair_targets": [{"node_id": target_node_id, "field": "/summary"}],
            "severity": "major",
            "target_node_ids": [target_node_id],
            "problem": "该部分缺少足够铺垫。",
            "modification_plan": "补足因果铺垫并保持既有结局方向。",
            "expected_result": "转折可以由前文事实自然推出。",
            "preserve": ["保留人物关系的渐进变化"],
            "repair_scope": "text",
        })
    return {
        "scores": scores,
        "strengths": ["双角色关系方向清楚"],
        "metric_advice": {
            "recommended_count": 1,
            "summary": "一个信任指标足以支撑当前路线。",
            "add": [],
            "remove": [],
            "merge": [],
        },
        "relationship_advice": [],
        "issues": issues,
    }


def _quality_wire_payload(payload):
    # 原有报告断言继续检查持久化协议；模型替身按新的逐维填写协议输出。
    from copy import deepcopy
    wire = deepcopy(payload)
    issues = wire.pop("issues", [])
    for dimension, row in wire.get("scores", {}).items():
        row.setdefault("fact_issue_ids", [])
        row["issues"] = [{key: value for key, value in issue.items() if key != "dimension"}
                         for issue in issues if issue["dimension"] == dimension]
    return wire


def _plan_review_reply(messages):
    # 独立的模型替身逐条返回复核，不把旧报告静默当成已复核报告。
    plans = json.loads(messages[1]["content"])["plans"]
    return json.dumps({"conflicts": [], "checks": [{"issue_id": row["issue_id"], "status": "ready",
        "reason": "测试方案与字段一致", "missing_targets": []} for row in plans]})


def _reviewed_fixture(report):
    # 仅供下游补丁测试：这些测试显式构造已通过方案复核的报告；旧报告测试不调用它。
    report["repair_plan_version"] = 3
    for row in [*report.get("issues", []), *report.get("relationship_advice", [])]:
        row["plan_review"] = {"status": "ready", "reason": "测试方案与字段一致", "missing_targets": [], "conflicting_issue_ids": []}
    return report


def _quality_reply(payload):
    # 文学测试使用合法空事实报告，避免把第一阶段结构失败误认作文学校验失败。
    def reply(messages, **kwargs):
        if kwargs.get("operation") == "numeric_v2_fact_review":
            context = json.loads(messages[1]["content"])
            return json.dumps({"checked_node_ids": context["checked_node_ids"], "issues": []})
        if kwargs.get("operation") == "numeric_v2_repair_plan_review":
            return _plan_review_reply(messages)
        return json.dumps(_quality_wire_payload(payload), ensure_ascii=False)
    return reply


def _quality_story_context():
    story = NumericV2Generator()._project_story(
        title="清河晚风",
        original_idea=_generation_setup()["brief"],
        setup=_generation_setup(),
        outline=_idea_outline(),
        tone=["克制", "温柔"],
    )
    authoring = {
        "mainline_node_ids": [f"mainline_{index:02d}" for index in range(1, 5)],
        "key_props": _idea_outline()["key_props"],
    }
    return story, authoring


def test_numeric_v2_quality_assessor_runs_only_when_called_and_returns_node_plans():
    assessor = NumericV2QualityAssessor()
    calls = []

    def fake_call(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        if kwargs.get("operation") in {"numeric_v2_fact_review", "numeric_v2_repair_plan_review"}:
            return _quality_reply({})(messages, **kwargs)
        payload = _quality_payload()
        payload["relationship_advice"] = [{
            "repair_targets": [{"node_id": "mainline_01", "field": "/summary"}],
            "target_node_ids": ["mainline_01"],
            "problem": "开场关系仍有误会。",
            "suggestion": "让双方先核验旧信来源。",
            "expected_result": "关系推进建立在共同证据上。",
            "repair_scope": "text",
        }]
        return json.dumps(_quality_wire_payload(payload), ensure_ascii=False)

    assessor.call_llm = fake_call
    story, authoring = _quality_story_context()
    story["nodes"][1]["route_gates"].append({
        "id": "route_to_branch_outside_scope",
        "target_node_id": "branch_scene",
        "conditions": {
            "all": [{"type": "metric_compare", "metric": "trust", "op": ">=", "value": 30}]
        },
    })
    story["nodes"].append({"id": "branch_scene", "type": "scene", "route_gates": []})
    authoring["route_semantics"] = {
        "route_to_branch_outside_scope": {"label": "当信任达到试探状态"}
    }
    assessment = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)

    assert assessment["passed"] is True
    assert assessment["overall_score"] == 82.0
    assert set(assessment["scores"]) == set(QUALITY_DIMENSIONS)
    assert len(assessment["content_sha256"]) == 64
    assert assessment["metric_advice"]["current_count"] == 1
    assert assessment["relationship_advice"][0]["target_node_ids"] == ["mainline_01"]
    assert assessment["relationship_advice"][0]["repairable"] is True
    assert [call["operation"] for call in calls] == ["numeric_v2_fact_review", "numeric_v2_quality_assessment", "numeric_v2_repair_plan_review"]
    assert "fact_review" in calls[1]["messages"][0]["content"]
    model_input = json.loads(calls[1]["messages"][1]["content"])
    outline = model_input["story_outline"]
    assert set(outline) == {
        "author_intent", "characters", "metrics", "key_props", "mainline", "branches", "endings"
    }
    assert outline["key_props"][0]["id"] == "dated_old_letter"
    assert outline["mainline"][0]["opening_scene"].startswith("雨水沿着花店玻璃")
    assert outline["mainline"][0]["transition_goal"].startswith("在核对当前记录后")
    assert outline["mainline"][0]["outgoing_routes"][0]["transition_contract"][
        "bridge_scene_narration"
    ] == "雨声渐缓，花店阁楼的灯在下一次整点时亮起。"
    assert outline["endings"][0]["id"] == "ending_normal"
    assert outline["endings"][0]["opening_scene"].startswith("雨停后")
    assert outline["branches"][0]["id"] == "route_to_branch_outside_scope"
    assert outline["branches"][0]["nodes"][0]["id"] == "branch_scene"
    assert outline["branches"][0]["entry_condition"]["label"] == "当信任达到试探状态"
    assert outline["branches"][0]["entry_condition"]["rules"][0] == {
        "metric": "trust", "op": ">=", "value": 30
    }
    assert set(outline["mainline"][0]) == {
        "id", "title", "summary", "opening_scene", "goals", "relationship_state",
        "relationship_goal", "transition_goal", "outgoing_routes", "character_state",
        "narrative_focus", "must_not_happen", "acting_contract",
        "text_repair_fields",
    }
    assert "min_turns" not in json.dumps(model_input, ensure_ascii=False)


def test_numeric_v2_quality_assessor_rejects_incomplete_dimension_schema():
    assessor = NumericV2QualityAssessor()
    payload = _quality_payload()
    payload["scores"].pop("emotional_resonance")
    assessor.call_llm = _quality_reply(payload)
    story, authoring = _quality_story_context()

    with pytest.raises(QualityAssessmentError) as caught:
        assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)

    assert caught.value.code == "invalid_quality_assessment"


@pytest.mark.parametrize("score", [75, 100])
@pytest.mark.parametrize("severity", ["minor", "major", "blocking"])
def test_numeric_v2_quality_assessor_keeps_every_issue_for_passing_dimension(score, severity):
    # 用户要求全部展示；轻微建议不否定分数合格，严重问题必须阻止报告通过。
    assessor = NumericV2QualityAssessor()
    payload = _quality_payload(weak_dimension="plot", target_node_id="mainline_02")
    payload["scores"]["plot"]["score"] = score
    payload["issues"][0]["severity"] = severity
    assessor.call_llm = _quality_reply(payload)
    story, authoring = _quality_story_context()

    assessment = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)

    assert assessment["passed"] is (severity == "minor")
    assert assessment["failed_dimensions"] == []
    assert assessment["scores"]["plot"]["score"] == score
    assert len(assessment["issues"]) == 1
    assert assessment["issues"][0]["severity"] == severity
    assert assessment["issues"][0]["repairable"] is True


def test_numeric_v2_quality_assessor_optimizes_one_map_node_with_all_relevant_suggestions():
    assessor = NumericV2QualityAssessor()
    story, authoring = _quality_story_context()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    payload = _quality_payload(weak_dimension="plot", target_node_id="mainline_02")
    payload["issues"][0]["repair_targets"].append({"node_id": "mainline_02", "field": "/transition_goal"})
    payload["relationship_advice"] = [{
        "repair_targets": [{"node_id": "mainline_02", "field": "/summary"}],
        "target_node_ids": ["mainline_02"],
        "problem": "双方的信任推进缺少共同动作。",
        "suggestion": "让两人共同核验旧信日期，并分别承担一次判断风险。",
        "expected_result": "关系推进由共同选择支撑。",
        "repair_scope": "text",
    }]
    assessment = assessor._validated_assessment(context, payload)
    _reviewed_fixture(assessment)
    calls = []

    def fake_call(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        return json.dumps({
            "node_updates": [{
                "node_id": "mainline_02",
                "story_beat": {
                    "summary": "两人共同核验旧信日期，并各自承担判断错误的风险。",
                    "transition_goal": "让共同选择成为下一幕调查与关系推进的直接因果。",
                },
            }],
        }, ensure_ascii=False)

    assessor.call_llm = fake_call
    optimized = assessor.optimize_node(
        story=story,
        setup=_generation_setup(),
        authoring=authoring,
        assessment=assessment,
        node_id="mainline_02",
    )

    assert calls[0]["operation"] == "numeric_v2_quality_single_node_optimization"
    model_input = json.loads(calls[0]["messages"][1]["content"])
    assert model_input["target_node"]["id"] == "mainline_02"
    assert {item["source"] for item in model_input["accepted_suggestions"]} == {
        "quality_issue",
        "relationship_advice",
    }
    assert optimized["nodes"][1]["story_beat"]["summary"].startswith("两人共同核验")
    assert optimized["nodes"][0] == story["nodes"][0]
    assert optimized["nodes"][1]["route_gates"] == story["nodes"][1]["route_gates"]


def test_quality_repair_updates_existing_state_text_without_rewriting_scene_or_permissions():
    from copy import deepcopy
    # 已获确认的状态文本修正应直达状态字段，不得被迫改写本来正确的开场。
    assessor = NumericV2QualityAssessor()
    story, authoring = _quality_story_context()
    original = deepcopy(story)
    assessment = assessor._validated_assessment(
        assessor._assessment_context(story, _generation_setup(), authoring),
        _quality_payload(weak_dimension="plot", target_node_id="mainline_02"),
    )
    _reviewed_fixture(assessment)
    assessment["issues"][0]["repair_targets"] = [{"node_id": "mainline_02", "field": "/character_state/catgirl"}]
    replacement = "女主双手空着，旧信已放在柜台上。"
    assessor.call_llm = lambda *_args, **_kwargs: json.dumps({"node_updates": [{
        "node_id": "mainline_02", "story_beat": {
            "character_state": {"catgirl_state": replacement},
        },
    }]}, ensure_ascii=False)
    repaired = assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring,
                                     assessment=assessment, node_id="mainline_02")
    expected = deepcopy(original)
    expected["nodes"][1]["story_beat"]["character_state"]["catgirl_state"] = replacement
    assert repaired == expected
    assert story == original
    NumericV2Compiler(InProcessPackageGateway()).compile(repaired)


@pytest.mark.parametrize("state_patch", [
    {}, {"catgirl_state": ""}, {"player_state": []},
    {"scene_boundaries": []}, {"continuity_from_previous": []},
    {"acting_contract": {"dialogue_policy": "forbidden"}},
    {"unknown_field": "女主站在柜台旁。"},
])
def test_quality_state_text_repair_rejects_empty_or_permission_changes(state_patch):
    from copy import deepcopy
    # 修正持物/站位描述不授权改认知、边界或状态结构；失败不能污染调用方的原稿。
    story, _ = _quality_story_context()
    original = deepcopy(story)
    with pytest.raises(QualityAssessmentError):
        NumericV2QualityAssessor._apply_node_updates(story, {"node_updates": [{
            "node_id": "mainline_02", "story_beat": {"character_state": state_patch},
        }]}, ["mainline_02"])
    assert story == original


@pytest.mark.parametrize("existing_state", [None, {}, {"player_state": "男主站在柜台旁。"}])
def test_quality_state_text_repair_does_not_invent_missing_legacy_state(existing_state):
    from copy import deepcopy
    # 本入口只修已有文本；旧包缺主体状态时，不能凭一条修订建议创建新的状态结构。
    story, _ = _quality_story_context()
    story["nodes"][1]["story_beat"]["character_state"] = existing_state
    original = deepcopy(story)
    with pytest.raises(QualityAssessmentError):
        NumericV2QualityAssessor._apply_node_updates(story, {"node_updates": [{
            "node_id": "mainline_02", "story_beat": {"character_state": {
                "catgirl_state": "女主双手空着。",
            }},
        }]}, ["mainline_02"])
    assert story == original


def test_quality_context_retains_goal_identity_evidence_and_state_effects():
    from copy import deepcopy
    # 评分要能定位错误目标并核对证据/发声变化，不能在精简输入时丢掉这些依据。
    story, authoring = _quality_story_context()
    goal = story["nodes"][0]["story_beat"]["goals"][0]
    goal["delivery"]["state_effects"] = {"dialogue_policy": "forbidden"}
    original = deepcopy(story)
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    projected = context["mainline"][0]["goals"][0]
    assert projected["id"] == goal["id"]
    assert projected["source_ids"] == goal["delivery"]["source_ids"]
    assert projected["evidence"] == goal["evidence"]
    assert projected["state_effects"] == {"dialogue_policy": "forbidden"}
    projected["source_ids"].clear()
    projected["state_effects"].clear()
    assert story == original


def test_quality_context_retains_delivery_output_and_route_sources_without_expanding_repair_scope():
    from copy import deepcopy
    # 行动主体需要结合输出字段核对，桥段结果则要能追溯来源目标；两者均只读。
    story, authoring = _quality_story_context()
    node = story["nodes"][0]
    goal = node["story_beat"]["goals"][0]
    route = node["route_gates"][0]
    route["transition_contract"]["source_ids"] = [f"goal.{goal['id']}"]
    original = deepcopy(story)
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    compact = context["mainline"][0]
    assert compact["goals"][0]["output_field"] == goal["delivery"]["output_field"]
    sources = compact["outgoing_routes"][0]["transition_contract"]["source_ids"]
    assert sources == route["transition_contract"]["source_ids"]
    sources.clear()
    assert story == original
    # 能读交付来源不代表文字修订可以改写证据，仍由现有白名单拒绝。
    with pytest.raises(QualityAssessmentError):
        NumericV2QualityAssessor._apply_node_updates(story, {"node_updates": [{
            "node_id": node["id"], "routes": [{"route_id": route["id"],
                "transition_contract": {"source_ids": ["runtime.player_input"]}}],
        }]}, [node["id"]])
    assert story == original


def test_numeric_v2_quality_assessor_marks_structural_plan_as_manual_only():
    assessor = NumericV2QualityAssessor()
    story, authoring = _quality_story_context()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    payload = _quality_payload(weak_dimension="plot", target_node_id="mainline_02")
    payload["issues"][0]["modification_plan"] = "在第二章后插入一个新的过渡节点并重连路线。"
    payload["issues"][0]["repair_scope"] = "structure"

    assessment = assessor._validated_assessment(context, payload)
    _reviewed_fixture(assessment)

    assert assessment["issues"][0]["repairable"] is False
    with pytest.raises(QualityAssessmentError) as caught:
        assessor.optimize_node(
            story=story,
            setup=_generation_setup(),
            authoring=authoring,
            assessment=assessment,
            node_id="mainline_02",
        )
    assert caught.value.code == "quality_node_not_repairable"


@pytest.mark.parametrize("preset,count", [
    ("short", 2), ("short", 7), ("standard", 5), ("standard", 11),
    ("long", 9), ("long", 17),
])
def test_numeric_v2_generator_rejects_mainline_outside_length_range(preset, count):
    # 仅放宽短篇下限；两端越界及其它篇幅仍须拒绝，不能把三幕许可扩大为不限幕数。
    generator = NumericV2Generator()
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(
        _idea_outline(chapter_count=count),
        ensure_ascii=False,
    )

    with pytest.raises(NumericV2GenerationError) as caught:
        setup = numeric_v2_setup()
        setup["length_preset"] = preset
        setup["brief"] = "玩家来到陌生小镇后遇到猫娘，两人共同寻找失踪的旧友并逐渐建立信任。"
        generator.generate(title="陌生小镇", setup=setup)

    assert caught.value.code == "invalid_mainline_generation"
    assert "mainline_chapters" in {issue["path"] for issue in caught.value.issues}


@pytest.mark.parametrize("preset,count,minimum,maximum", [
    ("short", 3, 3, 6), ("short", 6, 3, 6),
    ("standard", 6, 6, 10), ("standard", 10, 6, 10),
    ("long", 10, 10, 16), ("long", 16, 10, 16),
])
def test_mainline_length_boundaries_generate_without_padding(preset, count, minimum, maximum):
    """Project valid chapter counts directly into the story instead of invoking continuation to invent chapters for an obsolete minimum."""

    generator = NumericV2Generator()
    calls = []

    def fake_call(messages, **kwargs):
        calls.append(messages)
        return json.dumps(_idea_outline(chapter_count=count), ensure_ascii=False)

    generator.call_llm = fake_call
    setup = _generation_setup()
    setup["length_preset"] = preset
    result = generator.generate(title="自然分幕", setup=setup)
    assert len(calls) == 1
    length = json.loads(calls[0][1]["content"])["length"]
    assert (length["mainline_chapter_min"], length["mainline_chapter_max"]) == (minimum, maximum)
    story = result["story"]
    assert sum(node["type"] != "ending" for node in story["nodes"]) == count
    assert sum(node["type"] == "ending" for node in story["nodes"]) == 1
    if preset == "short" and count == 3:
        # 新下限必须能通过真实 N.E.K.O 编译链，独立结局不拿来凑第三个主线章节。
        NumericV2Compiler(InProcessPackageGateway()).compile(story)


def test_numeric_v2_generator_continues_only_missing_transition_contract_fields():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    transition_goal = candidate["mainline_chapters"][0].pop("transition_goal")
    exit_plan = candidate["mainline_chapters"][0].pop("exit_plan")
    calls = []

    def fake_call(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        if len(calls) == 1:
            return json.dumps(candidate, ensure_ascii=False)
        model_input = json.loads(messages[1]["content"])
        assert set(model_input["requested_paths"]) == {
            "mainline_chapters[0].transition_goal",
            "mainline_chapters[0].exit_plan",
        }
        return json.dumps({
            "replacements": {
                "mainline_chapters[0].transition_goal": transition_goal,
                "mainline_chapters[0].exit_plan": exit_plan,
            },
        }, ensure_ascii=False)

    generator.call_llm = fake_call
    story = generator.generate(title="转场续写", setup=_generation_setup())["story"]

    assert len(calls) == 2
    assert story["nodes"][0]["story_beat"]["transition_goal"] == transition_goal


def test_numeric_v2_generator_rejects_unknown_key_prop_reference():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["mainline_chapters"][0]["exit_plan"]["carry_props"] = ["missing_prop"]
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    with pytest.raises(NumericV2GenerationError) as caught:
        generator.generate(title="道具引用", setup=_generation_setup())

    assert caught.value.code == "invalid_mainline_generation"
    assert any(
        issue["code"] == "transition_key_prop_unknown"
        and issue["path"] == "mainline_chapters[0].exit_plan.carry_props[0]"
        for issue in caught.value.issues
    )


def test_numeric_v2_generator_allows_multiple_key_prop_changes_in_one_chapter():
    generator = NumericV2Generator()
    candidate = _idea_outline()
    candidate["key_props"][0]["states"].append({
        "chapter_index": 1,
        "owner": "player",
        "state": "女主展示后交由男主暂时核对",
    })
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)

    result = generator.generate(title="同幕道具变化", setup=_generation_setup())

    assert result["key_props"][0]["states"][-1] == {
        "node_id": "mainline_01",
        "owner": "player",
        "state": "女主展示后交由男主暂时核对",
    }


def test_numeric_v2_node_enhancement_sends_node_type_context_and_metrics_once():
    generator = NumericV2Generator()
    story = NumericV2Generator()._project_story(
        title="清河晚风",
        original_idea="玩家回乡后与猫娘一起查清旧信背后的误会。",
        setup=_generation_setup(),
        outline=_idea_outline(),
        tone=["克制", "温柔"],
    )
    branch = {
        "id": "scene_branch",
        "type": "scene",
        "chapter": "雨夜坦白",
        "story_beat": {
            "summary": "玩家在雨夜主动说明当年离开的原因。",
            # 完善已有节点必须看见它已有的开场与状态，而不是仅凭摘要重新猜处境。
            "opening_scene": "旧信仍摊在两人之间，女主等候解释。",
            "character_state": {"catgirl_state": "女主仍保留意见，尚未听到本轮解释。"},
            "must_happen": [""],
            "must_not_happen": [],
            "catgirl_situation": "",
            "transition_goal": "",
        },
        "route_gates": [],
    }
    story["nodes"].append(branch)
    story["nodes"][0]["route_gates"].append({
        "id": "route_to_branch",
        "target_node_id": "scene_branch",
        "priority": 20,
        "conditions": {
            "all": [{"type": "metric_compare", "metric": "trust", "op": ">=", "value": 70}]
        },
        "transition_contract": {
            "reason": "信任足以允许坦白。",
            "must_deliver": ["进入雨夜谈话"],
            "must_preserve": ["旧信仍是真相线索"],
            "tone": "克制",
        },
    })
    calls: list[dict] = []

    def fake_call(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        return json.dumps({
            "opening_scene": "雨点敲在花店玻璃上，旧信摊在两人之间。",
            "narrative_focus": "这次坦白是否能被旧信中的日期接住，仍然悬在两人之间。",
            "ordered_goals": [{
                "owner": "player",
                "delivery_type": "player_action",
                "description": "玩家说出可被旧信日期验证的离开原因。",
                "evidence_mode": "semantic",
                "anchors": [],
                "sources": ["player_input"],
            }],
            "must_not_happen": ["猫娘立刻无条件原谅玩家"],
            "character_state": {
                "catgirl_state": "女主身体状态正常，记得上游已经核验的旧信事实。",
                "player_state": "男主身体状态正常，是否坦白仍由玩家决定。",
                "environment_state": "环境延续雨夜花店和桌上的旧信。",
                "acting_contract": {
                    "cognition_state": "normal",
                    "memory_state": "available",
                    "self_reference_mode": "persona_allowed",
                    "persona_scope": "full",
                    "dialogue_policy": "required",
                    "assertable_self_facts": [],
                    "allowed_behaviors": ["核验玩家主动说明的事实"],
                    "forbidden_behaviors": ["替玩家完成坦白"],
                },
                "continuity_from_previous": ["旧信仍是真相线索"],
                "scene_boundaries": ["不得替男主完成尚未由玩家输入的坦白"],
            },
            "catgirl_situation": "她愿意听完解释，但仍用旧信核对玩家的说法。",
            "transition_goal": "让坦白改变两人的认知，并保留是否继续信任的空间。",
        }, ensure_ascii=False)

    generator.call_llm = fake_call
    result = generator.enhance_node(
        story=story,
        node_id="scene_branch",
        key_props=generator._project_key_props(_idea_outline()["key_props"]),
    )

    assert len(calls) == 1
    assert calls[0]["max_retries"] == 1
    assert calls[0]["operation"] == "numeric_v2_node_enhancement"
    model_input = json.loads(calls[0]["messages"][1]["content"])
    assert model_input["node_type"] == "scene"
    assert model_input["existing_story_beat"] == branch["story_beat"]
    assert model_input["node_type_label"] == "幕节点"
    assert model_input["author_input"] == {
        "title": "雨夜坦白",
        "summary": "玩家在雨夜主动说明当年离开的原因。",
    }
    assert model_input["upstream_nodes"][0]["id"] == "mainline_01"
    assert model_input["incoming_routes"][0]["conditions"]["all"][0]["value"] == 70
    assert model_input["metrics"]["trust"]["increase_criteria"] == ["玩家兑现承诺"]
    assert model_input["key_props"][0]["states"][0]["node_id"] == "mainline_01"
    assert result["opening_scene"] == "雨点敲在花店玻璃上，旧信摊在两人之间。"
    assert result["narrative_focus"] == "这次坦白是否能被旧信中的日期接住，仍然悬在两人之间。"
    assert result["goals"][0]["delivery"]["output_field"] == "player_input"
    assert result["goals"][0]["evidence"] == {"mode": "semantic", "anchors": []}


def _ending_enhancement_case():
    # 结局已关闭玩家输入，目标仅标记开场交付；状态沿用已有有效合同。
    generator = NumericV2Generator()
    story = generator._project_story(
        title="雨后旧信", original_idea="核对旧信后保留分歧。",
        setup=_generation_setup(), outline=_idea_outline(), tone=["克制"],
    )
    candidate = {
        "opening_scene": "旧信平放在桌上，女主平静地看着已经核对的日期。",
        "narrative_focus": "女主接受日期记录，但保留对当年选择的不同看法。",
        "ordered_goals": [{
            "owner": "environment", "delivery_type": "environment_fact",
            "description": "结局开场已展示核对后的旧信与女主保留的意见。",
            "evidence_mode": "semantic", "anchors": [], "sources": ["opening"],
            "timing": "opening", "dialogue_policy_after": "unchanged",
        }],
        "must_not_happen": [],
        "character_state": _idea_outline()["character_state_arc"]["ending_stage"],
        "catgirl_situation": "她知道日期已经核实，没有因此放弃原有意见。",
        "transition_goal": "停留在核对结束后的平静状态。",
    }
    return generator, story, candidate


@pytest.mark.parametrize("copies", [1, 3])
def test_node_enhancement_does_not_accumulate_existing_state_prefix(copies):
    """When the model reuses assembled instructions, both enhancements retain one state description and independent supplements without mutating input."""
    from copy import deepcopy

    generator, story, candidate = _ending_enhancement_case()
    stage = candidate["character_state"]
    state_text = "".join(stage[key] for key in (
        "catgirl_state", "player_state", "environment_state",
    ))
    extra = candidate["catgirl_situation"]
    story["nodes"][-1]["story_beat"]["catgirl_situation"] = state_text * copies + extra
    observed = []

    def echo_context(messages, **_kwargs):
        request = json.loads(messages[1]["content"])
        context = request["existing_story_beat"]["catgirl_situation"]
        observed.append(context)
        return json.dumps({**candidate, "catgirl_situation": context}, ensure_ascii=False)

    generator.call_llm = echo_context
    for _ in range(2):
        before = deepcopy(story)
        result = generator.enhance_node(story=story, node_id="ending_normal")
        assert story == before
        assert result["catgirl_situation"] == state_text + extra
        story["nodes"][-1]["story_beat"].update(result)
    assert observed == [state_text * copies + extra, state_text + extra]


def test_character_context_preserves_nonprefix_and_different_state_text():
    """Remove only an exactly matching assembled prefix; quotations, partial matches and older states are not disposable facts."""
    generator, _story, candidate = _ending_enhancement_case()
    stage = candidate["character_state"]
    state_text = "".join(stage[key] for key in (
        "catgirl_state", "player_state", "environment_state",
    ))
    for context in (
        "她在复述纸上的文字：" + state_text,
        stage["catgirl_state"] + "她仍有一个尚未解释的顾虑。",
        "女主站在门边。男主在屋外。环境尚未变化。",
    ):
        assert generator._character_scene_context(stage, context) == state_text + context


@pytest.mark.parametrize("change", [
    {"timing": "turn"},
    {"owner": "player", "delivery_type": "player_action", "sources": ["player_input"], "timing": "turn"},
    {"owner": "catgirl", "delivery_type": "catgirl_dialogue"},
    {"dialogue_policy_after": "forbidden"},
    {"sources": ["player_input"]},
    {"evidence_mode": "exact", "anchors": ["旧信"]},
    None,
])
def test_ending_enhancement_rejects_unplayable_goals_without_mutating_story(change):
    from copy import deepcopy
    # 复现已结束节点被完善成待执行任务、禁言切换或多目标；不能默默丢弃坏目标。
    generator, story, candidate = _ending_enhancement_case()
    if change is None:
        candidate["ordered_goals"].append(deepcopy(candidate["ordered_goals"][0]))
    else:
        candidate["ordered_goals"][0].update(change)
    original = deepcopy(story)
    calls = []
    generator.call_llm = lambda *args, **kwargs: (calls.append(kwargs) or json.dumps(candidate, ensure_ascii=False))
    with pytest.raises(NumericV2GenerationError) as caught:
        generator.enhance_node(story=story, node_id="ending_normal")
    assert "ending_goal_contract_invalid" in {issue["code"] for issue in caught.value.issues}
    assert len(calls) == 1
    assert story == original


def test_ending_enhancement_preserves_opening_delivery_and_compiles():
    # 合法结局仍可完善并经生产编译，不能靠全面禁止结局编辑修复这一入口。
    generator, story, candidate = _ending_enhancement_case()
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)
    update = generator.enhance_node(story=story, node_id="ending_normal")
    ending = next(node for node in story["nodes"] if node["type"] == "ending")
    ending["story_beat"].update(update)
    delivery = update["goals"][0]["delivery"]
    assert delivery["timing"] == "opening"
    assert delivery["source_ids"] == ["opening.ending_normal"]
    assert "state_effects" not in delivery
    NumericV2Compiler(InProcessPackageGateway()).compile(story)


@pytest.mark.parametrize("boundaries", [[], ["不得让女主改写旧信日期"]])
def test_mainline_preserves_explicit_or_empty_state_boundaries(boundaries):
    # 空数组表示没有额外限制；有明确限制时生成投影必须原样保留，不能以简化为名删掉。
    outline = _idea_outline()
    outline["character_state_arc"]["stages"][0]["scene_boundaries"] = boundaries
    generator = NumericV2Generator()
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(outline, ensure_ascii=False)
    result = generator.generate(title="状态边界范围", setup=_generation_setup())
    beat = result["story"]["nodes"][0]["story_beat"]
    assert beat["character_state"]["scene_boundaries"] == boundaries
    assert all(item in beat["must_not_happen"] for item in boundaries)
    # 走真实 N.E.K.O 编译入口，防止生成器接受了最终运行包却不能消费的空边界。
    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(result["story"])
    assert compiled.story["nodes"][0]["story_beat"]["character_state"]["scene_boundaries"] == boundaries


@pytest.mark.parametrize("boundaries", [None, [""], ["不得越界"] * 5])
def test_state_boundary_relaxation_still_rejects_malformed_arrays(boundaries):
    # 放宽的是数量下限，不是字段形状、非空条目或四条上限。
    from theater_workshop.sdk.generation.numeric_v2 import _validate_character_state_stage
    state = _idea_outline()["character_state_arc"]["stages"][0]
    state["scene_boundaries"] = boundaries
    issues = []
    _validate_character_state_stage(state, "state", issues=issues,
                                    expected_chapter_index=1, continuity_required=False)
    assert any(item["path"] == "state.scene_boundaries" for item in issues)


def test_quality_context_preserves_author_intent_and_terminal_conflicts_without_mutation():
    # 两份摘要及终点状态故意互相矛盾；评分输入必须保留矛盾，不能替作者归一。
    from copy import deepcopy
    story, authoring = _quality_story_context()
    ending = next(node for node in story['nodes'] if node['type'] == 'ending')
    ending['story_beat']['summary'] = '合作已经结束。'
    ending['story_beat']['character_state']['player_state'] = '等待答复下一次邀约。'
    ending['story_beat']['must_not_happen'] = ['女主不得新增后续邀约。']
    story['endings'][0]['summary'] = '女主邀请男主下次再来。'
    authoring['character_state_arc'] = {'stages': [{
        'node_id': ending['id'], 'player_state': '滞后的作者缓存。',
    }]}
    old_story, old_authoring = deepcopy(story), deepcopy(authoring)
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    target = context['endings'][0]
    assert context['author_intent'] == _generation_setup()['brief']
    assert target['scene_summary'] == '合作已经结束。'
    assert target['summary'] == '女主邀请男主下次再来。'
    assert target['character_state']['player'] == '等待答复下一次邀约。'
    assert target['must_not_happen'] == ['女主不得新增后续邀约。']
    assert target['acting_contract'] == ending['story_beat']['acting_contract']
    assert target['entry_from']
    target['acting_contract']['allowed_behaviors'].append('修改评分副本。')
    target['must_not_happen'].clear()
    assert story == old_story and authoring == old_authoring


def test_quality_context_preserves_adjacent_delivery_and_legacy_state_fallback():
    # 旧包仍能读取作者状态；普通幕、支线和结局的真实路线可按 ID 联合核对。
    story, authoring = _quality_story_context()
    first, second = story['nodes'][:2]
    first['story_beat'].pop('character_state')
    authoring['character_state_arc'] = {'stages': [{
        'node_id': first['id'], 'player_state': '尚未进行选择。',
    }]}
    context = NumericV2QualityAssessor._assessment_context(story, {}, authoring)
    source, target = context['mainline'][:2]
    assert context['author_intent'] == ''
    assert source['character_state']['player'] == '尚未进行选择。'
    assert source['outgoing_routes'][0]['target_node_id'] == target['id']
    assert target['narrative_focus'] == second['story_beat']['narrative_focus']
    assert [item['timing'] for item in target['goals']] == [
        item['delivery']['timing'] for item in second['story_beat']['goals']
    ]
    assert target['must_not_happen'] == second['story_beat']['must_not_happen']


def test_quality_route_issue_resolves_to_owner_and_repairs_only_that_node():
    # 路线 ID 不是目标幕 ID；修桥段必须落到持有该出口的来源节点。
    story, authoring = _quality_story_context()
    first = story['nodes'][0]
    route = first['route_gates'][0]
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    payload = _quality_payload(weak_dimension='plot', target_node_id=route['id'])
    payload['issues'][0]['target_node_ids'].append(first['id'])
    payload['issues'][0]['repair_targets'] = [{'node_id': first['id'], 'field': '/outgoing_routes/0/transition_contract/bridge_scene_narration'}]
    assessment = NumericV2QualityAssessor._validated_assessment(context, payload)
    _reviewed_fixture(assessment)
    assert assessment['issues'][0]['target_node_ids'] == [first['id']]
    assessor = NumericV2QualityAssessor()
    assessor.call_llm = lambda *_args, **_kwargs: json.dumps({'node_updates': [{
        'node_id': first['id'], 'routes': [{'route_id': route['id'],
        'transition_contract': {'bridge_scene_narration': '灯光映亮了下一处工作台。'}}],
    }]}, ensure_ascii=False)
    repaired = assessor.optimize_node(story=story, setup=_generation_setup(),
        authoring=authoring, assessment=assessment, node_id=first['id'])
    assert repaired['nodes'][1:] == story['nodes'][1:]
    assert repaired['nodes'][0]['story_beat'] == first['story_beat']
    assert repaired['nodes'][0]['route_gates'][0]['target_node_id'] == route['target_node_id']


@pytest.mark.parametrize('invalid_target', ['unknown_route', None, 'ambiguous_route'])
def test_quality_does_not_guess_unknown_or_ambiguous_route_ownership(invalid_target):
    # 未知 ID、非法形状和跨节点重名都不能扩大优化权限。
    story, authoring = _quality_story_context()
    for node in story['nodes'][:2]:
        node['route_gates'][0]['id'] = 'ambiguous_route'
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    payload = _quality_payload(weak_dimension='plot', target_node_id=invalid_target)
    with pytest.raises(QualityAssessmentError):
        NumericV2QualityAssessor._validated_assessment(context, payload)


def test_quality_keeps_high_score_suggestions_alongside_low_score_issues():
    # 允许问题维度多于低分维度，不能把完整报告误判成非法格式。
    from copy import deepcopy
    story, authoring = _quality_story_context()
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    payload = _quality_payload(weak_dimension='plot', target_node_id='mainline_01')
    minor = deepcopy(payload['issues'][0])
    minor.update(dimension='prose_style', severity='minor', target_node_ids=['mainline_02'])
    payload['issues'].append(minor)
    report = NumericV2QualityAssessor._validated_assessment(context, payload)
    assert [item['dimension'] for item in report['issues']] == ['plot', 'prose_style']
    assert report['failed_dimensions'] == ['plot']
    assert report['passed'] is False
    # 放宽展示范围后，低分维度缺问题仍然无效。
    payload['issues'] = [minor]
    with pytest.raises(QualityAssessmentError):
        NumericV2QualityAssessor._validated_assessment(context, payload)


def test_quality_validates_high_score_issues_instead_of_silently_dropping_them():
    # 高分不能掩盖不存在的修复目标，避免错误建议进入节点优化入口。
    story, authoring = _quality_story_context()
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    payload = _quality_payload(weak_dimension='plot', target_node_id='unknown_node')
    payload['scores']['plot']['score'] = 95
    with pytest.raises(QualityAssessmentError):
        NumericV2QualityAssessor._validated_assessment(context, payload)


def test_literature_reads_review_first_without_losing_story_or_suggestions():
    # 捕获真实阶段请求：先读复核结论，作者顺序/原文与未知扩展字段仍完整保留。
    # 返回同修法的两项影响，验证阅读优化不会在服务端删掉模型已经给出的意见。
    from copy import deepcopy
    story, authoring = _quality_story_context()
    assessor = NumericV2QualityAssessor()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    context['author_extension'] = {'z': '保留原文', 'a': '不按字母重排'}
    facts = {'issues': [{'issue_id': 'fact_01'}], 'excluded_issue_ids': ['rejected'],
             'protected_repair_targets': [{'node_id': 'mainline_01', 'field': '/opening_scene'}]}
    original = deepcopy((context, facts))
    payload = _quality_payload(weak_dimension='prose_style', target_node_id='mainline_01')
    duplicate = deepcopy(payload['issues'][0]);duplicate['dimension'] = 'characterization'
    payload['issues'].append(duplicate)
    payload['scores']['plot'].update(score=65, fact_issue_ids=['fact_01'])
    payload['relationship_advice'] = []
    calls = []

    def reply(messages, **kwargs):
        request = json.loads(messages[1]['content']);calls.append(kwargs)
        assert list(request) == ['fact_review', 'story_outline']
        assert request == {'fact_review': facts, 'story_outline': context}
        assert list(request['story_outline']) == list(context)
        assert list(request['story_outline']['author_extension']) == ['z', 'a']
        return json.dumps(_quality_wire_payload(payload), ensure_ascii=False)

    assessor.call_llm = reply
    report = assessor._assess_literature(context, facts)
    assert len(calls) == 1 and calls[0]['max_retries'] == 1
    assert (context, facts) == original
    assert len(report['issues']) == 2
    assert {issue['dimension'] for issue in report['issues']} == {'prose_style', 'characterization'}
    assert report['scores']['plot']['fact_issue_ids'] == ['fact_01']
    assert report['relationship_advice'] == []


def test_single_node_request_limits_cross_node_quality_and_relationship_targets():
    # 真实失败曾把八幕润色字段全部发给单幕修订；两类建议都只能授权当前节点。
    from copy import deepcopy
    assessor = NumericV2QualityAssessor()
    story, authoring = _quality_story_context()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    payload = _quality_payload(weak_dimension='prose_style', target_node_id='mainline_01')
    targets = [{'node_id': node_id, 'field': '/summary'} for node_id in ('mainline_01', 'mainline_02')]
    payload['issues'][0].update(target_node_ids=['mainline_01', 'mainline_02'], repair_targets=targets)
    payload['relationship_advice'] = [{
        'target_node_ids': ['mainline_01', 'mainline_02'], 'repair_targets': deepcopy(targets),
        'problem': '两幕回应缺少各自的具体关切。', 'suggestion': '分别明确本幕回应所针对的选择。',
        'expected_result': '两幕各有回应对象。', 'repair_scope': 'text',
    }]
    report = assessor._validated_assessment(context, payload)
    _reviewed_fixture(report)
    original = deepcopy((report, story, authoring));calls = []

    def reply(messages, **kwargs):
        request = json.loads(messages[1]['content']);calls.append(kwargs)
        assert len(request['accepted_suggestions']) == 2
        for suggestion in request['accepted_suggestions']:
            assert suggestion['repair_targets'] == targets[:1]
        return json.dumps({'node_updates': [{'node_id': 'mainline_01', 'story_beat': {'summary': '女主说明为何在意本幕旧信的来历，等男主判断。'}}]})

    assessor.call_llm = reply
    candidate = assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring,
                                      assessment=report, node_id='mainline_01')
    assert len(calls) == 1 and calls[0]['max_retries'] == 1
    assert (report, story, authoring) == original
    assert candidate['nodes'][1:] == story['nodes'][1:]


def _linked_quality_payload():
    # 模型用本轮局部编号显式关联；两类问题均只写一次，后端转换为既有报告卡片编号。
    raw = _quality_payload(weak_dimension='prose_style', target_node_id='mainline_01')
    raw['issues'][0]['issue_id'] = 'L1'
    raw['relationship_advice'] = [{
        'issue_id': 'R1', 'target_node_ids': ['mainline_02'],
        'problem': '回应没有说明女主为何在意这次选择。', 'suggestion': '在原演绎方向中说明她的具体关切。',
        'expected_result': '回应与人物关切相连。', 'repair_scope': 'structure',
        'repair_targets': [{'node_id': 'mainline_02', 'field': '/narrative_focus'}],
    }]
    raw['scores']['characterization'].update(score=68, related_issue_ids=['R1'], summary='R1中回应缺乏关切，使人物显得功能化。')
    raw['scores']['pacing'].update(score=70, related_issue_ids=['L1'], summary='L1的重复表达也拖慢信息推进。')
    return raw


def test_literary_low_scores_explicitly_link_existing_issues_without_copying():
    from copy import deepcopy
    story, authoring = _quality_story_context();assessor = NumericV2QualityAssessor()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    raw = _linked_quality_payload();original = deepcopy(raw);calls = []
    def reply(messages, **kwargs):
        calls.append(kwargs)
        return json.dumps(_quality_wire_payload(raw), ensure_ascii=False)
    assessor.call_llm = reply
    report = assessor._assess_literature(context, {'issues': []})
    assert raw == original and len(calls) == 1 and calls[0]['max_retries'] == 1
    assert len(report['issues']) == 1 and len(report['relationship_advice']) == 1
    assert report['scores']['characterization']['related_issue_ids'] == ['relationship_advice_01']
    assert report['scores']['pacing']['related_issue_ids'] == ['quality_issue_01']
    assert report['scores']['characterization']['summary'] == raw['scores']['characterization']['summary']
    assert report['relationship_advice'][0]['repairable'] is False
    assert report['issues'][0]['repair_node_ids'] == ['mainline_01']
    assert set(report['failed_dimensions']) == {'characterization', 'pacing', 'prose_style'}
    assert report['passed'] is False


@pytest.mark.parametrize('defect', [
    'unknown_ref', 'duplicate_ref', 'string_refs', 'null_refs', 'object_ref',
    'duplicate_id', 'missing_id', 'invalid_id', 'fact_as_literary', 'missing_link',
])
def test_literary_links_reject_missing_ambiguous_or_non_explicit_basis(defect):
    story, authoring = _quality_story_context();assessor = NumericV2QualityAssessor()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    raw = _linked_quality_payload()
    if defect == 'unknown_ref': raw['scores']['pacing']['related_issue_ids'] = ['L2']
    elif defect == 'duplicate_ref': raw['scores']['pacing']['related_issue_ids'] = ['L1', 'L1']
    elif defect == 'string_refs': raw['scores']['pacing']['related_issue_ids'] = 'L1'
    elif defect == 'null_refs': raw['scores']['pacing']['related_issue_ids'] = None
    elif defect == 'object_ref': raw['scores']['pacing']['related_issue_ids'] = [{}]
    elif defect == 'duplicate_id': raw['relationship_advice'][0]['issue_id'] = 'L1'
    elif defect == 'missing_id': raw['issues'][0].pop('issue_id')
    elif defect == 'invalid_id': raw['issues'][0]['issue_id'] = ['L1']
    elif defect == 'fact_as_literary': raw['scores']['pacing']['related_issue_ids'] = ['fact_01']
    elif defect == 'missing_link': raw['scores']['characterization'].pop('related_issue_ids')
    assessor.call_llm = lambda *_args, **_kwargs: json.dumps(_quality_wire_payload(raw))
    with pytest.raises(QualityAssessmentError, match='invalid_quality_assessment'):
        assessor._assess_literature(context, {'issues': [{'issue_id': 'fact_01'}]})


def test_start_node_enhancement_allows_empty_prior_continuity_and_preserves_story():
    from copy import deepcopy
    # 合法start首幕没有上一幕；完善入口不能拒绝它或为通过校验凭空补前史。
    generator, story, candidate = _ending_enhancement_case()
    candidate["character_state"]["continuity_from_previous"] = []
    original = deepcopy(story)
    calls = []
    def call(messages, **kwargs):
        calls.append(messages)
        return json.dumps(candidate, ensure_ascii=False)
    generator.call_llm = call
    result = generator.enhance_node(story=story, node_id=story["start_node_id"])
    assert len(calls) == 1
    model_input = json.loads(calls[0][1]["content"])
    assert model_input["node_type"] == "start"
    assert model_input["upstream_nodes"] == []
    assert result["character_state"]["continuity_from_previous"] == []
    assert story == original


def test_scene_enhancement_still_requires_prior_continuity():
    # 首幕的空前史例外不能扩散到后续普通幕。
    generator, story, candidate = _ending_enhancement_case()
    candidate["character_state"]["continuity_from_previous"] = []
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)
    with pytest.raises(NumericV2GenerationError) as caught:
        generator.enhance_node(story=story, node_id="mainline_02")
    assert "character_state_items_required" in {x["code"] for x in caught.value.issues}
