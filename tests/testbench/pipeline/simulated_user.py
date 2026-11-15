"""Simulated User (SimUser) — P11.

An independent LLM impersonates the real user talking to the target AI and generates **one next-turn**
user message. This iteration **only generates drafts**: return the result directly to the frontend for testers to edit
and Send through the normal `/api/chat/send` pipeline. Do not touch ``session.messages``,
consume pending virtual-clock time, or invoke memory synthesis.

Design notes
------------
* **Independent LLM group**: use the session's ``simuser`` :class:`ModelGroupConfig`
  (see ``model_config.py``). Three-level API-key fallback / Lanlan free-endpoint bypass
  follow chat/memory groups; simply call ``resolve_group_config(session, "simuser")``.
* **Role reversal**: SimUser impersonates the user, so historical ``role=user`` messages
  are its **own** words (assistant to the SimUser LLM), while historical
  ``role=assistant`` messages are what the **other party** (target AI) said to SimUser
  (user to the SimUser LLM). Without reversal the LLM mistakes its own previous words
  for the other party's and easily starts answering itself. Injected ``role=system`` messages
  are test instructions for the target AI and should not be directly visible to SimUser (though
  brief excerpts could be appended to the system prompt to explain scenario changes). This iteration skips all;
  revisit excerpts if later tests show SimUser lacks context.
* **No automatic clock advancement**: leave
      ``session.clock`` / ``session.clock.consume_pending`` untouched before and after generation. Actual advancement
      occurs in ``/api/chat/send`` when the user finishes editing and clicks Send.
    * **Gemini strict-mode compatibility shim**: Vertex AI Gemini rejects empty ``contents``
      (system alone is insufficient) and context ending in ``model`` (=assistant). Thus,
      after assembling ``wire_messages``, append an explicit next-message generation nudge
      with ``role=user`` whenever there are no non-system messages or the last role is not ``user``.
      OpenAI / Anthropic / Lanlan are unaffected (the nudge merely adds redundant emphasis).
* **Error semantics match memory_runner**: :class:`SimUserError` carries
  stable machine codes (``NoActiveSession`` / ``SimuserModelNotConfigured`` /
  ``SimuserApiKeyMissing`` / ``LlmFailed`` / ``EmptyResponse``), Chinese
  user-facing wording, and a suggested HTTP status. This differs from chat_runner's ChatConfigError,
  whose wording hardcodes the "chat" group prefix rather than the required "simuser" prefix.
* **Clean generated output**: the prompt explicitly forbids role prefixes
  ("我:" / "用户:" / "User:"), surrounding quotes, narration, and action descriptions. After receiving output,
  :func:`_postprocess_draft` provides fallback cleanup (strip outer quotes /
  ``我:``/``用户:`` prefixes) in case the model ignores instructions. This especially matters for the picky style,
  whose user LLM often adopts an actor's voice.

Future extensions (P12-P13)
--------------------------
* ``P13`` (automatic dual-AI dialogue) will reuse :func:`generate_simuser_message` in
  a loop, persisting each generated turn immediately and invoking ``/chat/send``. Expose clock advancement
  as a parameter then; generation itself can retain its side-effect-free semantics.
* ``style`` is currently a closed preset set; when P14 Stage Coach introduces switching styles
  by stage, consider schema-driven styles (``{id, label, prompt_text}``). Premature
  design now would only complicate the UI.
"""  # noqa: DOCSTRING_CJK
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

#: Closed preset set. Synchronize i18n.js `chat.composer.simuser.style.*` when adding keys.
#: Preset descriptions enter the system prompt directly as second-person self-instructions beginning with "你 …" (you ...),
#: helping the LLM interpret them as behavioral instructions.
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

MAX_HISTORY_MESSAGES: Final[int] = 40  # Maximum messages fed to the SimUser LLM after role reversal and slicing.

#: Maximum history callers may pass through, preventing extreme sessions from flooding the prompt with thousands of messages.
#: After role reversal, take the last ``MAX_HISTORY_MESSAGES`` in reverse order, then restore ascending order.
#: 40 messages are about 20 exchanges, enough context for SimUser; additional messages mostly waste tokens.


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
        # ChatConfigError.code uses ``ChatModelNotConfigured`` / ``ChatApiKeyMissing`` uniformly across groups;
        # the frontend already uses these codes to select wording for `chat` errors.
        # Wrap again on the SimUser side: prefix error codes with ``Simuser`` so the frontend can distinguish toasts,
        # while retaining the original message (already mentioning "simuser 组", the simuser group).
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
    2. ``role=user``      → ``assistant`` (SimUser's own earlier words).
    3. ``role=assistant`` → ``user``      (the target AI's words to SimUser).
    4. ``role=system``    → drop (test instructions injected for the target AI, hidden from SimUser).

    Entries with empty content (for example, a placeholder after receiving only ``{event:'user'}``
    when the subsequent LLM send fails) are dropped too: they add no substantive context for SimUser,
    and retaining them can make the LLM assume either party previously said nothing and produce bizarre openings.

    Returns
    -------
    list[dict[str, str]]
        OpenAI ChatCompletion-style ``[{role, content}, ...]``.
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

    * Read character_name / master_name from ``session.persona``; either may be empty.
      Substitute generic other-party/you references when empty, avoiding literal ``{LANLAN_NAME}`` interpretation.
    * Use closed-set style text plus optional custom ``user_persona_prompt``.
    * ``extra_hint`` is temporary guidance for this generation only, appended to the prompt
      without persisting to the session. For example, testers can use this hint to ask SimUser
      to raise a sensitive topic.
    * Append opening-line guidance when ``first_turn=True``; otherwise respond to context.
    """
    persona = session.persona or {}
    character_name = (persona.get("character_name") or "").strip()
    master_name = (persona.get("master_name") or "").strip()

    target_alias = character_name or "对方"
    self_alias = master_name or "\u4f60"  # You

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


# Leading prefix patterns to strip: role-label phrasing models often emit that must never enter session.messages.
# Order matters: longer before shorter, with-colon before without-colon.
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
      "The SimUser LLM ignored instructions; fallback cleanup was applied" and the tester can inspect
      the raw version in the preview drawer if they care.
    """  # noqa: DOCSTRING_CJK
    warnings: list[str] = []
    s = (raw or "").strip()
    if not s:
        return "", warnings

    stripped_any = False
    if not preserve_role_prefix:
        changed = True
        # Allow stacked prefixes such as "用户: 我:" (user: me:); remove them iteratively.
        while changed:
            changed = False
            for prefix in _PREFIX_PATTERNS:
                if s.startswith(prefix):
                    s = s[len(prefix):].lstrip()
                    changed = True
                    stripped_any = True
                    break

    # Remove paired surrounding quotes only when both ends use the same quote type.
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
        Active session; the router must already have checked existence through ``_require_session``.
    style
        Style key; see :data:`STYLE_PRESETS`. ``None`` / unknown values fall back
        to ``friendly`` with a warning.
    user_persona_prompt
        Optional custom persona appended to the system prompt's additional persona/background section.
    extra_hint
        Temporary instructions for this generation only, never written to persistent state.

    Side effects — **none**:
        * Do not read or write ``session.messages``;
        * Do not consume or modify ``session.clock.pending_*``;
        * Do not invoke any memory manager;
        * Only write JSONL logs (``simuser.generate.begin`` / ``.end`` / ``.error``).

    Wire stamping (P25 r7 semantic partitioning, 2026-04-23):
        SimUser is only the conversation source, not the subject being evaluated or tested. Its wire adds no
        value for testers (whose focus always remains on the target AI), and instead pollutes the
        Chat page Preview Panel so the actual evaluated call is hidden. Therefore this
        LLM call **does not stamp** ``session.last_llm_wire``; stamps on the target AI side
        (``chat.send`` / ``auto_dialog_target``) remain independent, and the Chat
        page Preview always shows what the conversation AI sees.
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

    # Gemini (Vertex AI) compatibility shim: when wire has no non-system messages besides system
    # (first turn), or ends in assistant after reversal (the real user's last message becomes SimUser's own
    # assistant message), Gemini reports
    # "Model input cannot be empty" / invalid consecutive same-role messages. Permissive OpenAI / Anthropic
    # implementations are unaffected: this nudge is harmless redundant context reinforcing the current
    # task. Use the user role because the SimUser LLM must next emit assistant
    # (the output impersonating the user), and Gemini requires user as the last context role.
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

    # Persist the complete wire and model configuration to JSONL first for reproducibility, following chat_runner conventions.
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
        # NOSTAMP(wire_tracker): SimUser is the conversation source, not the subject under evaluation;
        # its wire is irrelevant to testers. Stamping it into session.last_llm_wire would cause
        # the Chat page Preview Panel to show a prompt other than what the conversation AI actually received,
        # seriously misleading testers. Therefore SimUser LLM calls bypass stamping.
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
        # Deliberate SimUser silence is valid output, but usually indicates the LLM shirking its task from a product perspective.
        # Do not throw (preserve silence semantics); append only a warning for the UI.
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

    Keep this as an independent function rather than directly exposing ``STYLE_PRESETS``, allowing future label /
    description fields without breaking the endpoint's return shape (simply add fields). Frontend i18n provides Chinese
    labels; expose only prompt text here for transparency.
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
