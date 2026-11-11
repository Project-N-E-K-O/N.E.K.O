"""Provide independent factual-check prompts and citation validation without rewriting or literary scoring."""

from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from typing import Any, Mapping

from .runtime_rules import ENTRY_STATE_TIMING_RULE, GOAL_METADATA_RULE

FACT_RUNTIME_RULES = GOAL_METADATA_RULE + "\n" + ENTRY_STATE_TIMING_RULE + """
本轮是静态作者稿审核，不是运行时事件登记。summary、goals、source_ids、道具台账和作者状态都是规划或资料，不能自证玩家已行动或结果已成立。程序不以逐项完成 goals 或枚举 source_ids 作为换幕条件。goal.owner/output_field说明行动归属，演出角色权限不授权玩家的新台词、操作或决定。runtime.player_input是动态来源标记，不存在可预填的真实玩家回答。
普通出幕需玩家接受已经公开的同一安排，或主动发起同一公开安排；拒绝、追问、暂缓可以留幕，不新增分支或确认锁。自动出幕只依据原completion_contract及route conditions，不把可选goals变门槛；all需全满足，any不能当全部成立。自然结局仍需核心结果及必要回应，不接新的待答问题。未受保证的来源历史不能在桥段自证；接受后正常移动、角色自主展示与环境当时变化可以首次发生，不要求预先登记。展示不等于玩家体验、理解或接受，递出待接不等于实际签收。
来源禁令按主体、条件和阶段解释；本幕仅讨论的禁令不套到已获准目标幕，也不解除持续禁令。不重演已交付原文或倒退不可逆结果。未知不补成确定事实，缺少重述不等于冲突；有认识来源的NPC答复合法。不能据单手姿态、佩戴、观察猜持有或权限。key_props.states是生命周期规划而非统一的开场既成状态。不因配角多少、关系升温或个人风格偏好报事实错误，不删除因果必需的配角回应。
audit_task由服务端指定本次职责。node只核对本幕内部、完成条件和实际自动入口；transition只核对指定(source_node_id,route_id,target_node_id)交接，其他入路单独列出而不合并授权。dependency_originals是按声明引用补入的完整原字段；original_refs指本次已完整提供的同一原字段，避免重复抄写。它们都不是已发生证明，不能把不同入路保证取并集。未提供的独立后续目标不代表原稿缺失。
"""

# The fact stage checks only citable conflicts; valid author drafts may have none, without suggestion-count quotas.
# Explain the actual framework before inspecting source conflicts so targets/routes are not mistaken for a separate runtime state machine.
# Initial review compares timing and submits confirmed conflicts only; remove duplicate reminders and include required revision fields in examples.
FACT_REVIEW_PROMPT = FACT_RUNTIME_RULES + "\n" + """你核对互动剧本的事实一致性，不评价文学、不补剧情。区分整幕规划与各入口实际使用的开场材料：规划不证明行动已发生，开场正文不能预写玩家的新台词或决定。只报告原文能确认的互斥事实、行动越权或出幕承接缺口，不能把缺少重述、推测或写法偏好写成错误。
逐幕核对summary、scene_boundaries、acting_contract和完成及出口合同是否允许相同的执行范围。本幕明确只决定、后幕才执行时，“达成共识前不得执行”仍允许共识后提前执行，应报告此条件与阶段范围的冲突。前幕明确允许交付的不可逆结果不得在后幕固定开场无条件恢复；可变选择只保留各入口保证的共同结果。接受下一项活动若同时充当本幕完成条件和出口授权，造成同一安排再次确认，应合并为一条因果问题。

terminal_node_ids由程序标明真实终止节点；目标在其中的出口可以自然收束，不因没有fallback_offer、accept_input或silent_choice就认定无法离幕。
exit_history_reviewed_ref占位的原句已由专项核对来源历史与出幕条件的保证关系，本轮不重复报告此关系或要求增删条件，也不把占位当原稿缺失；仍检查未省略的正文、目的地、时段、同刻互斥和行动越权，以及其他原句的承接问题。允许先做一件事不等于允许立即离幕，玩家尚可留幕完成其余真实条件；不能仅凭目标可选或顺序可变就指控条件过严、死路或承接缺口。
checked_node_ids只定位本任务的责任幕，audit_task.kind限定内部或指定交接；其他节点及entry_routes只供原文对照，省略其独立后续目标、出边与修订目录不代表原稿缺失，未提供的节点不代表不存在。node任务不复审单独分配的出边安排，route_entries只辅助完成条件的合法结果核对，不因未重复整份桥段而报缺失；transition任务不重报本幕内部或目标幕自身的独立错误。入路不能互相授权，也不擅改正确的来源幕。key_props.states是首次出现或状态变化时的生命周期规划，不统一对应开场演完时点；仅同node_id不能证明同刻互斥，“在某处被发现”也不反证后来已取出。

按以下顺序检查指定节点：
1. 先按 characters.player_name/player_identity 确认玩家，逐句检查该入口采用的开场材料与entry原文的发言主体。自动正文中的玩家新台词属于 ownership 问题，不必等到角色状态也冲突才报告；开场直接预定玩家说什么，即使是合理提议、疑问或日常客套也仍代替了玩家输入。再看 goals 与 acting_contract：核对明确行动者、权限、delivery_type、output_field、source_ids和timing。无需玩家输入便交付的开场内容，其中玩家的新主动动作、决定或台词本身就需要玩家授权，不能因作者已经写入正文而当作合法背景。例如开场预写“玩家说出自己的选择”，即使状态也写“正在交谈”，两处相容仍不代表玩家授权，须单独报ownership越权。关系中的已知玩家事实也不能提前把本幕待作的决定记成已发生；准备、构思及等待不等于已提出方案。player的行为应来自玩家输入；acting_contract是演出角色的权限，允许演出角色“协作”“对话”不能授权玩家在自动正文中说新台词或作决定。角色禁令和发声要求不转嫁给玩家。不能因目标列表/来源未枚举前置步骤就推断玩家被跳过。
2. 按入幕顺序核对同一人物的状态及同一物件的持有人、位置，区分持有与观察、查验。除实际用作旧稿开场的首句外，summary、scene_summary及narrative_focus 是整幕规划；goals按timing区分，opening对应开场已交付，turn对应普通回合待演，不能要求开场状态预先写成turn目标完成后的结果。未写细节不等于相反事实；人物关系概览不自动等于正在发生的物理动作，角色感受也不必额外提供外部动作证明。
3. 按路线的实际 source/target 逐条检查 outgoing_routes。先看 completion_contract 对应事实定义与路线条件保证什么，再区分接受前的 fallback_offer/silent_choice、玩家将提交的 accept_input 与接受后的桥段和目标开场；不假定未受保证的 goals 已经完成。advance_on_completion=true 时目标开场承接已完成事实的后果；若重新布置触发动作或跳过尚未获得的新许可，引用具体字段举证。核对来源叙事、合同与目标入口的整条因果链；若本幕明确允许互斥处置而路线条件未指定其一，固定桥段和结局不能无条件选定其中一种，只能保留各合法选择共同保证的结果。没有承诺不同结果时，不臆造新选择或要求新增分支。核对：未受保证的结果被固定出口宣称完成、引用了上一出口、目的地或时段不符、提前代做新选择及结局等待新的玩家回答均应举证；条件已经保证的结果无需逐字重述，保留结果也不等于再次执行。不能把合法出幕结果与来源开场当作同一时点而报冲突。

state/sequence 问题先核对上述时点和主体：同刻状态允许两处同时成立时不报互斥，不能把“如果把开场当结尾就冲突”这类假设列为问题。出幕承接则按运行规则核对最早合法出幕状态；problem须给出满足实际条件而所指来源结果尚未发生的具体反例，并引用条件原义与下游已然断言。反例违反明确限制、依赖编造前提或只有未列某个goal时不报；不能仅因理想演法可同时完成两件事就排除承接缺口。修法可收窄过度承诺或对齐真正必要的条件，不一律增加门槛。ownership 则检查自动正文是否预定玩家新发言或新决定，不要求另有一处相反事实；背景明确记载的往事、转述已有原话及已接受出口的正常移动不等于新发言。递向、递出待接不证明对方已接过；单手动作不证明双手被占用，佩戴设备也不证明手持介质，不能据这些推测报告持有者冲突。另行检查开场是否直接替玩家说话、操作或作新决定，不因漏报这种越权而用姿态推测代替。输出只写最终确认的冲突、具体原文依据与最小修法，不输出推敲过程。同一错误及其下游影响合成一条，不拆成状态和转场两条重复问题。明确代玩家执行、把未发生的核心交付或玩家发言记为既成历史、重复核心互动或结局待答必须为major或blocking。

引用与字段合同：story_outline字符串以{ref,text}表示，text是完整原文。ID及定位目录保留原值，不作冲突证据。evidence_refs至少含两个不同原字段的真实ref；程序恢复原路径和原文。ownership 可以引用该入口含玩家新台词的开场原字段 与明确该姓名属于玩家的 player_name/player_identity，或明确要求 runtime.player_input 的目标原文；这两处证明主体与越权，不需要编造一对互斥状态。不要输出旧evidence/path/quote，不把编号写入problem、modification_plan、preserve等正文。repair_targets列实际须改字段，不能把正确但用于佐证的字段也列进去；优先纠正错误字段，保留正确开场、状态与行动权边界。若开场越权替玩家说话，应修该入口实际使用的原字段，不能只让character_state追上这段台词而保留越权；行动越权与状态不一致不是同一个问题。当前节点text_repair_fields目录之外、goals或acting_contract的修改均为structure；不能借text修改结构，也不因目标文字修改要求改目标ID或出口引用。

不重做编译器的格式检查；兼容旧结局目标未声明timing不代表正文事实冲突，不能猜测运行失败。每条problem、modification_plan、expected_result各用一两句完整结论，不输出自我否定的观察项。字符串正文的引文用中文引号“”，不要内嵌未转义的英文双引号；JSON键和字符串边界仍用英文双引号。只输出JSON，必须填写下面全部字段，原样返回输入的checked_node_ids，正常稿允许issues=[]：
{"checked_node_ids":["真实节点ID"],"issues":[{"category":"state|ownership|sequence","severity":"minor|major|blocking","target_node_ids":["需改节点ID；出口问题填来源节点"],"problem":"已确认的同刻互斥、越权或具体合法出幕反例所证明的承接缺口","evidence_refs":["原字段ref","另一原字段ref"],"modification_plan":"只修错误字段的方案","expected_result":"修后相容的事实","preserve":["保留的正确事实"],"repair_scope":"text|structure","repair_targets":[{"node_id":"真实节点ID","field":"节点内的原字段JSON Pointer"}]}],"contract_checks":[]}

contract_targets逐项列出本幕三类静态检查，不是新增运行条件或已完成事实。未标kind的项是原completion完成条件；kind=transition_contract是该出边接受前的reason、邀约/静默安排、accept_input与接受后桥段、目标入口的一致性；kind=entry_ownership是该原字段在实际适用入口自动展示时的行动权。id/path/route_ids只定位，不作为证据。即使issues为空，contract_checks也必须对每项恰好回答一次，不用某个完成条件的clear替代出边或入口检查，不检查相邻幕的独立目标。每项形状为：
{"target_id":"原目标id","status":"clear|uncertain","subject":"实际行动者，区分角色、NPC和玩家","completion_evidence":"本项所需可见结果、前因及授权依据；缺少依据时明确写缺项","timing":"该项何时生效，区分来源、接受后转场和实际入口","refusal":"玩家拒绝或暂缓时如何处理；未声明则如实写未声明，不补剧情","evidence_refs":["目标definition_ref，可补充其它本次原文ref"],"alternatives":[]}
transition_contract逐路解释公开允许的安排与实际桥段、目标入口是否一致，引用reason及对应桥段或目标原文；互斥选项没有经实际选择区分时，固定正文不能强执行其中一种。普通接受同一安排可以授权相应移动，不要求新增共识事实。entry_ownership逐字段检查实际展示主体与既有授权；fixed_entry_only跳过的普通开场不当作该入路已播，entry固定原文按程序次序单独核对。角色自主行动及环境首次展示合法，不能因此认定玩家也已执行、回应或完成体验。
四个说明字段必须非空，不能在timing或completion_evidence中把summary/goals的理想顺序补为硬前提，也不能把拒绝可留幕解释成条件已完成或新增合法原值。clear只表示作者合同有清楚可观察的判断依据，不证明玩家已行动或实演可玩；一段展示、点单或邀请不能单独证明玩家完整体验、品尝或接受。无法从原稿确认可观测结果、主体或生效时点时填uncertain，允许如实报告不确定，不为凑两条互斥原文而编造issues。
alternatives仅用于有typed equals值的completion目标，逐一检查原稿明确允许的合法最终结果及拒绝/暂缓；没有则[]，不自创选项。transition_contract及entry_ownership的alternatives保持[]，另须完整填写下述专用矩阵，不能用自由说明替代。每项为{"kind":"completed|refusal|deferral","value":true,"outcome":"continue|stay|blocked|uncertain","route_ids":["可达的实际出口ID"],"entry_compatible":true,"reason":"原文承诺及实际合同和后幕是否相容","evidence_refs":["允许该结果的原文ref"]}。value按原value_type填写JSON原类型；非完成填null。completed包括原稿明确允许继续或收束的最终拒绝；refusal/deferral只指未作成决定而合理留幕。其余必要条件均满足后，completed必须能经实际出口承接相容后幕；字面target只接受A不能证明合法B可达。continue须有route_ids且entry_compatible=true；stay须route_ids=[]且entry_compatible=null；blocked/uncertain不能把本target判clear。固定后幕擅选互斥选项也不相容。可变决定用已有bool/true表达已决定，具体结果仍由实际选择保留；不把所有string事实改bool，不用通配符。
entry_ownership每项另填ownership_checks，逐ownership_units检查完整原字段，所有unit_id至少出现一次；单元start/end是definition_ref完整原文中从0开始、end不含的字符位置。单元切分只用于覆盖，不能忽略整字段中的条件或指代。一个单元有多主体或多动作时分别列行，不用演出角色的一项合法动作覆盖玩家动作。每行={"unit_id":"原u编号","quote":"该单元实际动作或描述的连续原句","subject":"原句中的主体原词","owner":"player|catgirl|npc|environment","kind":"new_action|speech|history|passive","authorization_refs":[],"status":"clear|violation|uncertain"}。环境被动描述允许subject为空。按characters真实姓名和完整上下文辨认主体；常规岗位操作也是主动行为，不能归passive。玩家新动作/发言的clear必须引用每条实际适用入路accept_input，且同一动作确被授权；演出角色allowed_behaviors、身份、作者state/目标、安排尚未接受都不是玩家授权。初始入口没有实际玩家输入，不能预填新行为。player/history判clear也须authorization_refs引用独立背景或已发生前情；当前入口、作者state/goals及演出角色权限不自证历史，依据不足填uncertain而不编造问题。已知往事、被动姿态、已接受同一安排的正常移动与角色自主操作允许；指代、授权或时点不明填uncertain。
transition_contract每项另填choice_checks，逐项列原reason明确允许的安排（包括可单做、合做、拒绝或暂缓），没有额外选项就引用完整reason作一行，不自创分支。所有choice_quote合起来须覆盖完整reason的非标点文字，可含必要上下文，不能只摘一种方案。每行={"choice_quote":"该方案在reason中的连续原句","guarantee_ids":["仅选本target.guarantees的id"],"downstream_refs":["本target.downstream_refs的全部E编号"],"compatible":true,"counterexample":""}。每种方案须核对本次可见的全部下游原文，不能只引相容的目标开场而漏审桥段、must_deliver或已然状态。逐行核对该方案满足实际出幕条件时，固定桥段/入口宣称的结果是否都相容。guarantees只列真实completion、conditions及手动接受原话；all/any原义保留，goals/summary不补成门槛。接受核实不自动授权外出或额外操作；角色在目标当时自主交付可合法首次发生。发现合法冲突反例时compatible=false并写具体满足条件却冲突的情况；不能确定为null，两者都不能把target判clear。共用入口依赖各合法入路保证的交集，不能合并授权。原文同时允许两者都做时，不改读成互斥选择。
矩阵确认violation或合法冲突反例须同时在issues举证；没有确证但不明则target uncertain，不能一边承认缺口一边issues空且clear。矩阵只表达审核证据，不自动批准事实或修订。
发现确切冲突时仍按原合同在issues举证；contract_checks如实写uncertain及原因，不另报问题索引、修订命令或requires_revision。正式修订结论只由原有独立证据复核产生，不能通过contract_checks新增修改授权。合法拒绝后暂留或协商不要求新增分支；只有原稿确实承诺的继续或收束与硬条件矛盾时才报问题。没有任何contract_targets时contract_checks为空。此检查不批准运行时事实，不认证排演或文学质量。
"""


def fact_review_prompt(task):
    """Use the same citation/output contract with one explicit node or edge responsibility."""
    focus = (
        "只审核audit_task指定的交接。先列reason的每个明确方案，再用真实条件/接受原话核对桥段与目标已然断言。"
        "为每个choice_checks给合法状态反例或明确相容依据，不用一种理想演法证明所有选项都相容。"
        "不复审来源幕独立目标；来源历史的专项占位不重复审查。正常移动、角色自主首次交付与未来提议允许。"
        if task["kind"] == "transition" else
        "只审核本幕内部、完成条件及实际自动入口。先逐单元填ownership_checks：按原句辨行动者和行为时点，"
        "玩家新操作/发言需要同一行动的实际授权，演出角色权限和岗位常规不授权玩家。"
        "再核同刻状态、明确阶段禁令与可观测完成条件；规划中的先后变化不是同刻矛盾。"
        "route_entries仅用于比较完成条件允许的合法结果，不重复审交接。"
    )
    return FACT_RUNTIME_RULES + "\n" + focus + "\n" + FACT_REVIEW_PROMPT[FACT_REVIEW_PROMPT.index("引用与字段合同："):]


# Explicitly compare opening and post-performance views in the same initial review so author snapshots are not read as usual entrance poses.
# Reuse only existing ref/text fields: add no facts, output protocol, or model stage; merge repeated state issues.
OPENING_STATE_COMPARISON_RULE = (
    "已改变的状态不能仍写成变化之前；依次展示的前后变化不算同一时点互斥。"
    "入口同刻状态比较只对明确互斥的结果报错；最后又恢复、计划未实施、不同对象及相容位置均可成立。"
    "relationship_state里重复的同一错误状态也应同步修正，与状态字段合为一条问题。"
    # Evidence IDs and revision paths are separate existing location contracts; long drafts must not put field paths in ref lists.
    "evidence_refs只填写对应原文对象的ref值（例如E12、E34），不能填写字段路径；"
    "repair_targets的field则填写节点内字段路径，不能填写证据编号。这两种定位不可混用。"
)


# Stable IDs and location indexes connect nodes; numbering them encourages model names to substitute for action evidence.
# source_ids still verify references; actual owner/output_field permission values remain valid ownership evidence.
_LOCATOR_FIELDS = {"id", "target_node_id", "source_node_id", "start_node_id", "entry_from", "destination_ids",
                   "text_repair_fields", "opening_fallback_source"}


def with_opening_source(node: dict[str, Any]) -> dict[str, Any]:
    """Point to the actual legacy opening source without repeating text or adding a citation path."""
    summary_field = "scene_summary" if "scene_summary" in node else "summary"
    if not node.get("opening_scene") and node.get(summary_field):
        return {**node, "opening_fallback_source": summary_field}
    return node


def node_fact_context(context: Mapping[str, Any], node_id: str | set[str]) -> dict[str, Any]:
    """Retain the focus nodes, all immediate neighbors and original branch metadata."""
    nodes = {node["id"]: node for node in [*context.get("mainline", []),
        *(node for branch in context.get("branches", []) for node in branch.get("nodes", [])),
        *context.get("endings", [])]}
    focus = {node_id} if isinstance(node_id, str) else node_id
    selected = focus | {
        route["target_node_id"] for key in focus for route in nodes[key].get("outgoing_routes", [])
    }
    selected.update(source for source, node in nodes.items() if any(
        route["target_node_id"] in focus for route in node.get("outgoing_routes", [])))
    result = deepcopy(dict(context))
    for key in ("mainline", "endings"):
        result[key] = [node for node in result.get(key, []) if node["id"] in selected]
    result["branches"] = [{**branch, "nodes": [node for node in branch.get("nodes", []) if node["id"] in selected]}
                          for branch in result.get("branches", []) if any(node["id"] in selected for node in branch.get("nodes", []))]
    return result


def node_fact_review_context(context: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    """Keep full focus evidence and adjacent entry/exit guarantees, not neighbors' independent future tasks."""
    result = node_fact_context(context, node_id)
    nodes = [*result.get("mainline", []), *result.get("endings", []),
             *(node for branch in result.get("branches", []) for node in branch.get("nodes", []))]
    incoming = {node["id"] for node in nodes if any(
        route["target_node_id"] == node_id for route in node.get("outgoing_routes", []))}

    def later_turn(goal):
        timing = goal.get("timing")
        return (timing.get("text") if isinstance(timing, Mapping) else timing) == "turn"

    for node in nodes:
        if node["id"] == node_id:
            continue
        for field in ("narrative_focus", "relationship_goal", "transition_goal", "text_repair_fields"):
            node.pop(field, None)
        node["outgoing_routes"] = [route for route in node.get("outgoing_routes", [])
                                   if route["target_node_id"] == node_id]
        if node["id"] not in incoming and "goals" in node:
            node["goals"] = [goal for goal in node["goals"] if not later_turn(goal)]
    return result


def fact_review_tasks(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Assign every static target once, preserving distinct routes into shared nodes."""
    nodes = [*context.get("mainline", []), *context.get("endings", []),
             *(node for branch in context.get("branches", []) for node in branch.get("nodes", []))]
    unique = {node["id"]: node for node in nodes}
    tasks = []
    for node_id, node in unique.items():
        targets = build_contract_targets(context, {node_id})
        tasks.append({"id": f"node:{node_id}", "kind": "node", "node_id": node_id,
                      "target_ids": [row["id"] for row in targets if row.get("kind") != "transition_contract"]})
        for index, route in enumerate(node.get("outgoing_routes", [])):
            if route["target_node_id"] not in unique:
                raise ValueError("audit_route_target_missing")
            target_id = f"{node_id}:transition:{index}"
            tasks.append({"id": f"transition:{node_id}:{route['id']}:{route['target_node_id']}",
                "kind": "transition", "source_node_id": node_id, "route_id": route["id"],
                "target_node_id": route["target_node_id"],
                "target_ids": [target_id] if any(row["id"] == target_id for row in targets) else []})
    return tasks


def fact_task_context(context: Mapping[str, Any], marked: Mapping[str, Any], task: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    """Project one internal scene or one actual handoff and resolve declared author-source references."""
    raw_nodes, marked_nodes, paths = {}, {}, {}
    for prefix, rows in [("/mainline", context.get("mainline", [])), ("/endings", context.get("endings", [])),
                         *((f"/branches/{i}/nodes", row.get("nodes", [])) for i, row in enumerate(context.get("branches", [])))]:
        for index, node in enumerate(rows):
            raw_nodes.setdefault(node["id"], node)
            paths.setdefault(node["id"], f"{prefix}/{index}")
            marked_nodes.setdefault(node["id"], _resolve_pointer(marked, f"{prefix}/{index}"))
    source_id = task.get("node_id", task.get("source_node_id"))
    selected = {source_id}
    if task["kind"] == "transition":
        selected.add(task["target_node_id"])

    def entry(node):
        value = {key: deepcopy(node[key]) for key in (
            "id", "title", "opening_scene", "opening_fallback_source", "summary", "scene_summary", "character_state",
            "acting_contract", "must_not_happen", "fixed_narrations", "entry_player_inputs", "text_repair_fields") if key in node}
        value["goals"] = [deepcopy(goal) for goal in node.get("goals", []) if (
            goal.get("timing", {}).get("text") if isinstance(goal.get("timing"), Mapping) else goal.get("timing")) != "turn"]
        return value

    def project(rows):
        result = []
        for node in rows:
            if node["id"] not in selected:
                continue
            if node["id"] == source_id:
                value = deepcopy(node)
                if task["kind"] == "transition":
                    value["outgoing_routes"] = [route for route in value.get("outgoing_routes", []) if route["id"] == task["route_id"]]
                else:
                    value["outgoing_routes"] = [{key: deepcopy(route[key]) for key in ("id", "target_node_id", "conditions")}
                                                for route in value.get("outgoing_routes", [])]
            else:
                value = entry(node)
            result.append(value)
        return result

    outline = {**deepcopy(dict(marked)), "mainline": project(marked.get("mainline", [])),
               "endings": project(marked.get("endings", [])),
               "branches": [{**deepcopy(branch), "nodes": project(branch.get("nodes", []))}
                            for branch in marked.get("branches", []) if any(node["id"] in selected for node in branch.get("nodes", []))]}
    # Actual alternative entries stay distinct; none is proof for another inbound route.
    outline["entry_routes"] = [{"source_node_id": node_id, "route_id": route["id"],
        "target_node_id": route["target_node_id"], "conditions": deepcopy(route["conditions"]),
        "completion_contract": deepcopy(marked_nodes[node_id].get("completion_contract", {})),
        "transition_contract": deepcopy(route.get("transition_contract", {}))}
        for node_id, node in marked_nodes.items() for route in node.get("outgoing_routes", [])
        if route["target_node_id"] in selected]
    if task["kind"] == "node":
        outline["route_entries"] = [{"route_id": route["id"], "entry": entry(marked_nodes[route["target_node_id"]])}
                                    for route in raw_nodes[source_id].get("outgoing_routes", [])]
    goal_paths = {}
    for node_id, node in raw_nodes.items():
        for index, goal in enumerate(node.get("goals", [])):
            goal_paths.setdefault(goal["id"], []).append(f"{paths[node_id]}/goals/{index}")
    dependencies, visited, visiting = [], set(), set()

    def refs(value):
        if isinstance(value, Mapping):
            if set(value) == {"ref", "text"} and isinstance(value["ref"], str) and isinstance(value["text"], str):
                return {value["ref"]}
            return set().union(*(refs(item) for item in value.values()))
        return set().union(*(refs(item) for item in value)) if isinstance(value, list) else set()

    visible = refs(outline)

    def add_original(ref, path, original):
        original_refs = refs(original)
        dependencies.append({"source_id": ref, "path": path, **(
            {"original_refs": sorted(original_refs)} if original_refs and original_refs.issubset(visible)
            else {"original": deepcopy(original)})})

    def resolve(ref):
        if ref == "runtime.player_input" or ref in visited:
            return
        if ref in visiting:
            raise ValueError("audit_dependency_cycle")
        visiting.add(ref)
        if ref.startswith("goal.") and len(goal_paths.get(ref[5:], [])) == 1:
            path = goal_paths[ref[5:]][0]
            raw = _resolve_pointer(context, path)
            for parent in raw.get("source_ids", []):
                resolve(parent)
            add_original(ref, path, _resolve_pointer(marked, path))
        elif ref.startswith("opening.") and ref[8:] in raw_nodes:
            node_id = ref[8:]
            add_original(ref, paths[node_id], entry(marked_nodes[node_id]))
        else:
            raise ValueError(f"audit_dependency_unresolved:{ref}")
        visiting.remove(ref)
        visited.add(ref)

    for node_id in selected:
        for goal in raw_nodes[node_id].get("goals", []):
            if node_id != source_id and goal.get("timing") == "turn":
                continue
            for ref in goal.get("source_ids", []):
                resolve(ref)
        for route in raw_nodes[node_id].get("outgoing_routes", []):
            if node_id == source_id and (task["kind"] == "node" or route["id"] == task["route_id"]):
                for ref in (route.get("transition_contract") or {}).get("source_ids", []):
                    resolve(ref)
    return outline, dependencies


def build_fact_sources(context: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Number citable string leaves; retain location metadata verbatim without treating it as conflict evidence."""
    # Preserve complete projections and original paths; place main routes before endings and opening state before targets/exits to reduce timing confusion.
    # Preserve unknown fields and author branch order; do not infer actual graph play order or target completion.
    def ordered(value, keys):
        return {key: value[key] for key in (*keys, *value) if key in value}
    context = ordered(deepcopy(dict(context)), ("author_intent", "background", "characters", "metrics", "key_props", "mainline", "branches", "endings"))
    node_keys = ("id", "title", "summary", "narrative_focus", "acting_contract", "must_not_happen",
                 "relationship_state", "opening_scene", "character_state", "goals", "relationship_goal",
                 "transition_goal", "outgoing_routes", "text_repair_fields")
    for nodes in [context.get("mainline", []), context.get("endings", []),
                  *(branch.get("nodes", []) for branch in context.get("branches", []))]:
        for index, node in enumerate(nodes):
            nodes[index] = ordered(with_opening_source(node), node_keys)
    sources: dict[str, dict[str, str]] = {}
    def walk(value: Any, path: str) -> Any:
        if isinstance(value, Mapping):
            return {key: deepcopy(item) if key in _LOCATOR_FIELDS else walk(item, path + "/" + key.replace("~", "~0").replace("/", "~1"))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item, f"{path}/{index}") for index, item in enumerate(value)]
        if isinstance(value, str) and value.strip():
            ref = f"E{len(sources) + 1}"
            sources[ref] = {"path": path, "quote": value}
            return {"ref": ref, "text": value}
        return value
    return walk(context, ""), sources


def _resolve_pointer(context: Mapping[str, Any], pointer: str) -> Any:
    """Read source text by explicit field path instead of inferring locations from explanation text."""
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("invalid_fact_evidence_path")
    value: Any = context
    for token in pointer[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not key.isdecimal() or str(int(key)) != key:
                raise ValueError("invalid_fact_evidence_index")
            value = value[int(key)]
        else:
            value = value[key]
    return value


def build_contract_targets(context: Mapping[str, Any], checked_node_ids: set[str]) -> list[dict[str, Any]]:
    """Enumerate completion, authored exit and actual automatic-entry checks without creating runtime gates."""
    groups = [("/mainline", context.get("mainline", [])), ("/endings", context.get("endings", []))]
    groups.extend((f"/branches/{index}/nodes", branch.get("nodes", []))
                  for index, branch in enumerate(context.get("branches", [])))
    targets = []
    seen_nodes = set()
    all_nodes = [node for _, nodes in groups for node in nodes]
    node_paths = {}
    for prefix, nodes in groups:
        for index, node in enumerate(nodes):
            node_paths.setdefault(node["id"], f"{prefix}/{index}")

    def entry_metadata(node, text, *, fixed=False):
        incoming = [(source, index, route) for source in all_nodes
                    for index, route in enumerate(source.get("outgoing_routes", []))
                    if route["target_node_id"] == node["id"]
                    and (fixed or not (route.get("transition_contract") or {}).get("fixed_entry_only"))]
        units, pending = [], ""
        for part in re.split(r"(?<=[。！？!?\n])", text):
            if any(not char.isspace() and not unicodedata.category(char).startswith("P") for char in part):
                units.append({"id": f"u{len(units)}", "text": pending + part})
                pending = ""
            elif units:
                units[-1]["text"] += part
            else:
                pending += part
        if not units and pending:
            units.append({"id": "u0", "text": pending})
        return {"ownership_units": units,
                "initial_entry": node["id"] == context.get("start_node_id") or not incoming,
                "authorization_paths": [{"route_id": route["id"],
                    "accepts_player_input": not (route.get("transition_contract") or {}).get("advance_on_completion"),
                    "path": f"{node_paths[source['id']]}/outgoing_routes/{index}/transition_contract/accept_input"}
                    for source, index, route in incoming]}
    for prefix, nodes in groups:
        for node_index, node in enumerate(nodes):
            if node["id"] not in checked_node_ids or node["id"] in seen_nodes:
                continue
            seen_nodes.add(node["id"])
            node_path = f"{prefix}/{node_index}"
            for index, requirement in enumerate(node.get("completion_contract", {}).get("all", [])):
                if "key" in requirement:
                    key = requirement["key"].replace("~", "~0").replace("/", "~1")
                    definition_path = f"/fact_contract/facts/{key}/description"
                else:
                    pieces = node.get("fixed_narrations", [])
                    piece_index = next((i for i, piece in enumerate(pieces)
                                        if piece["id"] == requirement["fixed_narration_id"]), None)
                    if piece_index is None:
                        raise ValueError("contract_definition_missing")
                    definition_path = f"{node_path}/fixed_narrations/{piece_index}/text"
                try:
                    definition = _resolve_pointer(context, definition_path)
                except (KeyError, IndexError, TypeError) as error:
                    raise ValueError("contract_definition_missing") from error
                if not isinstance(definition, str) or not definition.strip():
                    raise ValueError("contract_definition_missing")
                targets.append({
                    "id": f"{node['id']}:completion:{index}", "node_id": node["id"],
                    "path": f"{node_path}/completion_contract/all/{index}",
                    "requirement": deepcopy(requirement), "definition_path": definition_path,
                    "route_ids": [route["id"] for route in node.get("outgoing_routes", [])],
                })
            for index, route in enumerate(node.get("outgoing_routes", [])):
                if not (route.get("transition_contract") or {}).get("reason"):
                    continue
                path = f"{node_path}/outgoing_routes/{index}/transition_contract"
                destination = next(row for row in all_nodes if row["id"] == route["target_node_id"])
                opening = "opening_scene" if destination.get("opening_scene") else with_opening_source(destination).get("opening_fallback_source", "summary")
                destination_path = node_paths[route["target_node_id"]]
                entry_paths = [f"{destination_path}/fixed_narrations/{i}/text"
                               for i, piece in enumerate(destination.get("fixed_narrations", []))
                               if (piece.get("trigger") or {}).get("type") == "entry"]
                if not route["transition_contract"].get("fixed_entry_only") or not entry_paths:
                    entry_paths.insert(0, destination_path + "/" + opening)
                targets.append({"id": f"{node['id']}:transition:{index}", "kind": "transition_contract",
                    "node_id": node["id"], "path": path, "definition_path": path + "/reason",
                    "requirement": {}, "route_ids": [route["id"]],
                    "downstream_paths": [path + "/bridge_scene_narration", path + "/must_deliver", path + "/must_preserve",
                        *entry_paths, destination_path + "/character_state"],
                    "guarantees": [
                        {"id": "completion", "contract": deepcopy(node.get("completion_contract", {}))},
                        {"id": "conditions", "contract": deepcopy(route.get("conditions", {}))},
                        *([{ "id": "accept_input", "text": route["transition_contract"]["accept_input"]}]
                          if not route["transition_contract"].get("advance_on_completion")
                          and route["transition_contract"].get("accept_input") else [])]})
            pieces = [(index, piece) for index, piece in enumerate(node.get("fixed_narrations", []))
                      if (piece.get("trigger") or {}).get("type") == "entry"]
            incoming = [route for source in all_nodes for route in source.get("outgoing_routes", [])
                        if route["target_node_id"] == node["id"]]
            ordinary_entry = (node["id"] == context.get("start_node_id")
                or not context.get("start_node_id") and not incoming
                or any(not (route.get("transition_contract") or {}).get("fixed_entry_only") or not pieces
                       for route in incoming))
            if ordinary_entry:
                entry = with_opening_source(dict(node))
                field = "opening_scene" if entry.get("opening_scene") else entry.get("opening_fallback_source")
                if field:
                    path = f"{node_path}/{field}"
                    targets.append({"id": f"{node['id']}:entry:opening", "kind": "entry_ownership",
                        "node_id": node["id"], "path": path, "definition_path": path,
                        "requirement": {}, "route_ids": [], **entry_metadata(node, entry[field])})
            if ordinary_entry or incoming:
                for index, piece in pieces:
                    path = f"{node_path}/fixed_narrations/{index}/text"
                    targets.append({"id": f"{node['id']}:entry:original:{index}", "kind": "entry_ownership",
                        "node_id": node["id"], "path": path, "definition_path": path,
                        "requirement": {}, "route_ids": [], **entry_metadata(node, piece["text"], fixed=True)})
    return targets


def _validate_evidence_matrix(context, target, raw, sources, issues):
    kind = target.get("kind")
    if kind not in {"entry_ownership", "transition_contract"}:
        return {}
    field = "ownership_checks" if kind == "entry_ownership" else "choice_checks"
    rows = raw[field]
    if not isinstance(rows, list) or not rows:
        raise ValueError("incomplete_evidence_matrix")

    def refs_valid(refs):
        if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in sources for ref in refs) or len(refs) != len(set(refs)):
            return False
        for ref in refs:
            value = _resolve_pointer(context, sources[ref]["path"])
            if not isinstance(value, str) or not isinstance(sources[ref]["quote"], str) or sources[ref]["quote"] not in value:
                return False
        return True

    violation = False
    uncertain = False
    if kind == "entry_ownership":
        units = {unit["id"]: unit["text"] for unit in target["ownership_units"]}
        seen = set()
        names = context.get("characters", {})
        player, catgirl = names.get("player_name"), names.get("catgirl_name")
        authorization_paths = {row["path"] for row in target["authorization_paths"]}
        for row in rows:
            def invalid(field, expected):
                unit_id = row.get("unit_id") if isinstance(row, Mapping) else None
                raise ValueError(f"invalid_ownership_matrix:target_id={target['id']};unit_id={unit_id};field={field};expected={expected}")
            if (not isinstance(row, Mapping) or set(row) != {"unit_id", "quote", "subject", "owner", "kind", "authorization_refs", "status"}
                    or row["owner"] not in {"player", "catgirl", "npc", "environment"}
                    or row["kind"] not in {"new_action", "speech", "history", "passive"}
                    or row["status"] not in {"clear", "violation", "uncertain"}):
                invalid("shape", "declared_fields_and_enum_values")
            if row["unit_id"] not in units:
                invalid("unit_id", "one_of_provided_units")
            if not isinstance(row["quote"], str) or not row["quote"].strip() or row["quote"] not in units[row["unit_id"]]:
                invalid("quote", "nonempty_contiguous_original_in_unit")
            if (not isinstance(row["subject"], str) or row["kind"] != "passive"
                    and (not row["subject"] or row["subject"] not in row["quote"])):
                invalid("subject", "literal_contiguous_phrase_in_quote_without_added_explanation")
            if not refs_valid(row["authorization_refs"]):
                invalid("authorization_refs", "current_request_verified_refs")
            if (row["subject"] == player and row["owner"] != "player"
                    or row["subject"] == catgirl and row["owner"] != "catgirl"):
                raise ValueError("ownership_subject_mismatch")
            seen.add(row["unit_id"])
            if row["owner"] == "player" and row["kind"] in {"new_action", "speech"} and row["status"] == "clear":
                paths = {sources[ref]["path"] for ref in row["authorization_refs"]}
                if (target["initial_entry"] or not authorization_paths or paths != authorization_paths
                        or any(not row["accepts_player_input"] for row in target["authorization_paths"])):
                    raise ValueError("player_authorization_evidence_required")
            if row["owner"] == "player" and row["kind"] == "history" and row["status"] == "clear":
                parts = target["definition_path"].split("/")
                node_path = "/".join(parts[:5] if parts[1] == "branches" else parts[:3])
                paths = [sources[ref]["path"] for ref in row["authorization_refs"]]
                if not paths or any(path.startswith(node_path + "/") or
                        any(field in path.split("/") for field in ("character_state", "goals", "acting_contract")) or
                        path.startswith("/characters/") and path != "/characters/player_identity" for path in paths):
                    invalid("authorization_refs", "independent_history_not_current_entry_or_author_state_or_goals")
            violation |= row["status"] == "violation"
            uncertain |= row["status"] == "uncertain"
        if seen != units.keys():
            raise ValueError("incomplete_ownership_matrix")
    else:
        reason = _resolve_pointer(context, target["definition_path"])
        guaranteed = {item["id"] for item in target["guarantees"]}
        downstream = {ref for ref, source in sources.items()
                      if any(source["path"] == path or source["path"].startswith(path + "/")
                             for path in target["downstream_paths"])}
        covered = set()
        for row in rows:
            if (not isinstance(row, Mapping) or set(row) != {"choice_quote", "guarantee_ids", "downstream_refs", "compatible", "counterexample"}
                    or not isinstance(row["choice_quote"], str) or not row["choice_quote"].strip() or row["choice_quote"] not in reason
                    or not isinstance(row["guarantee_ids"], list) or not set(row["guarantee_ids"]).issubset(guaranteed)
                    or len(row["guarantee_ids"]) != len(set(row["guarantee_ids"]))
                    or not refs_valid(row["downstream_refs"]) or not row["downstream_refs"]
                    or any(not any(sources[ref]["path"] == path or sources[ref]["path"].startswith(path + "/")
                                   for path in target["downstream_paths"]) for ref in row["downstream_refs"])
                    or row["compatible"] is not None and type(row["compatible"]) is not bool
                    or not isinstance(row["counterexample"], str)
                    or row["compatible"] is False and not row["counterexample"].strip()
                    or row["compatible"] is True and row["counterexample"].strip()):
                raise ValueError("invalid_choice_matrix")
            if set(row["downstream_refs"]) != downstream:
                missing = ",".join(sorted(downstream - set(row["downstream_refs"])))
                raise ValueError(f"incomplete_choice_downstream:target_id={target['id']};missing_refs={missing}")
            for match in re.finditer(re.escape(row["choice_quote"]), reason):
                covered.update(range(match.start(), match.end()))
            violation |= row["compatible"] is False
            uncertain |= row["compatible"] is None
        if any(index not in covered and not char.isspace() and not unicodedata.category(char).startswith("P")
               for index, char in enumerate(reason)):
            raise ValueError(f"incomplete_choice_reason:target_id={target['id']};cover_complete_definition_ref")
    if (violation or uncertain) and raw["status"] == "clear":
        raise ValueError("evidence_matrix_not_clear")
    if violation and not any(isinstance(issue, Mapping) and target["node_id"] in issue.get("target_node_ids", [])
                             and any(sources.get(ref, {}).get("path") == target["definition_path"]
                                     for ref in issue.get("evidence_refs", [])) for issue in issues):
        raise ValueError("evidence_matrix_violation_issue_required")
    return {field: deepcopy(rows)}


def bind_contract_targets(targets, sources):
    """Bind target definitions and downstream evidence to this exact request's catalog."""
    by_path = {row["path"]: ref for ref, row in sources.items()}
    for target in targets:
        if target["definition_path"] not in by_path:
            raise ValueError("contract_definition_not_visible")
        target["definition_ref"] = by_path[target["definition_path"]]
        if target.get("kind") == "entry_ownership":
            offset, units = 0, []
            for unit in target["ownership_units"]:
                end = offset + len(unit["text"])
                units.append({"id": unit["id"], "start": offset, "end": end})
                offset = end
            target["ownership_units"] = units
        if target.get("kind") == "transition_contract":
            target["downstream_refs"] = [ref for ref, row in sources.items()
                if any(row["path"] == path or row["path"].startswith(path + "/") for path in target["downstream_paths"])]
    return targets


def _validate_contract_checks(context, payload, checked_node_ids, evidence_sources, expected_target_ids=None):
    targets = {target["id"]: target for target in build_contract_targets(context, checked_node_ids)}
    if expected_target_ids is not None:
        if len(expected_target_ids) != len(set(expected_target_ids)) or not set(expected_target_ids).issubset(targets):
            raise ValueError("invalid_contract_review_scope")
        targets = {key: value for key, value in targets.items() if key in expected_target_ids}
    raw_checks = payload.get("contract_checks", [] if not targets else None)
    if not isinstance(raw_checks, list) or len(raw_checks) != len(targets):
        raise ValueError("incomplete_contract_review")
    sources = evidence_sources if evidence_sources is not None else build_fact_sources(context)[1]
    checks = {}
    text_fields = {"subject", "completion_evidence", "timing", "refusal"}
    fields = text_fields | {"target_id", "status", "evidence_refs", "alternatives"}
    diagnostics = []
    for raw in raw_checks:
        if not isinstance(raw, Mapping) or raw.get("target_id") not in targets:
            continue
        target = targets[raw["target_id"]]
        matrix_field = {"entry_ownership": "ownership_checks", "transition_contract": "choice_checks"}.get(target.get("kind"))
        expected_fields = fields | ({matrix_field} if matrix_field else set())
        if set(raw) != expected_fields:
            diagnostics.append(f"invalid_contract_check_fields:target_id={target['id']};"
                               f"missing={','.join(sorted(expected_fields-set(raw)))};"
                               f"extra={','.join(sorted(str(key) for key in set(raw)-expected_fields))}")
        if matrix_field in raw and raw.get("status") in {"clear", "uncertain"}:
            try:
                _validate_evidence_matrix(context, target, raw, sources, payload.get("issues", []))
            except (KeyError, TypeError, ValueError) as error:
                diagnostics.append(str(error))
    if diagnostics:
        raise ValueError(" | ".join(diagnostics))
    for raw in raw_checks:
        if not isinstance(raw, Mapping):
            raise ValueError("invalid_contract_check")
        target = targets.get(raw.get("target_id"), {})
        expected_fields = fields | ({"ownership_checks"} if target.get("kind") == "entry_ownership" else
                                   {"choice_checks"} if target.get("kind") == "transition_contract" else set())
        if set(raw) != expected_fields:
            missing = ",".join(sorted(expected_fields - set(raw)))
            extra = ",".join(sorted(str(key) for key in set(raw) - expected_fields))
            raise ValueError(f"invalid_contract_check_fields:missing={missing};extra={extra}")
        target_id, status = raw["target_id"], raw["status"]
        if (not isinstance(target_id, str) or target_id not in targets or target_id in checks
                or status not in {"clear", "uncertain"}
                or any(not isinstance(raw[key], str) or not raw[key].strip() for key in text_fields)):
            raise ValueError("invalid_contract_check")
        target = targets[target_id]
        refs = raw["evidence_refs"]
        if (not isinstance(refs, list) or not refs
                or any(not isinstance(ref, str) or ref not in sources for ref in refs)
                or len(refs) != len(set(refs))):
            raise ValueError("invalid_contract_evidence_refs")
        citations = []
        for ref in refs:
            citation = sources[ref]
            value = _resolve_pointer(context, citation["path"])
            if (not isinstance(value, str) or not isinstance(citation["quote"], str)
                    or not citation["quote"].strip() or citation["quote"] not in value):
                raise ValueError("unverifiable_contract_evidence")
            citations.append({"path": citation["path"], "quote": citation["quote"]})
        if not any(item["path"] == target["definition_path"] for item in citations):
            raise ValueError("contract_definition_evidence_required")
        matrix = _validate_evidence_matrix(context, target, raw, sources, payload.get("issues", []))
        alternatives = raw["alternatives"]
        if not isinstance(alternatives, list):
            raise ValueError("invalid_choice_coverage")
        if target.get("kind") in {"transition_contract", "entry_ownership"} and alternatives:
            raise ValueError("noncompletion_alternatives")
        for alternative in alternatives:
            if (not isinstance(alternative, Mapping) or set(alternative) != {
                    "kind", "value", "outcome", "route_ids", "entry_compatible", "reason", "evidence_refs"}
                    or alternative["kind"] not in {"completed", "refusal", "deferral"}
                    or alternative["outcome"] not in {"continue", "stay", "blocked", "uncertain"}
                    or not isinstance(alternative["reason"], str) or not alternative["reason"].strip()):
                raise ValueError("invalid_choice_coverage")
            refs = alternative["evidence_refs"]
            routes = alternative["route_ids"]
            if (not isinstance(refs, list) or not refs
                    or any(not isinstance(ref, str) or ref not in sources for ref in refs)
                    or not isinstance(routes, list) or len(routes) != len(set(routes))
                    or any(not isinstance(route, str) or route not in target["route_ids"] for route in routes)
                    or alternative["entry_compatible"] is not None and type(alternative["entry_compatible"]) is not bool):
                raise ValueError("invalid_choice_coverage_evidence")
            value, outcome = alternative["value"], alternative["outcome"]
            if alternative["kind"] == "completed":
                expected_value = target["requirement"].get("equals")
                expected_type = {bool: "bool", int: "int", str: "string"}.get(type(expected_value))
                if expected_type is None:
                    raise ValueError("invalid_choice_completion_value")
                if type(value) is not type(expected_value):
                    raise ValueError(f"choice_completion_value_type_mismatch:target_id={target_id};"
                                     f"expected_json_type={expected_type}")
                if status == "clear" and (value != expected_value or outcome != "continue"):
                    raise ValueError("choice_completion_not_reachable")
            elif value is not None or outcome == "continue":
                raise ValueError("choice_noncompletion_has_value")
            if (outcome == "continue" and (not routes or alternative["entry_compatible"] is not True)
                    or outcome == "stay" and (routes or alternative["entry_compatible"] is not None)
                    or status == "clear" and (outcome in {"blocked", "uncertain"}
                                             or alternative["entry_compatible"] is False)):
                raise ValueError("choice_coverage_not_clear")
        checks[target_id] = {
            "target_id": target_id, **{key: deepcopy(value) for key, value in target.items() if key != "id"},
            **{key: raw[key].strip() for key in text_fields}, "status": status,
            "evidence": citations, "alternatives": deepcopy(alternatives), "issue_ids": [],
            **matrix,
        }
    return [checks[target_id] for target_id in targets]


def validate_fact_review(context: Mapping[str, Any], payload: Any, node_ids: set[str], *,
                         evidence_sources: Mapping[str, Mapping[str, str]] | None = None,
                         checked_node_ids: set[str] | None = None,
                         check_contracts: bool = True,
                         expected_target_ids: list[str] | None = None,
                         strict_repair_targets: bool = False) -> dict[str, Any]:
    """Return a copy after full validation; callers report failures at the factual stage rather than save a partially valid report."""
    try:
        if not isinstance(payload, Mapping):
            raise ValueError("invalid_fact_review")
        checked = payload.get("checked_node_ids")
        raw_issues = payload.get("issues")
        expected = node_ids if checked_node_ids is None else checked_node_ids
        if (not isinstance(checked, list) or not all(isinstance(x, str) for x in checked)
                or len(checked) != len(expected) or set(checked) != expected
                or not isinstance(raw_issues, list)):
            raise ValueError("incomplete_fact_review")
        issues = []
        for index, raw in enumerate(raw_issues, start=1):
            if not isinstance(raw, Mapping):
                raise ValueError("invalid_fact_issue")
            targets = raw.get("target_node_ids")
            preserve = raw.get("preserve")
            if (raw.get("category") not in {"state", "ownership", "sequence"}
                    or raw.get("severity") not in {"minor", "major", "blocking"}
                    or raw.get("repair_scope") not in {"text", "structure"}
                    or not isinstance(targets, list) or not targets
                    or not all(isinstance(x, str) and x in node_ids for x in targets)
                    or not isinstance(preserve, list)
                    or not all(isinstance(x, str) and x.strip() for x in preserve)):
                raise ValueError("invalid_fact_issue")
            fields = {key: raw.get(key) for key in ("problem", "modification_plan", "expected_result")}
            if not all(isinstance(x, str) and x.strip() for x in fields.values()):
                raise ValueError("invalid_fact_description")
            evidence = raw.get("evidence")
            if evidence_sources is not None:
                # Native calls accept only current-input IDs; after recovery, still verify quotations verbatim rather than trusting model-filled citations.
                refs = raw.get("evidence_refs")
                if ("evidence" in raw or not isinstance(refs, list) or len(refs) < 2
                        or any(not isinstance(ref, str) or ref not in evidence_sources for ref in refs)):
                    raise ValueError("invalid_fact_evidence_refs")
                evidence = [evidence_sources[ref] for ref in refs]
            if not isinstance(evidence, list) or len(evidence) < 2:
                raise ValueError("missing_fact_evidence")
            citations = []
            for item in evidence:
                if not isinstance(item, Mapping):
                    raise ValueError("invalid_fact_evidence")
                path, quote = item.get("path"), item.get("quote")
                if not isinstance(quote, str) or not quote.strip():
                    raise ValueError("invalid_fact_quote")
                value = _resolve_pointer(context, path)
                citation = {"path": path, "quote": quote}
                # Array-parent paths require unique verbatim matches; preserve original paths without rewriting quotes or silently choosing ambiguous items.
                if isinstance(value, list):
                    matches = [i for i, text in enumerate(value) if isinstance(text, str) and quote in text]
                    if len(matches) != 1:
                        raise ValueError("ambiguous_fact_evidence")
                    citation.update(original_path=path, path=f"{path}/{matches[0]}")
                    value = value[matches[0]]
                if not isinstance(value, str) or quote not in value:
                    raise ValueError("unverifiable_fact_evidence")
                citations.append(citation)
            if len({x["path"] for x in citations}) < 2:
                raise ValueError("duplicate_fact_evidence")
            if strict_repair_targets:
                nodes = {node["id"]: node for node in [*context.get("mainline", []), *context.get("endings", []),
                         *(node for branch in context.get("branches", []) for node in branch.get("nodes", []))]}
                for target in raw.get("repair_targets") or []:
                    try:
                        if target["node_id"] not in targets:
                            raise ValueError()
                        _resolve_pointer(nodes[target["node_id"]], target["field"])
                    except (KeyError, IndexError, TypeError, ValueError):
                        raise ValueError("invalid_fact_repair_target") from None
            # Share the existing node-suggestion list but use independent IDs/sources; retain all factual issues and evidence.
            issues.append({
                "issue_id": f"fact_issue_{index:02d}", "source": "facts",
                "category": raw["category"], "dimension": "plot", "severity": raw["severity"],
                "target_node_ids": list(dict.fromkeys(targets)), **fields,
                "preserve": deepcopy(preserve), "evidence": citations,
                # Scoring orchestration validates modified fields against actual node permissions, not merely model text labels.
                "repair_targets": deepcopy(raw.get("repair_targets")),
                "repair_scope": raw["repair_scope"], "repairable": raw["repair_scope"] == "text",
            })
        # The exit specialist shares citation/issue validation; the main review
        # still covers every completion definition exactly once.
        checks = _validate_contract_checks(context, payload, expected, evidence_sources, expected_target_ids) if check_contracts else []
        return {"checked_node_ids": list(checked), "issues": issues, "contract_checks": checks}
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("invalid_fact_review") from exc
