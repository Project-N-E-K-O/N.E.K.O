"""Minecraft Game Agent plugin.

Bridges a local Minecraft agent server (WebSocket) to the LLM via the
plugin SDK's ``@llm_tool`` decorator. Replaces the previous in-tree
integration that lived in ``main_logic/core.py`` and ``main_logic/
game_agent_client.py`` (commit ``bca0c5f3`` on the abandoned
``feat/game-agent-integration`` branch). Now everything game-agent-
specific is contained inside this directory and can be installed /
removed without patching core code.

Architecture (one paragraph): ``minecraft_task`` is registered as an
LLM tool via :func:`plugin.sdk.plugin.llm_tool`; when the model picks
it, the plugin sends the task text to the agent server over WebSocket
and blocks the handler on an :class:`asyncio.Event` until the
``task_finished`` frame arrives (or a configurable timeout fires).
Screenshots streamed by the agent server are forwarded into the
realtime LLM session via :class:`push_message v2 <push_message>` with
``ai_behavior="read"`` so they enter the model's vision context. A
background task periodically fires a "GAME_SYSTEM" nudge prompt with
the latest log digest and screenshot cache so the model keeps playing
autonomously when the user is silent.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    ui,
)

from . import prompts
from .service import GameAgentService, text_signals_blocked

# JSON Schema reused by the @llm_tool decorator below. Pulled into a
# module-level constant so the plugin's introspection (status entry,
# tests) can reference exactly what the LLM sees.
MINECRAFT_TASK_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": (
                "A plain-language Minecraft instruction. For a master-directed "
                "action, copy only master's most recent message: it must be a new "
                "explicit game instruction that has not already been dispatched, "
                "using the exact original wording without added details. During "
                "keep-going, a genuinely new autonomous action may instead be "
                "formed from the current game state, but never by recovering or "
                "replaying an older instruction or tool call. Never add in-game "
                "coordinates unless master explicitly asked for coordinates in "
                "the most recent message."
            ),
        },
        "overwrite": {
            "type": "boolean",
            "description": (
                "If True, interrupt the currently running task and start this one. "
                "Default False — sending a new task while one is in flight returns "
                "a 'busy' response without disturbing the in-flight task."
            ),
        },
    },
    "required": ["task"],
}


MINECRAFT_TASK_DESCRIPTION = (
    "Dispatch a single concrete action for the in-game character to "
    "perform. Fire-and-forget: returns immediately with an "
    "acknowledgement; the real outcome arrives asynchronously as fresh "
    "screenshots and a system feedback message tagged ``[你刚做完一段动作]`` "
    "(cue). "
    "Do not infer or claim results from the task text itself — only from "
    "actually-observed cues and screenshots.\n\n"
    "CRITICAL — this tool dispatches actions; it is not an awareness or heartbeat "
    "tool. A screenshot, log, state update, idle reminder, or task-completion cue "
    "is context only and is NEVER by itself a task to dispatch. Before "
    "dispatching, account for the current task and recent feedback. If an action "
    "may still be running, do not send another one.\n\n"
    "TASK SOURCE AUTHORITY — for a master-directed action, use only master's most "
    "recent message, and only when it contains a new explicit in-game instruction "
    "that has not already been dispatched. The task must not come from an earlier "
    "master message, an earlier tool call, task history, logs, screenshots, "
    "telemetry, or a completion cue. During keep-going, you may instead form one "
    "genuinely new autonomous action from the current game state when there is an "
    "obvious next step. NEVER call back, recover, retry, or re-dispatch an older "
    "master instruction or tool call, even if it failed or is mentioned by a "
    "state reminder.\n\n"
    "MASTER INSTRUCTION FIDELITY — when master (the user) gives an explicit "
    "instruction in that most recent message, relay the exact original wording "
    "in ``task``. Do not translate, "
    "paraphrase, split, optimize, clarify on master's behalf, or add targets, "
    "quantities, steps, tools, directions, or other details. The exact request "
    "takes priority over the usual preference for a concrete single-step goal.\n\n"
    "COORDINATE SAFETY — NEVER invent, infer, choose, or include in-game "
    "coordinates unless master explicitly asked you to use or provide coordinates. "
    "Coordinates visible in screenshots, logs, telemetry, or earlier agent output "
    "do not count as master's request. Preserve relative wording such as 'come "
    "here' exactly instead of turning it into coordinates.\n\n"
    "CRITICAL — never parrot the game's own logs. The Minecraft feed you see is "
    "the agent's internal telemetry: function-call lines (goToCoordinates(...), "
    "attackEntity(...)), '!commands', 'admin movement request', and coordinates it "
    "picked itself. NEVER copy any of that into a task — echoing a log line back "
    "as a task creates a dispatch loop. A task must come from master's most recent "
    "spoken request or a genuinely new autonomous choice based on current game "
    "state, written as a plain-language goal, never "
    "the game's raw command syntax or a coordinate you only saw in a log. If the "
    "user asked for a specific thing, do THAT — not whatever the log happens to say.\n\n"
    "Use this tool when master's most recent message asks the character to do "
    "something new and that request has not already been dispatched, or when a "
    "keep-going turn identifies a genuinely obvious new action from current game "
    "state. Do NOT dispatch merely because a task finished or new awareness "
    "context arrived. Do NOT use it for chat, status "
    "questions, or abstract intent — see ``query_inventory`` for inventory lookups.\n\n"
    "Parameters:\n"
    "  task (string, required): master's exact original new instruction when "
    "master-directed; otherwise one genuinely new autonomous action based on "
    "current game state. Never source it from an older message or tool call, and "
    "never repeat an already-dispatched command. Never add coordinates unless "
    "master explicitly requested them in the most recent message.\n"
    "  overwrite (bool, default false): if a previous task is still in "
    "flight, false rejects this call with a 'busy' summary and the "
    "previous task keeps running. Set true when:\n"
    "    (a) the user explicitly tells you to stop / change ('stop', "
    "'cancel that, do X', '别 Y', '换成 Z', '改用 X', '不要再 Y') — these "
    "are corrections that supersede whatever you're doing,\n"
    "    (b) you have directly observed the current task is hopelessly "
    "stuck (blocked for 15s+ with zero progress in screenshots), or\n"
    "    (c) the user complains the in-game behavior is wrong (wrong tool, "
    "wrong target, wrong direction) — apply the fix immediately, don't "
    "wait for the current task to finish.\n"
    "  Do NOT set true for 'better plan' / 'more efficient' subjective "
    "reasons. **CRITICAL**: when a 'busy' response comes back AND the user "
    "is actively correcting you, you MUST re-invoke with overwrite=true on "
    "the same turn — silently accepting busy while the user is asking for "
    "a change leaves Neko standing still doing the wrong thing.\n\n"
    "When the cue includes a 『背包』 line, that is the character's actual "
    "inventory after the action — items not in that line don't exist; do "
    "not narrate items the line doesn't show."
)


@neko_plugin
class GameAgentMinecraftPlugin(NekoPluginBase):
    """Plugin facade — minimal: lifecycle wiring + tool surface.

    Real logic lives in :class:`GameAgentService`; this class only
    handles the SDK integration boilerplate.
    """

    def __init__(self, ctx):
        super().__init__(ctx)
        # 跟随全局 NEKO_LOG_LEVEL（默认 INFO）。此前硬编码 "INFO" 会把
        # 自主 nudge loop 的 fire 决策（_log_debug "firing keep_going/
        # in_progress/general nudge"）和其它 DEBUG 诊断永久吞掉——排查
        # 「任务结束后猫娘不再开口 / self-prompt 没了」时根本看不到 nudge
        # 到底有没有 fire、有没有走到 push。改成跟随环境变量，开 DEBUG 即可见。
        _log_level = (os.environ.get("NEKO_LOG_LEVEL") or "INFO").strip().upper()
        try:
            self.file_logger = self.enable_file_logging(log_level=_log_level)
        except ValueError:
            # 未知级别（理论上不会发生）兜底回 INFO，别让插件因日志配置崩在 __init__。
            self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: Dict[str, Any] = {}
        # User-language short code for prompt resolution. Set tentatively
        # here (EN fallback so __init__ never throws), then upgraded in
        # ``startup`` once we can call into utils.language_utils. Both
        # the facade (this class) and the service consult the same value;
        # the service receives it via ``set_lang`` so its push_message
        # cues match the user's locale.
        self._lang: str = prompts.DEFAULT_LANG
        self._service = GameAgentService(
            logger=self.logger,
            push_message_fn=self.push_message,
        )
        # ``service.start()`` spawns long-running asyncio tasks (WS client
        # reconnect loop + autonomous nudge loop). It must run inside the
        # plugin host's *long-lived* main event loop — but the SDK invokes
        # ``@lifecycle(id="startup")`` via a transient ``asyncio.run(...)``
        # ([plugin/core/host.py:838]), which closes the loop the moment the
        # handler returns and cancels every ``asyncio.create_task`` started
        # underneath it. We therefore defer the real start until the first
        # entry handler call — those run on the host's long-lived async
        # command loop, so tasks created there actually survive.
        # ``_service_lazy_started`` is the gate; ``_service_start_lock``
        # serializes the lazy start so concurrent first-calls don't
        # double-spawn.
        self._service_lazy_started: bool = False
        self._service_start_lock: asyncio.Lock = asyncio.Lock()

    async def _ensure_service_started(self, *, connect_wait_s: float = 3.0) -> bool:
        """Idempotent lazy-start of the WS service.

        Returns ``True`` iff the WS client reports ``is_connected`` by the
        time this method returns. The handler uses the return value to
        decide whether to dispatch a detached task (and emit the standard
        avatar-framed acknowledgement) or short-circuit with an
        "avatar still waking up" message — without this gate the very
        first call after a plugin process spawn races the WS connect and
        leaks the underlying transport error string ("agent server is
        not connected") into the dialog LLM's context, which then makes
        the dialog LLM spontaneously talk about reconnecting and break
        the avatar framing.

        ``service.start()`` itself is synchronous up to spawning the
        reconnect coroutine — the actual WebSocket handshake completes
        a beat later. We poll the connected flag at 50ms cadence up to
        ``connect_wait_s`` (default 3s, which empirically covers ~99%
        of fresh-process boots: observed connect times are 1.5–2.5s).
        """
        async with self._service_start_lock:
            if not self._service_lazy_started:
                try:
                    await self._service.start()
                    self._service_lazy_started = True
                    self.logger.info(
                        "[lazy-start] service.start() ran on long-lived loop"
                    )
                except Exception as exc:
                    # Don't flip the flag — next call retries. The WS client
                    # has its own reconnect loop, so a successful start with
                    # an unreachable mc-agent is fine; we only want to retry
                    # if start() itself raised before scheduling the tasks.
                    self.logger.warning(
                        "[lazy-start] service.start failed; will retry on next call — {}: {}",
                        type(exc).__name__, exc,
                    )
                    return False

            # Wait briefly for the WS handshake to actually complete.
            # Lock is held throughout — concurrent first-callers all see
            # the same connected-or-not snapshot rather than racing.
            import time as _time
            deadline = _time.monotonic() + connect_wait_s
            client = getattr(self._service, "_client", None)
            while _time.monotonic() < deadline:
                if client is not None and getattr(client, "is_connected", False):
                    return True
                await asyncio.sleep(0.05)
            return bool(
                client is not None and getattr(client, "is_connected", False)
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_):
        # IMPORTANT: only do configuration here. Do NOT call service.start()
        # — see ``_ensure_service_started`` docstring for why the SDK's
        # transient asyncio.run() makes that unsafe.
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = (
            cfg.get("game_agent", {})
            if isinstance(cfg.get("game_agent"), dict)
            else {}
        )
        # Resolve user language now (it reads Steam / system locale via
        # ``utils.language_utils.get_global_language``, which lazy-inits
        # on first call) and propagate to the service so its autonomous
        # nudge loop and task_finished cues match the user's locale.
        self._lang = prompts.user_lang()
        self._service.set_lang(self._lang)
        self._service.configure(self._cfg)
        return Ok({"status": "ready", "result": self._service.get_status()})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if self._service_lazy_started:
            try:
                await self._service.stop()
            except Exception as exc:
                self.logger.warning(
                    "[shutdown] service.stop raised — {}: {}",
                    type(exc).__name__, exc,
                )
            self._service_lazy_started = False
        return Ok({"status": "shutdown"})

    async def _on_command_loop_start(self) -> None:
        """Eager-start the WS service on the host's long-lived command
        loop, the moment the plugin process is alive — instead of
        waiting for the first ``minecraft_task`` / entry trigger.

        The plugin SDK invokes this hook from inside
        ``_async_command_loop`` ([plugin/core/host.py:1216-1225]) before
        the command dispatch loop starts pumping messages. That loop is
        the SAME long-lived asyncio loop that later executes every
        ``@plugin_entry`` / ``@llm_tool`` handler, so the WS client task,
        nudge loop, locks and Events created here all bind to the loop
        the handlers will eventually run on — no cross-loop access risk.

        Earlier iteration of this fix used ``@timer_interval`` + a
        forever-blocking ``asyncio.Event().wait()`` to hold the tick
        open. That worked in the sense that the service tasks survived,
        but the tasks were bound to the timer's per-tick loop in a
        separate thread; the next ``minecraft_task`` call from the
        command loop would then hit ``RuntimeError: ... bound to a
        different event loop`` the moment it touched any of the
        service's asyncio primitives. Codex review on PR #1395 caught
        this — using the command-loop hook is the correct primitive.

        Without eager-start at all, user-reported symptom: 75+s of dead
        air between "go chop trees" → dialog LLM chats but doesn't call
        ``minecraft_task`` → plugin process never wakes service → nudge
        loop never starts → no self-prompt → Neko stands still until
        the user prods her into a second turn and analyzer finally
        lands on ``game_agent_status``.
        """
        try:
            await self._ensure_service_started()
        except Exception as exc:
            self.logger.warning(
                "[eager-start] service start failed; lazy-start fallback remains — {}: {}",
                type(exc).__name__, exc,
            )

    # ------------------------------------------------------------------
    # LLM-callable tool
    # ------------------------------------------------------------------

    @llm_tool(
        name="minecraft_task",
        description=MINECRAFT_TASK_DESCRIPTION,
        parameters=MINECRAFT_TASK_SCHEMA,
        # SDK wrapper timeout retained at the registry max even though the
        # handler itself returns in ~0ms now (fire-and-forget). Keeping it
        # at 300s means a future change that re-introduces an await won't
        # silently cap below the operator-configured task_timeout_seconds.
        timeout=300.0,
    )
    async def minecraft_task(self, *, task: Any = None, overwrite: Any = False, **_):
        # ``task`` declared as ``Any = None`` rather than ``str`` (required)
        # so that LLMs which violate the JSON schema (omit ``task``, pass
        # null, pass a non-string) reach the handler body instead of
        # raising ``TypeError`` at call dispatch — the SDK trigger path
        # would otherwise surface a raw stack trace via host.py:1082's
        # ``Unexpected error executing`` log, and the LLM tool result
        # would be a generic error envelope rather than something the
        # dialog LLM can act on. By accepting whatever the LLM sent and
        # producing a structured "you forgot the task" summary, the LLM
        # learns the schema by getting a clear message back.

        # ---- schema validation ----
        if not isinstance(task, str) or not task.strip():
            return {
                "summary": prompts.t("TASK_SCHEMA_ERROR", lang=self._lang),
            }
        task_text = task.strip()
        # Some LLMs pass ``"true"`` / ``"1"`` / ``1`` as overwrite. Strict
        # ``is True`` keeps the destructive interrupt path off-by-default;
        # anything other than the canonical Python bool ``True`` is treated
        # as False. (Service-side also strict-checks, but doing it here
        # makes the failure mode visible in this handler's local reasoning.)
        overwrite_flag = overwrite is True

        # Lazy-start the WS service on the host's long-lived loop AND wait
        # briefly (≤3s) for the WS handshake to complete. See
        # ``_ensure_service_started`` for the rationale on both halves —
        # without the wait, the very first task after a fresh process
        # leaks an "agent server is not connected" string into the dialog
        # LLM's context and the dialog LLM picks up bad framing from it.
        connected = await self._ensure_service_started()
        if not connected:
            return {
                "summary": prompts.t("TASK_NOT_CONNECTED", lang=self._lang),
            }

        # Atomic claim. Splitting "check + claim" from "run" lets the
        # facade give the dialog LLM a synchronous truthful answer
        # ("you're still doing X — wait it out") without the historical
        # race where two concurrent ``minecraft_task`` calls both saw
        # ``has_pending_task() == False`` (outside any lock) and both
        # dispatched, silently overwriting each other's pending state.
        # ``try_claim_pending`` does the check + slot claim under the
        # service's pending lock as one atomic step; ``None`` here means
        # "refuse as busy" — guaranteed mutually exclusive with the
        # accepted branch below.
        claimed = await self._service.try_claim_pending(
            task_text, overwrite=overwrite_flag
        )
        if claimed is None:
            current = (
                self._service.current_task_text()
                or prompts.t("PLACEHOLDER_JUST_FINISHED", lang=self._lang)
            )
            return {
                "summary": prompts.t(
                    "TASK_BUSY_HINT", lang=self._lang, current=current[:80]
                ),
            }

        # Fire-and-forget: run the claimed task in the background. The
        # dialog LLM's realtime turn must not block for the full action
        # (1-30s+); fresh screenshots + the cue from
        # ``_on_detached_task_done`` ground its later narration.
        detached = asyncio.create_task(
            self._service.run_claimed_task(claimed),
            name=f"game_agent_minecraft.task:{task_text[:40]}",
        )
        detached.add_done_callback(self._on_detached_task_done)
        return {
            "summary": prompts.t("TASK_DISPATCHED_ACK", lang=self._lang),
        }

    # ------------------------------------------------------------------
    # Detached task plumbing — runs after the @llm_tool handler has
    # already returned, pushes a brief completion cue back to the dialog
    # LLM via the standard push_message v2 channel.
    # ------------------------------------------------------------------

    def _on_detached_task_done(self, task: asyncio.Task) -> None:
        """Push a short [character status] cue when a detached
        minecraft_task finishes (any outcome — ok / timeout /
        interrupted / busy / error).

        Runs on the event loop in the done-callback context, so it must
        not raise — exceptions here would bubble into asyncio's default
        handler as "Exception in callback" noise. Wrap everything in
        try/except and log.
        """
        try:
            result = task.result()
        except asyncio.CancelledError:
            # Plugin shutdown / loop teardown cancelled it — silent.
            return
        except Exception as exc:
            self.logger.warning(
                "[detached] minecraft_task crashed: {}: {}",
                type(exc).__name__, exc,
            )
            return
        if not isinstance(result, dict):
            return
        cue = self._format_completion_cue(result)
        if not cue:
            return
        try:
            # ai_behavior="respond" + priority=7: the action just
            # finished, so the dialog LLM should immediately absorb and, when
            # useful, narrate the outcome to {MASTER_NAME}. The prompt frames
            # this as awareness rather than a command to dispatch another task;
            # the later keep-going turn remains responsible for any genuinely
            # new autonomous next action. Importance scale is HIGHER=more
            # important (repo-wide):
            # alert=9 (most important) > completion=7 > in_progress=4 >
            # keep-going=3.
            self.push_message(
                source="game_agent_minecraft",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": cue}],
                priority=7,
                coalesce_key="mc_completion",
            )
        except Exception as exc:
            self.logger.warning(
                "[detached] completion cue push failed: {}: {}",
                type(exc).__name__, exc,
            )

    def _format_completion_cue(self, result: Dict[str, Any]) -> str:
        """Render the ``run_claimed_task`` return into a short cue for the
        dialog LLM. Goal: tell猫娘 what just happened in as few words as
        possible — she should *know* the outcome, not parrot it. Long
        instructional preambles got复述 verbatim in our earlier testing
        ("【当前持有 ground truth】" became a literal台词)，which is
        exactly the "像个机器人一直念" problem we're fixing.

        Transport-level errors get rewritten to body/sensation language
        so the dialog LLM never sees "agent server is not connected" and
        starts narrating about reconnection.
        """
        if result.get("is_error"):
            status = str(result.get("error") or "error")
        else:
            status = str(result.get("status") or result.get("result") or "unknown")
        query = str(
            result.get("query") or result.get("currently_executing") or ""
        )
        detail = str(
            result.get("text")
            or result.get("reason")
            or result.get("hint")
            or ""
        )
        if isinstance(result.get("output"), dict):
            # AGENT_DISCONNECTED path nests query/error inside ``output``.
            if not query:
                query = str(result["output"].get("query") or "")
            output_err = result["output"].get("error")
            if output_err and not detail:
                detail = str(output_err)
        # Re-label transport/blocked statuses into localized phrases the
        # dialog LLM can paraphrase. The "received status string" stays
        # the surface text the LLM sees; routing logic below still keys
        # off the original ``ok`` / ``受阻`` sentinel values via the
        # same-language label, so the status branches below survive
        # localization without growing brittle "if any locale of 受阻"
        # checks.
        blocked_label = prompts.t("STATUS_LABEL_BLOCKED", lang=self._lang)
        # Gate the "not connected" text sniff on an actual error envelope.
        # The genuine transport failure already arrives as
        # status=="AGENT_DISCONNECTED" (is_error path above), so the substring
        # is only a belt-and-suspenders for error results whose text says
        # "not connected". Firing it on ANY detail containing that phrase
        # would hijack a legitimate completion (e.g. status="ok",
        # text="the lever is not connected to the piston") — or a new
        # interrupted/superseded frame — into the disconnect bucket.
        if status == "AGENT_DISCONNECTED" or (
            result.get("is_error") and "not connected" in detail.lower()
        ):
            status = prompts.t("STATUS_LABEL_DISCONNECTED", lang=self._lang)
            detail = prompts.t("STATUS_DETAIL_DISCONNECTED", lang=self._lang)
        # mc-agent quirk: chat-loop returns ``status="ok"`` even when the
        # in-game action was blocked (mineflayer couldn't resolve target,
        # missing tool, path obstructed, etc.) — the failure is buried in
        # the text message. Without re-labeling, the dialog LLM reads
        # "结果 ok" + "find player not found" and concludes "task done"
        # (cf. user-reported "她以为找博士成功了，没改用真 username"
        # bug). Surface the blocked-ness explicitly so she has to plan
        # around it. Marker list is shared with the retroactive cue via
        # ``text_signals_blocked`` so the two can't drift apart.
        elif status.strip().lower() == "ok" and text_signals_blocked(detail):
            status = blocked_label

        inv = result.get("inventory")
        inv_line = ""
        if isinstance(inv, dict) and inv:
            items = sorted(
                ((str(k), int(v)) for k, v in inv.items() if int(v) > 0),
                key=lambda kv: -kv[1],
            )
            if items:
                pieces = "、".join(f"{name}×{count}" for name, count in items[:20])
                inv_line = prompts.t(
                    "COMPLETION_INV_CURRENT_LINE", lang=self._lang, items=pieces
                )
        elif isinstance(inv, dict):
            inv_line = prompts.t("COMPLETION_INV_CURRENT_EMPTY", lang=self._lang)

        # Four-way: actual success ("ok", case-insensitive), rebadged
        # success-but-blocked (status == blocked_label), neutral
        # interrupted/superseded (task stopped short but nothing went
        # wrong), or genuine failure (disconnect, timeout, "failed",
        # error, "unknown", arbitrary is_error strings). The catch-all
        # branch previously cue'd everything that wasn't 受阻 as "做完 ...
        # 派下一步" which told the dialog LLM the action succeeded when it
        # actually disconnected / timed out / crashed — Codex review on
        # PR #1395 caught this, so we whitelist "ok" as success instead
        # of enumerating every failure. ``interrupted`` (our overwrite/
        # shutdown/bounce verdict) and ``superseded`` (mc-agent's "a newer
        # task replaced this") are carved out of the failure bucket: they
        # are non-completions, not failures, and narrating them as
        # "没做成——想清楚原因" is wrong (there's no cause; she just moved on).
        # Normalize once for the buckets: tolerate case + stray whitespace
        # from mc-agent (e.g. "ok " / " SUPERSEDED" must still classify).
        # ``is_blocked`` keeps the exact-label compare — blocked_label is a
        # localized display string set by the rewrite above, never padded.
        status_lc = status.strip().lower()
        is_blocked = status == blocked_label
        is_success = status_lc == "ok"
        is_interrupted = status_lc in ("interrupted", "superseded")
        # [NO-SWITCH-CUE] interrupted/superseded means a newer task already
        # replaced this one — the LLM (or user) did that on purpose and knows.
        # Reporting "your task got switched (re-dispatch if you want)" just makes
        # it fire another minecraft_task → the observed task-switch retry storm.
        # Emit nothing; the fresh task's own cues/screenshots carry the story.
        if is_interrupted:
            return ""
        if is_blocked:
            head_verb = prompts.t("HEAD_VERB_BLOCKED", lang=self._lang)
        elif is_success:
            head_verb = prompts.t("HEAD_VERB_SUCCESS", lang=self._lang)
        else:
            head_verb = prompts.t("HEAD_VERB_FAILED", lang=self._lang)
        lines = [prompts.t(
            "COMPLETION_HEAD_LINE", lang=self._lang,
            head_verb=head_verb, query=query[:100], status=status,
        )]
        if detail:
            lines.append(prompts.t(
                "COMPLETION_FEEDBACK_LINE", lang=self._lang, detail=detail[:240]
            ))
        if inv_line:
            lines.append(inv_line)
        if is_blocked:
            lines.append(prompts.t("COMPLETION_FOLLOWUP_BLOCKED", lang=self._lang))
        elif is_success:
            lines.append(prompts.t("COMPLETION_FOLLOWUP_SUCCESS", lang=self._lang))
        else:
            lines.append(prompts.t("COMPLETION_FOLLOWUP_FAILED", lang=self._lang))
        lines.append(prompts.t("INTERNAL_STATE_GAG", lang=self._lang))
        return prompts.t("CUE_PREFIX_DONE", lang=self._lang) + "\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # Hosted surface UI context
    # ------------------------------------------------------------------

    @ui.context(id="quickstart")
    async def quickstart_ui_context(self, **_):
        """quickstart guide surface 的只读上下文 provider。

        必须存在：hosted-surface 的 ``api.call`` 走 ``call_surface_action`` →
        ``host.get_ui_context(<surface 的 context_id>)``。surface 没声明
        ``context`` 时 context_id 取 surface id（这里 "quickstart"），缺了对应
        provider 会让 get_ui_context 直接报 "UI context not found"，连带
        ``_collect_ui_actions()`` 暴露的 action 列表也拿不到，``api.call`` 必败。

        surface 自身通过 ``props.api.call("game_agent_status")`` 拉实时状态，
        所以这里返回轻量快照即可（不强制 lazy-start，避免每次取 context 都阻塞
        3s 等 WS 握手）。返回值会和 host 注入的 ``actions`` 合并下发。
        """
        try:
            return {"status": self._service.get_status()}
        except Exception:
            return {"status": {}}

    # ------------------------------------------------------------------
    # Diagnostic plugin entries (callable from the plugin UI / CLI)
    # ------------------------------------------------------------------

    @ui.action(id="game_agent_status", label="刷新状态")
    @plugin_entry(
        id="game_agent_status",
        name="查询 mc-agent 连接状态",
        description=(
            "查询 mc-agent 的 WebSocket 连接状态、当前任务和缓存大小。"
            "适合在对话里明确出现连接异常（连不上 / 掉线 / 插件没反应 / 「mc-agent 连上了吗」）、"
            "或刚要开始玩、想确认 mc-agent 是否就绪时调用。"
            "不要在对话是要让化身在游戏里做事（过来、挖矿、建造、跟随等）、或普通闲聊时调用——"
            "那些场景不需要查询连接状态。"
        ),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def game_agent_status(self, **_):
        try:
            # Lazy-start so a status query right after plugin enable
            # actually shows a connecting/connected client, not the
            # never-started default. Without this the entry would report
            # connected=False forever even though the WS endpoint is up.
            await self._ensure_service_started()
            status = self._service.get_status()
            connected_label = prompts.t(
                "LABEL_CONNECTED" if status.get("connected") else "LABEL_DISCONNECTED",
                lang=self._lang,
            )
            pending = (
                status.get("pending_task")
                or prompts.t("PLACEHOLDER_IDLE", lang=self._lang)
            )
            status["summary"] = (
                f"ws={status.get('ws_url')} | {connected_label} | task={pending}"
            )
            return Ok(status)
        except Exception as exc:
            return Err(f"{type(exc).__name__}: {exc}")

    @plugin_entry(
        id="query_inventory",
        name="查询当前持有",
        description=(
            "查询当前 Minecraft 化身持有的物品（基于最近一次 task_finished "
            "或定时 nudge 缓存的 inventory snapshot）。供 analyzer 在用户问"
            "「我手里有啥」「背包里还剩多少 X」「现在能不能合成 Y」之类需要 "
            "ground-truth 库存事实的问题时调用。返回结构化 dict (item_name → "
            "count)，并附 summary 字符串方便对话 LLM 直接复述。"
        ),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def query_inventory(self, **_):
        try:
            connected = await self._ensure_service_started()
            # Always try a live query first — the cache piggy-backed on
            # task_finished frames goes stale fast (between explicit
            # actions, the player may have died / dropped / been hit and
            # we'd be reporting minutes-old fiction). ``request_fresh_inventory``
            # falls back to cache automatically when disconnected or
            # mc-agent doesn't respond in time, and tags the source so we
            # can be honest in the summary.
            snapshot = await self._service.request_fresh_inventory(timeout=2.0)
            inv = snapshot.get("inventory") or {}
            inv_at = snapshot.get("snapshot_at") or 0
            source = snapshot.get("source") or "cached"
            # `connected` was sampled before the 2s live-query window;
            # if the handshake completed inside that window and gave us
            # a live snapshot, the WS is provably connected even if the
            # pre-snapshot check said otherwise. Reconcile so the result
            # dict doesn't return source="live" + connected=False.
            connected = connected or source == "live"

            # Short, fact-only summaries. The dialog LLM only needs to
            # *know* the inventory, not复述 a long preamble — the old
            # version had her quoting "【ground truth — 完整且唯一】"
            # verbatim like a robot.
            if inv_at == 0:
                summary = prompts.t("INV_NO_DATA", lang=self._lang)
            elif source == "live" and inv:
                items = sorted(inv.items(), key=lambda kv: -kv[1])
                pieces = "、".join(f"{n}×{c}" for n, c in items)
                summary = prompts.t("INV_LIVE_NONEMPTY", lang=self._lang, pieces=pieces)
            elif source == "live":
                summary = prompts.t("INV_LIVE_EMPTY", lang=self._lang)
            elif inv:  # cached + has items
                age_s = max(0, int(time.time() - inv_at))
                items = sorted(inv.items(), key=lambda kv: -kv[1])
                pieces = "、".join(f"{n}×{c}" for n, c in items)
                summary = prompts.t(
                    "INV_CACHED_NONEMPTY", lang=self._lang, age_s=age_s, pieces=pieces
                )
            else:  # cached + empty
                age_s = max(0, int(time.time() - inv_at))
                summary = prompts.t("INV_CACHED_EMPTY", lang=self._lang, age_s=age_s)

            return Ok({
                "summary": summary,
                "inventory": inv,
                "snapshot_at": inv_at,
                "connected": connected,
                "source": source,
            })
        except Exception as exc:
            return Err(f"{type(exc).__name__}: {exc}")

    @plugin_entry(
        id="game_agent_reload_config",
        name="重载游戏插件配置",
        description=(
            "重新读取 plugin.toml [game_agent] 配置；纯数据项 (timeouts、"
            "intervals、screenshot 开关) 直接生效，ws_url 或重连间隔变更则会"
            "触发 WebSocket 客户端 stop+start 切换到新地址。"
        ),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def game_agent_reload_config(self, **_):
        try:
            # If the service hasn't been lazily started yet (i.e. plugin
            # was enabled but no entry / tool call has happened),
            # reload_config_live will return early without restarting
            # anything. Trigger lazy-start first so the new config
            # actually drives a live WS connection.
            await self._ensure_service_started()
            cfg = await self.config.dump(timeout=5.0)
            cfg = cfg if isinstance(cfg, dict) else {}
            self._cfg = (
                cfg.get("game_agent", {})
                if isinstance(cfg.get("game_agent"), dict)
                else {}
            )
            transport_restarted = await self._service.reload_config_live(self._cfg)
            summary = (
                "config reloaded with transport restart"
                if transport_restarted
                else "config reloaded (live)"
            )
            return Ok({
                "summary": summary,
                "transport_restarted": transport_restarted,
                "result": self._service.get_status(),
            })
        except Exception as exc:
            raise SdkError(f"reload failed: {type(exc).__name__}: {exc}")
