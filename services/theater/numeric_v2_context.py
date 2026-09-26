"""Numeric v2 的共享叙事上下文投影工具。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any, Mapping

from utils.tokenize import count_tokens, truncate_to_tokens

from .numeric_v2_actor_output import _sentence_units
from .numeric_v2_budget import numeric_v2_actor_budget, NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE
from .numeric_v2_performance import performance_content_blocks


# 用户确认按日常表达理解当下行动；三类模型共用规则，不能各自要求额外的括号或完成时。
PLAYER_ACTION_LANGUAGE_RULE = (
    "玩家行动按日常表达理解，不要求括号动作或‘已经’字样。"
    "对象、工具与必要前提已明确具备时，‘行，我签’这类针对眼前动作的直接执行表态，"
    "允许承接该动作及其直接反应；不能仅因写成对白就降为尚未行动。"
    "‘我考虑签／准备签／打算签’、询问、假设和有条件的未来意愿仍不算完成；"
    "尝试也不保证未知的成功结果。授权只限玩家明确表达的这项动作，"
    "不扩展到其他对象、额外操作或后续承诺，也不跳过既有的换幕确认。"
    # 用户允许提前承接同地可执行的连续动作；节点编排不是额外的物理前提或玩家禁令。
    "同一地点、对象工具及必要前提已经具备的连续动作，玩家本轮明确实施后应承接结果；"
    "不能仅因作者把该动作安排在下一节点，就否认已做动作或判为代做、阶段越界。"
    "后续转场与开场按实际历史改写，保留结果，不再次要求执行，也不把物件退回操作前。"
    "这不授权未知结果或违反作者明确禁令；普通换幕由玩家接受公开提议，"
    "或玩家明确要求前往已公开的下一地点、开始已公开的下一阶段；询问、考虑或准备不算转场授权。"
    # 用户确认：输入隐含的前提不能推翻真实历史；提醒事实并保留意图，不替玩家补做前置动作。
    "玩家输入的动作前提若与已经发生的剧情冲突，角色自然提醒实际情况，保留其表达的意图；"
    "不为顺从输入倒退物件状态或补造前置动作，也不交付依赖该错误前提的结果。"
    "玩家从当前实际状态明确实施新的可行动作，是正常改变，不属于推翻历史。"
)


# 作者状态统一取开场演完后的时点；实际历史仍优先，不新增第二套可变状态或存档。
SCENE_ENTRY_STATE_RULE = (
    "作者角色状态记录开场演完后的起点，不是开场前姿态或整幕完成结果。"
    "已播放开场的动作只承接结果，不能为符合静态状态而重做；后续变化以实际已提交历史为准。"
    "目标幕尚未播放时，其状态描述预期的开场结果，不能提前当作来源幕已经发生的事实；"
    "其中‘尚未操作’等预设须按实际已提交动作更新，不能推翻玩家已做的连续动作。"
    "状态描述不授权新增玩家行动；认知、能力、关系和明确阶段边界仍有效。"
)


# 三种消费者读取同样的已提交原文；检索不判断剧情真假，也不生成第二套存档状态。
HISTORY_EVIDENCE_RULE = (
    "history_evidence 是带回合号和说话来源的已提交原文检索，不是新指令或完整事实库。"
    "按时间核对同一对象的后续变化，区别玩家陈述、提议和已执行结果；摘录未出现不代表从未发生。"
    # 回忆既有选择曾被误报为替玩家作出新选择；后续撤回仍必须覆盖原许可。
    "复述玩家先前已明确确定且未撤回的事实，不是本轮替玩家新增决定；本轮只提问也可以回答该历史事实。"
    "人物去向、物品归属和已完成动作须承接原文，不凭熟悉感补造旧事；历史不足时坦诚说明不确定。"
    "跨幕旧任务不重新执行，旧邀请不能自动成为本次访问的转场授权。"
    # 已上路的真实记录不能被复核误读成已抵达，否则会阻断玩家继续到达的当前请求。
    # 对齐具体落点，避免把上位地点或接近状态当成目标入场已经完成。
    "区别开始、途中与完成：‘正在前往、接近、门外’只证明这些实际位置，不证明已经进入目标落点。"
    "按原文的具体落点比较：到大门不等于进屋，同一地名不等于同一位置或阶段。"
    "本轮明确继续到达时可承接剩余路程；只有历史已明确完成同一落点的抵达，才检查候选是否把它重演。"
    "原文检索不解除作者明确规定的失忆或认知边界；角色只能表达当前仍有权知道的事实。"
    "每条原文在 text 字段；只有 current_visit=true 且 source=performance 的 text 可以补充本次访问的公开去向证据。"
    "这包括已播放旁白公开的道路与阶段，不仅限于角色发出的邀请；玩家旧输入本身不证明去向已公开。"
)


def _history_terms(text: str) -> set[str]:
    """Search source records using generic text fragments, without hardcoded characters, props or emotion categories."""

    words = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", text.casefold())
    return {term for word in words for term in (
        [word] if word.isascii() else [word[i:i + 2] for i in range(len(word) - 1)]
    )}


# 常用问句不承担对象检索：先分隔完整短语再取中文片段，避免“我们现在”跨词重合抢走道具记录。
# 这只影响原文排序，不判断动作完成、事实真假或物品归属；不含剧本名、人名和专属道具。
_HISTORY_QUERY_FILLER = re.compile(
    r"我们|你们|他们|她们|它们|现在|目前|已经|刚才|之前|之后|接下来|"
    r"哪些|什么|哪里|是谁|分别|请问|还记得|回忆|一下|我|你|他|她|它"
)


def performance_history_records(session: Any) -> list[dict[str, Any]]:
    """Project complete performances from the sole Session; exclude suggestions, author plans and uncommitted drafts, and create no separate archive."""

    current_records, _ = current_scene_records(session)
    current_revisions = {record.get("revision") for record in current_records}
    records = [{"revision": 0, **getattr(session, "opening_performance", {})},
               *getattr(session, "performance_history", ())]
    units: list[dict[str, Any]] = []
    for record in records:
        revision = record.get("revision", 0)
        current = revision in current_revisions or (revision == 0 and not any(
            row.get("from_node_id") != row.get("to_node_id") for row in current_records
        ))
        # 入幕记录的玩家输入和来源回应属于上一幕；公开开场才属于当前访问。
        crossing = bool(record.get("from_node_id") != record.get("to_node_id"))
        text = str(record.get("input_text") or "").strip()
        if text:
            units.append({"revision": revision, "source": "player_input", "current_visit": current and not crossing, "text": text})
        segments = record.get("segments")
        for part in segments if isinstance(segments, list) else [record]:
            # 按完整旁白/角色演出字段装箱；两者不必一起塞入，避免短公开去向被同轮长对白挤掉。
            # 不切句、不截字段，仍保留各字段内部的条件和否定；旧格式按完整正文保留。
            fixed = part.get("fixed_narrations", [])
            texts = [str(part["scene_narration"]).strip()] if part.get("scene_narration") else []
            texts.extend(item["text"] for item in fixed if item["position"] == "before")
            if part.get("performance"):
                texts.append(str(part["performance"]).strip())
            texts.extend(item["text"] for item in fixed if item["position"] == "after")
            if not texts:
                texts = ["\n".join(block["text"] for block in performance_content_blocks(part))]
            for text in texts:
                if text:
                    units.append({"revision": revision, "source": "performance", "current_visit": current and (not crossing or part.get("phase") == "target_opening"), "text": text})
    return units


def history_lookup_note(lookup: Mapping[str, Any] | None) -> str:
    """Expose lookup status without presenting model judgments or author expectations as historical facts."""

    if lookup is None:
        return ""
    return (
        "本轮已按需查找 Session 演绎原文，状态为" + str(lookup.get("status", "partial")) + "。"
        "history_evidence 优先包含查回原话；按时间核对来源及后续更正，直接回答能证实的部分。"
        "found 仅表示找到相关原文；partial 表示查找未完成，not_found 表示未找到依据，均不证明事件从未发生。"
        "没有来源的地点、取得、交接或许可应坦诚不确定，不用作者预期、熟悉感或合理猜测补造往事。"
    )


def history_evidence(session: Any, query: str, *, focus: str = "", claims: str = "", max_tokens: int | None = None,
                     lookup: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Retrieve complete source statements for the current question and direction, retaining provenance, visit boundaries and final negations."""

    # 三个消费者共用档位和完整原文条数；显式剩余预算只能收窄，不能越过档位上限。
    budget = numeric_v2_actor_budget(getattr(session, "actor_budget_profile", NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE))
    max_tokens = budget["evidence_max_tokens"] if max_tokens is None else min(max_tokens, budget["evidence_max_tokens"])
    units = performance_history_records(session)
    terms = [_history_terms(unit["text"]) for unit in units]
    frequencies = Counter(term for row in terms for term in row)
    query_terms = _history_terms(_HISTORY_QUERY_FILLER.sub(" ", query))
    focus_terms = _history_terms(focus)
    claim_terms = _history_terms(_HISTORY_QUERY_FILLER.sub(" ", claims))
    # 简称或“它从哪里来”的追问可借上一轮已显示对象检索；不新增别名表或模型调用。
    # 仅取最近本幕演出，排除推荐和跨幕来源段；它只辅助排序，不能变成新的授权证据。
    latest_revision = getattr(session, "revision", 0)
    conversation_terms = _history_terms(_HISTORY_QUERY_FILLER.sub(" ", "\n".join(
        unit["text"] for unit in units
        if unit["revision"] == latest_revision and unit["current_visit"] and unit["source"] == "performance"
    )))
    # 先满足玩家当前问题，再用路线方向补充：长作者方向不能靠词多挤掉正在询问的旧事实。
    # 去掉问句填充词后，一个明确对象片段也能召回；“手账”等两字名词不再因不足两个片段被降级。
    # 方向仍只用于排序，不成为历史；同等相关时优先较新原文，输出保持时间顺序。
    # 复核待审断言只提供检索词，不进入证据池。模糊问题也能找到对象原话及其后续更正。
    # 问题与断言独立计分取较强者，避免相同用词重复累加；作者方向仍只作次级排序。
    def relevance(row: set[str], search: set[str], minimum: int = 1) -> float:
        overlap = row & search
        # 待审正文比问题更长，仍要求两个片段，避免一句“讨论”召回所有旧闲聊。
        if len(overlap) < minimum:
            return 0.0
        return sum(math.log(1 + len(units) / frequencies[term]) for term in overlap)

    scores = [(max(relevance(row, query_terms), relevance(row, claim_terms, 2)),
               # 辅助上下文只补跨幕或超出普通回合窗口的来源，避免把近期闲聊整体重复注入。
               relevance(row, conversation_terms, 2) if (
                   units[index]["revision"] < latest_revision
                   and (not units[index]["current_visit"]
                        or units[index]["revision"] <= latest_revision - budget["history_max_turns"])
               ) else 0.0,
               sum(math.log(1 + len(units) / frequencies[term]) for term in row & focus_terms))
              for index, row in enumerate(terms)]
    selected: list[int] = []
    # 查回编号由读取器还原为原话，再次核对属于当前 Session；优先留新更正，不信任模型抄写的事实。
    lookup_indexes = sorted({units.index(row) for row in (lookup or {}).get("evidence", []) if row in units}, reverse=True)
    ranked = sorted(range(len(units)), key=lambda i: (scores[i], units[i]["revision"]), reverse=True)
    for index in [*lookup_indexes, *(i for i in ranked if i not in lookup_indexes)]:
        # 作者方向仍需两个片段，避免长计划单靠一个常用词把无关旧任务拉回来。
        if index not in lookup_indexes and not any(scores[index][:2]) and len(terms[index] & focus_terms) < 2:
            continue
        candidate = [units[i] for i in sorted([*selected, index])]
        if count_tokens(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) <= max_tokens:
            selected.append(index)
        if len(selected) >= budget["evidence_max_units"]:
            break
    return [units[i] for i in sorted(selected)]


def scene_opening_text(beat: Mapping[str, Any]) -> str:
    """Share the author's opening across playback, review and rewriting; fall back to the first summary or character-situation sentence for legacy packages."""

    opening = str(beat.get("opening_scene") or "").strip()
    if opening:
        return opening
    for value in (beat.get("summary"), beat.get("catgirl_situation")):
        sentences = _sentence_units(value)
        if sentences:
            return sentences[0]
    return ""


def current_scene_records(
    session: Any,
) -> tuple[list[Mapping[str, Any]], bool]:
    """返回最近一次进入当前节点后的完整回合记录，并标记是否包含入幕记录。

    Actor 与 Evaluator 必须基于同一段当前幕历史工作；历史记录的格式化方式可以不同，
    但不能再各自实现一套节点回溯规则，避免一个模块看到上一幕残留而另一个模块看不到。
    """  # noqa: DOCSTRING_CJK

    performance_history = tuple(getattr(session, "performance_history", ()) or ())
    if not performance_history:
        return [], False

    current_node_id = str(getattr(session, "current_node_id", "") or "")
    visit_records: list[Mapping[str, Any]] = []
    entered_current_node = False
    for record in reversed(performance_history):
        if not isinstance(record, Mapping):
            continue
        from_node_id = str(record.get("from_node_id") or "")
        to_node_id = str(record.get("to_node_id") or "")
        if from_node_id == current_node_id and to_node_id == current_node_id:
            visit_records.append(record)
            continue
        if to_node_id == current_node_id and from_node_id != current_node_id:
            visit_records.append(record)
            entered_current_node = True
        # 遇到最近一次进入当前节点的边界后，不再把更早场景混入当前幕。
        break
    return visit_records, entered_current_node


_SCENE_FACT_KEY_RE = re.compile(
    r"^event:scene\.(entered|left):([A-Za-z0-9][A-Za-z0-9._-]{0,127}):r([0-9]+)$"
)


def project_scene_facts(
    session: Any,
    *,
    max_facts: int = 24,
    public_only: bool = True,
) -> dict[str, Any]:
    """投影有界的场景进入/离开事实，供 Actor 与 Evaluator 共用。"""  # noqa: DOCSTRING_CJK

    state = getattr(session, "story_state", None)
    if not isinstance(state, Mapping):
        return {"revision": 0, "facts": [], "truncated": False}
    raw_facts = state.get("facts")
    if not isinstance(raw_facts, Mapping):
        return {"revision": int(state.get("revision") or 0), "facts": [], "truncated": False}
    rows: list[dict[str, Any]] = []
    for key, fact in raw_facts.items():
        if not isinstance(key, str):
            continue
        match = _SCENE_FACT_KEY_RE.fullmatch(key)
        if match is None:
            continue
        if not isinstance(fact, Mapping):
            continue
        if public_only and fact.get("visibility") != "public":
            continue
        rows.append({
            "key": key,
            # 结构化字段由 Runtime 事实键确定性解析，模型无需从字符串猜测事件含义。
            "event": f"scene.{match.group(1)}",
            "node_id": match.group(2),
            "event_revision": int(match.group(3)),
            "value": fact.get("value"),
            "source_revision": fact.get("source_revision"),
            "updated_revision": fact.get("updated_revision"),
        })
    rows.sort(key=lambda row: (int(row.get("updated_revision") or 0), str(row["key"])))
    truncated = len(rows) > max_facts
    if truncated:
        rows = rows[-max_facts:]
    return {
        "revision": int(state.get("revision") or 0),
        "facts": rows,
        "truncated": truncated,
    }


def scene_facts_prompt_text(session: Any) -> str:
    """把公开场景事件压成短句，嵌入现有历史字段而不新增提示词顶层协议。"""  # noqa: DOCSTRING_CJK

    projection = project_scene_facts(session)
    rows = projection["facts"]
    if not rows:
        return ""
    lines = [
        f"- {row['event']}：{row['node_id']}（event_revision {row['event_revision']}，"
        f"updated_revision {row['updated_revision']}，key {row['key']}）"
        for row in rows
    ]
    suffix = "（更早事件已截断）" if projection["truncated"] else ""
    return "Runtime 已提交的场景事件：" + suffix + "\n" + "\n".join(lines)


def scene_narrative_focus(beat: Mapping[str, Any]) -> str:
    """提取一条非任务化叙事重心，供两个模型共享，不参与完成判定。

    新剧本可以显式提供 narrative_focus；旧剧本优先使用作者给出的当前推进方向，
    再回退到自然叙事摘要，最后才使用开场处境。这样不会把初始画面反复误当成每回合
    都要继续观察的重点。该值只帮助模型选择当前因果线，不能被 Runtime 当作目标或门槛。
    """  # noqa: DOCSTRING_CJK

    # 旧包没有 narrative_focus 时，transition_goal 比 opening_scene 更能表达“接下来
    # 如何自然推进”；summary 仍只作叙事方向，opening_scene 仅作为最后兼容回退。
    for key in ("narrative_focus", "transition_goal", "narrative_summary", "summary", "opening_scene"):
        value = str(beat.get(key) or "").strip()
        if value:
            return value
    return ""


def scene_narrative_summary(beat: Mapping[str, Any]) -> str:
    """Project the ordinary Actor's current situation without turning the scene plan into a checklist. opening_scene supplies observable opening facts and story_so_far supplies subsequent progress. Fall back to summary only when the package has no opening field, preserving author facts without rescanning and executing the entire scene plan each turn."""

    for key in ("narrative_summary", "opening_scene", "summary"):
        value = str(beat.get(key) or "").strip()
        if value:
            return value
    return ""


def pending_transition_record(
    session: Any,
    *,
    ledger_events: tuple[Mapping[str, Any], ...] = (),
    include_withdrawn: bool = False,
) -> Mapping[str, Any] | None:
    """Locate the original offer using this visit's latched record and Ledger attitude boundary, without treating follow-up questions as new invitations."""

    if not bool(getattr(session, "transition_offered", False)) and not include_withdrawn:
        return None
    # performance 的 true 表示“仍有待确认提议”，不是“本轮提出新提议”。
    # 拒绝后同轮又提出新邀请时，两轮记录都为 true，必须用已有 Ledger 区分来源。
    events_by_revision = {event.get("result_revision"): event for event in ledger_events}
    records, _ = current_scene_records(session)
    origin = None
    for record in records:
        # 已确认错误邀请是撤下边界，不等于玩家暂缓后仍可重新接受的合法邀请。
        # 同轮若公开了更正后的邀请，其原文从这一条开始；不能再回溯到错误旧话。
        if record.get("transition_offer_invalidated") is True:
            if record.get("transition_offered") is True:
                origin = record
            break
        # 本轮正文重新公开了经复核的有效邀请时，回复对象刷新到这一轮；后续仅保留状态的闲聊不会刷新。
        if record.get("transition_offer_presented") is True:
            origin = record
            break
        if record.get("transition_offered") is not True:
            # 重新考虑时只跳过当前访问中撤下邀请后的记录；找到最近一段 true 后仍定位其原文。
            # 不跨越 current_scene_records 已截断的入幕边界，也不把闲聊当成新邀请。
            if include_withdrawn and origin is None:
                continue
            break
        origin = record
        event = events_by_revision.get(record.get("revision"), {})
        if event.get("transition_intent") in {"accept", "initiate", "reject"}:
            break
    return origin


def pending_transition_performance(
    session: Any,
    *,
    max_tokens: int = 180,
    ledger_events: tuple[Mapping[str, Any], ...] = (),
    include_withdrawn: bool = False,
) -> str:
    """Return the original visible pending offer for the Actor and evaluator. Projecting this already displayed fact separately improves its visibility in long histories without introducing independent state or guessing intent from keywords."""

    record = pending_transition_record(session, ledger_events=ledger_events, include_withdrawn=include_withdrawn)
    if record is None:
        return ""
    # 邀请可能只写在旁白中；保留混合正文原文及括号，旧记录才走内容块兼容读取。
    if "performance" in record or "scene_narration" in record:
        performance = "\n".join(str(record.get(key) or "").strip()
            for key in ("scene_narration", "performance") if str(record.get(key) or "").strip())
    else:
        performance = " ".join(block["text"] for block in performance_content_blocks(record))
    return truncate_to_tokens(performance, max_tokens=max_tokens)


_CONTRACT_PROP_PATTERN = re.compile(r"关键道具\s*[“\"]([^”\"]+)[”\"]|\[([a-z][a-z0-9_]{2,})\]")


CONTRACT_NON_FACT_MARKERS = ("替玩家", "需要玩家回答", "篇幅", "节奏", "重复确认", "配角任务")


def project_contract_boundaries(
    beat: Mapping[str, Any],
    *,
    include_opening_only: bool = False,
    fact_only: bool = False,
    max_items: int | None = None,
    max_tokens: int | None = None,
) -> tuple[str, ...]:
    """按统一顺序投影作者边界，避免 Actor 与复核器各自拼接出不同合同。"""  # noqa: DOCSTRING_CJK

    if not isinstance(beat, Mapping):
        return ()
    if max_items is not None and int(max_items) <= 0:
        return ()
    character_state = beat.get("character_state")
    acting_contract = beat.get("acting_contract")
    opening_boundaries = (
        list(beat.get("opening_only_boundaries") or [])
        if include_opening_only
        else []
    )
    scene_boundaries = (
        list(character_state.get("scene_boundaries") or [])
        if isinstance(character_state, Mapping)
        else []
    )
    forbidden_behaviors = (
        list(acting_contract.get("forbidden_behaviors") or [])
        if isinstance(acting_contract, Mapping)
        else []
    )
    must_not_happen = list(beat.get("must_not_happen") or [])
    # 窄合同复核沿用原有 must_not_happen 优先顺序；Actor/Evaluator 的公开边界则共享场景优先顺序。
    candidates = [
        *(must_not_happen if fact_only else opening_boundaries),
        *(scene_boundaries if fact_only else []),
        *(opening_boundaries if fact_only else []),
        *(scene_boundaries if not fact_only else []),
        *(forbidden_behaviors if not fact_only else []),
        *(must_not_happen if not fact_only else []),
    ]
    boundaries: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        text = str(item or "").strip()
        if fact_only and any(marker in text for marker in CONTRACT_NON_FACT_MARKERS):
            continue
        if max_tokens is not None:
            text = truncate_to_tokens(text, max_tokens=max_tokens).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        boundaries.append(text)
        if max_items is not None and len(boundaries) >= max(0, int(max_items)):
            break
    return tuple(boundaries)


def contract_boundary_items(node: Mapping[str, Any]) -> tuple[str, ...]:
    """该幕作者写明的**世界事实**类禁令：must_not_happen 与 character_state.scene_boundaries。

    只取作者已经写死的短句，用于窄判定核对，不解释自然语言条件。玩家授权与写作风格类要求
    （提到玩家、篇幅、节奏、重复确认、配角任务）不进入窄判定：它们要么由复核模块的授权核对负责，
    要么按单轮可见文本无法核对，混进来只会把正常邀请与角色自主动作判成越界（问题2.143的run-D反例）。
    """  # noqa: DOCSTRING_CJK

    beat = node.get("story_beat") if isinstance(node, Mapping) else None
    return project_contract_boundaries(beat, fact_only=True)


def contract_required_names(node: Mapping[str, Any], target_node_id: str) -> tuple[str, ...]:
    """换场合同里作者声明本轮必须交付的关键道具名称。

    `must_preserve` 只约束状态不能互相矛盾，不要求每次换幕逐项复述；这里只读取
    `must_deliver` 中作者明确要求本轮可见交付的结构化道具声明。
    """  # noqa: DOCSTRING_CJK

    names: list[str] = []
    for route in node.get("route_gates") or []:
        if not isinstance(route, Mapping) or str(route.get("target_node_id") or "") != str(target_node_id):
            continue
        contract = route.get("transition_contract")
        if not isinstance(contract, Mapping):
            continue
        values = contract.get("must_deliver")
        if not isinstance(values, (list, tuple)):
            continue
        for item in values:
            # 中文道具名才是可见正文里可能出现的东西；内部编号只是兜底。
            matches = list(_CONTRACT_PROP_PATTERN.finditer(str(item or "")))
            quoted = [m.group(1).strip() for m in matches if m.group(1) and m.group(1).strip()]
            fallback = [m.group(2).strip() for m in matches if m.group(2) and m.group(2).strip()]
            for name in (quoted or fallback):
                if name not in names:
                    names.append(name)
    return tuple(names)


def _visible_contract_delivery_text(performance: Mapping[str, Any]) -> str:
    """汇总玩家真正能看到的三段正文与旁白，供显式交付项做逐字核对。"""  # noqa: DOCSTRING_CJK

    parts: list[str] = []
    for field in ("performance", "scene_narration"):
        value = performance.get(field)
        if isinstance(value, str):
            parts.append(value)
    segments = performance.get("segments")
    if isinstance(segments, (list, tuple)):
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            for field in ("performance", "scene_narration"):
                value = segment.get(field)
                if isinstance(value, str):
                    parts.append(value)
    return "\n".join(parts)


def missing_contract_names(
    node: Mapping[str, Any],
    target_node_id: str,
    performance: Mapping[str, Any],
    session: Any,
) -> tuple[str, ...]:
    """作者要求本轮交付、但候选与已提交历史里都没有出现过的道具名。

    纯程序核对：只要道具中文名在候选可见文本或已提交原文里出现过就算交付。
    """  # noqa: DOCSTRING_CJK

    required = contract_required_names(node, target_node_id)
    if not required:
        return ()
    haystack = "\n".join((
        _visible_contract_delivery_text(performance),
        *(str(row.get("text") or "") for row in performance_history_records(session)),
    ))
    return tuple(name for name in required if name not in haystack)


def transition_bridge_leak_markers(
    *,
    target_opening: str,
    bridge_text: str,
    authored_bridge: str = "",
) -> tuple[str, ...]:
    """Return target-opening clauses copied into a transition bridge.

    This is intentionally a narrow provenance check, not a semantic duplicate detector. It only
    reports exact normalized clauses or time markers that belong to the target opening and are not
    explicitly present in the authored bridge contract. A paraphrase still needs model review or
    a future structured fact projection.
    """  # noqa: DOCSTRING_CJK

    target = str(target_opening or "").strip()
    bridge = str(bridge_text or "").strip()
    if not target or not bridge:
        return ()

    def normalize(value: str) -> str:
        return re.sub(r"[\s，,。！？!?；;：:、‘’“”\"'（）()【】\[\]]+", "", value)

    def units(value: str) -> tuple[str, ...]:
        result: list[str] = []
        for raw in re.split(r"[\n。！？!?；;,，]+", value):
            item = normalize(raw)
            # Short generic fragments such as “她看向窗外” are too common to prove a leak.
            if len(item) < 8 or item in result:
                continue
            result.append(item)
        return tuple(result)

    bridge_text = normalize(bridge)
    authored_text = normalize(str(authored_bridge or ""))
    markers: list[str] = []
    for unit in units(target):
        if unit in bridge_text and unit not in authored_text:
            markers.append(unit)

    # Time markers are useful even when punctuation or the surrounding clause was rewritten.
    authored_times = _time_markers(authored_bridge)
    for marker in sorted(_time_markers(target) & _time_markers(bridge)):
        if marker not in authored_times and marker not in markers:
            markers.append(marker)
    return tuple(markers)


_TIME_MARKER_PATTERN = re.compile(r"\d{1,2}\s*[:：]\s*\d{2}|\d{1,2}\s*月\s*\d{1,2}\s*[日号]")


def _time_markers(text: Any) -> set[str]:
    """Normalize clock and calendar markers so equal times compare equal across prose variants."""

    return {
        re.sub(r"\s+", "", match.group(0)).replace("：", ":")
        for match in _TIME_MARKER_PATTERN.finditer(str(text or ""))
    }


def _beat_source_text(beat: Mapping[str, Any]) -> str:
    """Collect the text an author already exposes for the node the player is currently in."""

    parts: list[str] = []
    for key in ("opening_scene", "opening_situation", "summary", "narrative_focus", "catgirl_situation"):
        value = beat.get(key)
        if isinstance(value, str):
            parts.append(value)
    state = beat.get("character_state")
    if isinstance(state, Mapping):
        for value in state.values():
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, (list, tuple)):
                parts.extend(str(item) for item in value if isinstance(item, str))
    return "\n".join(parts)


def _visible_narration_text(performance: Mapping[str, Any]) -> str:
    """Only narration asserts present facts; dialogue and buttons may legitimately propose an exit."""

    parts: list[str] = []
    if isinstance(performance.get("scene_narration"), str):
        parts.append(performance["scene_narration"])
    segments = performance.get("segments")
    if isinstance(segments, (list, tuple)):
        for segment in segments:
            if isinstance(segment, Mapping) and isinstance(segment.get("scene_narration"), str):
                parts.append(segment["scene_narration"])
    return "\n".join(parts)


def premature_target_markers(
    engine: Any,
    session: Any,
    outcome: Any,
    performance: Mapping[str, Any],
    player_input: str = "",
) -> tuple[str, ...]:
    """Return time markers an ordinary turn narrated although only the next scene owns them.

    The bridge text is projected as a not-yet-happened movement range so the actor can propose a
    concrete exit; it must not be played back as present narration (issue 2.141 B3). Returns an
    empty tuple for transition turns, unknown exits, or markers the current scene already owns.
    """  # noqa: DOCSTRING_CJK

    ledger = getattr(outcome, "ledger_event", None) or {}
    source_id = str(ledger.get("from_node_id") or getattr(session, "current_node_id", "") or "")
    if not source_id or str(ledger.get("to_node_id") or source_id) != source_id:
        # 正式转场本来就要交付目标幕开场，不适用本检查。
        return ()
    nodes = getattr(engine, "nodes", {}) or {}
    source = nodes.get(source_id)
    if not isinstance(source, Mapping) or not source.get("route_gates"):
        return ()
    route = engine.preview_route(source_id, getattr(session, "metrics", {}) or {})
    if not isinstance(route, Mapping):
        return ()
    target = nodes.get(str(route.get("target_node_id") or ""))
    if not isinstance(target, Mapping):
        return ()
    contract = route.get("transition_contract")
    contract = contract if isinstance(contract, Mapping) else {}
    target_text = "\n".join((
        scene_opening_text(target.get("story_beat") or {}),
        str(contract.get("bridge_scene_narration") or ""),
        str(contract.get("reason") or ""),
    ))
    allowed_text = "\n".join((
        _beat_source_text(source.get("story_beat") or {}),
        str(player_input or ""),
        *(str(row.get("text") or "") for row in performance_history_records(session)),
    ))
    narrated = _time_markers(_visible_narration_text(performance))
    owned_by_target = _time_markers(target_text) - _time_markers(allowed_text)
    return tuple(sorted(narrated & owned_by_target))


def premature_target_scene_facts(
    engine: Any,
    session: Any,
    outcome: Any,
    performance: Mapping[str, Any],
    player_input: str = "",
) -> tuple[str, ...]:
    """返回目标幕尚未进入前被旁白写出的逐字开场片段。

    这里只核对目标幕开场中的较长、可定位原文片段；玩家输入、来源幕作者事实和已提交
    演绎会作为已知文本排除。目标幕已经有 Runtime 入幕事实时允许再次提及，避免把重返
    场景误判为首次抵达。改写或同义复述仍交给模型复核，不在这里猜测语义。
    """  # noqa: DOCSTRING_CJK

    ledger = getattr(outcome, "ledger_event", None) or {}
    source_id = str(ledger.get("from_node_id") or getattr(session, "current_node_id", "") or "")
    if not source_id or str(ledger.get("to_node_id") or source_id) != source_id:
        # 正式转场本来就要交付目标幕开场，不适用本检查。
        return ()
    nodes = getattr(engine, "nodes", {}) or {}
    source = nodes.get(source_id)
    if not isinstance(source, Mapping) or not source.get("route_gates"):
        return ()
    route = engine.preview_route(source_id, getattr(session, "metrics", {}) or {})
    if not isinstance(route, Mapping):
        return ()
    target_id = str(route.get("target_node_id") or "")
    target = nodes.get(target_id)
    if not target_id or not isinstance(target, Mapping):
        return ()

    # 事实投影只证明已经发生过入幕；已有入幕记录时，当前检查不再把重返开场当作提前。
    scene_facts = project_scene_facts(session)
    target_entry_prefix = f"event:scene.entered:{target_id}:"
    if any(str(row.get("key") or "").startswith(target_entry_prefix)
           for row in scene_facts.get("facts", ())):
        return ()

    def normalize(value: str) -> str:
        return re.sub(r"[\s，,。！？!?；;：:、‘’“”\"'（）()【】\[\]]+", "", value)

    def units(value: str) -> tuple[str, ...]:
        result: list[str] = []
        for raw in re.split(r"[\n。！？!?；;,，]+", value):
            item = normalize(raw)
            # 过短的通用片段缺乏归属证据；时钟/日期由 premature_target_markers 单独核对。
            if len(item) < 8 or item in result:
                continue
            result.append(item)
        return tuple(result)

    target_units = units(scene_opening_text(target.get("story_beat") or {}))
    if not target_units:
        return ()
    allowed_text = "\n".join((
        _beat_source_text(source.get("story_beat") or {}),
        str(player_input or ""),
        *(str(row.get("text") or "") for row in performance_history_records(session)),
    ))
    allowed = normalize(allowed_text)
    narrated = normalize(_visible_narration_text(performance))
    return tuple(unit for unit in target_units if unit in narrated and unit not in allowed)


__all__ = [
    "contract_boundary_items",
    "project_contract_boundaries",
    "contract_required_names",
    "missing_contract_names",
    "transition_bridge_leak_markers",
    "HISTORY_EVIDENCE_RULE",
    "history_evidence",
    "PLAYER_ACTION_LANGUAGE_RULE",
    "premature_target_markers",
    "premature_target_scene_facts",
    "scene_opening_text",
    "current_scene_records",
    "project_scene_facts",
    "scene_facts_prompt_text",
    "pending_transition_performance",
    "pending_transition_record",
    "scene_narrative_focus",
    "scene_narrative_summary",
]
