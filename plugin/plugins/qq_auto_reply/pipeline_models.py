from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(slots=True)
class QQPipelineStageTrace:
    stage: str
    status: str
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# 合成来源：这些轮次的 sender 只是名义上的发言人（主动搭话的控制指令、
# 缓冲合并/确认、延迟投递、回溯补回、入群通知），其 prompt 文本不是这个人
# 说的话。写侧（不入 participant bucket）、读侧（不召回该成员的 scoped
# 记忆）、mention 计数三处必须用同一份判据，否则一处漏掉就等于用别人的
# 私人事实去生成公开发言。
SYNTHETIC_SOURCE_KINDS = frozenset({
    "proactive_speech",
    "rapid_fire_flush",
    "buffer_delayed",
    "retroactive_review",
    "group_join_notice",
})


def delivered_blocks_text(blocks) -> str:
    """All user-visible text of a plan, not just the first block.

    A `<msg>` reply can span several blocks; postprocess keeps only the
    first one in `reply_text`. Anything disclosed in a later text/voice
    block would otherwise never bump its scoped mention counter and never
    reach anti-repeat suppression."""
    parts: list[str] = []
    for block in blocks or []:
        # 与投递侧同一优先级：record 块在文本之前被处理并 continue，所以
        # 一个既有 text 又有 record 的块，用户听到的是语音、看不到那段
        # 文本——两段都记会把没送出去的内容写进记忆与 mention 计数。
        record = str(getattr(block, "record", "") or "").strip()
        value = record or str(getattr(block, "text", "") or "").strip()
        if value:
            parts.append(value)
        # 选项文案在**文本块**上才会送到用户面前（开放平台渲染成按钮，
        # NapCat/私聊把它并进正文，语音把它念出来）。record 块走的是另一
        # 条分支并直接 continue，keyboard 根本不会渲染——把它记下来等于
        # 让没人看过的选项进记忆与 mention 计数。
        if not record:
            labels = " / ".join(
                part.strip()
                for part in str(getattr(block, "keyboard", "") or "").split("|")
                if part.strip()
            )
            if labels:
                parts.append(labels)
    return "\n".join(parts)


def is_synthetic_source(source_kind: str | None) -> bool:
    """True when the turn's nominal sender did not actually say anything."""
    return str(source_kind or "") in SYNTHETIC_SOURCE_KINDS


@dataclass(slots=True)
class QQReplyRequest:
    message_text: str
    sender_id: str
    attachments: list[dict[str, Any]] | None = None
    is_group: bool = False
    group_id: Optional[str] = None
    user_nickname: Optional[str] = None
    is_at_bot: bool = False
    source_kind: str = "incoming"
    use_memory_context: Optional[bool] = None
    persist_memory: Optional[bool] = None
    ephemeral_session: bool = False
    group_facing: bool = False
    group_scene_mode: str = ""
    current_message_id: str = ""
    quoted_message_id: str = ""
    mentioned_user_ids: list[str] = field(default_factory=list)
    mentions_other_user: bool = False
    mentions_all: bool = False
    reply_message_id: str = ""
    at_user_id: str = ""
    fallback_to_text_on_voice_failure: bool = True
    # 内嵌合成轮（ack / 强制总结 / 缓冲汇总）继承缓冲里那些草稿的授权
    # 依赖：合成轮自己的 prompt 是干净的（快照为空），但它原样引用了
    # 记忆派生的旧草稿，撤销必须能作用到它。
    inherited_consent_snapshot: dict[str, bool] = field(default_factory=dict)
    permission_level_override: str | None = None
    force_reply: bool = False
    suppression_reason: str = ""
    forward_sub_count: int = 0
    # 接收边界的 member 记忆政策快照（None=旁路调用者，build 内回退实时
    # 读）：handler 排队期间 OFF->ON 不得让收到时无授权的发言被收集。
    member_memory_at_receipt: bool | None = None
    # 接收边界的私聊 participant 记忆政策快照（语义同上，作用于非 admin
    # 私聊轮；admin 私聊与群轮忽略它）。
    participant_memory_at_receipt: bool | None = None
    # 私聊权限的接收边界快照。权限编辑可能发生在 handler 排队期间；记忆
    # 域必须仍按消息到达时的 admin/participant 身份选择。
    private_permission_level_at_receipt: str | None = None


@dataclass(slots=True)
class QQReplyDecision:
    action: str
    permission_level: str
    relay_probability: float | None = None
    attention_enabled: bool = False
    attention_score: float | None = None
    attention_focus_group_id: str = ""
    attention_focus_score: float | None = None
    attention_multiplier: float | None = None
    attention_gate_reason: str = ""


@dataclass(slots=True)
class QQInstructionBundle:
    system_prompt: str
    memory_context_used: bool
    core_memory_text: str
    scene_mode: str
    # 跨群上下文段原文（未注入时为空）：consent 是运行时开关，构建后到
    # 生成前的 await 窗口里可能被关掉/回滚，届时按原文从 prompt 中摘除。
    cross_group_section: str = ""
    # 活跃会话清单里披露了其他会话时的原文：与话题段同为跨群内容，撤销时
    # 一并撤除、同样计入授权依赖（私聊轮的话题段恒为空，此前那条路径根本
    # 没有可撤的依赖）。
    cross_session_section: str = ""
    # core memory 段是否含 participant 域内容：member 开关在后续 await
    # 窗口里被关掉时，该段要按同样方式撤除。
    used_member_subject: bool = False


@dataclass(slots=True)
class QQReplyContext:
    message: str
    attachments: list[dict[str, Any]] | None
    permission_level: str
    sender_id: str
    is_group: bool
    group_id: str | None
    user_nickname: str | None
    use_memory_context: bool
    persist_memory: bool
    ephemeral_session: bool
    group_facing: bool
    group_scene_mode: str
    scene_mode: str
    master_name: str
    her_name: str
    user_title: str
    character_prompt: str
    character_card_fields: dict[str, Any]
    prompt_message: str
    system_prompt: str
    memory_context_used: bool
    core_memory_text: str
    recalled_memory_text: str
    recalled_memory_used: bool
    login_status: str
    login_self_id: str | None
    login_nickname: str | None
    current_message_id: str = ""
    force_reply: bool = False
    source_kind: str = ""
    # 轮次构建时刻的 group_member_memory_enabled 快照：成员发言入 bucket
    # 与否绑定发言时刻的授权状态——生成期间才切 ON 的轮不得回溯收集。
    member_memory_enabled: bool = False
    # 本轮是否为私聊 participant 记忆轮（非 admin 私聊 + 接收时刻政策
    # ON）：读写都以对方的 participant 域为界，绝不落入 legacy 私聊主人
    # 语料（bridge 侧 subjects=None 的语义）。
    participant_memory_enabled: bool = False
    # 本轮按接收时权限选择的持久化域；None 表示本轮没有私聊记忆授权。
    private_memory_mode: str | None = None
    private_permission_level_at_receipt: str | None = None
    # 本轮 prompt 里的跨群段原文（未注入时为空）：生成前在会话锁内复检
    # 授权，撤销时按原文摘除。
    cross_group_section: str = ""
    # 同上，活跃会话清单段。
    cross_session_section: str = ""
    # core memory 段是否含 participant 域：member 授权在生成前被撤销时
    # 该段（及混合域召回）要一并撤除。
    used_member_subject: bool = False
    # 本轮召回通道：True=挂 recall_memory 工具、模型自主决定何时查（此时
    # 构建期不做同步召回，recalled_memory_text 恒空）；False=线路不支持
    # tool call（免费代理会静默丢 tools），回落到宿主生成前同步召回。
    recall_via_tool: bool = False
    # 本轮上下文的唯一标识：投递钩子的幂等键。绝不能用 id(context)——
    # CPython 会把刚释放的同尺寸对象原样发回，下一轮的 context 常常拿到
    # 同一地址，幂等扫描会把新一轮的行误判成"已经补过了"。
    turn_uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    # 生成时刻的授权依赖快照：直投路径在真正发出去之前再比一次（buffer
    # 路径由 PendingReply.consent_snapshot 负责），"生成完成→发送"之间
    # 的窗口也不得漏掉撤销。None=还没生成过（读当前设置兜底）；空 dict
    # 是有意义的值——本轮没用任何记忆，撤销与它无关，不能当成"没快照"
    # 而去采样当前开关，否则一条与记忆无关的草稿会被无谓丢弃。
    consent_snapshot: dict[str, bool] | None = None
    traces: list[QQPipelineStageTrace] = field(default_factory=list)


@dataclass(slots=True)
class QQModelResult:
    reply_text: str | None = None
    # Exact visible prefix emitted before the tool-call turn. Postprocess uses
    # this structural boundary instead of guessing from literal <msg> text.
    pre_tool_text: str = ""
    source: str = "none"
    used_fallback: bool = False
    timed_out: bool = False
    allow_fallback: bool = False
    fallback_reason: str = ""
    traces: list[QQPipelineStageTrace] = field(default_factory=list)
    # Exact session-history row produced by this generation. Delivery can
    # finish after a later turn has overwritten the session-wide current row.
    history_ai_row: Any = None


@dataclass(slots=True)
class QQRelayPlan:
    source_type: str
    source_id: str
    sender_id: str
    original_message: str
    relay_text: str
    relay_probability: float
    target_admin_qq: str


@dataclass(slots=True)
class QQRelayResult:
    relayed: bool
    source_type: str
    source_id: str
    sender_id: str
    relay_text: str | None


@dataclass(slots=True)
class QQMessageBlock:
    """KiraAI-style 消息块：对应 LLM 输出的一个 <msg>...</msg>"""
    text: str = ""
    emoji: str = ""        # QQ 表情 ID（如 "277"）
    at_user: str = ""       # @的QQ号
    reply_to: str = ""      # 引用的消息ID
    sticker: str = ""       # 表情包 ID
    poke: str = ""          # 戳一戳目标 QQ
    record: str = ""        # <record> 语音文本
    keyboard: str = ""      # 按钮文本
    ark: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class QQDeliveryPlan:
    target_type: str
    target_id: str
    blocks: list[QQMessageBlock] = field(default_factory=list)
    fallback_to_text_on_voice_failure: bool = True


@dataclass(slots=True)
class QQDeliveryResult:
    delivered: bool
    target_type: str
    target_id: str
    reply_text: str | None


@dataclass(slots=True)
class QQReplyOutcome:
    action: str
    reply_text: str | None = None
    used_default_message: bool = False
    # True when the reply came from the direct-LLM fallback: the shared
    # session history has NO ai row for this turn, so the buffer must not
    # mark the previous (delivered) reply as an undelivered draft.
    used_fallback: bool = False
    raw_reply_text: str | None = None
    pre_tool_text: str = ""
    wait_directive_text: str | None = None
    postprocess_reason: str = ""
    blocks: list[QQMessageBlock] = field(default_factory=list)
    relay_plan: QQRelayPlan | None = None
    relay_result: QQRelayResult | None = None
    delivery_plan: QQDeliveryPlan | None = None
    delivery_result: QQDeliveryResult | None = None
    traces: list[QQPipelineStageTrace] = field(default_factory=list)
    history_ai_row: Any = None
