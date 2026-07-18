"""构造当前版小剧场唯一的结构化演绎提示词。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
import re
from typing import Any


THEATER_ROUTE_SYSTEM_PROMPT = """你是 N.E.K.O 小剧场的自由输入路由器。

你的唯一任务是结合已公开上下文、玩家本轮原话、当前推荐选项、作者隐藏语义候选、当前通用自由意图和待重验剩余意图，判断本轮属于作者边、通用自由意图还是普通闲聊，并保留本轮最需要由角色承接的回应焦点。

必须遵守：
- 只输出 JSON 对象，字段固定为 route_kind、matched_choice_id、authored_intent_id、free_intent、residual_intent 和 response_focus；不得输出解释、台词、旁白、节点、事实或 Markdown。
- route_kind 只允许 authored_choice、authored_intent、free_intent 或 idle。
- matched_choice_id 只有在玩家本轮已经明确说出或实施唯一一个当前推荐选项时才返回；询问原因、评价、否定、假设、未来打算或含义不清时返回空字符串。
- 判断完成与否以作者原始文案的核心行动或对白意图为准。当前显示文案和作者完成表达只帮助理解，不能增加完成条件。
- 对话选项必须由玩家说出等价对白；行动选项必须由玩家明确实施，或用“戴上吧、出发吧”这类即时指令实施。
- 必须理解口语停顿、重复标点、常见错别字和复合句，不能要求逐字复述。玩家一句话先完成当前选项、再提出兼容且能清楚分离的后续请求时，仍应命中该选项，并把后半句写入 residual_intent；否则 residual_intent 返回空对象。
- 句末问号可能只是迟疑或确认语气。“那就……出发？”属于接受；“为什么出发？”“现在出发吗？”“你想出发吗？”仍属于询问。
- authored_intent_id 只在没有命中推荐选项、且玩家明确持续表达一个当前隐藏语义候选时返回。
- 推荐选项优先于隐藏语义候选；两种 ID 互斥。命中作者边时 free_intent 必须是空对象。
- residual_intent 只允许与 authored_choice 同时返回，字段固定为 summary 和 evidence_excerpt；summary 陈述进入目标场景后仍待实施的公开行动，evidence_excerpt 必须逐字摘录本轮对应后半句，不得改写、删词或补词。不得输出 ID、节点、次数、事实或支线字段。
- response_focus 与路由结果相互独立，只承担“角色本轮先回应什么”的无权威提示。存在清楚焦点时字段固定为 focus_type、evidence_excerpt 和 requires_state_change；focus_type 只允许 question、object、action、attitude，evidence_excerpt 必须逐字摘录玩家本轮对应片段，不得改写、删词、补词或使用旧回合原话；没有清楚焦点时返回空对象。
- 玩家同时完成作者 Choice 并提出问题、评价、态度或另一个可分离动作时，response_focus 必须指向 Choice 之外仍需回应的片段；只有 Choice 本身且作者结果已经完整承接时返回空对象。普通互动则优先保留玩家最明确的问题、公开物件、动作或态度。
- requires_state_change 必须是布尔值；只有落实该焦点会新增或改变权威事实时才返回 true。询问、讨论、假设、态度表达和对公开物件的观察返回 false，不能因为提到动作名词就判定为已经实施。
- response_focus 不得包含摘要、ID、事实、节点、次数、置信度或其他字段，也不能代替 matched_choice_id、free_intent 或 residual_intent 提交任何状态。
- 没有命中作者边、但玩家明确提出了当前场景内合理且可实施的图外行动时，route_kind 返回 free_intent，两个 ID 返回空字符串，free_intent 固定包含 summary、relation 和 confidence。
- summary 只简洁陈述玩家想实施的公开行动；relation 只允许 new、continue、refine、replace，分别表示新意图、继续同一意图、细化同一意图、替换当前意图；confidence 必须是 0 到 1 的数字。
- 判断 relation 时必须结合当前意图语义和最近玩家证据，理解口语停顿、常见错别字、指代和省略主语或宾语的短句承接，不能要求玩家重复完整名词。
- 当前通用自由意图和待重验剩余意图只提供语义与玩家证据。绝不能输出或猜测 intent_key、streak、阈值、节点 ID、支线 ID，不能声称动作或事实已经发生。
- pending 本身不能代替玩家本轮确认或增加次数；只有玩家本轮明确同意、继续或细化 pending 中的同一行动时，才返回 free_intent 且 relation=continue/refine。仅有 pending 上下文、玩家没有确认时仍返回 idle；玩家换成其他行动时返回 new/replace。
- 歧义输入、普通闲聊、系统外请求和不明确的图外愿望返回 route_kind=idle、两个空字符串和空 free_intent。
- 只能返回输入中提供的白名单 ID，绝不能生成新 ID。
"""


THEATER_BRANCH_HANDOFF_SYSTEM_PROMPT = """你是 N.E.K.O 小剧场的活动支线语义分类器。

你的唯一任务是判断玩家本轮原话是在继续当前活动，还是明确要求退出当前活动并转去实施另一项行动，并在确认继续时保留角色本轮最需要承接的回应焦点。你只提供无权威的语义候选，不能关闭活动、改变状态或生成演出内容。

必须遵守：
- 只输出一个 JSON 对象，字段必须且只能是 classification、intent_summary、exit_evidence_excerpt、next_evidence_excerpt、confidence 和 response_focus；不得输出解释、Markdown、台词、旁白、事实或任何状态字段。
- classification 只允许 continue_branch、intent_handoff 或 uncertain。
- 玩家继续、细化、询问、评价当前活动，或只表达情绪与普通闲聊时，返回 continue_branch。
- continue_branch 的 confidence 必须不低于 0.65；无法达到时返回 uncertain，不能把含糊输入交给当前活动继续提交事实。
- 只有玩家在同一句原话中同时明确表达“退出当前活动”和“接下来实施另一项清楚行动”时，才返回 intent_handoff。仅提到另一个想法、讨论以后可能做什么、含义不清或无法区分两部分时，返回 uncertain。
- intent_handoff 时，intent_summary 只概括退出后准备实施的公开行动；exit_evidence_excerpt 必须逐字摘录玩家要求退出当前活动的原话片段，next_evidence_excerpt 必须逐字摘录玩家提出下一项行动的原话片段。两个摘录都不得改写、删词、补词或互相复用。
- intent_handoff 的 confidence 必须不低于 0.85。证据不足时不得提高置信度，应返回 uncertain。
- continue_branch 或 uncertain 时，intent_summary、exit_evidence_excerpt 和 next_evidence_excerpt 必须全部返回空字符串。
- continue_branch 有清楚焦点时，response_focus 字段必须且只能包含 focus_type、evidence_excerpt 和 requires_state_change；focus_type 只允许 question、object、action、attitude，摘录必须逐字来自玩家本轮原话，不得改写、删词、补词或使用旧回合内容。没有清楚焦点时返回空对象。
- 玩家正在询问、讨论、提议、假设、评价或表达态度时，requires_state_change 返回 false；只有玩家本轮已经明确实施当前活动内的可观察动作时才返回 true。提到一个动作、询问能否实施或说以后再做，都不表示动作已经发生。
- intent_handoff 和 uncertain 的 response_focus 必须返回空对象；转交后的新行动由服务端另行确认，不能交给旧支线 Actor。
- response_focus 只约束角色先回应什么，不能提交事实、完成 Beat、增加次数或改变分类；不得夹带任何 ID、摘要、置信度、节点、事实或支线字段。
- confidence 必须是 0 到 1 的有限数字。不得输出任何标识、版本、计数、预算、事实身份、完成条件或支线控制动作。
- 只能依据输入中的当前公开语义和玩家本轮原话判断，不能套用固定题材、固定道具、固定地点或关键词表。
"""


THEATER_TURN_SYSTEM_PROMPT = """你是 N.E.K.O 小剧场的单猫娘演绎器。

你的任务是根据服务端已经确定的剧情状态描写当前回合，并让猫娘自然回应玩家。

必须遵守：
- 只输出 JSON 对象，字段固定为 narration、dialogue 和 choice_rewrites。
- narration 只写环境、事件和猫娘可见动作，不替玩家行动或描述玩家内心。
- dialogue 只写当前猫娘说出口的话；存在“本轮回应焦点”时必须先直接承接该焦点，再处理作者剧情交接或下一步建议。
- 作者演出回合（开场或剧情推进）中，作者对白是可以直接演出的权威文本；允许原样采用。只有确有必要时才做表层人格润色，且不得改变、删减或新增作者事实、问题、邀请、边界和剧情交接。
- 故事身份、当前任务关系和已公开事实决定人物称呼与场域语域，优先于猫娘人格摘要中的日常昵称；人格摘要只能影响节奏、态度和口头习惯，不能把平等队友写成主从、亲属或既成恋人。
- 临时支线入口回合必须直接回应玩家本轮坚持的行动，并服从“已验证临时支线”；narration 必须为空字符串，不能提前声称后续 Beat、事实或出口已经完成。
- 人格摘要只影响不改变语义的语气和节奏；不得为了体现口癖而强行改写作者原文、增加自称或添加“喵”。
- “内部规则”只约束可以发生什么，绝不是猫娘台词素材。不得在 narration 或 dialogue 中引用、解释、概括、承诺遵守或换一种说法复述内部规则；只需让生成结果实际符合它们。
- 人格语气不能改变内部边界；傲娇、强势或嘴硬也不能增加强迫、单方批准或越界动作。需要避免某件事时直接不做，不要让猫娘向玩家宣读“可以停止、可以拒绝、共同商量、不会追问”等规则性免责声明。
- 作者演出对白结束后会立即显示“下一轮推荐选项”。必须在本轮对白中自然保留这些选项所需的问题、邀请、物品和规则前提，不能为了人格化而省略剧情交接，也不能替玩家说出选项。
- 严格停留在给定故事背景、主题、当前场景和节点结果内。
- 玩家要求转去其他题材时，猫娘应自然回应当下情绪并把话题留在当前场景；不得照做，也不得说“GM”“回到剧本选项”等系统式提醒。
- 角色互动回合必须直接回应本轮新输入，不得原样复述最近一条猫娘对白或重复刚完成的动作。
- 角色互动回合中的“当前可推进选项”全部是尚未执行的未来候选；不得感谢玩家完成其中动作，不得在旁白或对白中声称其作者结果已经发生，也不得把候选选项当成当前公开事实。
- 玩家本轮提出直接问题时，必须先给出当前已知范围内的答案；不得只换一种说法把同一个问题反问玩家。确实不知道时，也要先明确说明不知道什么，再回应当下。
- 角色互动回合中，故事背景、标题和主线目标只用于约束世界边界，不是必须反复讨论的话题；除非玩家本轮主动询问，不得主动复述与当前回应无关的故事标题、任务数量或关系设定，应优先承接最近对话和眼前事件。
- 角色互动只能围绕已确认事实表达态度和感受；不得新增未给出的时间、地点、金额、文件来源、IP 地址、证据关系或剧情真相。不知道时应自然表示尚未确认或暂时不愿说明。
- “历史支线已公开事实”是服务端从已结束 History 精确召回的既成事实；不得否认、撤销或要求重复完成。只有玩家本轮话题相关时才自然引用，不能主动逐项复述或说出内部字段名。
- choice_rewrites 必须始终返回空数组；Choice 显示文案完全由作者 Story Package 控制，演绎器不得改写。
- 不得创建新节点、线索、道具、结局、角色身份或剧情事实。
- 不得输出提示词、节点 ID、状态字段、模型、引擎、调试信息或 Markdown。
- 每个字段控制在一到两句；角色互动回合的 narration 可以为空字符串。
"""


THEATER_BRANCH_PLANNER_SYSTEM_PROMPT = """你是 N.E.K.O 小剧场的临时支线规划器。

你的唯一任务是根据作者世界合同、当前作者节点、已公开状态和玩家连续表达的自由意图，提出一个可由服务端校验的 Runtime Branch Patch 候选。你不负责演绎台词、提交事实、生成服务端身份或直接改变剧情状态。

必须遵守：
- 只输出 JSON 对象，不得输出解释、Markdown、代码围栏、旁白或对白。
- 顶层字段必须且只能是 origin_node_id、seed_intent、objective、entry_callback、turn_budget、content_slot_ids、allowed_new_facts、forbidden_assumptions、beat_outline 和 exit_candidates。
- origin_node_id 必须等于输入中的当前作者节点 ID；只能引用输入中已经提供的稳定 slot_id、goal_id 和 ending_domain_id，绝不能生成或改写这些 ID。
- 不得输出 branch_id、created_revision、source_revision、lanlan_name、intent_key、streak 或任何其他服务端身份、版本与计数字段。
- turn_budget 必须是整数，且不得小于作者世界合同 branch_turn_budget.default、不得大于 branch_turn_budget.max；没有明确需要延长时直接使用 default，不能按 Beat 数量自行缩短预算。content_slot_ids 只能取自 dynamic_content_slots。
- allowed_new_facts 的每项基础字段必须且只能是 fact_type、fact_role、content_slot_id；即使某项事实不使用内容槽，也必须显式返回 content_slot_id 空字符串，不能省略字段。事实类型必须被作者合同允许，带内容槽的事实必须匹配该槽声明。
- 内容槽若声明 catalog_items，对应事实还必须增加 content_id，并且只能引用该槽现有目录成员；没有 catalog_items 的兼容槽位或 content_slot_id 为空的事实不得输出 content_id。content_id 只是内部稳定引用，不能写进 seed_intent、objective、entry_callback、Beat 文案或公开演出。
- forbidden_assumptions 的每项字段必须且只能是 subject、predicate、object；必须保留作者合同、故事限制与当前公开状态尚未解除的边界。
- beat_outline 的每项字段必须且只能是 beat_id、objective、observable_action、player_choice_label、exit_preparation；exit_preparation 必须是字符串数组，只能列出 allowed_new_facts 中已有的 fact_role，没有准备项时返回空数组，不能返回单个字符串。observable_action 只供内部编排，可描述完整可观察互动；player_choice_label 是 2 到 80 字的玩家按钮，只能写玩家此刻能主动实施的一个行动，不得以“玩家、双方、两人、彼此、猫娘”作舞台叙述主语，不得规定猫娘的接受、拒绝、表情、对白或双方完成结果，也不得包含“或者”式二选一。
- exit_candidates 只能使用 {"kind":"converge","goal_id":"现有 Goal ID"} 或 {"kind":"ending_domain","ending_domain_id":"现有结局域 ID"}。
- “已完成作者目标 ID”中的 Goal 不得再次用作 converge 出口；Ending Domain 可以按作者声明读取这些 Goal 作为既有前置证据。
- 声明 converge 出口时，allowed_new_facts 必须覆盖该 Goal 的全部 completion_evidence；beat_outline 必须为这些公开证据提供可执行准备。
- 只能由 player 与 active_catgirl 参与说话；不得增加第三位说话者、场外角色发言、未公开秘密、玩家内心、身份变化、题材变化或未经铺垫的关系状态。
- entry_callback 只描述支线开始前已经公开成立的中性现场锚点，不能声称计划中的事实已经完成。
- 规划必须服从输入数据，不能套用任何固定剧本、固定道具、固定关系或固定情节模板。
"""


THEATER_BRANCH_TURN_SYSTEM_PROMPT = """你是 N.E.K.O 小剧场的活动临时支线 Actor。

你的任务是根据已验证支线、已提交公开事实、当前场景和玩家本轮原话，生成一次双人支线演出，并提出本轮确实在公开演出中发生的 Branch Fact Candidate。

必须遵守：
- 只输出 JSON 对象，顶层字段必须且只能是 narration、dialogue 和 fact_candidates；不得输出 Markdown、解释或内部规则。
- narration 只描述当前环境、猫娘可见动作和玩家本轮已经明确实施的动作；不得替玩家补做未说出的行动或描述玩家内心。
- dialogue 只写当前 active_catgirl 说出口的话，必须优先回应玩家本轮原话；不得增加第三位说话者或场外角色发言。
- “本轮回应焦点”只约束本轮如何回应，不证明任何事实已经发生，也不能代替玩家实施动作；问题、讨论、提议、假设和态度必须先被直接承接，不能被写成已完成结果。
- fact_candidates 必须是数组；没有新的公开事实时返回空数组，不能为了表示“有进度”重复旧事实或编造候选。
- 每个事实候选的基础字段必须且只能是 goal_id、fact_type、fact_role、subject、predicate、object、content_slot_id，以及可选 public_entity；若对应事实合同包含 content_id，候选还必须原样返回同一个 content_id，其他候选不得自行增加该字段。
- public_entity 是可选字段，只有该候选对应的 allowed_new_facts.content_slot_id 非空时才能携带；content_slot_id 为空字符串时必须完全省略 public_entity，不能返回空对象或把同一物件重复挂到动作事实上。携带时字段必须且只能是 kind、label、status，不得输出 entity_id；道具 kind=prop 时 status 只能是 available、selected 或 used，线索 kind=clue 时 status 只能是 discovered。
- 对应作者目录内容的候选必须携带 public_entity；object、kind、label 必须分别精确使用作者目录给出的 fact_object、entity_kind、label。不得用同义词改写标签、跨槽替换 content_id，或根据名称自行猜测 traits。
- 候选的 fact_type、fact_role 和 content_slot_id 必须来自“已验证支线”的 allowed_new_facts；goal_id 只能引用该支线 convergence 出口中的现有 Goal ID，没有对应 Goal 时使用空字符串。
- 只有 narration 或 dialogue 已明确公开发生的可观察结果才能进入 fact_candidates；计划、愿望、提议、问题、未获回应的动作、玩家内心和关系推断都不能作为事实。
- 必须承认“已提交支线事实”，不得再次生成语义相同的候选，也不得让演出否认、撤销或重演已经发生的结果。
- 优先核对“当前待推进Beat”和“尚未提交事实合同”：玩家本轮原话若已经明确实施其中的可观察行动，演出必须承认该结果并生成对应候选；若只是在询问、提议或计划，则不得抢先提交。
- 只有本轮事实候选覆盖全部“尚未提交事实合同”时，才可以把本轮写成汇流前的最后一步；否则不得提前宣布、暗示或表演“可能汇流语义”中的共同主线事件已经发生。
- 确实补齐全部合同时，dialogue 仍须先回应玩家本轮原话，再自然带到共同主线事件；可结合“已验证做法回顾”和本轮公开动作，提及一至两个已经验证的具体做法，让猫娘以当前人格承认玩家怎样抵达这里。
- 不得宣读事实清单或支线总结，不得复述内部字段，也不得把未提交事实、未公开动机或纯风味互动写成长期影响；作者 fallback callback 仍是共同主线事件的权威旁白。
- 按 beat_outline 推进当前目标，但 Beat 不是固定台词；不得宣读 beat_id、事实角色、Goal、Ending、预算、次数、Patch 或支线系统术语。
- 严格遵守 forbidden_assumptions、作者限制和已公开状态；不得新增身份、秘密、题材、未经铺垫的关系状态或未开放内容槽位。
- 所有内容必须来自当前用户 Story Package 和本轮公开语境，不能套用固定道具、固定地点、固定关系或固定情节。
"""


def _selected_catalog_items(
    story: dict[str, Any],
    patch: dict[str, Any],
) -> list[dict[str, str]]:
    """只投影 Patch 已绑定的作者目录成员，避免把整个 catalog 重复交给 Actor。"""  # noqa: DOCSTRING_CJK
    contract = (
        story.get("world_contract")
        if isinstance(story.get("world_contract"), dict)
        else {}
    )
    slots = {
        str(slot.get("slot_id") or ""): slot
        for slot in contract.get("dynamic_content_slots") or []
        if isinstance(slot, dict) and str(slot.get("slot_id") or "").strip()
    }
    selected: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for rule in patch.get("allowed_new_facts") or []:
        if not isinstance(rule, dict):
            continue
        slot_id = str(rule.get("content_slot_id") or "").strip()
        content_id = str(rule.get("content_id") or "").strip()
        key = (slot_id, content_id)
        if not slot_id or not content_id or key in seen:
            continue
        slot = slots.get(slot_id)
        if not isinstance(slot, dict):
            continue
        item = next(
            (
                candidate
                for candidate in slot.get("catalog_items") or []
                if isinstance(candidate, dict)
                and str(candidate.get("content_id") or "").strip() == content_id
            ),
            None,
        )
        if not isinstance(item, dict):
            continue
        seen.add(key)
        selected.append(
            {
                "content_slot_id": slot_id,
                "content_id": content_id,
                "fact_object": str(item.get("fact_object") or ""),
                "entity_kind": str(item.get("entity_kind") or ""),
                "label": str(item.get("label") or ""),
            }
        )
    return selected


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
    pullback_intent_summary: str = "",
    runtime_branch_patch: dict[str, Any] | None = None,
    completed_branch_recall: list[dict[str, Any]] | None = None,
    response_focus: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """把本轮公开事实压缩为单次 LLM 请求，私有规则不进入提示词。"""  # noqa: DOCSTRING_CJK
    # 可选剧本段落可能被作者写成 null 或其他类型；统一降级为空对象，避免在模型安全回退前中断回合。
    seed = story.get("seed") if isinstance(story.get("seed"), dict) else {}
    scenario_card = (
        story.get("scenario_card")
        if isinstance(story.get("scenario_card"), dict)
        else {}
    )
    runtime_guardrails = (
        story.get("runtime_guardrails")
        if isinstance(story.get("runtime_guardrails"), dict)
        else {}
    )
    guide = (
        node.get("runtime_generation_guide")
        if isinstance(node.get("runtime_generation_guide"), dict)
        else {}
    )
    bounded_response_focus = (
        dict(response_focus) if isinstance(response_focus, dict) else {}
    )
    authored_performance = progress_kind in {"opening", "graph_progress"}
    branch_entry = progress_kind == "branch_entry" and isinstance(
        runtime_branch_patch, dict
    )
    if branch_entry:
        # 支线入口只使用已验证行动方向；Planner 文本不能作为已经发生的旁白事实。
        target_node = {}
        turn_instruction = (
            "这是已验证临时支线的入口。直接回应玩家本轮坚持的公开行动，narration 必须为空字符串；"
            "只能为第一个可执行 Beat 建立下一步，不能宣布任何 allowed_new_facts、Goal、Ending 或交换结果已经完成。"
            "不得解释支线、Patch、Beat、事实角色、预算或内部校验。"
        )
    elif authored_performance and bounded_response_focus:
        # 作者正文由服务端保留为不可改写段；Actor 只补齐同句中尚未被 Choice 消费的回应义务。
        target_node = {
            "title": str(node.get("title") or ""),
            "summary": str(node.get("summary") or ""),
            "author_dialogue": str(node.get("scripted_dialogue") or ""),
        }
        turn_instruction = "先回应焦点，并让回应能自然衔接目标节点；只生成焦点所需的简短补充对白，不要复述、改写或省略作者对白。作者对白将由服务端作为不可改写段逐字保留。不得增加口癖、自称、事实或关系；内部规则只通过不越界来执行，不能转述成对白。"
    elif authored_performance:
        # 作者对白本身可直接演出；模型只能在不改变任何作者语义时做可选的表层润色。
        target_node = {
            "title": str(node.get("title") or ""),
            "summary": str(node.get("summary") or ""),
            "author_dialogue": str(node.get("scripted_dialogue") or ""),
        }
        turn_instruction = "优先采用作者对白原文，并完整保留其中的公开事实、当下问题、角色邀请和剧情交接。只有确有必要时才按当前猫娘人格调整表层语气；不得增加口癖、自称、事实或关系，也不得删改作者边界。内部规则只通过不越界来执行，不得转述成对白、免责声明或玩法说明。承接最近对话；若作者结果与猫娘刚说过的话表面冲突，只能调整不影响事实的衔接措辞，不得否认已经公开的内容。"
    else:
        # 当前节点标题描述的是已经完成的上一步；自由互动再次注入会让模型错误回答旧输入。
        target_node = {}
        turn_instruction = "只回应本轮唯一目标，并承接最近对白；不得继续回答上一轮玩家输入，不得复述上一句台词、重演上一动作或再次讨论已经完成的上一个 Choice，除非玩家本轮主动追问。背景和主线只用于防止越界，不得把其中的名称、数量或秘密当成默认话题反复提起。"

    # 只有标点通常表示玩家质疑上一回答，明确要求提供新反应，避免模型原句重播。
    punctuation_only = bool(
        re.fullmatch(r"[\s?？!！…。,.，、]+", str(user_message or ""))
    )
    if branch_entry:
        response_requirement = (
            "先明确承接玩家本轮意图，再给出符合当前猫娘人格的入口回应；narration 必须为空字符串，"
            "dialogue 不得替玩家作出下一步行动或宣告后续事实已经成立。"
        )
    elif progress_kind == "opening":
        response_requirement = "这是正式开场，还没有玩家本轮输入。优先直接采用作者开场对白，并确保第一组推荐选项所需前提完整保留。"
    elif progress_kind == "graph_progress":
        response_requirement = (
            "本轮作者选择已经发生，但同一句中还有独立回应焦点；先直接回应该焦点，再自然进入作者剧情交接。不得把询问、讨论或态度误写成已经实施的新事实。"
            if bounded_response_focus
            else "本轮作者选择已经发生；只回应本轮选择。优先直接采用作者对白，并完整保留下一轮推荐选项所需前提；不得否认、撤销或替换作者回调中的动作与结果。"
        )
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
        # 公开目标只服务选剧卡；模型读取作者 Narrative Goal，避免同一字段兼任剧透文案和内部合同。
        "主线目标": [
            str(item.get("summary") or "")
            for item in story.get("narrative_goals") or []
            if isinstance(item, dict) and str(item.get("summary") or "").strip()
        ],
        "本轮演绎指令": turn_instruction,
        "本轮回应要求": response_requirement,
        "本轮回应焦点边界": (
            "回应义务不等于事实已经发生。先承接玩家原话，但不得仅凭 response_focus 宣告动作、Beat、"
            "Branch Fact、Goal 或后续结果已经完成；支线入口不得宣告后续事实。"
            if bounded_response_focus
            else ""
        ),
        # 普通互动只允许借候选标签理解下一步，任何选项结果都必须等 Router 命中并由服务端提交。
        "未提交选项边界": (
            "当前可推进选项均尚未执行，只能用于理解尚未发生的下一步；不得改写显示文案，也不得在 narration 或 dialogue 中"
            "声称玩家已经完成、猫娘已经收到或结果已经成立。"
            if progress_kind == "roleplay_response" and choice_options
            else ""
        ),
        # 隐藏边前两次命中时只提供语义目标，不提供 ID、次数或分支名称，避免玩法规则泄漏进台词。
        "本轮支线留步要求": (
            "先完整回应玩家正在坚持的话题，再用当前公开物件、天气或尚未回答的问题自然把注意力带回眼前事件；"
            "不得拒答，也不得解释主线、支线、拉回或次数。玩家意图："
            + str(pullback_intent_summary)
            if str(pullback_intent_summary).strip()
            else ""
        ),
        "当前节点禁用对白": list(guide.get("forbidden_dialogue_phrases") or []),
        "作者演绎意图": {
            "旁白意图": str(guide.get("narrator_intent") or ""),
            "猫娘意图": str(guide.get("catgirl_raw_intent") or ""),
        },
    }
    if branch_entry:
        # 只投影 Actor 执行入口所需的已验证内容，不把服务端 branch_id、revision 或意图计数交给模型。
        internal_rules["已验证临时支线"] = {
            "seed_intent": str(runtime_branch_patch.get("seed_intent") or ""),
            "objective": str(runtime_branch_patch.get("objective") or ""),
            "content_slot_ids": list(
                runtime_branch_patch.get("content_slot_ids") or []
            ),
            "allowed_new_facts": list(
                runtime_branch_patch.get("allowed_new_facts") or []
            ),
            "forbidden_assumptions": list(
                runtime_branch_patch.get("forbidden_assumptions") or []
            ),
            "beat_outline": list(runtime_branch_patch.get("beat_outline") or []),
            "exit_candidates": list(runtime_branch_patch.get("exit_candidates") or []),
            # 入口只看当前 Patch 已选择的作者成员，不重复注入未选择目录或让模型重选。
            "selected_catalog_items": _selected_catalog_items(
                story,
                runtime_branch_patch,
            ),
        }
    performance_context = {
        "猫娘名称": str(lanlan_name or "Lan"),
        "猫娘人格摘要": str(character_profile or "保持当前猫娘自然说话风格"),
        "故事背景": str(story.get("background") or story.get("world_seed") or ""),
        "故事主题": str(story.get("theme") or ""),
        "玩家身份": str(
            scenario_card.get("player_role") or seed.get("user_role") or "故事参与者"
        ),
        # 故事职责与日常人格分开提供，避免人格昵称覆盖当前剧本已经声明的专业关系。
        "猫娘故事身份": str(
            scenario_card.get("catgirl_role") or "当前故事中的共同主角"
        ),
        "当前场景": {
            "title": str(scene.get("title") or ""),
            "text": str(scene.get("text") or ""),
        },
        "本轮类型": progress_kind,
        # 焦点已经由服务端证明来自本轮玩家原话，只提供回应义务，不授予事实提交权。
        "本轮回应焦点": bounded_response_focus,
        # 支线入口锚点不是作者静态回调，避免模型把 Planner 候选误认为作者固定节点。
        "作者回调": "" if branch_entry else callback,
        "目标节点": target_node,
        "已公开状态": public_state,
        # 这里只接收服务端已去除 Fact/Branch/Entity ID 的有限投影，供汇流后的普通演绎保持事实连续性。
        "历史支线已公开事实": list(completed_branch_recall or []),
        "最近对话": recent_turns[-4:],
        "当前可推进选项": [
            {
                "choice_id": str(item.get("choice_id") or ""),
                "当前显示文案": str(item.get("label") or ""),
                "作者原始文案": str(
                    item.get("author_label") or item.get("label") or ""
                ),
                "类型": str(item.get("choice_mode") or ""),
            }
            for item in choice_options
        ]
        if progress_kind == "roleplay_response"
        else [],
        # 这些按钮会在本轮对白之后立刻出现；只给模型看公开标签，用于保证人格转述不丢失必要的剧情交接。
        "下一轮推荐选项": [
            {
                "显示文案": str(item.get("label") or ""),
                "类型": str(item.get("choice_mode") or ""),
            }
            for item in choice_options
        ]
        if authored_performance
        else [],
        # 把本轮输入放在公开演绎上下文末尾，减少小模型被旧节点和上一轮对话抢走注意力。
        "本轮唯一回应目标": response_target,
    }
    # 使用 JSON 序列化上下文，减少小模型误读分隔符或混淆字段层级。
    prompt_envelope = {
        "内部规则（只执行，不复述）": internal_rules,
        "公开演绎上下文": performance_context,
    }
    user_prompt = "请根据以下分区数据生成本轮 JSON：\n" + json.dumps(
        prompt_envelope, ensure_ascii=False
    )
    return THEATER_TURN_SYSTEM_PROMPT, user_prompt


def build_theater_branch_turn_prompts(
    *,
    lanlan_name: str,
    story: dict[str, Any],
    scene: dict[str, Any],
    user_message: str,
    public_state: dict[str, Any],
    recent_turns: list[dict[str, str]],
    character_profile: str,
    patch: dict[str, Any],
    branch_facts: list[dict[str, Any]],
    node: dict[str, Any] | None = None,
    response_focus: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """构造活动支线 Actor 上下文，排除服务端 ID、revision、计数和私有意图。"""  # noqa: DOCSTRING_CJK
    current_node = node if isinstance(node, dict) else {}
    guide = (
        current_node.get("runtime_generation_guide")
        if isinstance(current_node.get("runtime_generation_guide"), dict)
        else {}
    )
    # 服务端按已提交事实计算当前缺口，避免模型在每轮完整 Beat 列表中反复猜测进度。
    observed_fact_roles = {
        str(item.get("fact_role") or "")
        for item in branch_facts
        if isinstance(item, dict) and str(item.get("fact_role") or "")
    }
    remaining_fact_contracts = [
        dict(item)
        for item in patch.get("allowed_new_facts") or []
        if isinstance(item, dict)
        and str(item.get("fact_role") or "") not in observed_fact_roles
    ]
    # 只把事实已经证明完成的 Beat 投影成人类可读做法，Actor 不接触 beat_id 或事实身份。
    completed_method_recall: list[dict[str, str]] = []
    for beat in patch.get("beat_outline") or []:
        if not isinstance(beat, dict):
            continue
        prepared_roles = {
            str(item)
            for item in beat.get("exit_preparation") or []
            if isinstance(item, str) and str(item).strip()
        }
        if not prepared_roles or not prepared_roles.issubset(observed_fact_roles):
            continue
        method = {
            "objective": str(beat.get("objective") or ""),
            "observable_action": str(beat.get("observable_action") or ""),
        }
        if any(method.values()):
            completed_method_recall.append(method)

    # 汇流提示只匹配当前 Patch 声明的作者 Goal，并去掉稳定 ID 与目标节点身份。
    narrative_goals = {
        str(goal.get("goal_id") or ""): goal
        for goal in story.get("narrative_goals") or []
        if isinstance(goal, dict) and str(goal.get("goal_id") or "").strip()
    }
    convergence_semantics: list[dict[str, str]] = []
    for exit_candidate in patch.get("exit_candidates") or []:
        if not isinstance(exit_candidate, dict) or str(
            exit_candidate.get("kind") or ""
        ) != "converge":
            continue
        goal = narrative_goals.get(str(exit_candidate.get("goal_id") or ""))
        if not isinstance(goal, dict):
            continue
        summary = str(goal.get("summary") or "").strip()
        common_event = str(goal.get("fallback_convergence_callback") or "").strip()
        if summary and common_event:
            convergence_semantics.append(
                {
                    "完成条件摘要": summary,
                    "共同主线事件": common_event,
                }
            )
    current_beat: dict[str, Any] = {}
    for beat in patch.get("beat_outline") or []:
        if not isinstance(beat, dict):
            continue
        prepared_roles = {
            str(item)
            for item in beat.get("exit_preparation") or []
            if isinstance(item, str) and str(item).strip()
        }
        if prepared_roles and not prepared_roles.issubset(observed_fact_roles):
            # 只投影首个尚未完成 Beat 的公开行动语义，不提供计数、服务端身份或隐藏状态。
            current_beat = {
                "objective": str(beat.get("objective") or ""),
                "observable_action": str(beat.get("observable_action") or ""),
                "pending_fact_roles": sorted(prepared_roles - observed_fact_roles),
            }
            break
    actor_context = {
        "猫娘名称": str(lanlan_name or "Lan"),
        "猫娘人格摘要": str(character_profile or "保持当前猫娘自然说话风格"),
        "故事边界": {
            "背景": str(story.get("background") or story.get("world_seed") or ""),
            "主题": str(story.get("theme") or ""),
            "作者限制": list(story.get("restrictions") or []),
            # Patch 已由服务端并入 seed 禁止假设；节点级边界另显式提供，避免活动 Actor 绕过普通演绎护栏。
            "当前节点禁用对白": list(
                guide.get("forbidden_dialogue_phrases") or []
            ),
            "当前节点演绎意图": {
                "旁白意图": str(guide.get("narrator_intent") or ""),
                "猫娘意图": str(guide.get("catgirl_raw_intent") or ""),
            },
        },
        "当前场景": {
            "title": str(scene.get("title") or ""),
            "text": str(scene.get("text") or ""),
        },
        "已公开状态": public_state,
        # Actor 只需要 Patch 的行动语义与稳定白名单；origin、branch_id 和预算计数不参与演绎。
        "已验证支线": {
            "seed_intent": str(patch.get("seed_intent") or ""),
            "objective": str(patch.get("objective") or ""),
            "allowed_new_facts": list(patch.get("allowed_new_facts") or []),
            "forbidden_assumptions": list(patch.get("forbidden_assumptions") or []),
            "beat_outline": list(patch.get("beat_outline") or []),
            "exit_candidates": list(patch.get("exit_candidates") or []),
            # 目录成员的公开标签和事实对象来自作者；Actor 只负责在公开动作发生时精确引用。
            "selected_catalog_items": _selected_catalog_items(story, patch),
        },
        # 这两项完全由上方 Patch 白名单和已提交事实派生，只帮助 Actor 聚焦本轮仍可提交的合同。
        "当前待推进Beat": current_beat,
        "尚未提交事实合同": remaining_fact_contracts,
        # 已提交事实只投影语义字段和可选公开实体，服务端 fact/entity/branch ID 与 revision 永不下发。
        "已提交支线事实": [
            {
                "goal_id": str(item.get("goal_id") or ""),
                "fact_type": str(item.get("fact_type") or ""),
                "fact_role": str(item.get("fact_role") or ""),
                "subject": str(item.get("subject") or ""),
                "predicate": str(item.get("predicate") or ""),
                "object": str(item.get("object") or ""),
                "content_slot_id": str(item.get("content_slot_id") or ""),
                "public_entity": {
                    key: str(item["public_entity"].get(key) or "")
                    for key in ("kind", "label", "status")
                }
                if isinstance(item.get("public_entity"), dict)
                else {},
            }
            for item in branch_facts[-12:]
            if isinstance(item, dict)
        ],
        # 最多保留靠近当前出口的四段已验证做法；Actor 只能自然选取一至两个细节回应。
        "已验证做法回顾": completed_method_recall[-4:],
        # 作者 callback 只帮助最后一轮对白自然朝共同主线转身，真正旁白仍由提交后的服务层覆盖。
        "可能汇流语义": convergence_semantics,
        "最近公开对话": recent_turns[-4:],
        # 焦点只帮助 Actor 承接本轮输入；事实是否可提交仍由服务端动作门控与合同共同决定。
        "本轮回应焦点": dict(response_focus)
        if isinstance(response_focus, dict)
        else {},
        # 玩家本轮原话放在最后，减少旧事实和 Beat 抢走 Actor 的回应焦点。
        "玩家本轮原话": str(user_message or ""),
    }
    return (
        THEATER_BRANCH_TURN_SYSTEM_PROMPT,
        "请根据以下已验证支线与公开语境生成本轮 Actor JSON：\n"
        + json.dumps(actor_context, ensure_ascii=False),
    )


def build_theater_branch_planner_prompts(
    *,
    story: dict[str, Any],
    scene: dict[str, Any],
    current_node_id: str,
    current_node: dict[str, Any],
    public_state: dict[str, Any],
    dynamic_intent: dict[str, Any],
    recent_turns: list[dict[str, str]],
    completed_goal_ids: list[str] | None = None,
) -> tuple[str, str]:
    """构造通用 Planner 上下文，只暴露作者合同、公开事实与自由意图语义。"""  # noqa: DOCSTRING_CJK
    # Planner 只需要作者节点的公开语义；节点边、回调、内部条件和服务器 revision 不进入模型上下文。
    planner_context = {
        "故事边界": {
            "背景": str(story.get("background") or story.get("world_seed") or ""),
            "主题": str(story.get("theme") or ""),
            "作者限制": list(story.get("restrictions") or []),
        },
        "作者世界合同": story.get("world_contract")
        if isinstance(story.get("world_contract"), dict)
        else {},
        # Planner 只需知道可证明条件与稳定出口，不读取主线目标节点或作者回退演出文本。
        "作者叙事目标": [
            {
                "goal_id": str(item.get("goal_id") or ""),
                "summary": str(item.get("summary") or ""),
                "completion_evidence": list(item.get("completion_evidence") or []),
                "convergence_fact_roles": list(
                    item.get("convergence_fact_roles") or []
                ),
            }
            for item in story.get("narrative_goals") or []
            if isinstance(item, dict)
        ],
        # Planner 需要知道哪些作者 Goal 已经完成，但不获得完成 revision、事实 ID 或 History 身份。
        "已完成作者目标 ID": [
            str(item) for item in completed_goal_ids or [] if str(item).strip()
        ],
        # Ending Domain 只暴露进入条件；具体 ending_id 仍由服务端在退出时确定性解析。
        "作者结局域": [
            {
                "ending_domain_id": str(item.get("ending_domain_id") or ""),
                "required_goal_ids": list(item.get("required_goal_ids") or []),
                "required_fact_types": list(item.get("required_fact_types") or []),
                "required_fact_roles": list(item.get("required_fact_roles") or []),
                "forbidden_fact_roles": list(item.get("forbidden_fact_roles") or []),
            }
            for item in story.get("ending_domains") or []
            if isinstance(item, dict)
        ],
        "当前场景": {
            "title": str(scene.get("title") or ""),
            "text": str(scene.get("text") or ""),
        },
        "当前作者节点": {
            "node_id": str(current_node_id or ""),
            "title": str(current_node.get("title") or ""),
            "summary": str(current_node.get("summary") or ""),
        },
        "已公开状态": public_state,
        "最近公开对话": recent_turns[-4:],
        # 只提供持续意图的公开语义证据；intent_key、origin 和 streak 始终由服务端保管。
        "当前自由意图": {
            "意图说明": str(dynamic_intent.get("intent_summary") or ""),
            "最近玩家证据": [
                str(item)
                for item in dynamic_intent.get("evidence_messages") or []
                if str(item).strip()
            ][-3:],
        },
    }
    return (
        THEATER_BRANCH_PLANNER_SYSTEM_PROMPT,
        "请根据以下作者合同与公开语境生成 Runtime Branch Patch JSON：\n"
        + json.dumps(planner_context, ensure_ascii=False),
    )


def build_theater_route_prompts(
    *,
    story: dict[str, Any],
    scene: dict[str, Any],
    user_message: str,
    public_state: dict[str, Any],
    recent_turns: list[dict[str, str]],
    choice_options: list[dict[str, Any]],
    latent_transitions: list[dict[str, Any]],
    current_dynamic_intent: dict[str, Any] | None = None,
    current_pending_intent: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """构造统一语义路由提示，不让 Router 获得状态身份或参与角色演绎。"""  # noqa: DOCSTRING_CJK
    dynamic_intent = (
        current_dynamic_intent if isinstance(current_dynamic_intent, dict) else {}
    )
    pending_intent = (
        current_pending_intent if isinstance(current_pending_intent, dict) else {}
    )
    route_context = {
        "故事背景": str(story.get("background") or story.get("world_seed") or ""),
        "当前场景": {
            "title": str(scene.get("title") or ""),
            "text": str(scene.get("text") or ""),
        },
        "已公开状态": public_state,
        "最近对话": recent_turns[-4:],
        "当前推荐选项": [
            {
                "choice_id": str(item.get("choice_id") or ""),
                "类型": str(item.get("choice_mode") or ""),
                "当前显示文案": str(item.get("label") or ""),
                "作者原始文案": str(
                    item.get("author_label") or item.get("label") or ""
                ),
                "作者回调": str(item.get("callback") or ""),
                "目标结果": str(item.get("target_summary") or ""),
                "作者完成表达": [
                    str(value) for value in item.get("completion_phrases") or []
                ],
            }
            for item in choice_options
        ],
        # 隐藏边仍只暴露作者白名单语义；目标节点、事实增量和结局不交给模型。
        "当前隐藏语义候选": [
            {
                "intent_id": str(item.get("intent_id") or ""),
                "意图说明": str(item.get("intent_summary") or ""),
                "表达示例": [str(value) for value in item.get("intent_examples") or []],
            }
            for item in latent_transitions
        ],
        # Router 只获得判断 relation 所需的语义证据；服务端 ID、origin 和 streak 永不进入提示词。
        "当前通用自由意图": {
            "意图说明": str(dynamic_intent.get("intent_summary") or ""),
            "最近玩家证据": [
                str(item)
                for item in dynamic_intent.get("evidence_messages") or []
                if str(item).strip()
            ][-3:],
        }
        if dynamic_intent
        else {},
        # Pending 只暴露后续语义与原话摘录；来源节点、目标节点和 revision 仍由服务端独占。
        "待重验剩余意图": {
            "意图说明": str(pending_intent.get("summary") or ""),
            "原话摘录": str(pending_intent.get("evidence_excerpt") or ""),
        }
        if pending_intent
        else {},
        # 玩家原话放在最后，降低长背景和历史对当前语义判断的干扰。
        "玩家本轮原话": str(user_message or ""),
    }
    return (
        THEATER_ROUTE_SYSTEM_PROMPT,
        "请根据以下公开数据判断本轮路由：\n"
        + json.dumps(route_context, ensure_ascii=False),
    )


def build_theater_branch_handoff_prompts(
    *,
    story: dict[str, Any],
    scene: dict[str, Any],
    user_message: str,
    recent_turns: list[dict[str, str]],
    active_branch: dict[str, Any],
) -> tuple[str, str]:
    """只投影活动支线的公开语义，构造无状态权限的转交分类提示。"""  # noqa: DOCSTRING_CJK
    patch = active_branch.get("patch") if isinstance(active_branch, dict) else {}
    if not isinstance(patch, dict):
        patch = {}
    handoff_context = {
        # 标题与主题只帮助模型理解当前公开题材；Story 身份、作者合同和稳定引用均不进入提示词。
        "当前故事公开语义": {
            "title": str(story.get("title") or ""),
            "theme": str(story.get("theme") or ""),
            "background": str(story.get("background") or story.get("world_seed") or ""),
        },
        "当前公开场景": {
            "title": str(scene.get("title") or ""),
            "text": str(scene.get("text") or ""),
        },
        # 活动对象只读取已经验证 Patch 中可公开陈述的种子意图和目标，不下发整个 Patch。
        "当前活动公开语义": {
            "seed_intent": str(patch.get("seed_intent") or ""),
            "objective": str(patch.get("objective") or ""),
        },
        "最近公开对话": recent_turns[-4:],
        # 玩家原话放在最后，便于模型逐字返回两段可由服务端复核的证据。
        "玩家本轮原话": str(user_message or ""),
    }
    return (
        THEATER_BRANCH_HANDOFF_SYSTEM_PROMPT,
        "请根据以下公开语义判断是否形成活动转交候选：\n"
        + json.dumps(handoff_context, ensure_ascii=False),
    )
