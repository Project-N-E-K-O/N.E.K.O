from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(slots=True)
class QQPipelineStageTrace:
    stage: str
    status: str
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


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
    permission_level_override: str | None = None
    force_reply: bool = False
    suppression_reason: str = ""
    forward_sub_count: int = 0
    reply_context: str = ""     # _fetch_reply_content 产生的引用上下文，仅注入 LLM 不存历史


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
    rps: bool = False        # 猜拳
    dice: bool = False       # 骰子
    contact_type: str = ""   # 推荐类型 qq/group
    contact_id: str = ""     # 推荐目标
    music_type: str = ""     # qq/163/kugou/custom
    music_id: str = ""
    music_url: str = ""; music_audio: str = ""; music_title: str = ""; music_singer: str = ""; music_image: str = ""
    mface_id: str = ""; mface_pkg: str = ""; mface_key: str = ""
    file_name: str = ""; file_path: str = ""
    json_data: str = ""


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
    raw_reply_text: str | None = None
    postprocess_reason: str = ""
    blocks: list[QQMessageBlock] = field(default_factory=list)
    emoji_reaction_id: str = ""
    feeling: str = ""                 # <feeling> 标签提取的情绪
    forward_content: str = ""
    forward_target: str = ""
    relay_plan: QQRelayPlan | None = None
    relay_result: QQRelayResult | None = None
    delivery_plan: QQDeliveryPlan | None = None
    delivery_result: QQDeliveryResult | None = None
    traces: list[QQPipelineStageTrace] = field(default_factory=list)
