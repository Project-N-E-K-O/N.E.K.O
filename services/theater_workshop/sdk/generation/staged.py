"""Generate a fixed story frame, then commit one author scene at a time."""
from __future__ import annotations

from config.prompts.prompts_theater_pipeline import _FRAME_PROMPT, _STATE_PROMPT

from copy import deepcopy
import json
from typing import Mapping

from services.theater.numeric_v2_fixed_narration import MAX_FIXED_NARRATION_TOKENS
from utils.tokenize import count_tokens

from ..model import LLMCallFailure
from ..numeric_v2 import NUMERIC_V2_MAX_FACT_KEYS, typed_completion_fact_count
from ..numeric_v2_branch import NumericV2BranchError, validate_branch_fixed_narrations
from .runtime_rules import (
    STORY_DESIGN_RULE, AUTHOR_KNOWLEDGE_RULE, COMPLETION_RESULT_RULE, GOAL_METADATA_RULE, SHARED_EXIT_SELECTION_RULE,
    TRANSITION_PHASE_RULE,
)
from .numeric_v2 import (
    NumericV2GenerationError, _LENGTH_PRESETS,
    _FIXED_NARRATION_MAX_ITEMS, _MAINLINE_OUTPUT_CONTRACT, _UNSUPPORTED_AUTHOR_TRIGGER,
    _author_condition_is_explicit, _author_exit_contract_issues, _explicit_original_chapter, _fixed_narration_source_span,
    _fixed_narration_echo_issues, _marked_author_original_spans, _validate_idea_outline,
    _continuation_paths, _path_tokens, _set_value_at_path, _value_at_path,
)

FLOW = "staged.v1"
_SCENE_INPUT_TARGET = 10_000
_LOCAL_REPAIR_RULE = (
    '\n局部修订覆盖上述输出格式。只返回 {"replacements":{"请求路径":修正后的完整值}}。'
    '逐一使用 requested_replacements 的原样键，不返回整幕或其他字段。'
    'current_candidate仅供核对上下文；程序保留未列出的上一稿内容及固定原文，按同一故事框架和已交付事实修正。'
    '有 original_text 时，只有原文和前提本来就支持入幕展示，才能将触发器改为 entry。'
    '若必须等待玩家或未知结果，保留原触发器供作者处理，不能提前公开。'
)
_AUTHOR_BOUNDARIES_RULE = (
    "\n遵守适用的 author_content_boundaries，不抄入 scene_boundaries。策划与限制冲突时，以作者限制为准。"
)
_AUTHOR_INTENT_RULE = (
    "\n依据 author_brief 独立核对提纲。原始需求优先于生成的提纲，保留作者交给玩家或双方的合法选择。"
    "若提纲预设了其中一个答案，当前正文与结局只写已保证的结果，选择留给实际互动。"
    "〔原文由对应幕提供〕仅是占位，实际原文按幕另附。"
)
_CONTRACT = json.loads(_MAINLINE_OUTPUT_CONTRACT)
_FRAME_CONTRACT = {
    **{key: deepcopy(_CONTRACT[key]) for key in (
        "world", "story_protagonist", "player_role", "relationship", "tone", "key_props",
    )},
    "relationship_arc": {key: value for key, value in _CONTRACT["relationship_arc"].items() if key != "stages"},
    "mainline_chapters": [{
        "title": "本幕标题",
        "narrative": "约100—150字的简短规划，写清眼前处境、独有互动、可改变的安排、可见结果与下一幕由此发生的变化",
    }],
    "ending": {**{key: _CONTRACT["ending"][key] for key in ("type", "title")},
               "summary": "各合法选择共同成立的收束，不用若/如果分别编写互斥结局，不固定可变处置或许可"},
}
_SCENE_PROMPT = GOAL_METADATA_RULE + "\n\n" + AUTHOR_KNOWLEDGE_RULE + "\n\n" + TRANSITION_PHASE_RULE + """
你是当前一幕的编剧。本轮只返回 output_contract 中的 chapter、relationship_stage、character_state_stage。
story_frame 是已确定的策划。previous_scene.chapter.entry_prerequisites 是前幕此出口必经的结果，completion_facts 是其余收束目标。除下列确定展示外，合法入路可能尚未完成它们。未保证的结果保留未定，也不在本幕重复布置为任务。
previous_scene.chapter.completion_originals 是前幕完成合同引用的入幕原文，other_entry_originals 是其余已展示的入幕原文。两类均按片段 ID 列出，承接其中已公开的信息，不重放原文或增设完成条件。
在 story_frame.mainline_chapters 中按从一开始的 chapter_index 找到本幕提纲，沿用标题和核心因果，不重排、增删或重写其它幕。
从前幕已保证的变化出发，开场给玩家一个能立即回应的动作、问题或分歧。narrative 写出谁采取什么行动，以及现场会看到什么变化；narrative_focus 点明这幕最值得展开的互动或后果。
把必要因果和可发挥内容分开。只有会改变玩家选择、关键事实或后续条件的结果才进入完成合同；临场反应、氛围、交流方式和普通手续留给演绎。让配角在同幕交付首次答复或可见结果，允许拒绝或未知。没有新选择或障碍的连续过程合并。
entry_bridge 承接前幕已获授权的时空经过，同地可空，首幕为空。开场承接已定范围与分工，不重问。共用开场只写各入路共有状态，可变的内容、数量、归属留给实际入路，旁白不写互斥备选。无改日安排不跨日，截止时间不是当前时间。桥段不复制开场，两段均不替玩家行动、说话或承诺。
ordered_goals 用合同枚举和 1—6 项有限交付。previous_goal 只指本幕前一项目标，首项不用。opening 最多一项；player 与 shared 在 turn，player 的 sources 包含 player_input。owner 与 delivery_type 按主体配对，semantic_state 可用于 catgirl、player、shared。dialogue_policy_after 通常用 unchanged。
""" + COMPLETION_RESULT_RULE + SHARED_EXIT_SELECTION_RULE + """
trigger_fact_ids 只选本幕必要结果。target_value 按字面匹配，不写 any_non_empty。普通出口的 entry_prerequisite_fact_ids 只填下一幕独立要求的硬前提。typed 事实不超过 remaining_typed_fact_keys，展示引用不占事实键。
普通出口先确定是否仍需新的玩家决定。若本幕已做出的决定或已交付的结果自然引出下一幕，无新许可、承诺、去向或操作待选，填 advance_on_completion=true，player_decision、fallback_offer 和 accept_input 均为空，省略 silent_choice。proposal 写由既成结果引出的后果；目标开场从后果开始，不能再次要求完成触发事实。否则填 false，proposal 与 fallback_offer 邀请同一项新活动、地点和时段，accept_input 写明确接受原话。许可、交易和风险承诺须明确授权。需新授权且作者发声策略为 forbidden 时才省口头邀请并填写 silent_choice。邀约字段为至多30 tokens的单行。末幕沿用自然结局，advance_on_completion=false，两项邀约字段为空。
关系和权限不随换幕重置，关系上限相邻最多变一级。known_player_facts、allowed_behaviors、forbidden_behaviors 各最多4项。known_player_facts 以玩家全名开头；progress_opportunity 以当前角色全名或“环境”开头，只写有限变化。首幕 address_state 不用 known_from_prior_scene。scene_boundaries 只写会破坏本幕因果、已确定状态或玩家权限的硬限制，以“不得”“禁止”或“不能”开头。区分在场配角当轮答复与无依据的事后转述。上幕局部限制结束，持续作者限制沿用。
按 length.scene_expected_turns_target 的非空目标估计 expected_turns。entry 固定原文可交付独立于玩家的关键证据或NPC自主事件，开场不复制原文。初始开场只建立原文前的环境；后续非 fixed_entry_only 入幕，原文尚缺且已获准、前因明确的角色或在场NPC非语言必要触发动作，可在原文前以明确主体交付一次，不代玩家行动，不编未知成败或额外操作；其余自身动作、对白与新反应放在原文之后。原文已完整承担角色开场时用 optional；只有确认下一幕由原文独立开场时才标 exit_plan.fixed_entry_only。仍需玩家参与的选择、谈判、操作和结果留在 turn；无独立原文时 fixed_narrations 写[]。
narrative 和 opening_scene 各不超过384 tokens。关系和道具应简洁，为后续摘录状态与处境后合计384 tokens的字段留出余量。
issues.path 是当前输出中的修订位置，source_path 是整稿定位。native_validation_issues 是本幕正式编译错误；只修当前返回对象，保留其它正文与事件，不修改已完成前幕、后幕或故事框架。只返回 JSON object。"""
_FINAL_CHAPTER_CAUSALITY = (
    "\n末幕倒推结局必需的可见证据。行动及其直接反馈须实际交付，可合为最小结果；"
    "玩家行动仍由玩家输入证明。结局若另依赖角色、配角或环境交付的结果，只为这个可见结果"
    "标 evidence_source=performance，并同时引用到 trigger_fact_ids 和 entry_prerequisite_fact_ids。"
    "需要实际执行时，事后评价或共识不能替代结果。普通复盘随结局收尾。"
)
_ENDING_PROMPT = AUTHOR_KNOWLEDGE_RULE + "\n\n" + TRANSITION_PHASE_RULE + """
本轮只生成结局 ending 与 ending_stage，使用 output_contract。previous_scene.chapter.entry_prerequisites 是通往结局必经的结果，据此收束。completion_facts 是其余收束目标。除下列确定展示外，合法入路可能尚未完成它们，不写死未保证的细节。
previous_scene.chapter.completion_originals 是前幕完成合同引用的入幕原文，other_entry_originals 是其余已展示的入幕原文。承接其中已公开的信息，不重放原文或增设完成条件。
沿用 ending_identity 的 type、title 与收束方向。summary 是创作规划；与离幕保证冲突或依赖未确定选择的细节，不能当作已发生事实写进结局。
previous_scene.character_state_stage只保留角色能力与权限，不含前幕临时scene_boundaries，也不是出幕状态；持续作者限制仍按author_content_boundaries遵守。结局聚焦已保证结果的当前角色反应，不从前幕入幕推定地点、陈设、姿态或持物，也不另安排换场。
交付、许可或活动尚未实际完成时，结局保留当前准备状态，不安排新互动、玩家动作或跨时段后果。
同场自然收束不需要换场旁白，entry_bridge保持空字符串，不为填满字段写编写说明。
结局没有普通回合，不留待玩家回答的新问题或新决定，不借收束替玩家承诺、离开或执行附加动作。
结局旁白已完整交付结果、没有必要的新回应时，dialogue_policy使用optional；不为收束强求台词或动作，确有必须交付的角色回应才用required。
结局开场与摘要是同一时点；三方状态由后续独立摘录，本阶段不输出。未明确的姿态与持物省略，不另设计画面或新道具。承接人物改变的原因，保留合法分歧，不复制最后一幕全文。
只固定各合法选择共同保证的结果；处置方式、道具归属或去向取决于玩家选择时，不在静态结局擅自选定一种。
ending_stage 不带 chapter_index，continuity_from_previous 不能为空。opening_scene 及后续组合字段分别不超过384 tokens。
scene_boundaries 只放以“不得”“禁止”或“不能”开头的负向边界。
output_contract中的字符串是字段说明，返回时须换成实际正文，不返回编写规则。
若有 issues，修正当前结局，已完成主线与框架保持。只返回 JSON object。"""


_AUTHOR_ENTRY_RULE = (
    "\nauthor_entry_narrations 是本幕由程序原样加入的入幕原文，开场后按给定顺序播放。"
    "opening_scene 建立原文前的场景，不复制原文或提前写出其结果。"
    "只在 fixed_narrations 返回本轮另外编写的片段；不要重复返回程序原文。"
    "展示引用可使用给定的片段 id。"
)
_EARLIER_ENTRY_RULE = (
    "\nearlier_entry_originals 按幕号和片段编号保留更早已公开的入幕原文，仅承接其中与本幕有关的已知内容。"
    "不重放，不从原文推断后来选择、处置或状态。omitted_earlier_entry_chapters 表示本次未附原文，不补造其精确内容。"
)
_FRAME_ORIGINAL_REF_RULE = (
    "\ncore_idea 中的“原文编号”是作者已指定入幕位置的原文占位。"
    "author_passages 仍用占位外紧邻的原有边界引用对应原文，并保留作者指定的幕序和 entry。"
    "规划以作者说明为依据，不编造未附的正文。编写对应幕时会收到完整原文。"
)


def _author_entry_narrations(frame, brief, *, marked_originals, token_counter):
    entries, issues = {}, []
    rows = frame.get("author_passages", [])
    if not isinstance(rows, list):
        return entries, [{"code": "author_passages_invalid", "path": "author_passages",
                          "message": "author_passages 必须为数组。"}]
    seen = set()
    chapter_tokens = {}
    chapter_count = len(frame.get("mainline_chapters", ())) if isinstance(frame.get("mainline_chapters"), list) else 0
    for index, row in enumerate(rows):
        chapter = row.get("chapter_index") if isinstance(row, dict) else None
        span = _fixed_narration_source_span(brief, row.get("text_source")) if isinstance(row, dict) else None
        if type(chapter) is not int or not 1 <= chapter <= chapter_count + 1 or span is None or span in seen:
            issues.append({"code": "author_passage_invalid", "path": f"author_passages[{index}]",
                           "message": "原文须有唯一且有序的边界，每段只分配一次，幕序号须存在。"})
            continue
        trigger_type = row.get("trigger_type")
        if trigger_type == "condition":
            issues.append({**_UNSUPPORTED_AUTHOR_TRIGGER, "path": f"author_passages[{index}].trigger_type"})
            continue
        if trigger_type != "entry":
            issues.append({"code": "author_passage_invalid", "path": f"author_passages[{index}].trigger_type",
                           "message": "trigger_type 必须为 entry 或 condition。"})
            continue
        if span in marked_originals:
            assignment = _explicit_original_chapter(brief, span, ending_chapter=chapter_count + 1)
            if assignment and chapter != assignment[0]:
                issues.append({"code": "author_passage_wrong_chapter", "path": f"author_passages[{index}]",
                               "message": f"请将 chapter_index 改为作者指定的 {assignment[0]}。"})
                continue
        chapter_entries = entries.setdefault(str(chapter), [])
        text = brief[slice(*span)]
        if len(chapter_entries) >= _FIXED_NARRATION_MAX_ITEMS:
            issues.append({"code": "author_passages_limit", "path": f"author_passages[{index}]",
                           "message": "单幕作者原文片段超过上限；保留作者要求的全部正文，合并同一段连续原文的引用。"})
            continue
        total_tokens = chapter_tokens.get(chapter, 0) + token_counter(text)
        if total_tokens > MAX_FIXED_NARRATION_TOKENS:
            issues.append({"code": "author_passages_budget_exceeded", "path": f"author_passages[{index}]",
                           "message": f"该幕作者原文合计超过{MAX_FIXED_NARRATION_TOKENS} tokens；只按作者指定归属分配，不截短或擅自挪幕。"})
            continue
        chapter_tokens[chapter] = total_tokens
        seen.add(span)
        chapter_entries.append({
            "id": f"author_original_{index + 1}", "text": text,
            "trigger": {"type": "entry"}, "after": [], "required_before_exit": False,
        })
    if not issues and any(span not in seen for span in marked_originals):
        issues.append({"code": "author_passages_missing", "path": "author_passages",
                       "message": "作者用[原文起]和[原文止]明确标出的每段入幕原文都须完整引用并分配到对应幕。"})
    return entries, issues


def _belongs(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith((prefix + ".", prefix + "["))


def _unique_issues(issues):
    result = []
    for issue in issues:
        if issue not in result:
            result.append(deepcopy(issue))
    return result


def _before_state_extraction(issues, state_path):
    # These three writer fields are always overwritten by the mandatory
    # extractor. Its output, permissions and all other fields still validate.
    deferred = {
        ("character_state_catgirl_subject_invalid", f"{state_path}.catgirl_state"),
        ("character_state_player_subject_invalid", f"{state_path}.player_state"),
        ("character_state_environment_subject_invalid", f"{state_path}.environment_state"),
    }
    return [issue for issue in issues if (issue["code"], issue["path"]) not in deferred]


def _frame_projection(candidate):
    frame = {
        **{key: deepcopy(candidate[key]) for key in _FRAME_CONTRACT if key not in ("mainline_chapters", "ending")},
        "mainline_chapters": [
            {key: chapter[key] for key in ("title", "narrative")}
            for chapter in candidate["mainline_chapters"]
        ],
        "ending": {key: candidate["ending"][key] for key in ("type", "title", "summary")},
    }
    frame["relationship_arc"].pop("stages", None)
    return frame


def _frame_author_issue(issue):
    parts = _path_tokens(str(issue.get("path") or ""))
    if not parts or parts[0] in {"mainline_chapters", "ending"}:
        return False
    value = _FRAME_CONTRACT
    for part in parts:
        if isinstance(value, list) and type(part) is int and part >= 0 and value:
            value = value[0]
        elif isinstance(value, Mapping) and part in value:
            value = value[part]
        else:
            return False
    return True


def _merge_frame_repair(story_frame, frame, native_issues, author_issues=()):
    # Native paths name compiled fields; only their author inputs may change.
    sources = {
        "intro.background": (("world", "background"),),
        "intro.invitation": (("world", "invitation"),),
        "intro.player_identity": (("player_role", "identity"),),
        "intro.catgirl_identity": (("story_protagonist", "identity"), ("story_protagonist", "secret_or_wound")),
        "catgirl_binding.role_overlay": (("story_protagonist", "motivation"),),
    }
    preserved = deepcopy(story_frame)
    for issue in native_issues:
        for group, key in sources.get(issue.get("path"), ()):
            value = frame.get(group)
            preserved[group][key] = deepcopy(value.get(key)) if isinstance(value, Mapping) else None
    for issue in author_issues:
        if _frame_author_issue(issue):
            path = issue["path"]
            _set_value_at_path(preserved, path, deepcopy(_value_at_path(frame, path)))
    return preserved


def _scene_original_issues(scene, path):
    issues = _fixed_narration_echo_issues(scene, path)
    try:
        validate_branch_fixed_narrations(scene.get("fixed_narrations"), f"{path}.fixed_narrations")
    except NumericV2BranchError as error:
        issues.append({**_UNSUPPORTED_AUTHOR_TRIGGER, "path": error.details["path"],
                       "message": "固定原文只支持入幕展示。依赖玩家动作或未知结果的内容留在普通回合，不提前展示，并同步修正所引用的完成项。"})
    return issues


def _local_repair_fields(draft, contract, issues, *, author_ids):
    """Use only explicitly located fields in the phase contract for a smaller retry."""
    if not issues or any(not issue.get("path") for issue in issues):
        return {}
    fields = {}
    for path in _continuation_paths(issues):
        tokens = _path_tokens(path)
        normalized_path = "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in tokens).lstrip(".")
        if len(tokens) < 2 or path != normalized_path:
            return {}
        if "fixed_narrations" in tokens:
            # The phase draft still includes program-owned entries, so issue
            # indexes and editable pieces refer to the same original list.
            if len(tokens) != 4 or tokens[1] != "fixed_narrations" or tokens[3] != "trigger":
                return {}
            piece = _value_at_path(draft, path.rsplit(".", 1)[0])
            if (not isinstance(piece, Mapping) or piece.get("id") in author_ids
                    or not isinstance(piece.get("text"), str)):
                return {}
            fields[path] = {"current_value": piece.get("trigger"), "output_contract": {"type": "entry"},
                            "original_text": piece["text"]}
            continue
        try:
            example = contract
            for part in tokens:
                example = example[0 if isinstance(part, int) else part]
        except (KeyError, TypeError, IndexError):
            return {}
        fields[path] = {"current_value": _value_at_path(draft, path), "output_contract": example}
    return fields


def _native_issue_node_index(issue, chapter_count):
    parts = _path_tokens(str(issue.get("path") or ""))
    if len(parts) < 2 or parts[0] != "nodes":
        return None
    node = parts[1]
    if type(node) is int:
        return node if 0 <= node <= chapter_count else None
    if node == "ending_normal":
        return chapter_count
    return next((index for index in range(chapter_count) if node == f"mainline_{index + 1:02d}"), None)


def set_native_repair_phase(state, issues):
    """Keep node-local compiler errors out of the frame, including ID-based paths."""
    count = len(state["candidate"]["mainline_chapters"])
    locations = {_native_issue_node_index(issue, count) for issue in issues}
    repairs = sorted(index for index in locations if index is not None)
    state.update(stage="frame" if None in locations or not repairs else "repair",
                 repairs=repairs, native_issues=issues, issues=issues, phase_candidate=None)


def generate_staged(generator, *, title, setup, checkpoint=None, cast_names=None, save_checkpoint=None):
    """Keep completed scenes in the checkpoint; retries only replace the current phase."""
    brief = str(setup.get("brief") or "").strip()
    if len("".join(brief.split())) <= 30:
        raise NumericV2GenerationError("mainline_generation_input_too_short")
    author_boundaries = list(dict.fromkeys(
        item.strip() for item in setup.get("content_boundaries") or [] if item.strip()
    ))
    boundary_context = {"author_content_boundaries": author_boundaries} if author_boundaries else {}
    boundary_rule = _AUTHOR_BOUNDARIES_RULE if author_boundaries else ""
    preset = setup.get("length_preset")
    if preset not in _LENGTH_PRESETS:
        raise NumericV2GenerationError("invalid_length_preset")
    minimum, maximum = _LENGTH_PRESETS[preset]
    state = deepcopy(dict(checkpoint)) if checkpoint is not None else {
        "flow": FLOW, "stage": "frame", "candidate": {}, "completed_chapters": 0,
        "phase_candidate": None, "issues": [],
        **({"cast_names": dict(cast_names)} if cast_names is not None else {}),
    }
    if state.get("flow") != FLOW:
        raise NumericV2GenerationError("generation_checkpoint_invalid")
    if state["stage"] in {"complete", "frame"} and state.get("native_issues") and state.get("issues"):
        # Older complete checkpoints or frame checkpoints can contain node-local diagnostics.
        locations = {_native_issue_node_index(issue, len(state["candidate"]["mainline_chapters"]))
                     for issue in state["native_issues"]}
        if state["stage"] == "complete" or None not in locations:
            set_native_repair_phase(state, state["native_issues"])
        elif locations - {None}:
            state["repairs"] = sorted(set(state.get("repairs", ())) | (locations - {None}))
    names = state.get("cast_names")
    if names is not None and (
        not isinstance(names, Mapping)
        or any(not isinstance(names.get(key), str) or not names[key].strip()
               or names[key] != names[key].strip() for key in ("player_name", "catgirl_name"))
        or names["player_name"] == names["catgirl_name"]
    ):
        raise NumericV2GenerationError("invalid_cast_names")
    attempts = 0
    token_counter = generator._token_counter or count_tokens
    for chapter, pieces in state.get("author_entry_narrations", {}).items():
        if sum(token_counter(piece["text"]) for piece in pieces) > MAX_FIXED_NARRATION_TOKENS:
            raise NumericV2GenerationError("invalid_author_original", checkpoint=state, issues=[{
                "code": "author_passages_budget_exceeded", "path": "brief",
                "message": f"存档中第{chapter}幕的作者原文超过{MAX_FIXED_NARRATION_TOKENS} tokens；需调整作者需求后重新生成。",
            }])
    try:
        marked_originals = _marked_author_original_spans(brief)
    except ValueError:
        marked_originals = ()
        invalid_markers = True
    else:
        invalid_markers = False
    if any(_author_condition_is_explicit(brief, span) for span in marked_originals):
        raise NumericV2GenerationError("invalid_author_original", checkpoint=state, issues=[
            {**_UNSUPPORTED_AUTHOR_TRIGGER, "path": "brief"},
        ])
    if state["stage"] == "frame" and not state["completed_chapters"]:
        oversized = any(token_counter(brief[start:end]) > MAX_FIXED_NARRATION_TOKENS
                        for start, end in marked_originals)
        if invalid_markers or oversized:
            raise NumericV2GenerationError("invalid_author_original", checkpoint=state, issues=[{
                "code": "author_passages_budget_exceeded" if oversized else "author_passages_invalid",
                "path": "brief",
                "message": (f"单段原文超过每幕{MAX_FIXED_NARRATION_TOKENS} tokens 的上限；请调整作者原文。"
                            if oversized else "每段作者原文须用成对的[原文起]和[原文止]标记，且正文不能为空。"),
            }])
    frame_idea = brief
    if state["stage"] == "frame":
        for number, span in reversed(list(enumerate(marked_originals, 1))):
            assignment = _explicit_original_chapter(brief, span, ending_chapter=maximum + 1)
            if assignment and assignment[1]:
                start, end = span
                frame_idea = f"{frame_idea[:start]}〔原文编号 {number}〕{frame_idea[end:]}"

    def outline_issues(candidate, *, pending_state_index=None, pending_chapter_index=None):
        return _validate_idea_outline(candidate, minimum=minimum, maximum=maximum,
            scene_expected_turns_target=8 if preset == "short" else None,
            cast_names=names, token_counter=generator._token_counter,
            pending_state_index=pending_state_index,
            pending_chapter_index=pending_chapter_index)

    def persist():
        if save_checkpoint is not None:
            save_checkpoint(deepcopy(state))

    def consume_issues(locations, node_index=None):
        state["issues"] = _unique_issues(issue for issue in state.get("issues", ()) if not (
            any(_belongs(str(issue.get("path") or ""), prefix) for pair in locations for prefix in pair)
            or node_index is not None and issue in state.get("native_issues", ())
            and _native_issue_node_index(issue, len(state["candidate"]["mainline_chapters"])) == node_index))

    def resume_written_scene(scene_key, *, terminal=False):
        draft = state.get("written_scene")
        if draft is None:
            return None
        issues = _scene_original_issues(draft[scene_key], scene_key)
        if scene_key == "chapter":
            issues.extend(_author_exit_contract_issues(draft[scene_key], scene_key, terminal=terminal))
        if not issues:
            return deepcopy(draft)
        # Older checkpoints may already hold a duplicated opening. Repair only
        # that draft before asking the extractor to read its two display sources.
        state.update(phase_candidate=state.pop("written_scene"), issues=_unique_issues([*state.get("issues", ()), *issues]))
        persist()
        return None

    def phase(system, contract, context, validate, operation, max_tokens, locations=(),
              *, scene_key=None, author_entries=(), earlier_entries=None):
        if author_entries:
            system += _AUTHOR_ENTRY_RULE
            context = {**context, "author_entry_narrations": author_entries}
        author_ids = tuple(piece["id"] for piece in author_entries)
        author_texts = tuple(piece["text"] for piece in author_entries)

        def with_author_entries(draft, *, include):
            if not author_entries or not isinstance(draft, Mapping) or not isinstance(draft.get(scene_key), Mapping):
                return draft
            scene = draft[scene_key]
            pieces = scene.get("fixed_narrations", [])
            if not isinstance(pieces, list):
                return draft  # The normal validator reports malformed writer output.
            generated = [piece for piece in pieces if not isinstance(piece, dict)
                         or (piece.get("id") not in author_ids and piece.get("text") not in author_texts)]
            return {**draft, scene_key: {**scene, "fixed_narrations": [
                *(deepcopy(author_entries) if include else ()), *generated,
            ]}}

        def validate_draft(draft):
            issues = validate(draft)
            if scene_key and isinstance(draft.get(scene_key), Mapping):
                issues.extend(_scene_original_issues(draft[scene_key], scene_key))
            if author_entries and isinstance(draft.get(scene_key), Mapping):
                pieces = draft[scene_key].get("fixed_narrations")
                if isinstance(pieces, list) and sum(
                    token_counter(piece["text"]) for piece in pieces
                    if isinstance(piece, Mapping) and isinstance(piece.get("text"), str)
                ) > MAX_FIXED_NARRATION_TOKENS:
                    issues.append({
                        "code": "fixed_narration_budget_exceeded", "path": f"{scene_key}.fixed_narrations",
                        "message": f"固定原文合计超过{MAX_FIXED_NARRATION_TOKENS} tokens；只缩短或移除另写的片段，保留程序提供的作者原文。",
                    })
            return issues

        nonlocal attempts
        draft = state.get("phase_candidate")
        repairing_frame = operation == "numeric_v2_story_frame" and state["completed_chapters"] > 0
        if repairing_frame and isinstance(draft, Mapping):
            draft = _merge_frame_repair(state["story_frame"], draft, state.get("native_issues", []), state.get("issues", ()))
        draft = with_author_entries(draft, include=True)
        issues = validate_draft(draft) if isinstance(draft, Mapping) else []
        saved_issues = state.get("issues") or []
        native_issues = state.get("native_issues", ()) if "native_validation_issues" in context else ()

        def in_scope(issue):
            return (issue in context.get("native_validation_issues", ()) or not locations
                    or any(_belongs(str(issue.get("path") or ""), prefix)
                           for pair in locations for prefix in pair))

        remaining_issues = _unique_issues(issue for issue in saved_issues if not in_scope(issue))
        # Structural validation cannot reproduce all saved author/review diagnostics.
        if not issues:
            # Native diagnostics already have a phase-scoped channel.
            issues = _unique_issues(issue for issue in saved_issues if in_scope(issue) and issue not in native_issues)
        last_failure = None
        response_error = None

        def request(prompt, payload):
            if earlier_entries:
                prompt += _EARLIER_ENTRY_RULE
            separators = (",", ":") if scene_key else None

            def messages_for(data):
                return [{"role": "system", "content": prompt},
                        {"role": "user", "content": json.dumps(data, ensure_ascii=False, separators=separators)}]

            def send(messages):
                return generator.call_llm(
                    messages, temperature=0.25, max_tokens=max_tokens, max_retries=1,
                    response_format={"type": "json_object"}, thinking={"type": "disabled"}, operation=operation)

            if not earlier_entries:
                return send(messages_for(payload))

            def with_earlier(selected):
                return messages_for({
                    **payload,
                    "earlier_entry_originals": {index: earlier_entries[index] for index in sorted(selected)},
                    "omitted_earlier_entry_chapters": [index for index in earlier_entries if index not in selected],
                })

            # Prioritize recent evidence and retain only complete original groups.
            # The host is still authoritative and may have a smaller configured budget.
            selected = set()
            for index in reversed(earlier_entries):
                trial = selected | {index}
                if token_counter(json.dumps(with_earlier(trial), ensure_ascii=False)) <= _SCENE_INPUT_TARGET:
                    selected = trial
            try:
                return send(with_earlier(selected))
            except Exception as error:
                if not selected or getattr(error, "code", "") != "workshop_model_input_budget_exceeded":
                    raise
                return send(with_earlier(set()))

        for _ in range(3):
            payload = {**context, "cast_names": names}
            # Validator paths refer to the assembled outline; the model edits a phase response.
            local_issues = [{**issue, "source_path": issue["path"], "path": next(
                (target + issue["path"][len(source):] for source, target in locations
                 if _belongs(issue["path"], source)), issue["path"])} for issue in issues]
            editable_draft = with_author_entries(draft, include=False)
            repair_fields = {}
            # Missing text has a precise replacement; relational diagnostics
            # still need the whole phase even when their path names a scalar.
            if scene_key and isinstance(editable_draft, Mapping) and local_issues and all(
                    issue["code"] == "required_text" for issue in local_issues):
                repair_fields = _local_repair_fields(draft, contract, local_issues, author_ids=author_ids)
                if any(not isinstance(field["output_contract"], str) for field in repair_fields.values()):
                    repair_fields = {}
            attempts += 1
            try:
                request_data = {**payload, "output_contract": contract,
                                "current_candidate": editable_draft, "issues": local_issues}
                if repair_fields:
                    request_data["requested_replacements"] = repair_fields
                if response_error:
                    request_data["response_error"] = response_error
                try:
                    response = request(system + (_LOCAL_REPAIR_RULE if repair_fields else ""), request_data)
                except Exception as error:
                    if getattr(error, "code", "") != "workshop_model_input_budget_exceeded":
                        raise
                    if operation == "numeric_v2_story_frame" and frame_idea != brief:
                        response = request(system + _FRAME_ORIGINAL_REF_RULE, {**request_data, "core_idea": frame_idea})
                    else:
                        if scene_key is None or not isinstance(editable_draft, Mapping):
                            raise
                        repair_fields = _local_repair_fields(draft, contract, local_issues, author_ids=author_ids)
                        if not repair_fields:
                            raise
                        following = payload.get("following_scene")
                        if following and not any(path.startswith(("chapter.exit_plan", "chapter.transition_goal"))
                                                 for path in repair_fields):
                            # A local source edit can see the next scene's entry and
                            # constraints without loading the next scene's full original.
                            chapter = {key: value for key, value in following["chapter"].items()
                                       if key != "fixed_narrations"}
                            payload["following_scene"] = {**following, "chapter": chapter}
                        response = request(system + _LOCAL_REPAIR_RULE,
                            {**payload, "requested_replacements": repair_fields, "issues": local_issues,
                             **({"response_error": response_error} if response_error else {})})
            except Exception as error:
                # Preserve host refusals (budget, revision, storage) as their original public errors.
                error.checkpoint = deepcopy(state)
                raise
            if isinstance(response, LLMCallFailure):
                last_failure = response
                continue
            parsed = generator.parse_json_response(response) if isinstance(response, str) else None
            if not isinstance(parsed, Mapping) or parsed.get("parse_error"):
                response_error = {"code": "invalid_json_object", "message": "上一份答复必须是符合输出合同的JSON对象。"}
                if isinstance(response, str):
                    start, end = response.find("{"), response.rfind("}") + 1
                    candidate = response[start:end] if 0 <= start < end else response
                    try:
                        json.loads(candidate)
                    except json.JSONDecodeError as error:
                        response_error = {"code": "invalid_json", "message": error.msg,
                                          "line": error.lineno, "column": error.colno}
                continue
            response_error = None
            if repair_fields:
                replacements = parsed.get("replacements")
                if (set(parsed) != {"replacements"} or not isinstance(replacements, Mapping)
                        or set(replacements) != set(repair_fields)):
                    response_error = {"code": "invalid_replacements", "message": "只返回replacements，完整且仅覆盖请求路径。",
                                      "expected_paths": list(repair_fields)}
                    continue
                patched = deepcopy(dict(draft))
                if not all(_set_value_at_path(patched, path, replacements[path]) for path in repair_fields):
                    continue
                parsed = patched
            draft = with_author_entries(deepcopy(dict(parsed)), include=True)
            if repairing_frame:
                draft = _merge_frame_repair(state["story_frame"], draft, state.get("native_issues", []), state.get("issues", ()))
            state["phase_candidate"] = draft
            issues = validate_draft(draft)
            state["issues"] = _unique_issues([*remaining_issues, *issues])
            if not issues:
                return draft
        raise NumericV2GenerationError(
            last_failure.error_code if last_failure is not None else "invalid_mainline_generation",
            checkpoint=deepcopy(state), issues=issues, attempts=attempts,
            provider_details=last_failure.diagnostic() if last_failure is not None else None,
        )

    def extract_states(draft, scene_key, state_key, validate, locations, *, fixed_entry_only=False):
        # Persist the written scene before the short call; a failed extraction resumes here.
        if "written_scene" not in state:
            state.update(written_scene=deepcopy(draft), phase_candidate=None)
            persist()
        fields = ("catgirl_state", "player_state", "environment_state")
        entry_narrations = [item["text"] for item in draft[scene_key].get("fixed_narrations", [])
            if item["trigger"]["type"] == "entry"]
        opening_scene = "" if fixed_entry_only and entry_narrations else draft[scene_key]["opening_scene"]
        entry_bridge = draft[scene_key].get("entry_bridge", "")
        if scene_key == "chapter":
            fields += ("catgirl_situation",)

        def apply_excerpt(excerpt):
            result = deepcopy(draft)
            result[state_key].pop("temporal_context", None)
            result[state_key].update({key: value for key, value in excerpt.items()
                if key != "catgirl_situation" and not (key == "temporal_context" and value == {
                    "anchor": "", "relation_to_previous": "unspecified"})})
            if "catgirl_situation" in excerpt:
                result[scene_key]["catgirl_situation"] = excerpt["catgirl_situation"]
            return result

        def validate_excerpt(excerpt):
            if (set(excerpt) - {"temporal_context"} != set(fields)
                    or any(not isinstance(excerpt.get(key), str) for key in fields)):
                return [{"code": "expected_state_strings", "path": "", "message": "按合同返回三方状态及当前处境字符串，temporal_context可省略。"}]
            issues = validate(apply_excerpt(excerpt))
            temporal = excerpt.get("temporal_context")
            if isinstance(temporal, Mapping) and isinstance(temporal.get("anchor"), str):
                anchor = temporal["anchor"]
                if (anchor not in opening_scene
                        and anchor not in entry_bridge
                        and not any(anchor in text for text in entry_narrations)):
                    issues.append({"code": "temporal_anchor_without_source", "path": "temporal_context.anchor",
                                   "message": "时间锚须逐字来自本次开场、入幕原文或桥段，不推断日期。"})
            return issues

        try:
            excerpt = phase(_STATE_PROMPT, {
                "catgirl_state": f"以{(names or {}).get('catgirl_name', '女主')}开头的开场收笔状态",
                "player_state": f"以{(names or {}).get('player_name', '男主')}开头的开场收笔状态",
                "environment_state": "以环境开头的开场收笔状态",
                **({"catgirl_situation": "开场明确的当前角色当前顾虑、立场或未决问题；未交代时说明未明确，不补剧情"} if scene_key == "chapter" else {}),
                "temporal_context": {"anchor": "有依据时逐字摘录当前时间短语，未交代则省略整个temporal_context",
                                     "relation_to_previous": "unspecified | same_day | next_day | after_interval"},
            }, {**({"opening_scene": opening_scene} if opening_scene else {}),
                **({"entry_narrations": entry_narrations} if entry_narrations else {}),
                "entry_bridge": entry_bridge}, validate_excerpt,
                "numeric_v2_opening_state_extraction", 600, locations)
        except NumericV2GenerationError as error:
            # Issues outside the extractor's fields (e.g. relationship items or key props
            # in the combined situation budget) cannot be fixed by re-extracting the same
            # scene. Resume from the writer with these issues instead of looping here.
            if any(issue.get("path") not in ("", "temporal_context.anchor")
                   and not any(_belongs(issue["path"], source) for source, _ in locations)
                   for issue in error.issues):
                state.pop("written_scene")
                state.update(phase_candidate=deepcopy(draft), issues=_unique_issues([*state.get("issues", ()), *error.issues]))
                persist()
                error.checkpoint = deepcopy(state)
            raise
        result = apply_excerpt(excerpt)
        state.pop("written_scene")
        return result

    candidate = state["candidate"]
    if checkpoint is not None and state["completed_chapters"]:
        chapters = candidate["mainline_chapters"]
        completed = state["completed_chapters"]
        counts = [typed_completion_fact_count(chapter) for chapter in chapters[:completed]]
        excess = sum(counts) + len(chapters) - completed - NUMERIC_V2_MAX_FACT_KEYS
        repairs = set(state.get("repairs", ()))
        for index in range(completed - 1, -1, -1):
            if excess > 0 and counts[index] > 1:
                repairs.add(index)
                excess -= counts[index] - 1
            if any(issue["code"] == _UNSUPPORTED_AUTHOR_TRIGGER["code"]
                   for issue in _scene_original_issues(chapters[index], "chapter")):
                repairs.add(index)
            if _author_exit_contract_issues(chapters[index], "chapter", terminal=index == len(chapters) - 1):
                repairs.add(index)
        if any(issue["code"] == _UNSUPPORTED_AUTHOR_TRIGGER["code"]
               for issue in _scene_original_issues(candidate["ending"], "ending")):
            repairs.add(len(chapters))
        if repairs:
            old_repairs = state.get("repairs", [])
            native_issues = [issue for issue in state.get("native_issues", [])
                             if issue.get("code") != "fact_contract_limit_exceeded"]
            needs_frame = state["stage"] == "frame" and (any(
                _native_issue_node_index(issue, len(chapters)) is None for issue in native_issues)
                or any(_frame_author_issue(issue) for issue in state.get("issues", ())))
            ordered_repairs = sorted(repairs)
            if not needs_frame and (state["stage"] != "repair" or old_repairs[:1] != ordered_repairs[:1]):
                state.pop("written_scene", None)
                state.update(stage="repair", phase_candidate=None)
            state.update(repairs=ordered_repairs, native_issues=native_issues)
            persist()
    if "story_frame" not in state and (state["stage"] != "frame" or state["completed_chapters"]):
        # Pre-snapshot checkpoints only contain the current outline. Freeze the
        # best available projection once so later retries do not drift further.
        state["story_frame"] = _frame_projection(candidate)
    if state["stage"] == "frame":
        if state["completed_chapters"] and state.get("phase_candidate") is None:
            state["phase_candidate"] = deepcopy(state["story_frame"])
        def validate_frame(frame):
            prefixes = ["world", "story_protagonist", "player_role", "relationship", "tone",
                        "key_props", "relationship_arc.opening_relationship",
                        "relationship_arc.long_term_direction", "ending.type", "ending.title", "ending.summary"]
            chapters = frame.get("mainline_chapters")
            issues = outline_issues(frame)
            author_issues = [] if state["completed_chapters"] else _author_entry_narrations(
                frame, brief, marked_originals=marked_originals, token_counter=token_counter)[1]
            unsupported = [issue for issue in author_issues if issue["code"] == _UNSUPPORTED_AUTHOR_TRIGGER["code"]]
            if unsupported:
                raise NumericV2GenerationError("invalid_author_original", checkpoint=deepcopy(state),
                                               issues=unsupported, attempts=attempts)
            return author_issues + [issue for issue in issues if any(
                _belongs(issue["path"], prefix) for prefix in prefixes)
                or issue["path"] == "mainline_chapters"
                or (isinstance(chapters, list) and any(
                    issue["path"] in (f"mainline_chapters[{index}].title", f"mainline_chapters[{index}].narrative")
                    for index in range(len(chapters))))]
        frame_contract = {**_FRAME_CONTRACT, "author_passages": [{
            "chapter_index": "主线幕序号，从1开始；结局为主线幕数加一",
            "text_source": {"start_after": "紧邻原文前的唯一原话", "end_before": "紧邻原文后的唯一原话"},
            "trigger_type": "entry | condition",
        }]}
        author_setup = {key: value for key, value in setup.items() if key not in ("brief", "length_preset")}
        if "content_boundaries" in author_setup:
            author_setup["content_boundaries"] = author_boundaries
        frame_locations = tuple((key, key) for key in frame_contract
                                if key not in {"mainline_chapters", "ending", "relationship_arc"}) + tuple(
            (f"relationship_arc.{key}", f"relationship_arc.{key}") for key in frame_contract["relationship_arc"])
        frame = phase(STORY_DESIGN_RULE + "\n\n" + _FRAME_PROMPT, frame_contract,
            {"core_idea": brief, "author_setup": author_setup,
             "native_validation_issues": _unique_issues(issue for issue in state.get("native_issues", [])
                 if _native_issue_node_index(issue, len(candidate["mainline_chapters"])) is None),
             "length": {"preset": preset, "minimum": minimum, "maximum": maximum}},
            validate_frame, "numeric_v2_story_frame", 5000, frame_locations if state["completed_chapters"] else ())
        if state["completed_chapters"]:
            # Native errors outside nodes repair the frame, without replacing completed scenes or ending.
            for key in _FRAME_CONTRACT.keys() - {"mainline_chapters", "ending", "relationship_arc"}:
                candidate[key] = deepcopy(frame[key])
            candidate["relationship_arc"].update({key: deepcopy(value)
                for key, value in frame["relationship_arc"].items() if key != "stages"})
            state["story_frame"] = deepcopy(frame)
            state.update(stage="repair" if state.get("repairs") else "complete", phase_candidate=None)
        else:
            entries, _ = _author_entry_narrations(
                frame, brief, marked_originals=marked_originals, token_counter=token_counter)
            candidate = deepcopy({key: value for key, value in frame.items() if key != "author_passages"})
            if entries:
                state["author_entry_narrations"] = entries
                # Preserve the original request without exposing another scene's
                # verbatim asset early, including custom boundary markers.
                author_brief = brief
                spans = [_fixed_narration_source_span(brief, row["text_source"])
                         for row in frame.get("author_passages", ())]
                for start, end in sorted((span for span in spans if span is not None), reverse=True):
                    author_brief = f"{author_brief[:start]}〔原文由对应幕提供〕{author_brief[end:]}"
                state["author_brief"] = author_brief
            candidate["relationship_arc"]["stages"] = []
            candidate["character_state_arc"] = {"stages": [], "ending_stage": {}}
            state.update(candidate=candidate, stage="chapter", phase_candidate=None)
        state["story_frame"] = _frame_projection(frame)
        persist()

    frame = state["story_frame"]
    # Older checkpoints with verbatim entries did not retain their exact source
    # spans. Resume their original context instead of leaking future passages.
    if "author_brief" in state or not state.get("author_entry_narrations"):
        boundary_context["author_brief"] = state.get("author_brief", brief)
        boundary_rule += _AUTHOR_INTENT_RULE
    count = len(candidate["mainline_chapters"])

    def previous(index):
        if index == 0:
            return None
        return {"chapter": deepcopy(candidate["mainline_chapters"][index - 1]),
                "relationship_stage": deepcopy(candidate["relationship_arc"]["stages"][index - 1]),
                "character_state_stage": deepcopy(candidate["character_state_arc"]["stages"][index - 1])}

    def entry_originals(chapter):
        return {piece["id"]: piece["text"] for piece in chapter.get("fixed_narrations", ())
                if piece["trigger"]["type"] == "entry"}

    def earlier_originals(index):
        entries = {}
        for offset in range(min(index - 1, state["completed_chapters"])):
            originals = entry_originals(candidate["mainline_chapters"][offset])
            if originals:
                entries[offset + 1] = originals
        return entries

    def exit_results(chapter):
        exit_plan = chapter["exit_plan"]
        required = set(exit_plan.get("entry_prerequisite_fact_ids", ()))
        if exit_plan.get("advance_on_completion") is True:
            required.update(exit_plan["trigger_fact_ids"])
        targets = set(exit_plan["trigger_fact_ids"]) - required
        prerequisites = [fact for fact in chapter["completion_facts"] if fact["id"] in required]
        facts = [fact for fact in chapter["completion_facts"] if fact["id"] in targets]
        display_ids = {fact["fixed_narration_id"] for fact in [*prerequisites, *facts] if "fixed_narration_id" in fact}
        entries = entry_originals(chapter)
        originals = {piece_id: text for piece_id, text in entries.items() if piece_id in display_ids}
        other_originals = {piece_id: text for piece_id, text in entries.items() if piece_id not in display_ids}
        return {"title": chapter["title"], "entry_prerequisites": prerequisites, "completion_facts": facts,
                **({"completion_originals": originals} if originals else {}),
                **({"other_entry_originals": other_originals} if other_originals else {})}

    def schedule_completed_entry_repairs():
        completed = state["completed_chapters"]
        if not any(chapter["exit_plan"].get("fixed_entry_only") is True
                   for chapter in candidate["mainline_chapters"][:max(0, completed - 1)]):
            return
        # The ending writer cannot repair an already-written mainline exit.
        # Check only source/target pairs whose target chapter is available.
        issues = [issue for issue in outline_issues(candidate, pending_chapter_index=completed - 1)
                  if issue["code"] == "transition_fixed_entry_only_invalid"]
        if issues:
            state.update(stage="repair", repairs=sorted({_path_tokens(issue["path"])[1] for issue in issues}),
                         phase_candidate=None, issues=issues)

    if state["stage"] in {"chapter", "ending"} and "written_scene" not in state:
        schedule_completed_entry_repairs()

    while ((state["stage"] == "chapter" and state["completed_chapters"] < count)
           or (state["stage"] == "repair" and state["repairs"][0] < count)):
        repairing = state["stage"] == "repair"
        index = state["repairs"][0] if repairing else state["completed_chapters"]
        completed = state["completed_chapters"]
        repairs = set(state.get("repairs", ())) if repairing else set()
        committed_facts = sum(typed_completion_fact_count(chapter)
                              for i, chapter in enumerate(candidate["mainline_chapters"][:completed]) if i not in repairs)
        reserved = count - completed - (0 if repairing else 1) + sum(i < completed and i != index for i in repairs)
        fact_budget = min(8, max(0, NUMERIC_V2_MAX_FACT_KEYS - committed_facts - reserved))
        if repairing and state.get("phase_candidate") is None and "written_scene" not in state:
            state["phase_candidate"] = {"chapter": deepcopy(candidate["mainline_chapters"][index]),
                "relationship_stage": deepcopy(candidate["relationship_arc"]["stages"][index]),
                "character_state_stage": deepcopy(candidate["character_state_arc"]["stages"][index])}
        assigned = deepcopy(frame["mainline_chapters"][index])
        prior_issues = outline_issues(candidate) if repairing else []
        contract = {"chapter": {**_CONTRACT["mainline_chapters"][0],
                        "fixed_narrations": [{**_CONTRACT["mainline_chapters"][0]["fixed_narrations"][0],
                            "id": "本幕独立关键信息或NPC自主事件的唯一英文编号；没有此类内容才返回空数组",
                            "text": "实际公开的信息或NPC自主事件原文，不写任务说明，不含玩家新行为、决定或未谈妥的结果；与opening_scene不重复",
                            "trigger": {"type": "entry"}}],
                        "exit_plan": {**_CONTRACT["mainline_chapters"][0]["exit_plan"],
                            "entry_prerequisite_fact_ids": []},
                        "opening_scene": "首幕可使用作者初始设定；后续幕只写环境、NPC与当前角色，并承接已选出口的直接结果，不新增玩家手势、视线、站坐或持物变化，待玩家实施的动作留在turn"},
                    # The example is part of the model's input contract, so make its required subject explicit too.
                    "relationship_stage": {**_CONTRACT["relationship_arc"]["stages"][0],
                        "known_player_facts": [f"以{(names or {}).get('player_name', '男主')}开头，只列开场前已知或入路保证的事实，不把本幕turn目标中的玩家决定提前写成已知"],
                        "progress_opportunity": f"以{(names or {}).get('catgirl_name', '女主')}或环境开头，说明本幕提供的具体关系发展机会，不代表自动升温"},
                    "character_state_stage": {
                        key: value for key, value in _CONTRACT["character_state_arc"]["stages"][0].items()
                        if key not in ("catgirl_state", "player_state", "environment_state")}}
        contract["chapter"].pop("catgirl_situation", None)

        def validate_scene(draft, *, extracted=False):
            required = ("chapter", "relationship_stage", "character_state_stage")
            missing = [{"code": "expected_object", "path": key, "message": "必须返回当前幕的完整对象。"}
                       for key in required if not isinstance(draft.get(key), Mapping)]
            if missing:
                return missing
            proposed = deepcopy(candidate)
            proposed["mainline_chapters"][index] = draft["chapter"]
            for field, key in (("relationship_arc", "relationship_stage"), ("character_state_arc", "character_state_stage")):
                stages = proposed[field]["stages"]
                if index < len(stages):
                    stages[index] = deepcopy(draft[key])
                else:
                    stages.append(deepcopy(draft[key]))
            if not extracted:
                # Only the opening extractor owns time; stale writer output is replaced before publication.
                proposed["character_state_arc"]["stages"][index].pop("temporal_context", None)
            prefixes = (f"mainline_chapters[{index}]", f"relationship_arc.stages[{index}]",
                        f"character_state_arc.stages[{index}]")
            issues = [issue for issue in outline_issues(proposed,
                pending_state_index=None if extracted else index,
                pending_chapter_index=completed - 1 if repairing else index) if any(
                _belongs(issue["path"], prefix) for prefix in prefixes)
                or (repairing and issue not in prior_issues)]
            if not extracted:
                issues = _before_state_extraction(issues, f"character_state_arc.stages[{index}]")
                # The mandatory opening extractor supplies this derived field.
                # Do not invent a default or reject the writer before it runs;
                # the extracted scene still receives the complete validation.
                issues = [issue for issue in issues if not (
                    issue["code"] == "required_text"
                    and issue["path"] == f"mainline_chapters[{index}].catgirl_situation")]
            chapter = draft["chapter"]
            if typed_completion_fact_count(chapter) > fact_budget:
                issues.append({"code": "fact_contract_limit_exceeded", "path": f"mainline_chapters[{index}].completion_facts",
                               "message": f"本幕最多使用 {fact_budget} 个 typed 事实键。保留必要结果和硬前提，合并重复项并同步更新出口引用。"})
            exit_plan = chapter.get("exit_plan")
            facts = chapter.get("completion_facts")
            if isinstance(exit_plan, Mapping) and isinstance(facts, list):
                typed_ids = [fact["id"] for fact in facts if isinstance(fact, Mapping)
                    and isinstance(fact.get("id"), str) and "fixed_narration_id" not in fact]
                for issue in issues:
                    if (issue["code"] == "entry_prerequisite_invalid" and issue["path"] ==
                            f"mainline_chapters[{index}].exit_plan.entry_prerequisite_fact_ids"):
                        issue["message"] += "本幕可引用的typed事实ID：" + json.dumps(typed_ids, ensure_ascii=False) + \
                            "。保留必要且有效的引用；目标值有误则修值或类型，仅删除跨幕、展示项或重复引用。"
                selected = exit_plan.get("trigger_fact_ids", [])
                prerequisites = exit_plan.get("entry_prerequisite_fact_ids", [])
                if index == count - 1 and isinstance(selected, list) and isinstance(prerequisites, list):
                    performance_ids = {fact["id"] for fact in facts if isinstance(fact, Mapping)
                        and isinstance(fact.get("id"), str) and fact.get("evidence_source") == "performance"}
                    selected_ids = {key for key in selected if isinstance(key, str)}
                    prerequisite_ids = {key for key in prerequisites if isinstance(key, str)}
                    missing_prerequisites = (performance_ids & selected_ids) - prerequisite_ids
                    if missing_prerequisites:
                        issues.append({"code": "performance_exit_prerequisite_required",
                            "path": f"mainline_chapters[{index}].exit_plan.entry_prerequisite_fact_ids",
                            "message": "本出口选中的实际演出结果须先作为进入条件成立：" + "、".join(sorted(missing_prerequisites)) +
                                "。在entry_prerequisite_fact_ids引用这些已有事实，不新增事实或代写完成。"})
                    missing_completion = (performance_ids & prerequisite_ids) - selected_ids
                    if missing_completion:
                        issues.append({"code": "performance_exit_completion_required",
                            "path": f"mainline_chapters[{index}].exit_plan.trigger_fact_ids",
                            "message": "这些已选为结局硬前提的演出结果也须列入本幕完成条件："
                                + "、".join(sorted(missing_completion)) + "。请引用已有事实，不新增事实或代写完成。"})
            if draft["chapter"].get("title") != assigned["title"]:
                issues.append({"code": "assigned_title_changed", "path": "chapter.title",
                               "message": "沿用故事框架指定的当前幕标题，不重排剧情。"})
            return issues

        source_scene = previous(index)
        if source_scene is not None:
            # Entry snapshots describe the beginning of the previous scene,
            # not its exit. Send the relevant exit contracts instead.
            chapter = source_scene["chapter"]
            source_scene["chapter"] = {**exit_results(chapter), "exit_plan": chapter["exit_plan"]}
            source_scene["character_state_stage"] = {key: value for key, value in
                source_scene["character_state_stage"].items() if key not in (
                    "catgirl_state", "player_state", "environment_state",
                    "continuity_from_previous", "temporal_context", "scene_boundaries")}
        draft = resume_written_scene("chapter", terminal=index == count - 1)
        if draft is not None and typed_completion_fact_count(draft["chapter"]) > fact_budget:
            state.update(phase_candidate=state.pop("written_scene"))
            draft = None
        draft = draft or phase(_SCENE_PROMPT + (_FINAL_CHAPTER_CAUSALITY if index == count - 1 else "") + boundary_rule, contract, {
            "story_frame": frame, "chapter_index": index + 1,
            **boundary_context,
            "remaining_typed_fact_keys": fact_budget,
            "length": {"preset": preset, "scene_expected_turns_target": 8 if preset == "short" else None},
            "previous_scene": source_scene, "is_final_mainline": index == count - 1,
            "following_scene": previous(index + 2) if repairing and index + 1 < completed else None,
            "native_validation_issues": _unique_issues(issue for issue in state.get("native_issues", [])
                if _native_issue_node_index(issue, count) == index),
        }, validate_scene, "numeric_v2_chapter_generation", 5000, (
            (f"mainline_chapters[{index}]", "chapter"),
            (f"relationship_arc.stages[{index}]", "relationship_stage"),
            (f"character_state_arc.stages[{index}]", "character_state_stage"),
        ), scene_key="chapter", author_entries=state.get("author_entry_narrations", {}).get(str(index + 1), ()),
            earlier_entries=earlier_originals(index))
        draft = extract_states(draft, "chapter", "character_state_stage", lambda value: validate_scene(value, extracted=True), tuple(
            (f"character_state_arc.stages[{index}].{field}", field)
            for field in ("catgirl_state", "player_state", "environment_state", "temporal_context"))
            + ((f"mainline_chapters[{index}].catgirl_situation", "catgirl_situation"),),
            fixed_entry_only=source_scene is not None and source_scene["chapter"]["exit_plan"].get("fixed_entry_only") is True)
        candidate["mainline_chapters"][index] = deepcopy(draft["chapter"])
        for field, key in (("relationship_arc", "relationship_stage"), ("character_state_arc", "character_state_stage")):
            stages = candidate[field]["stages"]
            if index < len(stages):
                stages[index] = deepcopy(draft[key])
            else:
                stages.append(deepcopy(draft[key]))
        consume_issues(((f"mainline_chapters[{index}]", "chapter"),
                        (f"relationship_arc.stages[{index}]", "relationship_stage"),
                        (f"character_state_arc.stages[{index}]", "character_state_stage")), index)
        state.update(phase_candidate=None)
        if repairing:
            state["repairs"].pop(0)
            if not state["repairs"]:
                state["stage"] = ("chapter" if completed < count else
                                  "complete" if candidate["ending"].get("opening_scene") else "ending")
        else:
            state["completed_chapters"] = index + 1
            if index + 1 == count:
                state["stage"] = "ending"
            schedule_completed_entry_repairs()
        persist()

    if state["stage"] == "ending" or (state["stage"] == "repair" and state["repairs"] == [count]):
        if state["stage"] == "repair" and state.get("phase_candidate") is None and "written_scene" not in state:
            state["phase_candidate"] = {"ending": deepcopy(candidate["ending"]),
                "ending_stage": deepcopy(candidate["character_state_arc"]["ending_stage"])}
        def validate_ending(draft, *, extracted=False):
            if not isinstance(draft.get("ending"), Mapping) or not isinstance(draft.get("ending_stage"), Mapping):
                return [{"code": "expected_object", "path": "ending", "message": "必须返回 ending 和 ending_stage 对象。"}]
            proposed = deepcopy(candidate)
            proposed["ending"] = draft["ending"]
            proposed["character_state_arc"]["ending_stage"] = deepcopy(draft["ending_stage"])
            if not extracted:
                proposed["character_state_arc"]["ending_stage"].pop("temporal_context", None)
            issues = outline_issues(proposed, pending_state_index=None if extracted else count,
                pending_chapter_index=count)
            for field in ("type", "title"):
                if draft["ending"].get(field) != frame["ending"][field]:
                    issues.append({"code": "assigned_ending_identity_changed", "path": f"ending.{field}",
                                   "message": "沿用 ending_identity 指定的类型和标题。"})
            return issues if extracted else _before_state_extraction(issues, "character_state_arc.ending_stage")
        ending_source = previous(count)
        last_chapter = ending_source["chapter"]
        # A proposed next activity is not a completed fact for the input-closed ending.
        ending_source["chapter"] = exit_results(last_chapter)
        # No entry-time physical state proves where or how the player's final choice ended.
        ending_source["character_state_stage"] = {key: value for key, value in
            ending_source["character_state_stage"].items()
            if key not in ("catgirl_state", "player_state", "environment_state", "continuity_from_previous", "temporal_context", "scene_boundaries")}
        draft = resume_written_scene("ending") or phase(_ENDING_PROMPT + boundary_rule,
            {"ending": {**_CONTRACT["ending"],
                "summary": "概括各合法入路共同保证的结果和当前角色对此的回应；不替泛指共识补出具体方案、许可或物品去向",
                "opening_scene": "在当前位置以当前角色的简短反应收束；未保证的物品归属、交接和答复省略，不写玩家新行动",
                "entry_bridge": "",
            }, "ending_stage": {
                **{key: value for key, value in _CONTRACT["character_state_arc"]["ending_stage"].items()
                   if key not in ("catgirl_state", "player_state", "environment_state")},
                "continuity_from_previous": ["用自然语言重述本次入路保证的结果，不填写ID，不新增处置、物品归属或许可"],
            }},
            {"story_frame": {key: frame[key] for key in
                ("world", "story_protagonist", "player_role", "tone")},
             "ending_identity": frame["ending"],
             **boundary_context,
             "previous_scene": ending_source,
             "native_validation_issues": _unique_issues(issue for issue in state.get("native_issues", [])
                 if _native_issue_node_index(issue, count) == count)},
            validate_ending, "numeric_v2_ending_generation", 3000,
            (("ending", "ending"), ("character_state_arc.ending_stage", "ending_stage")),
            scene_key="ending", author_entries=state.get("author_entry_narrations", {}).get(str(count + 1), ()),
            earlier_entries=earlier_originals(count))
        draft = extract_states(draft, "ending", "ending_stage", lambda value: validate_ending(value, extracted=True), tuple(
            (f"character_state_arc.ending_stage.{field}", field)
            for field in ("catgirl_state", "player_state", "environment_state", "temporal_context")),
            fixed_entry_only=last_chapter["exit_plan"].get("fixed_entry_only") is True)
        candidate["ending"] = deepcopy(draft["ending"])
        candidate["character_state_arc"]["ending_stage"] = deepcopy(draft["ending_stage"])
        consume_issues((("ending", "ending"), ("character_state_arc.ending_stage", "ending_stage")), count)
        state.update(stage="complete", phase_candidate=None)
        state.pop("repairs", None)
        persist()

    return generator._generation_result(title=title, normalized_idea=brief, normalized_setup=setup,
        candidate=candidate, cast_names=names)
