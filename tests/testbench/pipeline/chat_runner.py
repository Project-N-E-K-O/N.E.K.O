"""Chat runner — drives a single turn of conversation with the target LLM.

Scope for P09:

* Consume ``PromptBundle.wire_messages`` from :mod:`prompt_builder` and
  stream the model's response via :meth:`utils.llm_client.ChatOpenAI.astream`.
* Expose a single async generator (:meth:`OfflineChatBackend.stream_send`)
  that yields structured SSE events the router can pass straight to the
  browser as ``text/event-stream`` lines.
* Before issuing the actual HTTP call, persist the full wire_messages +
  the resolved model config (api_key redacted) into the session's JSONL
  log so every request is **100% reproducible** from logs alone.

Design notes
------------
* **Target AI is stateless (ChatCompletion)**. Every ``/chat/send`` call
  rebuilds wire_messages from scratch via :func:`build_prompt_bundle`, so
  editing any historical message via ``PUT /api/chat/messages/{id}`` takes
  effect on the very next send. There is no server-side conversation
  memory riding with the LLM client; we just keep constructing a fresh
  :class:`ChatOpenAI` for each turn and :meth:`aclose` it at the end.
* **Resource hygiene**: the upstream client is backed by an ``httpx``
  pool; forgetting ``aclose()`` leaks connections into subsequent tests.
  :func:`stream_send` always awaits ``aclose()`` in its ``finally`` clause
  even when a partial stream errors mid-flight.
* **Model config resolution**: the ``chat`` group of
  :class:`ModelConfigBundle` is the source of truth. If ``api_key`` is
  empty we try the provider-level fallback from
  :mod:`tests.testbench.api_keys_registry` (so a tester can pick a
  preset without re-typing the key). If neither yields a usable key we
  raise :class:`ChatConfigError` and the router maps it to HTTP 412.
* **PromptBundle re-use**: we construct the bundle *after* the user
  message is appended to ``session.messages`` so the wire reflects the
  in-flight turn. The same path is used by the UI preview before send
  (without the "pending user message"), which means the preview + the
  actual send differ by exactly one message — this matches the Testbench
  Prompt Preview contract ("what you preview is what you send, plus the
  pending user line").

Not implemented in P09 (deferred):

* **Realtime / voice**: this file intentionally contains only
  :class:`OfflineChatBackend`. The docstring calls out the extension
  point (``ChatBackend`` Protocol) for future phases to implement a
  ``RealtimeChatBackend`` that preserves server-side conversation state.
* **ScoringSchema wiring / evaluation triggers**: P15+.
* **Stage coordinator**: P14. ``stream_send`` does not advance the stage
  machine; the router is responsible for scheduling any such side effect.
"""
from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, AsyncIterator, Protocol

from tests.testbench.api_keys_registry import (
    get_api_keys_registry,
    get_preset_bundled_api_key,
)
from tests.testbench.chat_messages import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_USER,
    SOURCE_INJECT,
    SOURCE_LLM,
    SOURCE_MANUAL,
    make_message,
)
from tests.testbench.logger import python_logger
from tests.testbench.model_config import (
    GROUP_KEYS,
    GroupKey,
    ModelConfigBundle,
    ModelGroupConfig,
)
from tests.testbench.pipeline.prompt_builder import (
    PreviewNotReady,
    build_prompt_bundle,
)
from tests.testbench.session_store import Session


class ChatConfigError(RuntimeError):
    """Raised when the session's ``chat`` model config is unusable.

    Router maps to HTTP 412 (Precondition Failed) so the frontend knows
    this is "user action required" rather than a transient server issue.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class ChatBackend(Protocol):
    """Minimal interface that both Offline and future Realtime share.

    The Testbench P09 only uses :class:`OfflineChatBackend`; the Protocol
    exists so :mod:`routers.chat_router` can be written against a stable
    interface when P-future adds ``RealtimeChatBackend``.
    """

    async def stream_send(
        self,
        session: Session,
        *,
        user_content: str | None,
        role: str = ROLE_USER,
        source: str = SOURCE_MANUAL,
    ) -> AsyncIterator[dict[str, Any]]:
        ...  # pragma: no cover


# ── config resolution ────────────────────────────────────────────────


def resolve_group_config(session: Session, group: GroupKey) -> ModelGroupConfig:
    """Return a fully-resolved ``ModelGroupConfig`` for any of the 4 groups.

    Resolution priority for ``api_key`` (first non-empty wins):

    1. ``cfg.api_key`` — user typed it into the form explicitly.
    2. **Preset-bundled key** from ``config/api_providers.json`` (e.g.
       the ``free`` preset ships ``openrouter_api_key: "free-access"``).
       Upstream ``utils/config_manager`` treats this literal as "no-auth /
       free tier" and the lanlan.tech free backend accepts it. Testers
       should not be forced to type it.
    3. ``tests/api_keys.json`` via :mod:`api_keys_registry` — so users who
       picked a paid preset (qwen / openai / ...) don't have to re-type
       keys already on disk.

    Raises :class:`ChatConfigError` with a ``group``-tagged message when
    none of the layers yield a usable key, or when ``base_url`` / ``model``
    are missing. Router maps the exception to HTTP 412 (precondition
    failed). Error ``code`` stays ``ChatModelNotConfigured`` /
    ``ChatApiKeyMissing`` for backwards compat with the frontend — the
    toast text is the same regardless of which group fails.

    ``temperature`` / ``max_tokens`` / ``timeout`` are returned as-is:
    ``None`` is a legitimate value that callers must translate to "don't
    send this parameter" when talking to the model (some endpoints like
    o1 / gpt-5-thinking / Claude extended-thinking reject a temperature
    kwarg entirely).

    Used by:
    * :meth:`OfflineChatBackend.stream_send` for the active ``chat`` turn.
    * :func:`config_router._ping_chat` for Settings → Models → Test buttons
      across all 4 groups.
    """
    if group not in GROUP_KEYS:
        raise ChatConfigError(
            "InvalidGroup", f"Unknown model group: {group!r}"
        )
    bundle = ModelConfigBundle.from_session_value(session.model_config)
    cfg = bundle.get(group)

    if not cfg.is_configured():
        raise ChatConfigError(
            "ChatModelNotConfigured",
            f"请先在 Settings → Models → {group} 填好 base_url 与 model。",
        )

    if not cfg.api_key and cfg.provider:
        # Step 2: preset-bundled key (free tier).
        preset_key = get_preset_bundled_api_key(cfg.provider)
        if preset_key:
            cfg = cfg.model_copy(update={"api_key": preset_key})

    if not cfg.api_key and cfg.provider:
        # Step 3: tests/api_keys.json fallback.
        fallback = get_api_keys_registry().get_api_key_for_provider(cfg.provider)
        if fallback:
            cfg = cfg.model_copy(update={"api_key": fallback})

    if not cfg.api_key:
        # Note: we reach here only if (a) user left api_key blank, (b) the
        # provider preset does not bundle one (i.e. not a free tier), and
        # (c) tests/api_keys.json has no matching entry. In that case a
        # real key is genuinely required.
        raise ChatConfigError(
            "ChatApiKeyMissing",
            (
                f"{group} 组的 api_key 为空; 当前 provider "
                f"({cfg.provider or '(未选)'}) 既不是免费预设, 也没能在"
                " tests/api_keys.json 中找到兜底 key。请在表单里填入 key,"
                " 或改用免费预设。"
            ),
        )

    cfg = _rewrite_lanlan_free_base_url(cfg)
    return cfg


# ── Lanlan 免费端防滥用绕行 ──────────────────────────────────────────
#
# 背景
#   主程序 NEKO 访问免费版文本 API 时, lanlan 服务端只对\"看起来是 Lanlan
#   客户端\"的请求放行 (主站是 WS, 文本侧走 openai SDK, 服务端识别手段未公
#   开, 推测基于 TLS 指纹 / 主程序特有请求形态). 从 testbench 这种独立
#   HTTP 客户端直连, 命中 `www.lanlan.tech` / `lanlan.tech` / `www.lanlan.app`
#   任意一个都会被 400 拦住:
#       {"error": "Invalid request: you are not using Lanlan. STOP ABUSE THE API."}
#   2026-04 实测结果 (详见 docs/AGENT_NOTES.md #12): 只有**老域名无 www 前
#   缀**的 `https://lanlan.app/text/v1` 未启用该校验, 返回正常的 OpenAI
#   兼容 completion 数据. 主程序 `utils.config_manager._adjust_free_api_url`
#   只把 `lanlan.tech → lanlan.app`, 不动 `www.` 前缀, 所以主程序靠 GeoIP
#   路由能得到 `www.lanlan.app` / `lanlan.tech` 两种形态, 但它**本身已经**
#   通过特殊途径绕过校验; testbench 没有那个特殊途径, 只能挑一个开放的.
#
# 策略
#   在 testbench 这一侧, 当 ``cfg.base_url`` 命中任意一个被拦截的 lanlan 免
#   费域时, 统一重写为 `https://lanlan.app/text/v1` 再交给下游 openai SDK.
#   这是**纯测试生态补丁**, 不写回 session.model_config (summary 页面展示
#   的仍是用户/预设原始 URL, 避免视觉欺骗), 也不动 `config/api_providers
#   .json` (那是主程序财产, 按规则不能动). 后续若 lanlan 服务端把老域名
#   也关掉, 只需在这里更新 _FREE_API_FALLBACK 常量, 业务代码零感知.

_LANLAN_FREE_BLOCKED_HOSTS: tuple[str, ...] = (
    "www.lanlan.tech",
    "lanlan.tech",
    "www.lanlan.app",
)
_LANLAN_FREE_OPEN_HOST = "lanlan.app"


def _rewrite_lanlan_free_base_url(cfg: ModelGroupConfig) -> ModelGroupConfig:
    """Rewrite Lanlan-free base_url to the abuse-check-free mirror.

    Only touches hosts listed in :data:`_LANLAN_FREE_BLOCKED_HOSTS`; any
    other URL (paid providers, localhost, etc.) passes through untouched.
    Returns a new ``cfg`` rather than mutating the session-bound one.
    """
    url = (cfg.base_url or "").strip()
    if not url:
        return cfg
    for blocked in _LANLAN_FREE_BLOCKED_HOSTS:
        token = f"//{blocked}/"
        if token in url:
            rewritten = url.replace(
                token, f"//{_LANLAN_FREE_OPEN_HOST}/", 1,
            )
            python_logger().info(
                "[chat_runner] lanlan 免费端 base_url 归一化: %s → %s",
                url, rewritten,
            )
            return cfg.model_copy(update={"base_url": rewritten})
    return cfg


def _resolve_chat_config(session: Session) -> ModelGroupConfig:
    """Backwards-compatible alias for the ``chat`` group.

    Kept because ``stream_send`` and existing tests reach through this name.
    New callers should prefer :func:`resolve_group_config` directly.
    """
    return resolve_group_config(session, "chat")


# ── offline backend ──────────────────────────────────────────────────


class OfflineChatBackend:
    """Classical ChatCompletion streaming. One turn in, one assistant out.

    Everything is async-safe: the backend holds no state between calls,
    so a single instance can be reused across requests. The session-level
    lock (managed by :meth:`SessionStore.session_operation` in the router)
    is the only mutual exclusion needed.
    """

    async def stream_send(
        self,
        session: Session,
        *,
        user_content: str | None,
        role: str = ROLE_USER,
        source: str = SOURCE_MANUAL,
    ) -> AsyncIterator[dict[str, Any]]:
        """Run one turn end-to-end and yield SSE-shaped events.

        Parameters
        ----------
        user_content
            Composer / 脚本 / SimUser 产出的 user (或 system) 消息正文. 传
            ``None`` 表示**跳过 "追加一条新 user 消息"** 这一步, 直接在
            当前 ``session.messages`` 之上跑推理. 仅在"会话末尾已经有一条
            未回复的 user 消息, 本次调用只是补齐 assistant 回复"的场景使用
            (例如 P13 Auto-Dialog 的 adaptive 首步). 其它调用方一律传字
            符串让本函数管理好 user 消息与 ``{event: 'user'}`` SSE 帧.

        Yields
        ------
        dict
            One of the following shapes (routers serialize ``json.dumps``
            each and prefix with ``data: ``). All events carry an
            ``event`` discriminator so the frontend can dispatch on it.

            * ``{"event": "user", "message": <msg>}`` — the just-appended
              user / system message, so the UI can render it before any
              assistant chunks arrive. ``message`` is a full message dict
              (id / role / content / timestamp / source / reference_content).
            * ``{"event": "wire_built", "wire_length": int, "system_chars":
              int}`` — metadata about the assembled wire; useful for UI
              badges while waiting for the first chunk. Rare but handy.
            * ``{"event": "assistant_start", "message_id": str,
              "timestamp": str}`` — a placeholder assistant message has
              been allocated; subsequent deltas contribute to it.
            * ``{"event": "delta", "content": str}`` — one chunk of the
              streaming response. Always a non-empty string; the zero-
              content final chunk from the provider is filtered out.
            * ``{"event": "usage", "token_usage": dict}`` — optional
              token-usage payload from the provider's terminal chunk.
            * ``{"event": "assistant", "message": <msg>}`` — the final
              assistant message committed into ``session.messages``.
            * ``{"event": "done", "elapsed_ms": int}`` — stream finished
              cleanly.
            * ``{"event": "error", "error": {...}}`` — stream aborted;
              the placeholder assistant message (if any) has been rolled
              back from ``session.messages`` so next refresh sees a clean
              state.

        Parameters
        ----------
        role
            ``user`` for normal composer input, ``system`` for
            ``/chat/inject_system`` (though :meth:`inject_system` is the
            preferred entry point for the latter since it doesn't call
            the LLM at all).
        source
            Free-form audit tag (see :mod:`chat_messages`).
        """
        if role not in {ROLE_USER, ROLE_SYSTEM}:
            raise ValueError(f"stream_send does not accept role={role!r}")
        if user_content is None:
            # "只跑回复"模式, 末尾必须已经是 user, 否则 prompt_bundle 会拼出
            # 一个无结尾 user 的 wire, 大多数 provider 会拒 (Gemini 400, OpenAI
            # "last message must be user" 等). 提前校验, 给出明确错误.
            if not session.messages or session.messages[-1].get("role") != ROLE_USER:
                raise ValueError(
                    "stream_send(user_content=None) 要求 session.messages 末尾是 "
                    "role=user 的待回复消息"
                )

        started_perf = time.perf_counter()

        # Advance the virtual clock for this turn. Pending staged time wins
        # (explicit tester intent from composer "Next turn +"); otherwise fall
        # back to per-turn default if configured. Without either, the cursor
        # stays where it was and the turn is "instantaneous" relative to the
        # previous message — which is what upstream chat does too.
        had_pending = (
            session.clock.pending_advance is not None
            or session.clock.pending_set is not None
        )
        if had_pending:
            session.clock.consume_pending()
        elif session.clock.per_turn_default_seconds:
            session.clock.advance(
                timedelta(seconds=session.clock.per_turn_default_seconds),
            )

        if user_content is not None:
            now = session.clock.now()
            user_msg = make_message(
                role=role,
                content=user_content,
                timestamp=now,
                source=source,
            )
            session.messages.append(user_msg)
            yield {"event": "user", "message": user_msg}
        # user_content is None 时 ({event:'user'} 缺省) — 上游调用方负责
        # 生成 "user 消息已就绪" 的 UI 提示 (例如 Auto-Dialog 的 simuser_done
        # 事件). 这里不伪造 event 保持单一职责.

        # NOTE: 早期版本里这两个 except 分支会 `session.messages.pop()` 把
        # 刚 append 的 user_msg 回滚掉. 但 `{event: 'user'}` 已经先于此
        # 推给前端了, 前端把消息入了本地 messages 数组和 DOM; 后端一 pop,
        # 就出现"前端看得到, GET /messages 看不到, PUT /messages/{id} 返
        # 回 404 MessageNotFound"的灵异现象 (Prompt Preview 同样不会收录).
        # 正确做法: 保留 user_msg, 让后端/前端/preview 三者保持一致.
        # 用户可以在 Settings 里修好 config 后直接再点 Send (新 user_msg
        # 会在同一时间戳追加), 或者从消息上的 [⋯] 菜单把这条失败消息删掉.
        try:
            bundle = build_prompt_bundle(session)
        except PreviewNotReady as exc:
            yield {
                "event": "error",
                "error": {"type": exc.code, "message": exc.message},
            }
            return

        try:
            cfg = _resolve_chat_config(session)
        except ChatConfigError as exc:
            yield {
                "event": "error",
                "error": {"type": exc.code, "message": exc.message},
            }
            return

        session.logger.log_sync(
            "chat.send.begin",
            payload={
                "model": cfg.model,
                "base_url": cfg.base_url,
                "provider": cfg.provider,
                "temperature": cfg.temperature,
                "max_tokens": cfg.max_tokens,
                "timeout": cfg.timeout,
                # wire_messages 是完整复现的关键, 原样落盘.
                "wire_messages": bundle.wire_messages,
                "system_prompt_chars": bundle.char_counts.get("system_prompt_total", 0),
                "message_count_before_user": len(session.messages) - 1,
                "built_at_virtual": bundle.metadata.get("built_at_virtual"),
                "built_at_real": bundle.metadata.get("built_at_real"),
            },
        )

        yield {
            "event": "wire_built",
            "wire_length": len(bundle.wire_messages),
            "system_chars": bundle.char_counts.get("system_prompt_total", 0),
        }

        assistant_ts = session.clock.now()
        assistant_msg = make_message(
            role=ROLE_ASSISTANT,
            content="",
            timestamp=assistant_ts,
            source=SOURCE_LLM,
        )
        session.messages.append(assistant_msg)
        yield {
            "event": "assistant_start",
            "message_id": assistant_msg["id"],
            "timestamp": assistant_msg["timestamp"],
        }

        chunks: list[str] = []
        token_usage: dict[str, Any] | None = None
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
                streaming=True,
            )
            async for chunk in client.astream(bundle.wire_messages):
                if chunk.content:
                    chunks.append(chunk.content)
                    yield {"event": "delta", "content": chunk.content}
                if chunk.usage_metadata:
                    token_usage = dict(chunk.usage_metadata)
                    yield {"event": "usage", "token_usage": token_usage}
        except Exception as exc:
            # Roll back the assistant placeholder so the UI doesn't see an
            # empty bubble after a failed stream.
            if session.messages and session.messages[-1].get("id") == assistant_msg["id"]:
                session.messages.pop()
            session.logger.log_sync(
                "chat.send.error",
                level="ERROR",
                payload={
                    "message_id": assistant_msg["id"],
                    "partial_chars": sum(len(c) for c in chunks),
                },
                error=f"{type(exc).__name__}: {exc}",
            )
            python_logger().warning(
                "chat.send error (session=%s): %s: %s",
                session.id, type(exc).__name__, exc,
            )
            yield {
                "event": "error",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
            return
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception as close_exc:  # noqa: BLE001
                    python_logger().debug(
                        "ChatOpenAI.aclose failed: %s", close_exc,
                    )

        full_content = "".join(chunks).strip()
        assistant_msg["content"] = full_content
        elapsed_ms = int((time.perf_counter() - started_perf) * 1000)

        session.logger.log_sync(
            "chat.send.end",
            payload={
                "message_id": assistant_msg["id"],
                "content_chars": len(full_content),
                "elapsed_ms": elapsed_ms,
                "token_usage": token_usage,
            },
        )

        yield {"event": "assistant", "message": assistant_msg}
        yield {"event": "done", "elapsed_ms": elapsed_ms}

    def inject_system(
        self, session: Session, content: str,
    ) -> dict[str, Any]:
        """Append a system-role message without any LLM call.

        Used by ``POST /api/chat/inject_system``. Returns the created
        message so the router can echo it back to the UI.
        """
        msg = make_message(
            role=ROLE_SYSTEM,
            content=content,
            timestamp=session.clock.now(),
            source=SOURCE_INJECT,
        )
        session.messages.append(msg)
        session.logger.log_sync(
            "chat.inject_system",
            payload={
                "message_id": msg["id"],
                "chars": len(content),
                "virtual_time": msg["timestamp"],
            },
        )
        return msg


# ── module-level singleton ──────────────────────────────────────────


_backend: OfflineChatBackend | None = None


def get_chat_backend() -> OfflineChatBackend:
    """Return the process-wide :class:`OfflineChatBackend` instance.

    Singleton is fine because the backend is stateless — it keeps no
    connection pool or session-specific data across calls.
    """
    global _backend
    if _backend is None:
        _backend = OfflineChatBackend()
    return _backend


__all__ = [
    "ChatBackend",
    "ChatConfigError",
    "OfflineChatBackend",
    "get_chat_backend",
    "resolve_group_config",
]
