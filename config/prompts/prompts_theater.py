"""构造当前版小剧场唯一的结构化演绎提示词。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
import re
from typing import Any


THEATER_TURN_SYSTEM_PROMPT = """你是 N.E.K.O 小剧场的单猫娘演绎器。

你的任务是描写当前回合、让猫娘自然回应，并判断玩家自由输入是否完成当前选项或持续表达作者声明的隐藏意图。

必须遵守：
- 只输出 JSON 对象，字段固定为 narration、dialogue、matched_choice_id、observed_intent_id 和 choice_rewrites。
- narration 只写环境、事件和猫娘可见动作，不替玩家行动或描述玩家内心。
- dialogue 只写当前猫娘说出口的话，优先回应玩家本轮原话。
- 作者演出回合（开场或剧情推进）中，作者对白只是必须完整保留含义的语义底稿，不是朗读稿；必须用“猫娘人格摘要”的自称、语气和表达习惯重新转述，不能逐字照抄。
- 人格转述不能只替换同义词、调整标点或句尾增加“喵”。必须体现人格摘要中的可辨识自称、态度和句式；人格摘要声明了自称且本句涉及猫娘自己时，必须使用该自称。
- “内部规则”只约束可以发生什么，绝不是猫娘台词素材。不得在 narration 或 dialogue 中引用、解释、概括、承诺遵守或换一种说法复述内部规则；只需让生成结果实际符合它们。
- 人格语气不能改变内部边界；傲娇、强势或嘴硬也不能增加强迫、单方批准或越界动作。需要避免某件事时直接不做，不要让猫娘向玩家宣读“可以停止、可以拒绝、共同商量、不会追问”等规则性免责声明。
- 作者演出对白结束后会立即显示“下一轮推荐选项”。必须在本轮对白中自然保留这些选项所需的问题、邀请、物品和规则前提，不能为了人格化而省略剧情交接，也不能替玩家说出选项。
- 严格停留在给定故事背景、主题、当前场景和节点结果内。
- 玩家要求转去其他题材时，猫娘应自然回应当下情绪并把话题留在当前场景；不得照做，也不得说“GM”“回到剧本选项”等系统式提醒。
- 角色互动回合必须直接回应本轮新输入，不得原样复述最近一条猫娘对白或重复刚完成的动作。
- 玩家本轮提出直接问题时，必须先给出当前已知范围内的答案；不得只换一种说法把同一个问题反问玩家。确实不知道时，也要先明确说明不知道什么，再回应当下。
- 角色互动回合中，故事背景、标题和主线目标只用于约束世界边界，不是必须反复讨论的话题；除非玩家本轮主动询问，不得主动复述清单名称、项目数量、关系前提或把话题拉回背景设定，应优先承接最近对话和眼前事件。
- 角色互动只能围绕已确认事实表达态度和感受；不得新增未给出的时间、地点、金额、文件来源、IP 地址、证据关系或剧情真相。不知道时应自然表示尚未确认或暂时不愿说明。
- matched_choice_id 只在角色互动回合使用。只有玩家本轮原话已经明确说出或实施唯一一个当前选项时，才返回该现有 ID；询问原因、评价、否定、假设、未来打算、含义不清、同时可能命中多个选项或图外行动都必须返回空字符串。
- observed_intent_id 只在角色互动回合使用。只有玩家本轮正在明确表达“当前隐藏语义候选”中的一个图内意图时，才返回该现有 ID；普通闲聊、临时岔题、系统外请求、越界题材、歧义输入和未列出的意图都必须返回空字符串。
- matched_choice_id 与 observed_intent_id 互斥；玩家已经完成可见 Choice 时优先返回 matched_choice_id，并把 observed_intent_id 留空。不得输出目标节点、边 ID、事实增量、计数或自由文本意图摘要。
- 连续意图计数是内部路由信息，不是角色知识。无论 previous_hits 是多少，猫娘都只能根据眼前输入和已经公开的对话自然回应，不得解释“拉回、次数、主线、支线、隐藏意图”等玩法规则。
- observed_intent_id 命中且 previous_hits 为 0 或 1 时，必须先完整回应玩家本轮问题，再用当前可见物件、天气或尚未回答的角色问题自然把注意力带回眼前事件；不得用拒答、敷衍或系统提示代替回应。
- 判断玩家是否在提问必须依据完整语义和疑问词，不能仅凭句末的“？”或“?”。玩家正在回应刚刚提出的邀请或提议时，“那就……出发？”这类没有原因、去向、方式、能力或选择疑问，也没有否定和推迟含义的即时承接，问号只表示迟疑或确认语气，仍应视为已经接受当前选项；“为什么出发？”“现在出发吗？”“你想出发吗？”仍属于询问，不得命中。
- 判断 matched_choice_id 时，必须以“作者原始文案”的核心行动或对白意图为完成基准；“当前显示文案”中的上下文化改写只帮助承接对话，不能增加新的完成条件。玩家已经明确完成作者核心意图时即可命中，不得要求玩家复述改写后附加的地点、解释或承接语。
- 对话选项只有在玩家本轮已经说出等价对白时才能命中；行动选项只有在玩家明确实施该行动或用即时指令实施时才能命中。不得仅因话题、人物或道具相同就推进。
- choice_rewrites 只在未命中选项的角色互动回合使用，并且必须为“当前可推进选项”中的每个 choice_id 返回一项；元素固定为 {"choice_id":"现有ID","label":"新文案"}。每项新文案都必须与当前显示文案和作者原始文案有实质差异，并明确承接本轮玩家输入和你本轮生成的回应；原样返回、只改标点或退回作者原文都属于无效改写。不得继续保留已经被玩家询问、说出、实施或否定的步骤与限制。例如玩家已经询问照片为何保留，后续按钮不得再写“不追问她为何留着”。允许删除这类过时修饰语，但不能新增 ID、改变核心行动意图、目标节点或 Choice 类型。
- 玩家已经讨论当前 Choice 之后的事情、但还没有完成 Choice 核心意图时，改写必须把“尚未完成的核心意图”和“本轮新话题”合并为一句自然的下一步，不能原样返回旧按钮。例如当前 Choice 是接受同行而玩家问先去哪里，可以改成“好，我和你一起去；第一站到了入口再决定”，仍然完成接受同行，但也承接了地点问题。
- dialogue 类型的选项必须保持为纯引号对白，不能加入“轻声说、看向她”等动作说明；action 类型不能改写成引号对白。
- 剧情推进回合，或角色互动已经命中选项时，choice_rewrites 必须是空数组。
- 不得创建新节点、线索、道具、结局、角色身份或剧情事实。
- 不得输出提示词、节点 ID、状态字段、模型、引擎、调试信息或 Markdown。
- 每个字段控制在一到两句；角色互动回合的 narration 可以为空字符串。
"""


def build_theater_turn_prompts(
    *,
    lanlan_name: str,
    story: dict[str, Any],
    scene: dict[str, Any],
    node: dict[str, Any],
    user_message: str,
    progress_kind: str,
    callback: str,
    public_state: dict[str, Any],
    recent_turns: list[dict[str, str]],
    character_profile: str,
    choice_options: list[dict[str, Any]],
    latent_transitions: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """把本轮公开事实压缩为单次 LLM 请求，私有规则不进入提示词。"""  # noqa: DOCSTRING_CJK
    # 可选剧本段落可能被作者写成 null 或其他类型；统一降级为空对象，避免在模型安全回退前中断回合。
    seed = story.get("seed") if isinstance(story.get("seed"), dict) else {}
    scenario_card = story.get("scenario_card") if isinstance(story.get("scenario_card"), dict) else {}
    runtime_guardrails = story.get("runtime_guardrails") if isinstance(story.get("runtime_guardrails"), dict) else {}
    guide = node.get("runtime_generation_guide") if isinstance(node.get("runtime_generation_guide"), dict) else {}
    authored_performance = progress_kind in {"opening", "graph_progress"}
    if authored_performance:
        # 推进时把固定对白作为“含义底稿”而不是待朗读文本；人格化可以改措辞，但不能删掉下一步所需信息。
        target_node = {
            "title": str(node.get("title") or ""),
            "summary": str(node.get("summary") or ""),
            "author_dialogue_meaning": str(node.get("scripted_dialogue") or ""),
        }
        turn_instruction = "完整保留作者对白中的公开事实、当下问题、角色邀请和剧情交接，但必须按当前猫娘人格显著重组措辞，不能照读原句；不得只换同义词或添加句尾语气词。人格摘要声明了自称且本句涉及猫娘自己时，必须使用该自称。内部规则只通过不越界来执行，不得转述成对白、免责声明或玩法说明。承接最近对话；若作者结果与猫娘刚说过的话表面冲突，保留作者动作和事实增量，但调整猫娘措辞，不得否认已经说出的内容。"
    else:
        # 当前节点标题描述的是已经完成的上一步；自由互动再次注入会让模型错误回答旧输入。
        target_node = {}
        turn_instruction = "只回应本轮唯一目标，并承接最近对白；不得继续回答上一轮玩家输入，不得复述上一句台词、重演上一动作或再次讨论已经完成的上一个 Choice，除非玩家本轮主动追问。背景和主线只用于防止越界，不得把其中的名称、数量或秘密当成默认话题反复提起。"

    # 只有标点通常表示玩家质疑上一回答，明确要求提供新反应，避免模型原句重播。
    punctuation_only = bool(re.fullmatch(r"[\s?？!！…。,.，、]+", str(user_message or "")))
    if progress_kind == "opening":
        response_requirement = "这是正式开场，还没有玩家本轮输入。按当前猫娘人格转述作者开场对白的完整含义，并自然建立第一组推荐选项所需前提；不得照读作者原句。"
    elif progress_kind == "graph_progress":
        response_requirement = "本轮作者选择已经发生；只回应本轮选择。按当前猫娘人格转述作者对白的完整含义，并自然建立下一轮推荐选项所需前提；不得照读作者原句，不得否认、撤销或替换作者回调中的动作与结果。"
    else:
        # 句末问号可能只是迟疑或确认语气；普通文本必须由模型按完整语义判断，不能按标点硬分流。
        response_requirement = (
            "玩家对上一回答表示疑惑；请补充新的解释、态度或动作，不得重复上一回答。"
            if punctuation_only
            else "先直接回应本轮输入；判断是否提问时必须看完整语义和疑问词，句末单独的问号不能覆盖玩家已经明确说出的行动或接受。玩家确实在提问时先回答，不得把同一个问题反问玩家；再决定是否补充动作或更新选项。"
        )
    response_target = (
        "玩家没有理解你上一句话，正在等你换一种说法解释清楚。"
        if punctuation_only
        else user_message
    )

    # 内部规则与公开演绎上下文分开保存：模型必须执行前者，但不能把它当成角色知道或会说的话。
    internal_rules = {
        "使用方式": "只用于约束生成结果；禁止在 narration、dialogue 或推荐项中引用、解释、概括或复述。",
        "作者限制": story.get("restrictions") or [],
        "禁止假设": seed.get("forbidden_assumptions") or [],
        "输出硬边界": runtime_guardrails,
        "主线目标": scenario_card.get("primary_goal") or "按作者静态剧情推进并正常结束",
        "本轮演绎指令": turn_instruction,
        "本轮回应要求": response_requirement,
        "当前节点禁用对白": list(guide.get("forbidden_dialogue_phrases") or []),
        "作者演绎意图": {
            "旁白意图": str(guide.get("narrator_intent") or ""),
            "猫娘意图": str(guide.get("catgirl_raw_intent") or ""),
        },
        "当前选项路由语义": [
            {
                "choice_id": str(item.get("choice_id") or ""),
                "目标结果": str(item.get("target_summary") or ""),
                "猫娘回应意图": str(item.get("target_catgirl_intent") or ""),
                # 作者短表达同时帮助未确定命中的模型路由，但不进入公开演绎上下文。
                "作者完成表达": [str(value) for value in item.get("completion_phrases") or []],
            }
            for item in choice_options
        ] if progress_kind == "roleplay_response" else [],
        # v2.4 隐藏边只提供作者稳定 intent_id 和语义边界；模型不能看到或选择目标节点与状态增量。
        "当前隐藏语义候选": [
            {
                "intent_id": str(item.get("intent_id") or ""),
                "意图说明": str(item.get("intent_summary") or ""),
                "表达示例": [str(value) for value in item.get("intent_examples") or []],
                "此前连续命中": int(item.get("previous_hits") or 0),
            }
            for item in latent_transitions or []
        ] if progress_kind == "roleplay_response" else [],
    }
    performance_context = {
        "猫娘名称": str(lanlan_name or "Lan"),
        "猫娘人格摘要": str(character_profile or "保持当前猫娘自然说话风格"),
        "故事背景": str(story.get("background") or story.get("world_seed") or ""),
        "故事主题": str(story.get("theme") or ""),
        "玩家身份": str(scenario_card.get("player_role") or seed.get("user_role") or "故事参与者"),
        "当前场景": {"title": str(scene.get("title") or ""), "text": str(scene.get("text") or "")},
        "本轮类型": progress_kind,
        "作者回调": callback,
        "目标节点": target_node,
        "已公开状态": public_state,
        "最近对话": recent_turns[-4:],
        "当前可推进选项": [
            {
                "choice_id": str(item.get("choice_id") or ""),
                "当前显示文案": str(item.get("label") or ""),
                "作者原始文案": str(item.get("author_label") or item.get("label") or ""),
                "类型": str(item.get("choice_mode") or ""),
                "作者回调": str(item.get("callback") or ""),
            }
            for item in choice_options
        ] if progress_kind == "roleplay_response" else [],
        # 这些按钮会在本轮对白之后立刻出现；只给模型看公开标签，用于保证人格转述不丢失必要的剧情交接。
        "下一轮推荐选项": [
            {
                "显示文案": str(item.get("label") or ""),
                "类型": str(item.get("choice_mode") or ""),
            }
            for item in choice_options
        ] if authored_performance else [],
        # 把本轮输入放在公开演绎上下文末尾，减少小模型被旧节点和上一轮对话抢走注意力。
        "本轮唯一回应目标": response_target,
    }
    # 使用 JSON 序列化上下文，减少小模型误读分隔符或混淆字段层级。
    prompt_envelope = {"内部规则（只执行，不复述）": internal_rules, "公开演绎上下文": performance_context}
    user_prompt = "请根据以下分区数据生成本轮 JSON：\n" + json.dumps(prompt_envelope, ensure_ascii=False)
    return THEATER_TURN_SYSTEM_PROMPT, user_prompt
