"""Generate a fixed story frame, then commit one author scene at a time."""
from __future__ import annotations

from config.prompts.prompts_theater_pipeline import _FRAME_PROMPT, _STATE_PROMPT

from copy import deepcopy
import json
from typing import Mapping

from ..model import LLMCallFailure
from .numeric_v2 import (
    NumericV2GenerationError, _AUTHORING_RUNTIME_RULES, _LENGTH_PRESETS,
    _MAINLINE_OUTPUT_CONTRACT, _validate_idea_outline,
)

FLOW = "staged.v1"
_CONTRACT = json.loads(_MAINLINE_OUTPUT_CONTRACT)
_FRAME_CONTRACT = {
    **{key: deepcopy(_CONTRACT[key]) for key in (
        "world", "story_protagonist", "player_role", "relationship", "tone", "key_props",
    )},
    "relationship_arc": {key: value for key, value in _CONTRACT["relationship_arc"].items() if key != "stages"},
    "mainline_chapters": [{"title": "本幕标题", "narrative": "约100—150字的简短规划：时空起点、独有互动、关键线索及来源、可改变的安排、有限结果与下一安排；不展开完整正文或分支，不拆碎同一工作或重做前幕"}],
    "ending": {**{key: _CONTRACT["ending"][key] for key in ("type", "title")},
               "summary": "各合法选择共同成立的收束，不用若/如果分别编写互斥结局，不固定可变处置或许可"},
}
_SCENE_PROMPT = _AUTHORING_RUNTIME_RULES + """
你是当前一幕的编剧。本轮只返回 output_contract 中的 chapter、relationship_stage、character_state_stage。
story_frame 是已确定的策划；previous_scene 只提供前一幕出口合同与权限，所选 completion_facts 是前幕合法离开时保证的结果，不是本幕要重新完成的目标。
按照 assigned_chapter 的标题和核心因果写当前幕，不能重排、增删或重写其它幕。chapter_index 使用当前指定序号。
只展开 assigned_chapter 独有的核心互动；前幕结果不重做，后幕事件不提前塞入本幕。开场从前幕出口的结果继续，不能复制故事初始场景或前幕开场。已经结束的交谈、已作出的决定和已完成工作保持完成，确有新证据才产生新的问题。
narrative须把本幕框架展开为可演的具体因果，不照抄提纲。本幕负责锁定后幕人物或地点时，写明线索的具体内容、由谁在当前地点怎样交付及其指向的明确对象，不能只写“获得地址或身份线索”让演出另编对象。
narrative_focus用一句话点明实际待交付的事件或信息及回应后的出口，不只写建立合作、体现性格或埋下悬念。谨慎态度不扩成新手续；检查须有具体对象、已知风险和有限结果，不能靠反复确认绝对安全收束。
核对前幕出口、当前 entry_bridge、opening_scene 和开场演完后的三方状态：时段、地点、持物、立场和未决选择必须相容。
前幕没有安排等候或改日时，entry_bridge不自行跨日；同日的自然经过仍须与开场时段相容。
前幕narrative和目标是计划，不证明配角已经答复或同意。entry_bridge只承接来源出口与接受输入共同保证的移动，不扩大行动范围。出口只决定接触或尝试时，开场停在结果尚未发生的边界，所需配角接待、许可及结果后的姿态留在本幕turn中实际交付，不能凭本幕规划预先跨过。保留未决状态，不用“无论选择哪种”混合互斥结果。
opening_scene 与 entry_bridge 不新增玩家行动、心理、台词或承诺；第一幕 entry_bridge 为空。桥段不重复开场。
entry_bridge只写这次获准安排的时空经过与既有结果；本幕首次出现的新危机、发现和角色反应写在opening_scene，不让演员在两段重复同一事件。
开场在需要玩家回应或操作的位置停笔：女主提出请求后，不能接着写玩家已经照做，也不能替玩家接话；这些内容留在 turn 目标。先写开场，再摘录收笔时的三方状态，不能第二遍设计场面；只保留开场或合法入路明确支持的姿态、视线、位置与持物，未明确的细节省略，不为填满状态补出动作或新道具，也不补玩家的心理、期待或决定。递出待接不等于已交付，已完成交接不仍写原持有人持物；开场确有后续动作时按最后状态摘录。
每幕给出1—6项 ordered_goals，完整使用合同枚举。sources 只允许 opening、player_input、previous_goal；previous_goal 只指本幕前一个目标，不是前幕，首个目标不能引用它。player 目标 sources 包含 player_input；opening 最多一项。
一次询问与对方首次明确答复属于同一幕；答复可以拒绝或说明未知，但不能把报价、许可等核心答复拖到下一幕才交付。下一幕从新的互动开始，不重复收回物品或回答旧问题。
owner与delivery_type必须配对：catgirl对应catgirl_dialogue/catgirl_action，environment对应environment_fact，player对应player_action，shared对应shared_agreement；semantic_state可对应catgirl/player/shared。玩家与shared目标的timing均为turn。
dialogue_policy_after通常为unchanged；只有睡眠、昏迷、禁言或恢复发声等真实权限变化才填写其它值，不用它表示暂时倾听或下一句想说话。
completion_facts 是本幕独立可观察的结果；Runtime的completion_contract只由exit_plan.trigger_fact_ids引用的事实组成，目标列表和preserve_facts不会保证结果发生。下一幕确实依赖的本幕交付或配角答复须在本幕可见演出中完成，并由trigger_fact_ids引用；决定去商量不等于已获对方同意。只纳入必要结果，不把可选准备、接受换幕或下一幕结果当本幕完成。
角色、NPC或环境必须实际完成的结果使用evidence_source=performance；末幕通向自然结局时，被trigger_fact_ids选中的同类项必须列入entry_prerequisite_fact_ids，保证先有已演原文再结束。普通出口只把确实不可跳过的许可或交付列为入口前提，不把所有performance完成项一概变成硬锁。玩家自己的选择和实施仍使用原有来源，不能以命令、请求或准备代替他人已完成。
包含玩家参与的立场、选择或双方约定不设performance来源；只登记本幕必要的实质选择，不预定玩家必须反对或赞成。接受exit_plan的未来邀约不作为本幕completion_fact或进入前提，避免必须先完成换幕接受才能提出换幕邀请的循环。
玩家完成项描述实际表达的选择及允许的不同回应，不把查看手机、喝完饮料、必须留下等可选动作或预定立场作为唯一离幕条件。自主可见信息由环境或NPC交付，不要求玩家先猜中作者安排的操作。
前往或抵达下一幕地点属于本次换幕，写在proposal和目标entry_bridge，不列为本幕completion_fact、入口前提或普通turn目标；本幕的独立互动完成后先公开邀请，得到玩家授权再移动。当前场内必要移动不受此限制。
completion_facts逐项按实际主体选择来源：输出合同的普通示例不带evidence_source；仅他人或环境的实际结果额外填写performance，不能给所有条目照抄同一种来源。涉及后幕执行的分工写清谁做什么；若执行者由玩家选择，后幕保留该分工可变，不在固定开场或目标中重新指定执行者。
非最后主线幕的 fallback_offer 和 accept_input 必须是对应同一具体安排的完整邀请与接受输入；最后主线幕两项为空，自然进入结局。最后一幕也完整返回 exit_plan；preserve_facts、carry_props 等数组没有条目时返回[]，不省略字段或用空对象代替。
出口的 proposal、fallback_offer、accept_input 明说下一阶段的活动与必要时空，不仅是赞成当前方案或交接当前工作；与 next_outline 的起点逐项核对。同地开始新互动时桥段可为空，不复用以前已经走过的路程。正文中的当前时间、剩余时长与故事统一截止时间相容。
只有按本幕目标顺序最终仍 forbidden 时才可省略口头邀请，并填写 silent_choice 的具体去向和待接受安排。
关系与三方状态使用已有合同，不因换幕重置；沿用已知名字、持物和未决分歧，关系上限相邻最多变化一级。known_player_facts、allowed_behaviors、forbidden_behaviors各最多4项；known_player_facts 每条以玩家全名开头。catgirl_situation只写开场时女主已有的顾虑或立场，不重复位置、持物，不提前认定本幕目标已发生。progress_opportunity以女主全名或“环境”开头，写清什么实际互动可引起怎样的有限态度变化；玩家拒绝或条件未成立时保留分歧，不预写回应或自动升温。第一幕address_state不能使用known_from_prior_scene。scene_boundaries 每条按当前NEKO合同以“不得”“禁止”或“不能”开头，正向状态写入三方state，不转成禁令。
短篇 expected_turns 不超过8，合并重复事件，不删核心选择。本幕必须公开且不依赖玩家选择的关键证据、读数、来信或自主开场事件，使用entry固定旁白直接交付，并可用fixed_narration_id表示实际展示；不能只写“有数据/发现线索”让演员另编内容。依赖玩家行动、谈判或尚未知晓的结果仍留在turn，不冻结普通对话、玩家决定或整幕。没有这类独立信息时fixed_narrations写[]。
本模式主动为上述独立交付编写作者原文，不要求brief另外指定逐字文本。opening_scene建立环境和起点，entry原文交付实际信息或NPC自主事件，两处不重演同一动作；女主的自由回应留给演员。原文不包含玩家新动作、心理、选择或承诺，不把待谈判的结果预先固定。
本幕核心回应足以形成下一步时，可选准备不写入ordered_goals或完成事实。普通操作只承担眼前互动所需的有限结果，不因“做好准备”扩成格式、参数、备份或额外认证链；需要等候的配角须实际回复可用时段，不能把询问或拨号写成已经同意。
文字要简洁：三方状态、本幕处境、关系与道具会共同组装到384-token字段，不能每项各写成长段。
issues.path 是当前输出中的修订位置，source_path 是整稿定位。native_validation_issues 是本幕正式编译错误；只修当前返回对象，保留其它正文与事件，不修改已完成前幕、后幕或故事框架。只返回 JSON object。"""
_ENDING_PROMPT = _AUTHORING_RUNTIME_RULES + """
本轮只生成结局 ending 与 ending_stage，使用 output_contract。previous_scene.chapter.completion_facts只含末幕离幕合同保证的结果，据此收束，不补做后续计划。
previous_scene.character_state_stage只保留权限与边界，不是出幕状态。结局聚焦已保证结果的女主反应，不从前幕入幕推定地点、陈设、姿态或持物，也不另安排换场。
交付、许可或活动尚未实际完成时，结局保留当前准备状态，不安排新互动、玩家动作或跨时段后果。
同场自然收束不需要换场旁白，ending.entry_bridge返回空字符串，不为填满字段写编写说明。
结局没有普通回合，不留待玩家回答的新问题或新决定，不借收束替玩家承诺、离开或执行附加动作。
结局旁白已完整交付结果、没有必要的新回应时，dialogue_policy使用optional；不为收束强求台词或动作，确有必须交付的角色回应才用required。
结局开场、摘要与三方状态是同一时点；状态摘录开场收笔时已有依据的姿态与持物，未明确的细节省略，不另设计画面或新道具。承接人物改变的原因，保留合法分歧，不复制最后一幕全文。
只固定各合法选择共同保证的结果；处置方式、道具归属或去向取决于玩家选择时，不在静态结局擅自选定一种。
ending_stage 不带 chapter_index，continuity_from_previous 不能为空。各项简洁，组合字段上限384 tokens。
catgirl_state、player_state分别以cast_names.catgirl_name和cast_names.player_name的完整姓名开头，environment_state以“环境”开头；没有姓名时才用“女主”“男主”。
scene_boundaries 只放以“不得”“禁止”或“不能”开头的负向边界，正向描述放在对应状态字段。
output_contract中的字符串是字段说明，返回时须换成实际正文；entry_bridge保持空字符串，不返回编写规则。
若有 issues，修正当前结局，已完成主线与框架保持。只返回 JSON object。"""


def _belongs(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith((prefix + ".", prefix + "["))


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


def _merge_frame_repair(candidate, frame, native_issues):
    # Native paths name compiled fields; only their author inputs may change.
    sources = {
        "intro.background": (("world", "background"),),
        "intro.player_identity": (("player_role", "identity"),),
        "intro.catgirl_identity": (("story_protagonist", "identity"), ("story_protagonist", "secret_or_wound")),
        "catgirl_binding.role_overlay": (("story_protagonist", "motivation"),),
    }
    preserved = _frame_projection(candidate)
    for issue in native_issues:
        for group, key in sources.get(issue.get("path"), ()):
            value = frame.get(group)
            preserved[group][key] = deepcopy(value.get(key)) if isinstance(value, Mapping) else None
    return preserved


def generate_staged(generator, *, title, setup, checkpoint=None, cast_names=None, save_checkpoint=None):
    """Keep completed scenes in the checkpoint; retries only replace the current phase."""
    brief = str(setup.get("brief") or "").strip()
    if len("".join(brief.split())) <= 30:
        raise NumericV2GenerationError("mainline_generation_input_too_short")
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
    if state["stage"] == "complete" and state.get("native_issues") and state.get("issues"):
        # Resume checkpoints saved before global native failures had an executable repair phase.
        state["stage"] = "frame"
    names = state.get("cast_names")
    if names is not None and (
        not isinstance(names, Mapping)
        or any(not isinstance(names.get(key), str) or not names[key].strip()
               or names[key] != names[key].strip() for key in ("player_name", "catgirl_name"))
        or names["player_name"] == names["catgirl_name"]
    ):
        raise NumericV2GenerationError("invalid_cast_names")
    attempts = 0

    def outline_issues(candidate, *, pending_state_index=None, pending_chapter_index=None):
        return _validate_idea_outline(candidate, minimum=minimum, maximum=maximum,
            scene_expected_turns_target=8 if preset == "short" else None,
            cast_names=names, token_counter=generator._token_counter,
            pending_state_index=pending_state_index,
            pending_chapter_index=pending_chapter_index)

    def persist():
        if save_checkpoint is not None:
            save_checkpoint(deepcopy(state))

    def phase(system, contract, context, validate, operation, max_tokens, locations=()):
        nonlocal attempts
        draft = state.get("phase_candidate")
        repairing_frame = operation == "numeric_v2_story_frame" and state["completed_chapters"] > 0
        if repairing_frame and isinstance(draft, Mapping):
            draft = _merge_frame_repair(state["candidate"], draft, state.get("native_issues", []))
        issues = validate(draft) if isinstance(draft, Mapping) else []
        # Structural validation cannot reproduce all saved author/review diagnostics.
        if not issues:
            issues = deepcopy(state.get("issues") or [])
        last_failure = None
        for _ in range(3):
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({
                    **context, "cast_names": names, "output_contract": contract,
                    "current_candidate": draft,
                    # Validator paths refer to the assembled outline; the model edits a phase response.
                    "issues": [{**issue, "source_path": issue["path"], "path": next(
                        (target + issue["path"][len(source):] for source, target in locations
                         if _belongs(issue["path"], source)), issue["path"])} for issue in issues],
                }, ensure_ascii=False)},
            ]
            attempts += 1
            try:
                response = generator.call_llm(messages, temperature=0.25, max_tokens=max_tokens,
                    max_retries=1, response_format={"type": "json_object"},
                    thinking={"type": "disabled"}, operation=operation)
            except Exception as error:
                # Preserve host refusals (budget, revision, storage) as their original public errors.
                error.checkpoint = deepcopy(state)
                raise
            if isinstance(response, LLMCallFailure):
                last_failure = response
                continue
            parsed = generator.parse_json_response(response) if isinstance(response, str) else None
            if not isinstance(parsed, Mapping) or parsed.get("parse_error"):
                continue
            draft = deepcopy(dict(parsed))
            if repairing_frame:
                draft = _merge_frame_repair(state["candidate"], draft, state.get("native_issues", []))
            state["phase_candidate"] = draft
            issues = validate(draft)
            state["issues"] = issues
            if not issues:
                return draft
        raise NumericV2GenerationError(
            last_failure.error_code if last_failure is not None else "invalid_mainline_generation",
            checkpoint=deepcopy(state), issues=issues, attempts=attempts,
            provider_details=last_failure.diagnostic() if last_failure is not None else None,
        )

    def extract_states(draft, scene_key, state_key, validate, locations):
        # Persist the written scene before the short call; a failed extraction resumes here.
        if "written_scene" not in state:
            state.update(written_scene=deepcopy(draft), phase_candidate=None, issues=[])
            persist()
        fields = ("catgirl_state", "player_state", "environment_state")
        entry_narrations = [item["text"] for item in draft[scene_key].get("fixed_narrations", [])
            if item["trigger"]["type"] == "entry"]
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
                if (anchor not in draft[scene_key]["opening_scene"]
                        and anchor not in draft[scene_key].get("entry_bridge", "")
                        and not any(anchor in text for text in entry_narrations)):
                    issues.append({"code": "temporal_anchor_without_source", "path": "temporal_context.anchor",
                                   "message": "时间锚须逐字来自本次开场、入幕原文或桥段，不推断日期。"})
            return issues

        excerpt = phase(_STATE_PROMPT, {
            "catgirl_state": f"以{(names or {}).get('catgirl_name', '女主')}开头的开场收笔状态",
            "player_state": f"以{(names or {}).get('player_name', '男主')}开头的开场收笔状态",
            "environment_state": "以环境开头的开场收笔状态",
            **({"catgirl_situation": "开场明确的女主当前顾虑、立场或未决问题；未交代时说明未明确，不补剧情"} if scene_key == "chapter" else {}),
            "temporal_context": {"anchor": "有依据时逐字摘录当前时间短语，未交代则省略整个temporal_context",
                                 "relation_to_previous": "unspecified | same_day | next_day | after_interval"},
        }, {"opening_scene": draft[scene_key]["opening_scene"],
            **({"entry_narrations": entry_narrations} if entry_narrations else {}),
            "entry_bridge": draft[scene_key].get("entry_bridge", "")}, validate_excerpt,
            "numeric_v2_opening_state_extraction", 600, locations)
        result = apply_excerpt(excerpt)
        state.pop("written_scene")
        return result

    candidate = state["candidate"]
    if state["stage"] == "frame":
        if state["completed_chapters"] and state.get("phase_candidate") is None:
            state["phase_candidate"] = _frame_projection(candidate)
        def validate_frame(frame):
            prefixes = ["world", "story_protagonist", "player_role", "relationship", "tone",
                        "key_props", "relationship_arc.opening_relationship",
                        "relationship_arc.long_term_direction", "ending.type", "ending.title", "ending.summary"]
            chapters = frame.get("mainline_chapters")
            issues = outline_issues(frame)
            return [issue for issue in issues if any(
                _belongs(issue["path"], prefix) for prefix in prefixes)
                or issue["path"] == "mainline_chapters"
                or (isinstance(chapters, list) and any(
                    issue["path"] in (f"mainline_chapters[{index}].title", f"mainline_chapters[{index}].narrative")
                    for index in range(len(chapters))))]
        frame = phase(_FRAME_PROMPT, _FRAME_CONTRACT,
            {"core_idea": brief, "author_setup": dict(setup),
             "native_validation_issues": state.get("native_issues", []),
             "length": {"preset": preset, "minimum": minimum, "maximum": maximum}},
            validate_frame, "numeric_v2_story_frame", 5000)
        if state["completed_chapters"]:
            # Native errors outside nodes repair the frame, without replacing completed scenes or ending.
            for key in _FRAME_CONTRACT.keys() - {"mainline_chapters", "ending", "relationship_arc"}:
                candidate[key] = deepcopy(frame[key])
            candidate["relationship_arc"].update({key: deepcopy(value)
                for key, value in frame["relationship_arc"].items() if key != "stages"})
            state.update(stage="repair" if state.get("repairs") else "complete", phase_candidate=None, issues=[])
        else:
            candidate = deepcopy(frame)
            candidate["relationship_arc"]["stages"] = []
            candidate["character_state_arc"] = {"stages": [], "ending_stage": {}}
            state.update(candidate=candidate, stage="chapter", phase_candidate=None, issues=[])
        persist()

    frame = _frame_projection(candidate)
    count = len(candidate["mainline_chapters"])

    def previous(index):
        if index == 0:
            return None
        return {"chapter": deepcopy(candidate["mainline_chapters"][index - 1]),
                "relationship_stage": deepcopy(candidate["relationship_arc"]["stages"][index - 1]),
                "character_state_stage": deepcopy(candidate["character_state_arc"]["stages"][index - 1])}

    while ((state["stage"] == "chapter" and state["completed_chapters"] < count)
           or (state["stage"] == "repair" and state["repairs"][0] < count)):
        repairing = state["stage"] == "repair"
        index = state["repairs"][0] if repairing else state["completed_chapters"]
        if repairing and state.get("phase_candidate") is None and "written_scene" not in state:
            state["phase_candidate"] = {"chapter": deepcopy(candidate["mainline_chapters"][index]),
                "relationship_stage": deepcopy(candidate["relationship_arc"]["stages"][index]),
                "character_state_stage": deepcopy(candidate["character_state_arc"]["stages"][index])}
        assigned = deepcopy(frame["mainline_chapters"][index])
        prior_issues = outline_issues(candidate) if repairing else []
        contract = {"chapter": {**_CONTRACT["mainline_chapters"][0],
                        "completion_facts": [{key: value for key, value in
                            _CONTRACT["mainline_chapters"][0]["completion_facts"][0].items()
                            if key not in ("evidence_source", "fixed_narration_id")}],
                        "fixed_narrations": [{**_CONTRACT["mainline_chapters"][0]["fixed_narrations"][0],
                            "id": "本幕独立关键信息或NPC自主事件的唯一英文编号；没有此类内容才返回空数组",
                            "text": "实际公开的信息或NPC自主事件原文，不写任务说明，不含玩家新行为、决定或未谈妥的结果；与opening_scene不重复",
                            "trigger": {"type": "entry"}}],
                        "exit_plan": {**_CONTRACT["mainline_chapters"][0]["exit_plan"],
                            "entry_prerequisite_fact_ids": []},
                        "opening_scene": "首幕可使用作者初始设定；后续幕只写环境、NPC与女主，并承接已选出口的直接结果，不新增玩家手势、视线、站坐或持物变化，待玩家实施的动作留在turn"},
                    # The example is part of the model's input contract, so make its required subject explicit too.
                    "relationship_stage": {**_CONTRACT["relationship_arc"]["stages"][0],
                        "known_player_facts": [f"以{(names or {}).get('player_name', '男主')}开头，只列开场前已知或入路保证的事实，不把本幕turn目标中的玩家决定提前写成已知"],
                        "progress_opportunity": f"以{(names or {}).get('catgirl_name', '女主')}或环境开头，说明本幕提供的具体关系发展机会，不代表自动升温"},
                    "character_state_stage": {**_CONTRACT["character_state_arc"]["stages"][0],
                        "catgirl_state": f"以{(names or {}).get('catgirl_name', '女主')}开头，摘录opening_scene收笔时的明确状态；未写的手势、视线和持物省略，不另设计姿态，不填动作前状态",
                        "player_state": f"以{(names or {}).get('player_name', '男主')}开头，摘录opening_scene收笔时已有依据的状态；不新增手势、持物、玩家动作、台词或选择",
                        "environment_state": "以环境开头，地点、时段和物件位置与opening_scene演完后的三方状态相容"}}

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
                pending_chapter_index=index) if any(
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
            exit_plan = chapter.get("exit_plan")
            facts = chapter.get("completion_facts")
            if isinstance(exit_plan, Mapping) and isinstance(facts, list):
                typed_ids = [fact["id"] for fact in facts if isinstance(fact, Mapping)
                    and isinstance(fact.get("id"), str) and "fixed_narration_id" not in fact]
                for issue in issues:
                    if (issue["code"] == "entry_prerequisite_invalid" and issue["path"] ==
                            f"mainline_chapters[{index}].exit_plan.entry_prerequisite_fact_ids"):
                        issue["message"] += "本幕可引用的typed事实ID：" + json.dumps(typed_ids, ensure_ascii=False) + \
                            "。只修当前引用，删除跨幕、展示项或重复引用，不修改已完成幕。"
                selected = exit_plan.get("trigger_fact_ids", [])
                prerequisites = exit_plan.get("entry_prerequisite_fact_ids", [])
                if index == count - 1 and isinstance(selected, list) and isinstance(prerequisites, list):
                    required = {fact["id"] for fact in facts if isinstance(fact, Mapping)
                        and isinstance(fact.get("id"), str) and fact["id"] in selected
                        and fact.get("evidence_source") == "performance"}
                    missing = required - {key for key in prerequisites if isinstance(key, str)}
                    if missing:
                        issues.append({"code": "performance_exit_prerequisite_required",
                            "path": f"mainline_chapters[{index}].exit_plan.entry_prerequisite_fact_ids",
                            "message": "本出口选中的实际演出结果须先作为进入条件成立：" + "、".join(sorted(missing)) +
                                "。在entry_prerequisite_fact_ids引用这些已有事实，不新增事实或代写完成。"})
            if draft["chapter"].get("title") != assigned["title"]:
                issues.append({"code": "assigned_title_changed", "path": "chapter.title",
                               "message": "沿用故事框架指定的当前幕标题，不重排剧情。"})
            return issues

        source_scene = previous(index)
        if source_scene is not None:
            # Entry snapshots describe the beginning of the previous scene,
            # not its exit. Only the selected results can seed this opening.
            chapter = source_scene["chapter"]
            source_scene["chapter"] = {
                "title": chapter["title"],
                "completion_facts": [fact for fact in chapter["completion_facts"]
                    if fact["id"] in chapter["exit_plan"]["trigger_fact_ids"]],
                "exit_plan": chapter["exit_plan"],
            }
            source_scene["character_state_stage"] = {key: value for key, value in
                source_scene["character_state_stage"].items() if key not in (
                    "catgirl_state", "player_state", "environment_state",
                    "continuity_from_previous", "temporal_context")}
        draft = deepcopy(state["written_scene"]) if "written_scene" in state else phase(_SCENE_PROMPT, contract, {
            "story_frame": frame, "assigned_chapter": assigned, "chapter_index": index + 1,
            "previous_scene": source_scene, "is_final_mainline": index == count - 1,
            "next_outline": frame["mainline_chapters"][index + 1] if index + 1 < count else frame["ending"],
            "following_scene": previous(index + 2) if repairing and index + 1 < count else None,
            "native_validation_issues": [issue for issue in state.get("native_issues", [])
                if _belongs(issue.get("path", ""), f"nodes[{index}]")],
        }, validate_scene, "numeric_v2_chapter_generation", 5000, (
            (f"mainline_chapters[{index}]", "chapter"),
            (f"relationship_arc.stages[{index}]", "relationship_stage"),
            (f"character_state_arc.stages[{index}]", "character_state_stage"),
        ))
        draft = extract_states(draft, "chapter", "character_state_stage", lambda value: validate_scene(value, extracted=True), tuple(
            (f"character_state_arc.stages[{index}].{field}", field)
            for field in ("catgirl_state", "player_state", "environment_state", "temporal_context"))
            + ((f"mainline_chapters[{index}].catgirl_situation", "catgirl_situation"),))
        candidate["mainline_chapters"][index] = deepcopy(draft["chapter"])
        for field, key in (("relationship_arc", "relationship_stage"), ("character_state_arc", "character_state_stage")):
            stages = candidate[field]["stages"]
            if index < len(stages):
                stages[index] = deepcopy(draft[key])
            else:
                stages.append(deepcopy(draft[key]))
        state.update(phase_candidate=None, issues=[])
        if repairing:
            state["repairs"].pop(0)
            if not state["repairs"]:
                state["stage"] = "complete"
        else:
            state["completed_chapters"] = index + 1
        if not repairing and index + 1 == count:
            state["stage"] = "ending"
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
            return issues if extracted else _before_state_extraction(issues, "character_state_arc.ending_stage")
        ending_source = previous(count)
        last_chapter = ending_source["chapter"]
        # A proposed next activity is not a completed fact for the input-closed ending.
        ending_source["chapter"] = {"title": last_chapter["title"], "completion_facts": [
            fact for fact in last_chapter["completion_facts"]
            if fact["id"] in last_chapter["exit_plan"]["trigger_fact_ids"]]}
        # No entry-time physical state proves where or how the player's final choice ended.
        ending_source["character_state_stage"] = {key: value for key, value in
            ending_source["character_state_stage"].items()
            if key not in ("catgirl_state", "player_state", "environment_state", "continuity_from_previous", "temporal_context")}
        draft = deepcopy(state["written_scene"]) if "written_scene" in state else phase(_ENDING_PROMPT,
            {"ending": {**_CONTRACT["ending"],
                "summary": "只概括completion_facts已明确的结果和女主对此的回应；不替泛指共识补出具体方案、许可或物品去向",
                "opening_scene": "在当前位置以女主的简短反应收束；完成事实未明确的物品归属、交接和答复省略，不写玩家新行动",
                "entry_bridge": "",
            }, "ending_stage": {
                **_CONTRACT["character_state_arc"]["ending_stage"],
                "catgirl_state": "摘录本次ending.opening_scene收笔时女主的明确状态，不复制前幕初始姿态或持物",
                "player_state": "摘录本次结局开场已明确的玩家状态，不补心理、期待或新动作",
                "environment_state": "摘录本次结局开场的地点、时段和物件位置；物品不在场就不再描写触碰或注视它",
                "continuity_from_previous": ["用自然语言重述本次completion_facts保证的结果，不填写ID，不新增处置、物品归属或许可"],
            }},
            {"story_frame": {key: frame[key] for key in
                ("world", "story_protagonist", "player_role", "tone")},
             "ending_identity": {key: frame["ending"][key] for key in ("type", "title")},
             "previous_scene": ending_source,
             "native_validation_issues": [issue for issue in state.get("native_issues", [])
                 if _belongs(issue.get("path", ""), f"nodes[{count}]")]},
            validate_ending, "numeric_v2_ending_generation", 3000,
            (("character_state_arc.ending_stage", "ending_stage"),))
        draft = extract_states(draft, "ending", "ending_stage", lambda value: validate_ending(value, extracted=True), tuple(
            (f"character_state_arc.ending_stage.{field}", field)
            for field in ("catgirl_state", "player_state", "environment_state", "temporal_context")))
        candidate["ending"] = deepcopy(draft["ending"])
        candidate["character_state_arc"]["ending_stage"] = deepcopy(draft["ending_stage"])
        state.update(stage="complete", phase_candidate=None, issues=[])
        state.pop("repairs", None)
        persist()

    return generator._generation_result(title=title, normalized_idea=brief, normalized_setup=setup,
        candidate=candidate, cast_names=names)
