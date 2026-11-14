"""Numeric v2 player-action outcome projection.

Retain only short states directly provable from the player's words or Runtime's committed outcomes. This is not
a natural-language interpreter and does not infer destinations, successful outcomes, emotions, or implicit intent.
"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


PLAYER_ACTION_PROJECTION_SCHEMA = "neko.script.player_action_projection.numeric.v1"
_MAX_EVIDENCE_CHARS = 180
_MAX_ROWS = 8

# These action words span genres and denote no particular script object. Generate a player-action line only
# when an explicit completion/execution structure is present without future or question markers.
_ACTION_TERMS = (
    "签", "拿", "交", "放", "打开", "关上", "关闭", "填写", "报名", "参加",
    "接受", "拒绝", "选择", "开始", "继续", "离开", "回去", "回家", "前往",
    "进入", "出来", "走", "复习", "学习", "留下", "等待", "等", "检查", "核对",
    "整理", "收拾", "确认", "按下", "接过", "还给", "递给", "端起", "读", "看",
)
_DEPARTURE_TERMS = (
    "先回去", "先回家", "先走", "走了", "离开这里", "离开", "回家",
    "走出门", "走出去", "推门离开", "转身离开", "起身离开", "那我走", "我们走",
)
_EXIT_APPROACH_TERMS = ("走向门口", "走向巷口")
_NONPLAYER_SUBJECTS = ("你们", "他们", "她们", "它们", "您", "你", "他", "她", "它")
_QUESTION_MARKERS = ("？", "?", "吗", "能不能", "可以吗", "要不要", "是否", "有没有")
_UNCOMMITTED_MARKERS = (
    "想", "希望", "准备", "打算", "考虑", "计划", "可能", "也许", "如果", "尝试", "试试",
    "以后再", "有空再",
)
_FUTURE_MARKERS = (
    "明天", "后天", "周一", "周二", "周三", "周四", "周五", "周六", "周日", "周天",
    "下周", "周末", "之后", "改天", "回头", "再来", "再见", "见面",
)
_SINGLE_ACTION_SUFFIXES = {
    "签": "了字名署",
    "拿": "起出到给走来着过",
    "交": "给上出付",
    "放": "下在到进回",
    "走": "了吧向到出进开去着在",
    "看": "完到见着书一下过了",
    "等": "待你一下到着",
    "读": "完着书到了",
}
_HANDOFF_GIVE = ("递给", "递出", "递上", "递向", "递过", "递到", "递交", "交给", "交出", "交到", "交还", "移交", "送还")
_HANDOFF_RECEIVE = ("接过", "接稳", "接住", "接下", "接到", "收下", "收到")
_HANDOFF_NON_ACTION = (
    "建议", "不妨", "可以", "稍后", "等会", "拒绝", "将要", "还未", "明日", "翌日", "改日",
    "再把", "再将", "我让", "我叫", "我请", "好不好",
)
_HANDOFF_CONDITIONAL = ("如果", "假如", "倘若", "只要", "等到", "等你", "等他", "等她", "等对方", "待你", "待他", "待她")
_HANDOFF_OBSERVING = re.compile(r"(?:看着|看见|看到|望着|目送|瞧见|见到)")
_HANDOFF_ADVERBS = r"(?:平稳|稳稳|慢慢|小心|轻轻|缓缓|双手|恭敬地)"
_HANDOFF_ADVERB = re.compile(_HANDOFF_ADVERBS + r"+$")
_HANDOFF_HOLDING = re.compile(r"^(?:我)?(?:双手|单手|伸手)?(?:托稳|托住|托起|捧起|捧稳|拿起|拿稳|握住)")
_HANDOFF_OMITTED_OBJECT = re.compile(
    _HANDOFF_ADVERBS + r"*(?:递向[^，,：:。！？]+|(?:递给|交给)(?:对方|你|他|她)|(?:递出|递上|交出)(?:去|了)?)"
)
_HANDOFF_SELF = re.compile(r"^(?:我们|我|咱们|那我)(?!们|的)")
_HANDOFF_PLAYER_IN_PROSE = re.compile(r"^(?:你们|你|您)(?!们|的|手中|手里|怀中|怀里|身上)")


# An immediately preceding negation, optionally with one or two modal characters (e.g. will not, has not again, never),
# means the action/departure is not completed; "不得不" (have to) is a double negative and still counts as execution.
_NEGATION_BEFORE_TERM = re.compile(
    r"(?<!不得)[不没别未勿莫甭][有会要再能想用肯敢得准许必可]{0,2}$"
)


def _negated_at(text: str, start: int) -> bool:
    return bool(_NEGATION_BEFORE_TERM.search(text[:start]))


def _term_starts(text: str, term: str) -> list[int]:
    starts: list[int] = []
    start = text.find(term)
    while start >= 0:
        starts.append(start)
        start = text.find(term, start + 1)
    return starts


def _evidence(value: Any) -> str:
    return " ".join(str(value or "").strip().split())[:_MAX_EVIDENCE_CHARS]


def _is_question(text: str) -> bool:
    return any(marker in text for marker in _QUESTION_MARKERS)


def _has_future_or_uncommitted_marker(text: str) -> bool:
    return any(marker in text for marker in _UNCOMMITTED_MARKERS)


def _explicit_subject(text: str, *, allow_omitted: bool = False) -> bool:
    # Expressions such as going back to study or let's go directly omit the subject; "带我去" (take me there) asks
    # the neko to act and must not count as the player having moved.
    subjects = "我|我们|咱们|那我" + ("|先|这就" if allow_omitted else "")
    return bool(re.search(rf"(?:^|[，,。；;：:\s])(?:{subjects})(?!的)", text))


def _has_unambiguous_nonplayer_subject(text: str, start: int) -> bool:
    # 按当前动作自己的词位读取分句，不能借同一括号中别的“我做了……”认领他人动作。
    prefix = re.split(r"[，,。；;：:\n（）()]+", text[:start])[-1].lstrip()
    subject = next((word for word in _NONPLAYER_SUBJECTS if prefix.startswith(word)), "")
    if not subject:
        return False
    remainder = prefix[len(subject):]
    # “你的包我提着离开”是物主结构；含后续“我”的嵌套句也保留原规则，避免误伤
    # “她看着我离开”。这里不尝试判断宾语、任意人名或逗号后省略的主语。
    if remainder.startswith("的"):
        return False
    return re.search(r"(?:我们|咱们|我)(?!们|的)", remainder) is None


def _action_term(text: str) -> str:
    for term in sorted(_ACTION_TERMS, key=len, reverse=True):
        for start in _term_starts(text, term):
            if _negated_at(text, start) or _has_unambiguous_nonplayer_subject(text, start):
                continue
            if len(term) > 1:
                return term
            suffixes = _SINGLE_ACTION_SUFFIXES.get(term)
            if suffixes is None:
                return term
            end = start + len(term)
            if end == len(text) or text[end] in suffixes:
                return term
    return ""


def _clauses(text: str) -> list[str]:
    """Retain action evidence by natural clauses in player input, without treating later agreements as negating an earlier clause."""  # noqa: DOCSTRING_CJK

    parenthetical = re.findall(r"（[^）]*）|\([^)]*\)", text)
    remainder = re.sub(r"（[^）]*）|\([^)]*\)", "", text)
    return parenthetical + [
        clause.strip()
        for clause in re.split(r"[，,。；;：:\n]+", remainder)
        if clause.strip()
    ]


def _handoff_clauses(text: str, *, allow_omitted_subject: bool = False) -> list[tuple[str, bool, str]]:
    clauses = []
    for sentence in re.findall(r"[^。！？!?；;\n]+[。！？!?；;]?", text):
        if sentence.lstrip(" （(").startswith(_HANDOFF_CONDITIONAL):
            continue
        parts = [sentence.rstrip("。；;")] if allow_omitted_subject and not re.search(r"[（）()]", sentence) else _clauses(sentence)
        for part in parts:
            parenthetical = part.startswith(("（", "(")) and part.endswith(("）", ")"))
            previous = ""
            for clause in re.split(r"[，,：:]+", part[1:-1] if parenthetical else part):
                clause = clause.strip()
                if (clause and not _is_question(clause) and not _has_future_or_uncommitted_marker(clause)
                        and not clause.startswith(("请", "麻烦", "劳烦"))
                        and not any(word in clause for word in (*_HANDOFF_NON_ACTION, *_FUTURE_MARKERS))):
                    clauses.append((clause, parenthetical, previous))
                    previous = clause
                else:
                    previous = ""
    return clauses


def _handoff_action_prefixes(clause: str, item_names: Sequence[str], verbs: Sequence[str]) -> list[str]:
    prefixes = []
    for verb in verbs:
        for start in _term_starts(clause, verb):
            before, after = clause[:start].rstrip(), clause[start + len(verb):]
            disposer = max(before.rfind("把"), before.rfind("将"))
            pre_action = before[:disposer].rstrip() if disposer >= 0 else before
            if (_negated_at(clause, start) or (disposer >= 0 and _negated_at(clause, disposer))
                    or pre_action.endswith(("会", "再", "要", "应当", "须", "将"))):
                continue
            before_object = _HANDOFF_ADVERB.sub("", before)
            after_object = re.split(r"(?:后便|然后|随后|并把|并将|后看|并看)", after, maxsplit=1)[0]
            if any(name and (before_object.endswith(name) or (disposer < 0 and name in after_object))
                   for name in item_names):
                prefixes.append(before)
    return prefixes


def player_handoff_is_explicit(
    text: str, item_names: Sequence[str], *, allow_omitted_subject: bool = False,
) -> bool:
    """Find a current first-person or role-action handoff of a declared item in player input."""
    clauses = _handoff_clauses(text, allow_omitted_subject=allow_omitted_subject)
    return any(
        not _HANDOFF_OBSERVING.search(prefix)
        and not _has_unambiguous_nonplayer_subject(clause, len(prefix))
        and (_HANDOFF_SELF.match(prefix) or ((parenthetical or allow_omitted_subject) and (
            not prefix or prefix.startswith(("把", "将", "双手", "伸手", "单手", "捧起", "托起"))
        )))
        for clause, parenthetical, _ in clauses
        for prefix in _handoff_action_prefixes(clause, item_names, _HANDOFF_GIVE)
    ) or any(
        # The immediately preceding action can supply the object when both
        # clauses belong to the same explicit player action or verified click.
        (parenthetical or allow_omitted_subject)
        and (holding := _HANDOFF_HOLDING.match(previous)) is not None
        and previous[holding.end():] in item_names
        and _HANDOFF_OMITTED_OBJECT.fullmatch(clause)
        for clause, parenthetical, previous in clauses
    )


def performed_player_handoff(text: str, item_names: Sequence[str], *, receipt_only: bool = False) -> bool:
    """Find a prose assertion of the player's transfer or another character receiving the item."""
    # Quoted dialogue and offers are not performed action. Actor directions and
    # unquoted narration can directly assert an already completed receipt.
    text = re.sub(r"“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|\"[^\"]*\"|'[^']*'", "", text)
    for clause, _, _ in _handoff_clauses(text):
        if clause.endswith("吧"):
            continue
        if not receipt_only and any(
            _HANDOFF_PLAYER_IN_PROSE.match(_HANDOFF_OBSERVING.split(prefix)[-1].lstrip())
            for prefix in _handoff_action_prefixes(clause, item_names, _HANDOFF_GIVE)
        ):
            return True
        if any(not _HANDOFF_PLAYER_IN_PROSE.match(_HANDOFF_OBSERVING.split(prefix)[-1].lstrip())
               for prefix in _handoff_action_prefixes(clause, item_names, _HANDOFF_RECEIVE)):
            return True
    return False


def _is_parenthetical_action(clause: str) -> bool:
    return (
        len(clause) >= 2
        and clause[0] in "（("
        and clause[-1] in "）)"
        and bool(_action_term(clause))
    )


def _explicit_action_clause(text: str, *, departure: bool = False) -> str:
    terms = _DEPARTURE_TERMS if departure else _ACTION_TERMS
    conditional_sentences = [sentence for sentence in re.split(r"[。！？!?；;\n]+", text)
                             if sentence.lstrip(" （(").startswith(_HANDOFF_CONDITIONAL)]
    for clause in _clauses(text):
        if departure:
            has_term = any(
                not _negated_at(clause, start)
                and not _has_unambiguous_nonplayer_subject(clause, start)
                and not any(
                    start < approach_start + len(approach)
                    and start + len(term) > approach_start
                    for approach in _EXIT_APPROACH_TERMS
                    for approach_start in _term_starts(clause, approach)
                )
                for term in terms
                for start in _term_starts(clause, term)
            )
        else:
            action = _action_term(clause)
            has_term = bool(action)
            prefix = clause[:clause.find(action)] if action else ""
            disposer = max(prefix.rfind("把"), prefix.rfind("将"))
            if disposer >= 0:
                prefix = prefix[:disposer]
            # Requests and observations do not make the referenced actor's action the player's.
            if (re.search(r"请|让|叫|麻烦|劳烦|拜托", prefix) or _HANDOFF_OBSERVING.search(prefix)
                    or any(clause in sentence for sentence in conditional_sentences)
                    or any(marker in clause for marker in _FUTURE_MARKERS)
                    or any(_negated_at(clause, start)
                           or re.search(r"(?:会|要|应当|须|将)$", clause[:start])
                           for term in (*_ACTION_TERMS, "把", "将") for start in _term_starts(clause, term))):
                continue
        if not has_term:
            continue
        if _is_question(clause) or _has_future_or_uncommitted_marker(clause):
            continue
        if _explicit_subject(clause, allow_omitted=departure) or _is_parenthetical_action(clause):
            return clause
    return ""


def _departure_is_explicit(text: str) -> bool:
    return bool(_explicit_action_clause(text, departure=True))


def _exit_approach_clause(text: str) -> str:
    # When departure is explicitly canceled, prior approach actions must not suppress a new invitation; do not infer a return destination.
    if any(_negated_at(text, start) for term in _DEPARTURE_TERMS
           for start in _term_starts(text, term)):
        return ""
    for clause in _clauses(text):
        has_player_approach = any(
            not _has_unambiguous_nonplayer_subject(clause, start)
            for term in _EXIT_APPROACH_TERMS
            for start in _term_starts(clause, term)
        )
        if has_player_approach and _explicit_action_clause(clause):
            return clause
    return ""


def _future_reference(text: str) -> bool:
    if _is_question(text) or _has_future_or_uncommitted_marker(text):
        return False
    if not any(marker in text for marker in _FUTURE_MARKERS):
        return False
    return any(term in text for term in ("见", "回来", "再来", "联系", "继续", "碰面"))


def project_player_action_result(
    message: str,
    *,
    revision: int = 0,
    transition_intent: str = "unclear",
    route_changed: bool = False,
    fact_operations: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Generate a bounded player-action outcome projection.

    `player_input` supplies only source evidence; add it to `confirmed_actions` only when it satisfies
    a conservative direct-execution structure. Runtime transitions and fact operations are separate deterministic sources.
    """  # noqa: DOCSTRING_CJK

    # 判定使用完整原话，避免180字符摘录裁掉句尾疑问、条件或动作括号；只有输出引文限长。
    text = " ".join(str(message or "").strip().split())
    confirmed: list[dict[str, Any]] = []
    future: list[dict[str, Any]] = []
    if text and _departure_is_explicit(text):
        confirmed.append({
            "kind": "leave_current_scene",
            "status": "completed",
            "source": "player_input",
            "evidence_quote": _evidence(text),
        })
    approach_clause = _exit_approach_clause(text) if not confirmed else ""
    action_clause = _explicit_action_clause(text)
    if approach_clause:
        confirmed.append({
            "kind": "approach_scene_exit",
            "status": "performed",
            "source": "player_input",
            "evidence_quote": _evidence(approach_clause),
        })
    elif text and not confirmed and action_clause:
        confirmed.append({
            "kind": "player_action",
            "status": "performed",
            "action_term": _action_term(action_clause),
            "source": "player_input",
            "evidence_quote": _evidence(action_clause),
        })
    if text and _future_reference(text):
        future.append({
            "kind": "future_return_or_follow_up",
            "status": "future",
            "source": "player_input",
            "evidence_quote": _evidence(text),
        })

    if route_changed and transition_intent in {"accept", "initiate"}:
        confirmed.append({
            "kind": "transition_committed",
            "status": "completed",
            "source": "runtime_transition",
        })
    for operation in fact_operations or ():
        if not isinstance(operation, Mapping):
            continue
        key = str(operation.get("key") or "").strip()
        if not key:
            continue
        confirmed.append({
            "kind": "runtime_result",
            "status": "confirmed",
            "source": "runtime_fact",
            "fact_key": key,
            "value": operation.get("value"),
        })

    confirmed = confirmed[:_MAX_ROWS]
    future = future[:_MAX_ROWS]
    return {
        "schema": PLAYER_ACTION_PROJECTION_SCHEMA,
        "source_revision": int(revision),
        "confirmed_actions": confirmed,
        "future_references": future,
        "has_confirmed_player_action": any(
            row.get("source") == "player_input" for row in confirmed
        ),
        "player_left_current_scene": any(
            row.get("kind") == "leave_current_scene" for row in confirmed
        ),
        "player_departure_pending": any(
            row.get("kind") == "approach_scene_exit" for row in confirmed
        ),
    }


def normalize_player_action_projection(value: Any) -> dict[str, Any]:
    """Validate and copy the projection for Ledger/Prompt consumption, rejecting externally injected free-form fields."""  # noqa: DOCSTRING_CJK

    if not isinstance(value, Mapping) or value.get("schema") != PLAYER_ACTION_PROJECTION_SCHEMA:
        return project_player_action_result("")
    confirmed = value.get("confirmed_actions")
    future = value.get("future_references")
    if not isinstance(confirmed, list) or not isinstance(future, list):
        return project_player_action_result("")
    normalized_confirmed: list[dict[str, Any]] = []
    for row in confirmed[:_MAX_ROWS]:
        if not isinstance(row, Mapping):
            continue
        item = {
            "kind": str(row.get("kind") or ""),
            "status": str(row.get("status") or ""),
            "source": str(row.get("source") or ""),
        }
        for key in ("action_term", "evidence_quote", "fact_key"):
            if row.get(key) is not None and str(row.get(key) or ""):
                item[key] = _evidence(row.get(key))
        if "value" in row and isinstance(row.get("value"), (bool, int, str)):
            item["value"] = row.get("value")
        if item["kind"] and item["status"] and item["source"]:
            normalized_confirmed.append(item)
    normalized_future: list[dict[str, Any]] = []
    for row in future[:_MAX_ROWS]:
        if not isinstance(row, Mapping):
            continue
        item = {
            "kind": str(row.get("kind") or ""),
            "status": str(row.get("status") or ""),
            "source": str(row.get("source") or ""),
        }
        if row.get("evidence_quote"):
            item["evidence_quote"] = _evidence(row.get("evidence_quote"))
        if item["kind"] and item["status"] and item["source"]:
            normalized_future.append(item)
    return {
        "schema": PLAYER_ACTION_PROJECTION_SCHEMA,
        "source_revision": (
            value.get("source_revision")
            if type(value.get("source_revision")) is int and value.get("source_revision") >= 0
            else 0
        ),
        "confirmed_actions": normalized_confirmed,
        "future_references": normalized_future,
        "has_confirmed_player_action": any(
            row.get("source") == "player_input" for row in normalized_confirmed
        ),
        "player_left_current_scene": any(
            row.get("kind") == "leave_current_scene" for row in normalized_confirmed
        ),
        "player_departure_pending": any(
            row.get("kind") == "approach_scene_exit" for row in normalized_confirmed
        ),
    }


def player_action_projection_for_prompt(value: Any) -> dict[str, Any]:
    """Project input actions and authorized transitions without treating draft facts as confirmation."""

    projection = normalize_player_action_projection(value)
    # 这些结果行来自本轮 fact_operations，尚未提交；不能再成为 Actor 或复核自己的证据。
    # 仅过滤模型请求的副本，历史／账本仍沿用原 normalizer，玩家动作、未来约定和转场保持。
    projection["confirmed_actions"] = [
        row for row in projection["confirmed_actions"]
        if not (row["kind"] == "runtime_result" and row["source"] == "runtime_fact")
    ]
    return projection


def reference_player_action_input(projection: dict[str, Any], player_input: str) -> str:
    """Replace exact copies in a detached prompt projection and explain any references."""

    rule = ""
    if player_input:
        for row in (*projection["confirmed_actions"], *projection["future_references"]):
            if row.get("source") == "player_input" and row.get("evidence_quote") == player_input:
                row.pop("evidence_quote")
                row["evidence_ref"] = "player_input"
                rule = "evidence_ref 取 player_input 原话。"
    return rule


__all__ = [
    "PLAYER_ACTION_PROJECTION_SCHEMA",
    "normalize_player_action_projection",
    "player_action_projection_for_prompt",
    "reference_player_action_input",
    "project_player_action_result",
]
