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
    SOURCE_AUTO,
    SOURCE_INJECT,
    SOURCE_LLM,
    SOURCE_MANUAL,
    make_message,
)
from tests.testbench.logger import python_logger
from tests.testbench.pipeline.messages_writer import append_message
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
from tests.testbench.pipeline.wire_tracker import (
    record_last_llm_wire,
    update_last_llm_wire_reply,
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


# -- Lanlan free-endpoint anti-abuse detour --
#
# Background.
# NEKO's free text API accepts only requests resembling Lanlan clients;
# main voice uses WebSockets, text uses OpenAI SDK. Server identification was unpublished,
# presumed TLS fingerprints / main-program-specific request shapes. Standalone testbench HTTP
# clients contacting www.lanlan.tech / lanlan.tech / www.lanlan.app
# all received 400.
#       {"error": "Invalid request: you are not using Lanlan. STOP ABUSE THE API."}
# April 2026 testing (docs/AGENT_NOTES.md #12) found only the old non-www
# https://lanlan.app/text/v1 domain lacked validation and returned ordinary OpenAI-compatible
# completions. Main utils.config_manager._adjust_free_api_url rewrote
# lanlan.tech -> lanlan.app without changing www., so GeoIP routing yielded
# www.lanlan.app or lanlan.tech; main already had a special validation bypass,
# but testbench lacked it and had to choose an open endpoint.
#
# Strategy.
# In testbench only, rewrite cfg.base_url matching blocked Lanlan free domains
# to https://lanlan.app/text/v1 before passing to the OpenAI SDK.
# This test-only patch never writes session.model_config; summaries still show
# the user's original/preset URL transparently. It also leaves config/api_providers
# .json untouched as main-program-owned configuration. If the old domain closes too,
# update _FREE_API_FALLBACK here without changing business code.
#
# 2026-06-19 recheck (docs/UPSTREAM_SYNC_2026-06.md): www.lanlan.tech, bypass lanlan.app,
# and suggested www.lanlan.app now all return STOP ABUSE THE API on /text/v1/chat/
# completions, consistently via curl and OpenAI SDK. The old bypass is closed,
# exactly the warned-about scenario.
# www.lanlan.app actually serves main's overseas free voice/TTS/realtime Gemini
# proxy (main_logic/omni_realtime_client.py, tts_client/workers/step.py,
# wss://www.lanlan.app/tts), not text completions. The sole configured free text endpoint is config/
# api_providers.json https://www.lanlan.tech/text/v1. Switching to www.lanlan.app
# cannot restore testbench text access.
# Identical main-program ChatOpenAI, SSL context, and free-access keys still fail in testbench,
# indicating identification beyond TLS fingerprints. Most likely free text requires an active
# wss://.../core realtime session registering the client first; main always opens one,
# while testbench submits bare text completions and is classified as abuse. No free domain remains available.
# This bypass is inactive; do not reverse-engineer anti-abuse mechanisms further. Configure usable paid providers
# and API keys per chat/memory/judge/simuser group under Settings -> Models for real testbench LLM flows.
# Free Lanlan presets work only for the NEKO main program itself.
# Most smoke tests use mocks and remain unaffected; only real chat/memory/scoring manual tests need working keys.

# 2026-08-01: maintainers confirmed bare lanlan.app shut down entirely, beyond June's anti-abuse rejection.
# With the bypass target gone, disable rewriting: otherwise meaningful 400 STOP ABUSE THE API errors
# become opaque DNS/connection failures,
# degrading diagnostics.
#
# Retain the constant/function to document this route's history and rationale;
# an empty tuple naturally makes rewriting noop, forwarding base_url unchanged
# and letting free presets fail with the server's own explanation.
_LANLAN_FREE_BLOCKED_HOSTS: tuple[str, ...] = ()
_LANLAN_FREE_OPEN_HOST = ""


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
            User/system message text from Composer, scripts, or SimUser. Passing
            None skips appending a new user message and runs inference
            directly on current session.messages, only when the session already ends
            with an unanswered user message and this call merely completes its assistant reply,
            e.g. P13 Auto-Dialog's adaptive first step. All other callers pass strings
            so this function manages user messages and {event:'user'} SSE frames.

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
            # Reply-only mode requires an existing final user message; otherwise prompt_bundle constructs
            # wire without a terminal user and most providers reject it, e.g. Gemini 400 / OpenAI
            # last message must be user. Validate early with explicit errors.
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
            # P24 Day 8 manual test #6, section 13 F3 scope extension: detect prompt injection
            # in user messages. LESSONS says detect without modification or blocking:
            # - Never filter/rewrite; arbitrary offensive payloads are valid test material,
            #   and automatic sanitization would negate core testing capabilities.
            # - On matches, write diagnostic warnings visible under Diagnostics -> Errors,
            #   plus injection_warning SSE frames for UI badges.
            # - Continue original chat sending, without affecting persistence or LLM calls.
            # Extends F3's original judge_runner-only detection;
            # user dev_note L17 expected visible warnings at send time, implemented here.
            try:
                from tests.testbench.pipeline import (
                    prompt_injection_detect as _pid,
                )
                from tests.testbench.pipeline import diagnostics_store as _ds
                from tests.testbench.pipeline.diagnostics_ops import DiagnosticsOp
                hits = _pid.detect(user_content or "")
                if hits:
                    summary = _pid.summarize(hits)
                    _ds.record_internal(
                        DiagnosticsOp.PROMPT_INJECTION_SUSPECTED,
                        f"Chat 发送命中 {len(hits)} 条注入模式: "
                        + ", ".join(h.pattern_id for h in hits[:5])
                        + (" ..." if len(hits) > 5 else ""),
                        level="warning",
                        detail={
                            "source": "chat.send",
                            "session_id": getattr(session, "id", None),
                            "role": role,
                            "content_length": len(user_content),
                            "hits_count": len(hits),
                            # 2026-04-22 Day 8 manual test #2 fix: h.match_preview was a typo;
                            # the field is match_text. AttributeError was
                            # silently swallowed by except Exception:pass, making detection
                            # fail without matches or notices.
                            # Use to_dict() for the complete structure now.
                            "hits": [h.to_dict() for h in hits[:20]],
                            "summary": summary,
                        },
                    )
                    # SSE warning frames support advisory UI badges without blocking.
                    yield {
                        "event": "injection_warning",
                        "warning": {
                            "type": "prompt_injection_suspected",
                            "hits_count": len(hits),
                            "categories": sorted(set(h.category for h in hits)),
                            "message": f"检测到 {len(hits)} 条可疑注入模式 (不阻断)",
                        },
                    }
            except Exception as exc:  # noqa: BLE001 - Detection must not break the main flow.
                # 2026-04-22 manual test: except Exception:pass was the silent-fallback
                # antipattern from LESSONS #14, hiding all detection bugs
                # such as misspelled fields or import failures. Log one python_logger
                # warning instead: failed detection must not block chat, but must leave an audit clue.
                python_logger().warning(
                    "chat.send: prompt_injection detection skipped due to "
                    "%s: %s", type(exc).__name__, exc,
                )

            now = session.clock.now()
            user_msg = make_message(
                role=role,
                content=user_content,
                timestamp=now,
                source=source,
            )
            # P24 §12.5: SSE path uses coerce policy — if the virtual
            # clock was rewound into the past, bump ts forward so downstream
            # (dialog_template, recent_history slice, UI time separator)
            # never sees non-monotonic messages.
            result = append_message(session, user_msg, on_violation="coerce")
            user_msg = result.msg
            if result.coerced is not None:
                # User-visible surfacing: yield a warning frame before the
                # user event so the UI can toast before the bubble renders.
                yield {
                    "event": "warning",
                    "warning": {
                        "type": "timestamp_coerced",
                        "message_role": "user",
                        **result.coerced,
                    },
                }
            yield {"event": "user", "message": user_msg}

            # role=system + /chat/send is allowed by frontend composer.js, promising
            # persistence plus immediate LLM execution. Main-program semantics in main_logic/
            # omni_offline_client.py allow SystemMessage only at initial
            # _conversation_history[0], never runtime system messages.
            # Gemini rejects mid/end-wire systems, sometimes returning empty 200 replies
            # or 400; see prompt_builder.build_prompt_bundle's extended comments,
            # AGENT_NOTES #32 and section 7.25. The wire chokepoint now rewrites
            # role=system to role=user with a [system note] prefix,
            # telling testers their selected role is equivalent to a prefixed
            # user message to the LLM, not its initial system prompt. Emit a diagnostic
            # warning visible in Diagnostics -> Errors/Logs to expose contract deviations.
            if role == ROLE_SYSTEM:
                try:
                    from tests.testbench.pipeline import (
                        diagnostics_store as _ds,
                    )
                    from tests.testbench.pipeline.diagnostics_ops import (
                        DiagnosticsOp,
                    )
                    _ds.record_internal(
                        DiagnosticsOp.CHAT_SEND_SYSTEM_REWRITTEN,
                        (
                            "chat.send role=system — wire 层已重写为 "
                            "role=user + `[system note] ` 前缀 (主程序"
                            "运行期无 role=system 消息契约)."
                        ),
                        level="info",
                        detail={
                            "message_id": user_msg["id"],
                            "session_id": getattr(session, "id", None),
                            "content_length": len(user_content or ""),
                            "source": "chat.send",
                        },
                    )
                except Exception as exc:  # noqa: BLE001 - Auditing must not block the main flow.
                    python_logger().debug(
                        "chat.send: system-rewrite diagnostics log "
                        "failed: %s: %s", type(exc).__name__, exc,
                    )
        # With user_content=None and no {event:'user'}, callers supply the
        # user-ready UI event, e.g. Auto-Dialog simuser_done.
        # Do not fabricate events here; retain a single responsibility.

        # NOTE: earlier except branches popped session.messages, undoing newly appended
        # user_msg after {event:'user'} had already reached frontend,
        # whose local messages/DOM retained it. Backend removal then produced
        # frontend-visible messages absent from GET /messages and 404 MessageNotFound
        # on PUT /messages/{id}; Prompt Preview omitted them too.
        # Keep user_msg so backend, frontend, and preview stay consistent.
        # Users may fix Settings and Send again, appending a new same-timestamp message,
        # or delete the failed message through its [...] menu.
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
                # wire_messages is essential to exact reproduction; persist unchanged.
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
        # P24 §12.5: same as user_msg append — coerce-on-violation so the
        # assistant placeholder can't introduce a monotonicity break even
        # if clock was rewound between user and assistant turn.
        _asst_result = append_message(session, assistant_msg, on_violation="coerce")
        assistant_msg = _asst_result.msg
        if _asst_result.coerced is not None:
            yield {
                "event": "warning",
                "warning": {
                    "type": "timestamp_coerced",
                    "message_role": "assistant",
                    **_asst_result.coerced,
                },
            }
        yield {
            "event": "assistant_start",
            "message_id": assistant_msg["id"],
            "timestamp": assistant_msg["timestamp"],
        }

        chunks: list[str] = []
        token_usage: dict[str, Any] | None = None
        client = None

        # L36 section 7.25 r4: Prompt Preview equals actual wire.
        # chat.send wire and session.messages are designed to match: user messages
        # append before bundle construction, so previews reconstructed from
        # session.messages should match actual sends. Still snapshot
        # real wire: (a) external_events/auto_dialog/simuser require snapshots,
        # so doing the same here gives last-real-wire semantics across every entry,
        # preventing previews retaining a previous external-event wire after switching
        # to a newer chat.send.
        # (b) Ground-truth reply_chars calibration lets preview display actual LLM
        # response length. (c) Explicit bundle/actual-send pairing exposes future
        # drift, such as retries altering the last message, immediately.
        #
        # P25 Day 3 source routing:
        # - SOURCE_AUTO, target turns in two-AI auto dialogue, maps to auto_dialog_target.
        #   auto_dialog.py::_forward_target_stream enters with
        #   source=SOURCE_AUTO, meaning this automatic dialogue's target-AI turn.
        #   Preview slugs distinguish manual composer sends from
        #   batch-driven auto-dialogue rounds, helping explain suddenly changed
        #   wire in multiround automatic dialogue.
        # - Other sources (manual/inject/script) map to chat.send,
        #   preview's default ordinary-chat label.
        _wire_source = "auto_dialog_target" if source == SOURCE_AUTO else "chat.send"
        try:
            record_last_llm_wire(
                session,
                bundle.wire_messages,
                source=_wire_source,
                note=(
                    f"{_wire_source}:{len(bundle.wire_messages)}msgs"
                    f"@{cfg.provider}:{cfg.model}"
                ),
            )
        except Exception as exc:  # noqa: BLE001 — observability must not block LLM
            python_logger().debug(
                "chat.send: record_last_llm_wire failed: %s: %s",
                type(exc).__name__, exc,
            )

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
            try:
                update_last_llm_wire_reply(session, reply_chars=-1)
            except Exception:  # noqa: BLE001
                pass
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

        try:
            update_last_llm_wire_reply(session, reply_chars=len(full_content))
        except Exception as exc:  # noqa: BLE001
            python_logger().debug(
                "chat.send: update_last_llm_wire_reply failed: %s: %s",
                type(exc).__name__, exc,
            )

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
        # P24 §12.5: inject_system is a side-feature; coerce prevents
        # it from failing if clock is rewound before an injection. If a
        # coerce did happen, the diagnostics_store op=timestamp_coerced
        # entry (written inside append_message) is the sole user-visible
        # surfacing — inject_system has no SSE channel to yield a warning
        # frame, and the router returns the saved msg synchronously.
        # Callers reading ``msg["timestamp"]`` after this point see the
        # coerced (monotonic) value.
        _sys_result = append_message(session, msg, on_violation="coerce")
        msg = _sys_result.msg
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
