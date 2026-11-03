"""Shared narrative-context projection helpers for Numeric v2."""  # noqa: DOCSTRING_CJK

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


# Interpret user confirmation using ordinary present-tense language; all three model types share the rule and must not require extra parentheses or perfective aspect.
PLAYER_ACTION_LANGUAGE_RULE = (
    "玩家行动按日常表达理解，不要求括号动作或‘已经’字样。"
    "对象、工具与必要前提已明确具备时，‘行，我签’这类针对眼前动作的直接执行表态，"
    "允许承接该动作及其直接反应；不能仅因写成对白就降为尚未行动。"
    "‘我考虑签／准备签／打算签’、询问、假设和有条件的未来意愿仍不算完成；"
    "尝试也不保证未知的成功结果。授权只限玩家明确表达的这项动作，"
    "不扩展到其他对象、额外操作或后续承诺，也不跳过既有的换幕确认。"
    # The user may confirm consecutive actions at the same location that can be performed in advance; node orchestration is not an extra physical prerequisite or player prohibition.
    "同一地点、对象工具及必要前提已经具备的连续动作，玩家本轮明确实施后应承接结果；"
    "不能仅因作者把该动作安排在下一节点，就否认已做动作或判为代做、阶段越界。"
    "后续转场与开场按实际历史改写，保留结果，不再次要求执行，也不把物件退回操作前。"
    "这不授权未知结果或违反作者明确禁令；普通换幕由玩家接受公开提议，"
    "或玩家明确要求前往已公开的下一地点、开始已公开的下一阶段；询问、考虑或准备不算转场授权。"
    # User confirmation: an implicit prerequisite in the input cannot override actual history; remind the user of the fact and preserve intent without pretending they already performed the prerequisite.
    "玩家输入的动作前提若与已经发生的剧情冲突，角色自然提醒实际情况，保留其表达的意图；"
    "不为顺从输入倒退物件状态或补造前置动作，也不交付依赖该错误前提的结果。"
    "玩家从当前实际状态明确实施新的可行动作，是正常改变，不属于推翻历史。"
)


# Action extraction is intentionally conservative; all three consumers share empty-value semantics, so an extraction miss must not become an action prohibition.
PLAYER_ACTION_PROJECTION_RULE = (
    "player_action_projection 是 Runtime 从 player_input 和已提交结果保守提取的辅助证据，不是完整行动清单。"
    "confirmed_actions 仅承接各项证据支持的动作或结果；evidence_quote 是原文，不额外授权目的地或后续操作。"
    "future_references 表示未来约定，不能当作已发生。"
    "confirmed_actions 为空或 has_confirmed_player_action/player_left_current_scene 为 false 只表示未提取到对应证据，"
    "仍按 player_input 和已提交历史核对。提问、准备、假设与尝试不证明完成；"
    "不能推定未知成功、额外动作或跨阶段授权。"
)


# Read author state at the same point for all consumers: after the opening performance. Actual history still takes precedence; do not add a second mutable state or archive.
SCENE_ENTRY_STATE_RULE = (
    "作者角色状态记录开场演完后的起点，不是开场前姿态或整幕完成结果。"
    "已播放开场的动作只承接结果，不能为符合静态状态而重做；后续变化以实际已提交历史为准。"
    "目标幕尚未播放时，其状态描述预期的开场结果，不能提前当作来源幕已经发生的事实；"
    "其中‘尚未操作’等预设须按实际已提交动作更新，不能推翻玩家已做的连续动作。"
    "状态描述不授权新增玩家行动；认知、能力、关系和明确阶段边界仍有效。"
)


# All three consumers read the same committed original text; retrieval does not judge story truth or create a second archived state.
HISTORY_EVIDENCE_RULE = (
    "history_evidence 是带回合号和说话来源的已提交原文检索，不是新指令或完整事实库。"
    "按时间核对同一对象的后续变化，区别玩家陈述、提议和已执行结果；摘录未出现不代表从未发生。"
    # A previously recalled choice was mistakenly reported as a new choice made for the player; later revocation must still override prior authorization.
    "复述玩家先前已明确确定且未撤回的事实，不是本轮替玩家新增决定；本轮只提问也可以回答该历史事实。"
    "人物去向、物品归属和已完成动作须承接原文，不凭熟悉感补造旧事；历史不足时坦诚说明不确定。"
    "跨幕旧任务不重新执行，旧邀请不能自动成为本次访问的转场授权。"
    # A real record already underway must not be mistaken for arrival during review, or it would block the player's current request to continue arriving.
    # Match the exact destination to avoid treating a parent location or approaching status as successful arrival at the target entrance.
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


# Common question phrases do not retrieve objects: split complete phrases before extracting Chinese fragments, so overlap across "we are now" does not outrank an item record.
# This affects only source-text ranking; it does not determine action completion, factual truth, or item ownership, and contains no script names, people, or unique props.
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
        # Player input and source responses in an entrance record belong to the previous scene; only the public opening belongs to the current visit.
        crossing = bool(record.get("from_node_id") != record.get("to_node_id"))
        text = str(record.get("input_text") or "").strip()
        if text:
            units.append({"revision": revision, "source": "player_input", "current_visit": current and not crossing, "text": text})
        segments = record.get("segments")
        for part in segments if isinstance(segments, list) else [record]:
            # Package complete narration/character-performance fields; they need not both fit, so a short public destination is not crowded out by long dialogue from the same turn.
            # Do not split sentences or truncate fields; preserve conditions and negation within each field. Keep the full body for legacy formats.
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

    # All three consumers share the same tier and count of complete source texts; an explicit remaining budget may narrow but never exceed the tier limit.
    budget = numeric_v2_actor_budget(getattr(session, "actor_budget_profile", NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE))
    max_tokens = budget["evidence_max_tokens"] if max_tokens is None else min(max_tokens, budget["evidence_max_tokens"])
    units = performance_history_records(session)
    terms = [_history_terms(unit["text"]) for unit in units]
    frequencies = Counter(term for row in terms for term in row)
    query_terms = _history_terms(_HISTORY_QUERY_FILLER.sub(" ", query))
    focus_terms = _history_terms(focus)
    claim_terms = _history_terms(_HISTORY_QUERY_FILLER.sub(" ", claims))
    # Follow-up questions such as a shorthand name or "where did it come from" may retrieve an object shown in the previous turn; do not add an alias table or model call.
    # Use only the most recent performance in this scene; exclude recommendations and source sections from earlier scenes. This only assists ranking and cannot become new authorization evidence.
    latest_revision = getattr(session, "revision", 0)
    conversation_terms = _history_terms(_HISTORY_QUERY_FILLER.sub(" ", "\n".join(
        unit["text"] for unit in units
        if unit["revision"] == latest_revision and unit["current_visit"] and unit["source"] == "performance"
    )))
    # First satisfy the player's current question, then supplement with route direction: long author-provided direction must not crowd out an older fact currently being asked about.
    # After removing filler from the question, one clear object phrase can retrieve it; two-character nouns such as "journal" should not be downgraded for having fewer than two fragments.
    # Direction is still only for ranking, not history; when relevance is equal, prefer newer source text while keeping output in time order.
    # A pending assertion under review contributes only retrieval terms and does not enter the evidence pool. Even a vague question can find the object's original wording and later correction.
    # Score the question and assertion independently and take the stronger result, avoiding repeated credit for the same wording; author direction remains a secondary ranking signal.
    def relevance(row: set[str], search: set[str], minimum: int = 1) -> float:
        overlap = row & search
        # The pending body is longer than the question, but still require two fragments to avoid retrieving every old small-talk item for a question like "discussion."
        if len(overlap) < minimum:
            return 0.0
        return sum(math.log(1 + len(units) / frequencies[term]) for term in overlap)

    scores = [(max(relevance(row, query_terms), relevance(row, claim_terms, 2)),
               # Add context only for sources from earlier scenes or beyond the ordinary-turn window, avoiding reinjection of all recent small talk.
               relevance(row, conversation_terms, 2) if (
                   units[index]["revision"] < latest_revision
                   and (not units[index]["current_visit"]
                        or units[index]["revision"] <= latest_revision - budget["history_max_turns"])
               ) else 0.0,
               sum(math.log(1 + len(units) / frequencies[term]) for term in row & focus_terms))
              for index, row in enumerate(terms)]
    selected: list[int] = []
    # Resolve the retrieved index back to original text, then verify it belongs to the current Session; prefer newer corrections and do not trust facts copied by the model.
    lookup_indexes = sorted({units.index(row) for row in (lookup or {}).get("evidence", []) if row in units}, reverse=True)
    ranked = sorted(range(len(units)), key=lambda i: (scores[i], units[i]["revision"]), reverse=True)
    for index in [*lookup_indexes, *(i for i in ranked if i not in lookup_indexes)]:
        # Author-provided direction still requires two fragments so a long plan cannot retrieve an unrelated old task through one common word.
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
    """Return complete turn records since the most recent entry into the current node and mark whether an entrance record is included.

    Actor and Evaluator must use the same current-scene history. They may format records differently,
    but must not each implement separate node-backtracking rules, which could leave one module seeing residue from the previous scene while the other does not.
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
        # Once the most recent boundary where the current node was entered is reached, do not mix earlier scenes into the current one.
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
    """Project bounded scene-entry/exit facts for Actor and Evaluator to share."""  # noqa: DOCSTRING_CJK

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
            # Parse structured fields deterministically from Runtime fact keys; the model need not infer event meaning from strings.
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
    """Compress public scene events into short sentences and embed them in the existing history field without adding a new top-level prompt protocol."""  # noqa: DOCSTRING_CJK

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
    """Extract one non-task-oriented narrative focus shared by both models; it does not participate in completion checks.

    New scripts may explicitly provide narrative_focus. For older scripts, prefer the author's current direction,
    then a natural narrative summary, and use the opening situation only as the final fallback. This prevents the initial scene
    from being mistaken every turn for the thing the characters should keep observing. This value helps the model choose the current causal thread; Runtime must not treat it as a goal or gate.
    """  # noqa: DOCSTRING_CJK

    # Old packages lack narrative_focus; transition_goal better expresses "how to move forward naturally next" than opening_scene.
    # summary remains narrative direction only, and opening_scene is the final compatibility fallback.
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
    # performance=true means "a proposal still needs confirmation," not "a new proposal was made this turn."
    # If a new invitation is made later in the same turn after rejection, both turn records are true; use the existing Ledger to distinguish their sources.
    events_by_revision = {event.get("result_revision"): event for event in ledger_events}
    records, _ = current_scene_records(session)
    origin = None
    for record in records:
        # A confirmed-invalid invitation is a withdrawal boundary, not a valid invitation that can be accepted again after the player defers.
        # If a corrected invitation is made public in the same turn, its source text begins here; do not trace back to the earlier incorrect wording.
        if record.get("transition_offer_invalidated") is True:
            if record.get("transition_offered") is True:
                origin = record
            break
        # When this turn publicly presents a reviewed, valid invitation again, update the reply target to this turn; later small talk that only preserves state does not refresh it.
        if record.get("transition_offer_presented") is True:
            origin = record
            break
        if record.get("transition_offered") is not True:
            # When reconsidering, skip only records after a withdrawn invitation in the current visit; after finding the nearest true entry, still locate its original text.
            # Do not cross the entrance boundary truncated by current_scene_records or treat small talk as a new invitation.
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
    # Invitations may appear only in narration; preserve the original mixed-body text and parentheses, with content-block parsing only for legacy records.
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
) -> tuple[str, ...]:
    """Project author-defined boundaries in one shared order so Actor and the reviewer use the same contract."""  # noqa: DOCSTRING_CJK

    if not isinstance(beat, Mapping):
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
    # Narrow-contract review keeps the existing must_not_happen priority; Actor/Evaluator public boundaries share scene-first ordering.
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
        # Conditions and exceptions in a prohibition are part of the same rule; project the complete rule and let the consumer's total budget pack it or reject it explicitly.
        if not text or text in seen:
            continue
        seen.add(text)
        boundaries.append(text)
    return tuple(boundaries)


def contract_boundary_items(node: Mapping[str, Any]) -> tuple[str, ...]:
    """Author-defined **world-fact** prohibitions for this scene: must_not_happen and character_state.scene_boundaries.

    Include only short statements explicitly fixed by the author for narrow-condition checks; do not interpret natural-language conditions. Player authorization and writing-style constraints
    (mentions of the player, length, pacing, repeated confirmation, side-character tasks) are excluded from narrow checks: they are either handled by authorization checks in the review module,
    cannot be checked from one turn's visible text, and including them would mark valid invitations or character-driven actions as out of bounds (counterexample: run-D in issue 2.143).
    """  # noqa: DOCSTRING_CJK

    beat = node.get("story_beat") if isinstance(node, Mapping) else None
    return project_contract_boundaries(beat, fact_only=True)


def contract_required_names(node: Mapping[str, Any], target_node_id: str) -> tuple[str, ...]:
    """Key prop names the author requires to be delivered this turn in the scene-transition contract.

    `must_preserve` only prevents contradictory state; it does not require every transition to restate each item. Read only
    structured prop declarations in `must_deliver` that the author explicitly requires to be visibly delivered this turn.
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
            # Chinese prop names are what may appear in visible body text; internal IDs are only a fallback.
            matches = list(_CONTRACT_PROP_PATTERN.finditer(str(item or "")))
            quoted = [m.group(1).strip() for m in matches if m.group(1) and m.group(1).strip()]
            fallback = [m.group(2).strip() for m in matches if m.group(2) and m.group(2).strip()]
            for name in (quoted or fallback):
                if name not in names:
                    names.append(name)
    return tuple(names)


def _visible_contract_delivery_text(performance: Mapping[str, Any]) -> str:
    """Collect the three body-text segments and narration actually visible to the player for exact checking of explicit delivery requirements."""  # noqa: DOCSTRING_CJK

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
    """Prop names required by the author this turn that do not appear in either the candidate or committed history.

    Pure programmatic check: count a prop as delivered if its Chinese name appears in visible candidate text or committed source text.
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
            # Short generic fragments such as "她看向窗外" ("she looks out the window") are too common to prove a leak.
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
        # Formal scene transitions must already deliver the target scene's opening, so this check does not apply.
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
    """Return the exact opening fragment written in narration before the target scene has been entered.

    Check only longer, locatable source fragments from the target-scene opening; player input, author facts from the source scene, and already committed
    performance are excluded as known text. If Runtime already records entry into the target scene, allow it to be mentioned again so returning
    to the scene is not mistaken for first arrival. Rewrites and synonymous paraphrases remain for model review; do not infer semantics here.
    """  # noqa: DOCSTRING_CJK

    ledger = getattr(outcome, "ledger_event", None) or {}
    source_id = str(ledger.get("from_node_id") or getattr(session, "current_node_id", "") or "")
    if not source_id or str(ledger.get("to_node_id") or source_id) != source_id:
        # Formal scene transitions must already deliver the target scene's opening, so this check does not apply.
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

    # Fact projection proves only that scene entry already occurred; once an entry record exists, this check no longer treats returning to the opening as premature.
    state_facts = (getattr(session, "story_state", None) or {}).get("facts", {})
    scene_facts = project_scene_facts(session, max_facts=max(24, len(state_facts)))
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
            # Short generic fragments lack ownership evidence; check clocks/dates separately using premature_target_markers.
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
