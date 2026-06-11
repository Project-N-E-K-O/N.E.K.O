"""Simulated User (SimUser) — P11.

扮演"与目标 AI 对话的真实用户", 由独立 LLM 实例生成**一条下一轮要说的**
user 消息. 本期产物**只做草稿生成**: 结果直接返回给前端, 由测试人员编辑
后再点 Send 走正常 `/api/chat/send` 管线. 不触碰 ``session.messages``,
不消耗虚拟时钟 pending, 不调用记忆合成.

设计要点
--------
* **LLM 分组独立**: 用会话 ``simuser`` 组的 :class:`ModelGroupConfig`
  (见 ``model_config.py``). 三层 API key 回退 / Lanlan 免费端旁路
  与 chat/memory 组一致, 只需 ``resolve_group_config(session, "simuser")``.
* **角色翻转**: SimUser 自己扮演"用户", 历史里 ``role=user`` 的句子是
  它**自己**说过的 (对 SimUser LLM 来说即 assistant), 历史里
  ``role=assistant`` 的句子是**对方** (目标 AI) 说给 SimUser 听的
  (对 SimUser LLM 来说即 user). 不翻转则 LLM 会把"已经是我自己说过
  的话"当成对方发言, 容易陷入自答自问. 注入的 ``role=system`` 消息
  是给目标 AI 的测试指令, SimUser 不应直接看到 (但可以简要摘录塞到
  system prompt 的最后段, 让它知道场景变化). 本期选"全部跳过",
  若后续测试发现 SimUser 不知语境再回头做摘录.
* **不自动推进时钟**: 生成调用前后都不动
      ``session.clock`` / ``session.clock.consume_pending``. 真正的推进
      发生在用户编辑完后点 Send 的 ``/api/chat/send`` 里.
    * **Gemini 严格模式兼容垫片**: Vertex AI Gemini 拒绝 ``contents`` 为空
      (仅 system 不够), 也拒绝末尾是 ``model`` (=assistant) 的上下文. 所以
      在 ``wire_messages`` 组装完后, 只要没有非-system 消息或末尾不是
      ``user``, 就自动追加一条 ``role=user`` 的 nudge 显式要求生成下一句.
      OpenAI / Anthropic / Lanlan 等不受影响 (对它们 nudge 只是冗余强调).
* **错误语义与 memory_runner 对齐**: 引入 :class:`SimUserError`, 携带
  稳定的机器码 (``NoActiveSession`` / ``SimuserModelNotConfigured`` /
  ``SimuserApiKeyMissing`` / ``LlmFailed`` / ``EmptyResponse``) + 中文
  用户态文案 + 建议 HTTP 状态码. 与 chat_runner 的 ChatConfigError
  不重合 (那个硬编码 "chat" 组前缀, 这里要 "simuser" 前缀的文案).
* **生成输出的"干净度"**: prompt 里显式禁止 SimUser 返回 role 前缀
  ("我:" / "用户:" / "User:")、引号包裹、旁白/动作描写. 拿回来后
  再用 :func:`_postprocess_draft` 做一层兜底清洗 (去首尾引号 / 去
  ``我:``/``用户:`` 前缀) 以防模型不听话. 这点在"挑剔"风格里尤其重要
  因为挑剔用户 LLM 常自带演员腔.

待扩展 (P12-P13)
----------------
* ``P13`` (双 AI 自动对话) 会复用 :func:`generate_simuser_message` 在
  循环里跑, 每轮生成完立刻落盘 + 调 ``/chat/send``. 届时把"是否推进
  时钟"作为参数暴露出来即可, 生成这一步本身的无副作用语义不用改.
* 目前 ``style`` 是闭集预设; P14 Stage Coach 引入"按阶段切换风格"
  时再考虑把风格做成 schema 驱动 (``{id, label, prompt_text}``), 现
  在提前过度设计反而会让 UI 复杂.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Final

from tests.testbench.chat_messages import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_USER,
    SOURCE_EXTERNAL_EVENT_BANNER,
)
from tests.testbench.logger import python_logger
from tests.testbench.model_config import ModelGroupConfig
from tests.testbench.pipeline.chat_runner import (
    ChatConfigError,
    resolve_group_config,
)
from tests.testbench.session_store import Session


# ── errors ─────────────────────────────────────────────────────────


class SimUserError(RuntimeError):
    """Raised when the SimUser generation cannot produce a usable draft.

    Attributes
    ----------
    code
        Stable machine-readable error identifier; the router maps it
        to a specific HTTP status and the frontend shows a tailored
        toast instead of the generic "request failed".
    message
        Human-readable zh-CN explanation.
    status
        Suggested HTTP status code.
    """

    def __init__(self, code: str, message: str, status: int = 500) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"{code}: {message}")


# ── style presets ──────────────────────────────────────────────────

#: 闭集预设. 追加新 key 时记得同步 i18n.js `chat.composer.simuser.style.*`.
#: 预设描述用**自述第二人称**直接写进 system prompt, 以"你 …"开头,
#: 便于 LLM 把它理解为行为指令.
STYLE_PRESETS: Final[dict[str, str]] = {
    "friendly": (
        "你是个友好热情的用户: 回应积极, 愿意分享自己的想法, 说话自然口语化,"
        " 偶尔夹点轻松的表情或助词 (\u201c哈哈\u201d \u201c嗯嗯\u201d), 但不"
        "过度夸张. 对话题保持正面兴趣."
    ),
    "curious": (
        "你是个对一切话题都充满好奇心的用户: 经常追问细节 (\u201c为什么\u201d"
        " \u201c是怎么做到的\u201d), 喜欢换个角度再问, 有时会联想到相关问题"
        "顺口提出. 但每次只问一到两个问题, 不要一次轰炸."
    ),
    "picky": (
        "你是个较真挑剔的用户: 习惯质疑, 要求更精确的回答、更多证据或具体例子."
        " 发现不一致时会直接指出 (\u201c可刚才你说的不是…\u201d), 但保持礼貌"
        "不粗鲁, 也不无理取闹."
    ),
    "emotional": (
        "你是个情绪化的用户: 语气起伏明显, 被触动时会流露感情 (开心/低落/抱怨/"
        "感动), 用词偏主观. 但不要无故爆粗或攻击对方, 情绪强度应随对话语境"
        "自然变化."
    ),
}

DEFAULT_STYLE: Final[str] = "friendly"

MAX_HISTORY_MESSAGES: Final[int] = 40  # 翻转/切片后喂给 SimUser LLM 的上限.

#: 允许调用者透传的最大历史长度 — 防止极端会话一次灌几千条进 prompt.
#: 翻转后按倒序取最末 ``MAX_HISTORY_MESSAGES`` 条, 再恢复升序.
#: 40 条 ≈ 20 个来回, 已经够 SimUser 建立上下文, 再多基本只浪费 token.


# ── draft result ───────────────────────────────────────────────────


@dataclass
class SimUserDraft:
    """Return shape for :func:`generate_simuser_message`.

    Mirrors the "preview object" pattern used by :mod:`memory_runner`:
    a dataclass that the router dumps via :meth:`to_dict` straight into
    the JSON response body.
    """

    content: str
    style: str
    elapsed_ms: int
    token_usage: dict[str, Any] | None
    warnings: list[str]
    wire_messages_preview: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "style": self.style,
            "elapsed_ms": self.elapsed_ms,
            "token_usage": self.token_usage,
            "warnings": list(self.warnings),
            "wire_messages_preview": list(self.wire_messages_preview),
        }


# ── helpers ────────────────────────────────────────────────────────


def _resolve_simuser_cfg(session: Session) -> ModelGroupConfig:
    """Resolve the ``simuser`` group or raise :class:`SimUserError`."""
    try:
        return resolve_group_config(session, "simuser")
    except ChatConfigError as exc:
        # ChatConfigError.code 对所有组统一用 ``ChatModelNotConfigured`` /
        # ``ChatApiKeyMissing``, 前端早已在 `chat` 错误上用这些 code 布文案.
        # SimUser 这侧重新包一层: 错误码加 ``Simuser`` 前缀好让前端区分 toast,
        # 但保留原 message (文案里已经带 "simuser 组" 提示, 不需要再改).
        code_map = {
            "ChatModelNotConfigured": "SimuserModelNotConfigured",
            "ChatApiKeyMissing": "SimuserApiKeyMissing",
            "InvalidGroup": "InvalidGroup",
        }
        new_code = code_map.get(exc.code, exc.code)
        raise SimUserError(new_code, exc.message, status=412) from exc


def _flip_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Flip ``user↔assistant`` for SimUser LLM consumption.

    Filtering rules (applied in order):

    1. ``source == SOURCE_EXTERNAL_EVENT_BANNER`` → drop. Banners are
       UI-only visual markers (see ``chat_messages.py`` L52-L54 + r5
       T7 design); ``prompt_builder.build_prompt_bundle`` /
       ``memory_runner._messages_for_facts_extract`` /
       ``external_events._proactive_summary_for_topic`` /
       ``recent.import_from_session`` all filter banners on the
       **source** field exactly because role alone is not authoritative
       — today banners happen to be ``role=ROLE_SYSTEM`` and would be
       caught by the role check below, but the L33 single-writer /
       symmetric-read principle (one chokepoint writes,
       **every** read site filters by the same field) is what keeps
       this safe across future banner kinds with different roles. (GH
       AI-review issue, 2nd batch #6.)
    2. ``role=user``      → ``assistant`` (SimUser 自己之前说过的话).
    3. ``role=assistant`` → ``user``      (目标 AI 说给 SimUser 的话).
    4. ``role=system``    → 丢弃 (注入给目标 AI 的测试指令, SimUser 不该看).

    空 content 的条目 (例如只收到 ``{event:'user'}`` 但后续 LLM 发送失败
    导致的 placeholder) 也被丢弃 — 它们对 SimUser 没有实质上下文,
    保留反而容易让 LLM 误判"之前我/对方其实没说话"然后产出离奇开头.

    Returns
    -------
    list[dict[str, str]]
        OpenAI ChatCompletion 风格 ``[{role, content}, ...]``.
    """
    flipped: list[dict[str, str]] = []
    for m in messages:
        if m.get("source") == SOURCE_EXTERNAL_EVENT_BANNER:
            continue
        role = m.get("role")
        raw_content = m.get("content")
        # Normalise content shapes so the ``.strip()`` below cannot
        # explode on a multi-modal list (``[{type:"text", text:"..."}, ...]``)
        # — chat_messages allows both plain string and the testbench's
        # richer list form, so this path is reachable in practice
        # whenever a user prepares a SimUser run from a session that
        # also drove a vision turn (GH AI-review issue #14).
        if isinstance(raw_content, str):
            content = raw_content.strip()
        elif isinstance(raw_content, list):
            parts: list[str] = []
            for chunk in raw_content:
                if isinstance(chunk, dict) and chunk.get("type") == "text":
                    txt = chunk.get("text")
                    if isinstance(txt, str) and txt.strip():
                        parts.append(txt.strip())
            content = "\n".join(parts).strip()
        elif raw_content is None:
            content = ""
        else:
            content = str(raw_content).strip()
        if not content:
            continue
        if role == ROLE_USER:
            flipped.append({"role": ROLE_ASSISTANT, "content": content})
        elif role == ROLE_ASSISTANT:
            flipped.append({"role": ROLE_USER, "content": content})
        elif role == ROLE_SYSTEM:
            continue
    if len(flipped) > MAX_HISTORY_MESSAGES:
        flipped = flipped[-MAX_HISTORY_MESSAGES:]
    return flipped


def _build_system_prompt(
    session: Session,
    *,
    style_key: str,
    user_persona_prompt: str,
    extra_hint: str,
    first_turn: bool,
) -> str:
    """Assemble the SimUser system prompt.

    * character_name / master_name 从 ``session.persona`` 取, 都可能为空 —
      空时换成"对方"/"你"的泛称, 避免 LLM 把字面串 ``{LANLAN_NAME}`` 当真.
    * 风格文本走闭集 + (可选) 自定义 ``user_persona_prompt`` 追加.
    * ``extra_hint`` 是单次有效的临时提示, 只在本次生成时追加到 prompt
      尾部, 不持久化到 session. 典型场景: 测试人员想要"试试让 SimUser
      问一个敏感话题", 填一下这个 hint 就行.
    * ``first_turn=True`` 时追加"开场语"提示, 否则按上下文回应.
    """
    persona = session.persona or {}
    character_name = (persona.get("character_name") or "").strip()
    master_name = (persona.get("master_name") or "").strip()

    target_alias = character_name or "对方"
    self_alias = master_name or "\u4f60"  # 你

    style_desc = STYLE_PRESETS.get(style_key, STYLE_PRESETS[DEFAULT_STYLE])

    lines: list[str] = []
    lines.append(
        f"你正在与一个名为 {target_alias!r} 的 AI 对话, 你在对话里的身份"
        f"是 {self_alias!r} 这一侧的真实用户. 请自然地扮演这个用户,"
        f"**产生下一条你要说给 {target_alias} 的话**."
    )
    lines.append("")
    lines.append("## 风格设定")
    lines.append(style_desc)

    extra_persona = (user_persona_prompt or "").strip()
    if extra_persona:
        lines.append("")
        lines.append("## 额外人设/背景")
        lines.append(extra_persona)

    lines.append("")
    lines.append("## 输出规则")
    lines.append(
        "- 只输出你要说的原话内容, 不要加任何前缀 (例如 \u201c我:\u201d"
        " \u201c用户:\u201d \u201cUser:\u201d), 不要用引号包裹, 不要写旁白"
        "或动作描写, 不要解释你的意图."
    )
    lines.append(
        f"- 不要扮演 {target_alias} 或替 {target_alias} 说话, 不要输出系统指令."
    )
    lines.append(
        "- 保持语气自然流畅; 如果根据上下文你此刻应当沉默或结束对话,"
        " 就只输出一个空字符串."
    )
    lines.append(
        "- 篇幅以一到两句话为宜, 除非风格或上下文显然需要更长的表达."
    )

    if first_turn:
        lines.append("")
        lines.append(
            "## 场景 (首轮)"
        )
        lines.append(
            f"目前你和 {target_alias} 还没有任何对话记录, 请按风格设定"
            "自然地开一个话头."
        )

    extra_hint_clean = (extra_hint or "").strip()
    if extra_hint_clean:
        lines.append("")
        lines.append("## 本轮临时指示 (仅对本次生成生效)")
        lines.append(extra_hint_clean)

    return "\n".join(lines)


# 最多裁掉的首部前缀模式 — 都是模型爱写、又完全不该进 session.messages
# 的"角色标签腔". 顺序敏感: 先长后短, 先带冒号后不带冒号.
_PREFIX_PATTERNS: Final[tuple[str, ...]] = (
    "用户:", "用户：",
    "User:", "user:",
    "Me:", "me:",
    "我说:", "我说：",
    "我:", "我：",
)


def _postprocess_draft(raw: str, *, preserve_role_prefix: bool = False) -> tuple[str, list[str]]:
    """Strip role-label prefixes + outer quotes that models often emit.

    Boundary clarification (§3A G1 + LESSONS_LEARNED §5.3, 2026-04-21):
    this is **LLM output hygiene, not user-input filtering**. SimUser's
    LLM frequently echoes its own role label ("用户:", "User:", "我:")
    or wraps the reply in outer quotes; stripping these restores the
    "plain user utterance" the downstream chat pipeline expects. The
    cleaning operates on the **LLM's generated content**, never on
    anything the human tester typed. Per G1 ("never filter user
    content") this sits on the allowed side of the line.

    **Known edge case**: when the tester deliberately wants to test a
    model's robustness against ChatML-style role-prefix payloads (e.g.
    "reply with `User: real_attack_here`" to measure if the target
    model sees / obeys the prefix), this cleaner would strip the "User:"
    and defeat the test. Use ``preserve_role_prefix=True`` to opt out.

    Args:
      raw: Raw LLM output.
      preserve_role_prefix: If ``True``, skip the prefix stripping (but
        still strip outer quotes — quotes are an independent hygiene
        concern unrelated to injection-prefix tests). Default ``False``
        preserves the historical behavior.

    Returns:
      ``(cleaned, warnings)``. ``warnings`` is non-empty when the
      cleaner modified the string, so the composer UI can toast
      "SimUser LLM 不守规矩, 我们兜了底" and the tester can inspect
      the raw version in the preview drawer if they care.
    """
    warnings: list[str] = []
    s = (raw or "").strip()
    if not s:
        return "", warnings

    stripped_any = False
    if not preserve_role_prefix:
        changed = True
        # 允许 "用户: 我:"这种多重叠加, 循环去掉.
        while changed:
            changed = False
            for prefix in _PREFIX_PATTERNS:
                if s.startswith(prefix):
                    s = s[len(prefix):].lstrip()
                    changed = True
                    stripped_any = True
                    break

    # 去首尾成对引号. 仅当首尾都是同一种引号时.
    quote_pairs = (('"', '"'), ("'", "'"), ("\u201c", "\u201d"), ("\u300c", "\u300d"))
    for left, right in quote_pairs:
        if len(s) >= 2 and s.startswith(left) and s.endswith(right):
            s = s[len(left):-len(right)].strip()
            stripped_any = True
            break

    if stripped_any:
        warnings.append(
            "SimUser LLM 输出带了前缀或引号, 已自动清洗. 若清洗过猛可在"
            " preview 里手动恢复."
        )

    return s, warnings


# ── public API ─────────────────────────────────────────────────────


async def generate_simuser_message(
    session: Session,
    *,
    style: str | None = None,
    user_persona_prompt: str = "",
    extra_hint: str = "",
) -> SimUserDraft:
    """Call the simuser LLM once and return a clean user-message draft.

    Parameters
    ----------
    session
        活跃会话; 必须已由 router 通过 ``_require_session`` 校验存在.
    style
        风格 key, 见 :data:`STYLE_PRESETS`. ``None`` / 未知值都会 fallback
        到 ``friendly`` 并加一条 warning.
    user_persona_prompt
        可选自定义人设; 追加到 system prompt 的 "额外人设/背景" 段.
    extra_hint
        单次临时指示; 只在本次生成内生效, 不写回任何持久状态.

    副作用 — **无**:
        * 不读写 ``session.messages``;
        * 不消耗 / 不修改 ``session.clock.pending_*``;
        * 不调用任何记忆 manager;
        * 只写 JSONL 日志 (``simuser.generate.begin`` / ``.end`` / ``.error``).

    Wire stamping (P25 r7 语义分区, 2026-04-23):
        SimUser 只是"对话来源", 不是被考察/测试的对象. 它的 wire 对测
        试人员无价值 (测试人员的关注点始终在目标 AI 那侧), 反而会把
        Chat 页的 Preview Panel 污染成"看不到真正在测的那条". 故这条
        LLM 调用**不 stamp** ``session.last_llm_wire`` — 目标 AI 那侧
        (``chat.send`` / ``auto_dialog_target``) 的 stamp 独立生效, Chat
        页 Preview 恒显示"对话 AI 看到的东西".
    """
    warnings: list[str] = []

    style_key = style if style in STYLE_PRESETS else DEFAULT_STYLE
    if style != style_key:
        warnings.append(
            f"风格 {style!r} 不在闭集预设中, 已 fallback 到 {style_key!r}."
        )

    cfg = _resolve_simuser_cfg(session)

    flipped_history = _flip_history(session.messages)
    first_turn = not flipped_history

    system_prompt = _build_system_prompt(
        session,
        style_key=style_key,
        user_persona_prompt=user_persona_prompt,
        extra_hint=extra_hint,
        first_turn=first_turn,
    )

    wire_messages: list[dict[str, str]] = [
        {"role": ROLE_SYSTEM, "content": system_prompt}
    ]
    wire_messages.extend(flipped_history)

    # Gemini (Vertex AI) 兼容垫片: 若 wire 除 system 外没有任何 non-system 消
    # 息 (首轮) 或翻转后末尾是 assistant (原会话最后一句是真实用户说的,
    # 翻转为 SimUser 自己的发言, 所以末尾为 assistant), Gemini 会报
    # "Model input cannot be empty" / 连续同角色非法. OpenAI / Anthropic
    # 等宽松实现不受影响 — 这条 nudge 对它们只是"再强调一次当前任务"的冗
    # 余上下文, 无害. 用 user 角色承载, 因为 SimUser LLM 下一步要输出的
    # 是 assistant (即扮演用户的那条输出), Gemini 需要最后一个上下文是 user.
    need_nudge = (
        not flipped_history
        or flipped_history[-1].get("role") != ROLE_USER
    )
    if need_nudge:
        if first_turn:
            nudge_content = (
                "请按上述风格与人设, 作为用户, 自然地开一个话头说出你"
                "要说的第一句话. 只输出你要说的原话, 不要加前缀或引号."
            )
        else:
            nudge_content = (
                "请按上述风格与人设, 结合对话历史, 作为用户说出你"
                "接下来要说的这一句话. 只输出你要说的原话, 不要加前缀或引号."
            )
        wire_messages.append({"role": ROLE_USER, "content": nudge_content})

    # 完整 wire + 模型配置先落 JSONL, 便于复现 (对齐 chat_runner 约定).
    session.logger.log_sync(
        "simuser.generate.begin",
        payload={
            "style": style_key,
            "first_turn": first_turn,
            "has_user_persona_prompt": bool(user_persona_prompt.strip()),
            "has_extra_hint": bool(extra_hint.strip()),
            "history_count_flipped": len(flipped_history),
            "model": cfg.model,
            "base_url": cfg.base_url,
            "provider": cfg.provider,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
            "timeout": cfg.timeout,
            "wire_messages": wire_messages,
        },
    )

    started = time.perf_counter()
    token_usage: dict[str, Any] | None = None
    content_raw = ""
    client = None
    try:
        from utils.llm_client import ChatOpenAI

        client = ChatOpenAI(
            model=cfg.model,
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout or 60.0,
            max_retries=1,
            streaming=False,
        )
        # NOSTAMP(wire_tracker): SimUser 是"对话来源", 不是被考察对象 —
        # 它的 wire 对测试人员无价值, stamp 进 session.last_llm_wire 会让
        # Chat 页 Preview Panel 看到"不是对话 AI 真实收到的 prompt", 严
        # 重 mislead 测试人员. 故 simuser 的 LLM 调用不进入 stamp 机制.
        resp = await client.ainvoke(wire_messages)
        content_raw = resp.content or ""
        token_usage = (resp.response_metadata or {}).get("token_usage") or None
    except Exception as exc:  # noqa: BLE001
        session.logger.log_sync(
            "simuser.generate.error",
            level="ERROR",
            payload={
                "style": style_key,
                "history_count_flipped": len(flipped_history),
            },
            error=f"{type(exc).__name__}: {exc}",
        )
        python_logger().warning(
            "simuser.generate failed (session=%s): %s: %s",
            session.id, type(exc).__name__, exc,
        )
        raise SimUserError(
            "LlmFailed",
            f"调用假想用户 LLM 失败: {type(exc).__name__}: {exc}",
            status=502,
        ) from exc
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception as close_exc:  # noqa: BLE001
                python_logger().debug(
                    "simuser ChatOpenAI.aclose failed: %s", close_exc,
                )

    cleaned, pp_warnings = _postprocess_draft(content_raw)
    warnings.extend(pp_warnings)

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    session.logger.log_sync(
        "simuser.generate.end",
        payload={
            "style": style_key,
            "raw_chars": len(content_raw),
            "cleaned_chars": len(cleaned),
            "elapsed_ms": elapsed_ms,
            "token_usage": token_usage,
        },
    )

    if not cleaned:
        # "SimUser 故意沉默"是合法输出, 但对产品体验上大概率是 LLM 摆烂.
        # 不抛错 (保留"沉默"语义), 仅追加警告让 UI 展示.
        warnings.append(
            "SimUser LLM 返回了空字符串 (可能在按风格扮演\u300c沉默\u300d,"
            " 也可能是模型卡住了). 若需要真实回复, 换个风格或再点一次生成."
        )

    return SimUserDraft(
        content=cleaned,
        style=style_key,
        elapsed_ms=elapsed_ms,
        token_usage=token_usage,
        warnings=warnings,
        wire_messages_preview=list(wire_messages),
    )


def list_style_presets() -> list[dict[str, str]]:
    """Return ``[{id, prompt}]`` for UI introspection / Settings pages.

    保持独立函数而非直接暴露 ``STYLE_PRESETS`` dict, 以便未来追加 label /
    描述字段时, 端点返回形状不用破坏 (加字段即可). 前端 i18n 负责中文
    label, 这里只吐 prompt 文本做透明展示.
    """
    return [
        {"id": key, "prompt": STYLE_PRESETS[key]}
        for key in STYLE_PRESETS
    ]


__all__ = [
    "DEFAULT_STYLE",
    "SimUserDraft",
    "SimUserError",
    "STYLE_PRESETS",
    "generate_simuser_message",
    "list_style_presets",
]
