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
    # 本轮 prompt 里的跨群段原文（未注入时为空）：生成前在会话锁内复检
    # 授权，撤销时按原文摘除。
    cross_group_section: str = ""
    # core memory 段是否含 participant 域：member 授权在生成前被撤销时
    # 该段（及混合域召回）要一并撤除。
    used_member_subject: bool = False
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
    source: str = "none"
    used_fallback: bool = False
    timed_out: bool = False
    allow_fallback: bool = False
    fallback_reason: str = ""
    traces: list[QQPipelineStageTrace] = field(default_factory=list)


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
    postprocess_reason: str = ""
    blocks: list[QQMessageBlock] = field(default_factory=list)
    relay_plan: QQRelayPlan | None = None
    relay_result: QQRelayResult | None = None
    delivery_plan: QQDeliveryPlan | None = None
    delivery_result: QQDeliveryResult | None = None
    traces: list[QQPipelineStageTrace] = field(default_factory=list)
