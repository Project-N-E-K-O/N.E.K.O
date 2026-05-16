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
)

from .service import GameAgentService

# JSON Schema reused by the @llm_tool decorator below. Pulled into a
# module-level constant so the plugin's introspection (status entry,
# tests) can reference exactly what the LLM sees.
MINECRAFT_TASK_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": "A concrete, directly executable Minecraft goal in English.",
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
    "Use this tool when the user asks the character to do something in "
    "the game, or when continuing an in-game activity that needs another "
    "concrete step. Do NOT use it for chat, status questions, or "
    "abstract intent — see ``query_inventory`` for inventory lookups.\n\n"
    "Parameters:\n"
    "  task (string, required): one concrete executable action in "
    "English with specific targets — exact coordinates, specific block "
    "or entity types, specific quantities. Vague intents ('find a good "
    "place to build a house', 'find a blue block', 'come over here') "
    "are not executable. Prefer single-step actions (one mine / one "
    "craft / one walk) over long compound chains; chains complete "
    "piece by piece and each step's real outcome must be observed "
    "before claiming the next.\n"
    "  overwrite (bool, default false): if a previous task is still in "
    "flight, false rejects this call with a 'busy' summary and the "
    "previous task keeps running (correct ~95% of the time — let it "
    "finish and chain naturally). Set true ONLY when the user "
    "explicitly demands an interrupt ('stop', 'cancel that, do X'), or "
    "you have directly observed the current task is hopelessly stuck "
    "(blocked for 30s+ with zero progress in screenshots). Do NOT set "
    "true for 'better plan' / 'more efficient' subjective reasons — "
    "interrupting in-flight actions frequently causes chaos.\n\n"
    "Inventory ground truth: the cue includes a ``【当前持有 ground "
    "truth】`` line listing the character's actual inventory at task "
    "completion — items NOT listed there do not exist; never narrate "
    "items you don't see in that line."
)


@neko_plugin
class GameAgentMinecraftPlugin(NekoPluginBase):
    """Plugin facade — minimal: lifecycle wiring + tool surface.

    Real logic lives in :class:`GameAgentService`; this class only
    handles the SDK integration boilerplate.
    """

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: Dict[str, Any] = {}
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
                "summary": (
                    "调用没成功——缺了具体的动作描述。"
                    "想清楚你这次想干啥（比如 'mine 4 oak logs nearby'、"
                    "'walk to 120 64 -50'），再重新调用。"
                )
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
                "summary": (
                    "你刚连上游戏还没就位，没法立刻动。稍等再来一次。"
                )
            }

        # Pre-check pending state. Without this short-circuit, every
        # concurrent call returns the same standard "action dispatched"
        # ack — the dialog LLM then narrates as if its new action took,
        # but the detached task underneath actually got rejected by the
        # pending lock (returning ``{result: "busy", ...}``). Pre-checking
        # lets us tell the dialog LLM the truth (the slot is occupied) in
        # the synchronous tool result, instead of relying on an async cue
        # that may arrive too late.
        if not overwrite_flag and self._service.has_pending_task():
            return {
                "summary": (
                    "你还在做上一个动作，新动作没派出去。"
                    "等画面变化或上一个动作真的结束再来。"
                )
            }

        # Fire-and-forget: schedule the actual execution as a detached task
        # and return an acknowledgement immediately. Rationale:
        # - Dialog LLM has tight realtime constraints; blocking it 25–295s
        #   waiting for a long minecraft action would freeze the conversation.
        # - The dialog LLM doesn't need (and shouldn't have) the structured
        #   tool result — fresh screenshots + the [character status] cue
        #   pushed by ``_on_detached_task_done`` already give it everything
        #   to ground its narration to {MASTER_NAME}.
        # - The acknowledgement explicitly tells the dialog LLM not to
        #   fabricate results, which is the failure mode if we just returned
        #   None or {}.
        detached = asyncio.create_task(
            self._service.execute_minecraft_task(task=task_text, overwrite=overwrite_flag),
            name=f"game_agent_minecraft.task:{task_text[:40]}",
        )
        detached.add_done_callback(self._on_detached_task_done)
        return {
            "summary": (
                "刚开始动——结果还没出现，新画面和反馈会在接下来 1-30 秒陆续到。"
                "在看到之前不要描述任何具体成果（不要说『搞定了』、『拿到了 X』、"
                "『已经到 Y 了』），想说就只说『我去试试……』之类的第一人称。"
            )
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
            # ai_behavior="respond" + priority=2: the action just
            # finished; the dialog LLM should immediately narrate the
            # outcome to {MASTER_NAME} and (if appropriate) decide a
            # next concrete action. Without ``respond`` the cue would
            # only land in context as silent reading material; the
            # human-facing report would be deferred to the next user
            # turn, which feels unresponsive. Priority 2 sits between
            # alert (1, preempts everything) and normal screenshot
            # stream (3+, background read).
            self.push_message(
                source="game_agent_minecraft",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": cue}],
                priority=2,
            )
        except Exception as exc:
            self.logger.warning(
                "[detached] completion cue push failed: {}: {}",
                type(exc).__name__, exc,
            )

    def _format_completion_cue(self, result: Dict[str, Any]) -> str:
        """Format ``service.execute_minecraft_task`` return value into
        a single-line cue. Covers all five documented shapes:

        * ``{status: ok|timeout|interrupted, query, text/reason}``
        * ``{result: "busy", currently_executing, hint}``
        * ``{output, is_error: True, error}`` — also reads ``output.query``
          since AGENT_DISCONNECTED path nests task text inside ``output``.

        The tail nudges the dialog LLM to (a) ground its next utterance
        in actual visual/system state rather than imagine the outcome,
        and (b) immediately decide a next concrete action when relevant.
        ``{MASTER_NAME}`` is substituted by main_logic core's callback
        renderer at injection time so plugin text can refer to the dialog
        roles without hardcoding live names.
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
            # AGENT_DISCONNECTED path: real query text and error message
            # are inside the nested ``output`` dict.
            if not query:
                query = str(result["output"].get("query") or "")
            output_err = result["output"].get("error")
            if output_err and not detail:
                detail = str(output_err)
        # Avatar-framed rewrite for transport-level errors. The dialog LLM
        # must never see "agent server is not connected" or similar
        # underlying-transport vocabulary — that breaks the framing and
        # the LLM starts narrating about "reconnecting to the server".
        # Translate to body / sensation language; the actionable bit
        # (something went wrong, retry later) is preserved.
        if status == "AGENT_DISCONNECTED" or "not connected" in detail.lower():
            status = "暂时连不上游戏"
            detail = "和游戏的连接刚断了一下，稍后会自动恢复。"

        # GROUNDING: extract authoritative inventory snapshot from the
        # task_finished payload (mc-agent's ws_server.onTaskCompleted
        # always attaches an ``inventory`` dict, plus the body status
        # block). Surfacing this prominently in the cue head — and
        # marking it explicitly as ground truth — prevents the dialog
        # LLM from hallucinating items it doesn't have. Before this,
        # the inventory was hidden inside the verbose ``message`` field
        # which the LLM treated as soft narration; with the explicit
        # 【当前持有】 prefix it becomes a hard constraint.
        inventory_line = ""
        inv = result.get("inventory")
        if isinstance(inv, dict) and inv:
            items = sorted(
                ((str(k), int(v)) for k, v in inv.items() if int(v) > 0),
                key=lambda kv: -kv[1],
            )
            if items:
                snippet = "、".join(f"{name}×{count}" for name, count in items[:20])
                inventory_line = f"\n【当前持有 ground truth】{snippet}"
        elif isinstance(inv, dict):
            inventory_line = "\n【当前持有 ground truth】(空)"

        head = f"[你刚做完一段动作] 你刚才在做: \"{query[:120]}\" / 结果: {status}"
        body = f" / 反馈: {detail[:240]}" if detail else ""
        tail = (
            "\n用第一人称随口讲一句你刚才做了啥、现在看到/感觉到啥——"
            "**只能讲上面反馈和最新画面里真有的**，任务文本里没成真的步骤别提。"
            "【当前持有】行是你背包的 ground truth，绝对不要说出未列出的物品。"
            "然后立刻用 minecraft_task 接着派下一个具体动作——你在玩游戏，"
            "主动找事做，别站着等人指挥。"
        )
        return head + body + inventory_line + tail

    # ------------------------------------------------------------------
    # Diagnostic plugin entries (callable from the plugin UI / CLI)
    # ------------------------------------------------------------------

    @plugin_entry(
        id="game_agent_status",
        name="查询游戏代理状态",
        description="查询 Minecraft Agent 连接状态、当前任务和缓存大小。",
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
            connected = "connected" if status.get("connected") else "disconnected"
            pending = status.get("pending_task") or "(idle)"
            status["summary"] = (
                f"ws={status.get('ws_url')} | {connected} | task={pending}"
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
            inv = dict(self._service._last_inventory)  # snapshot copy
            inv_at = self._service._last_inventory_at
            # 断连场景：inv 默认 {}, inv_at 默认 0——这与"真的空背包"在数值上
            # 同形，但语义完全不同。如果照"空背包"模板返回，dialog LLM 会向
            # 用户复述一个假事实（"你背包是空的"）。改返回"未知"，让 LLM
            # 自然引导到"暂时看不到背包"而不是编造库存事实。
            if not connected and inv_at == 0:
                summary = (
                    "【背包 ground truth】暂时连不上游戏，看不到你现在的背包。"
                    "如果用户问到持有的物品，先说一声"
                    "『现在连不上游戏，等一下再确认』，别凭印象编。"
                )
            elif not inv:
                summary = (
                    "【背包 ground truth】你当前一无所有——背包是空的。"
                    "如果你刚才说过『拿到了 X』『有 Y』，那是幻想，立刻自己改口。"
                    if inv_at == 0
                    else
                    "【背包 ground truth】你当前背包是空的。如果你刚才说过持有"
                    "任何物品，那是幻想，立刻自己改口。"
                )
            else:
                items = sorted(inv.items(), key=lambda kv: -kv[1])
                snippet = "、".join(f"{n}×{c}" for n, c in items)
                summary = (
                    f"【背包 ground truth — 完整且唯一】你当前持有：{snippet}。"
                    "**这一行就是你背包的全部内容**——任何这里没列出的物品都"
                    "不在你身上。如果你刚才说过的话和这个列表对不上，立刻"
                    "用第一人称自己更正回真实情况。"
                )
            return Ok({"summary": summary, "inventory": inv, "snapshot_at": inv_at})
        except Exception as exc:
            return Err(f"{type(exc).__name__}: {exc}")

    @plugin_entry(
        id="game_agent_reload_config",
        name="重载游戏代理配置",
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
