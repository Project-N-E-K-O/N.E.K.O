# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tool-calling surface for ``LLMSessionManager``: register/unregister
and sync of tools, the builtin ``recall_memory`` tool, and tool-call
dispatch.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
import os
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult
from utils.screenshot_utils import analyze_image_with_vision_model
from utils.vision_capability import model_supports_vision
from config.prompts.prompts_sys import _loc
from config.prompts.prompts_memory import (
    RECALL_MEMORY_TOOL_DESCRIPTION,
    RECALL_MEMORY_TOOL_QUERY_DESCRIPTION,
    RECALL_MEMORY_TOOL_TIME_DESCRIPTION,
    RECALL_MEMORY_TOOL_NO_RESULT,
    RECALL_MEMORY_TOOL_NO_RESULT_LOOSEN,
    RECALL_MEMORY_TOOL_FOUND_HEADER,
    _normalize_memory_prompt_lang,
)
from ._shared import logger


class ToolCallingMixin:
    """Tool-calling surface methods (see module docstring)."""

    # ------------------------------------------------------------------
    # Tool calling — public API for agent_server / plugins
    # ------------------------------------------------------------------

    def register_tool(self, tool: ToolDefinition, *, replace: bool = True) -> None:
        """Register a tool with the unified registry.

        - ``tool.handler`` is an in-process callable (recommended) — same-process
          agent_bridge / built-in features take this path.
        - When ``tool.handler is None``, calls are routed to ``ToolRegistry``'s
          ``remote_dispatcher``, used for cross-process plugins / agent_server.
          The latter is attached by main_server at startup (HTTP forwarding to
          the corresponding plugin).

        ⚠️ This is the **synchronous** entry: it only updates registry state;
        session sync runs fire-and-forget via ``_fire_task``. If the caller needs
        to wait until "the tool is genuinely live on the wire" before returning,
        use ``await register_tool_and_sync(...)`` instead (the HTTP
        /api/tools/register endpoint already uses that path automatically).
        """
        self.tool_registry.register(tool, replace=replace)
        self._fire_task(self._sync_tools_to_active_session())

    async def register_tool_and_sync(self, tool: ToolDefinition, *, replace: bool = True) -> None:
        """The awaitable version of ``register_tool``: registers, then waits for the session sync push to finish.

        For remote entries like HTTP `/api/tools/register` — by the time the
        caller gets the response, the tools on the active/pending sessions are
        already up to date, with no "returned ok but the next model call still
        can't see the tool" window. Serialization is guaranteed by
        ``_tool_sync_lock``: multiple concurrent registers can't put the wire's
        session.update out of order.

        ⚠️ ``raise_on_failure=True``: if the session.update genuinely fails on the
        wire, propagate the exception upward, so HTTP /api/tools doesn't return a
        false ok=true.
        """
        self.tool_registry.register(tool, replace=replace)
        await self._sync_tools_to_active_session(raise_on_failure=True)

    def unregister_tool(self, name: str) -> bool:
        existed = self.tool_registry.unregister(name)
        if existed:
            self._fire_task(self._sync_tools_to_active_session())
        return existed

    async def unregister_tool_and_sync(self, name: str) -> bool:
        existed = self.tool_registry.unregister(name)
        if existed:
            await self._sync_tools_to_active_session(raise_on_failure=True)
        return existed

    def list_tools(self) -> list[str]:
        return self.tool_registry.names()

    def clear_tools(self, *, source: str | None = None) -> int:
        n = self.tool_registry.clear(source=source)
        if n > 0:
            self._fire_task(self._sync_tools_to_active_session())
        return n

    async def clear_tools_and_sync(self, *, source: str | None = None) -> int:
        n = self.tool_registry.clear(source=source)
        if n > 0:
            await self._sync_tools_to_active_session(raise_on_failure=True)
        return n

    async def _on_tool_call(self, call: ToolCall) -> ToolResult:
        """Bridge invoked by both clients when the model emits a tool
        call. Forwards to the registry (process-global, outliving any
        single session), then routes any images the tool returned.
        """
        result = await self.tool_registry.execute(call)
        if result.images:
            await self._route_tool_images(result)
        return result

    async def _route_tool_images(self, result: ToolResult) -> None:
        """Decide how a tool's pictures reach the model, and degrade if needed.

        This is the single place that judgement lives, because it is the one
        choke point both clients share. Doing it per-plugin would mean every
        plugin reimplementing the fallback — and a plugin cannot see which
        model the session is running anyway.

        Pixels survive only for an offline session on a vision-capable model;
        ``_astream_openai_with_tools`` injects them as a one-shot multimodal
        message. Otherwise they are transcribed here and the list is cleared,
        so no downstream code has to re-check.

        Realtime always transcribes: its ``function_call_output`` item carries
        a string, so there is nowhere to put an image.
        """
        if isinstance(self.session, OmniRealtimeClient):
            reason = "realtime session"
        else:
            model = str(getattr(self.session, "model", "") or "")
            if model_supports_vision(model):
                return
            reason = f"model {model!r} has no vision"

        descriptions = []
        for image in result.images:
            descriptions.append(await self._describe_tool_image(image))
        logger.info(
            "Tool '%s': transcribed %d image(s) to text (%s)",
            result.name, len(descriptions), reason,
        )
        result.images = []
        result.merge_into_output(_image_descriptions=descriptions)

    async def _describe_tool_image(self, image) -> str:
        """Run one image through the vision model, never raising.

        The caller is mid tool-call with the conversation model waiting, so a
        vision outage has to become a sentence the character can say rather
        than an exception. Silence would be worse than either: she would
        answer as though she had looked.
        """
        try:
            description = await analyze_image_with_vision_model(
                image.data_b64,
                extra_instruction=image.vision_prompt,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Tool image transcription failed: %s: %s", type(e).__name__, e)
            description = None
        if description:
            return description
        return "（画面无法解读：未配置视觉模型或解析失败）"

    # ------------------------------------------------------------------
    # 内置 pseudo 工具：recall_memory
    # ------------------------------------------------------------------
    # 机制层占位：先让 offline / realtime 两条路径都能 register、把
    # description / parameters 推到 wire、收到模型的 tool call、回 result。
    # handler 当前固定返回"没有找到相关记忆"，等真实记忆检索接好后只
    # 替换 ``_handle_recall_memory_call`` 即可，不动注册 / 同步链路。

    def _register_builtin_tools(self) -> None:
        """Re-register the built-in tools, with description / parameter docs in the current
        ``user_language``. Calls ``tool_registry.register(replace=True)`` directly
        rather than the public ``register_tool``, to avoid firing unnecessary
        ``_sync_tools_to_active_session`` on hot paths like __init__ /
        start_session — this method's callers decide whether to sync.

        Kill-switch: set ``NEKO_DISABLE_BUILTIN_TOOLS=1`` to make this method
        return early without writing any builtin into the registry. Intended for
        A/B debugging of "suspected tool-schema-induced voice stream stutter /
        StepFun-proxy compatibility issues" — flip the switch → restart → the same
        frontend code runs in a "no builtin tools at all" state, comparing which
        baseline (with vs. without) misbehaves. Effective when the value is
        ``1`` / ``true`` / ``yes``.
        """
        if os.environ.get("NEKO_DISABLE_BUILTIN_TOOLS", "").strip().lower() in ("1", "true", "yes"):
            logger.info(
                "[builtin tools] NEKO_DISABLE_BUILTIN_TOOLS set — skipping recall_memory registration"
            )
            return
        _lang = _normalize_memory_prompt_lang(self.user_language)
        recall_tool = ToolDefinition(
            name="recall_memory",
            description=_loc(RECALL_MEMORY_TOOL_DESCRIPTION, _lang),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": _loc(RECALL_MEMORY_TOOL_QUERY_DESCRIPTION, _lang),
                    },
                    "time": {
                        "type": "string",
                        "description": _loc(RECALL_MEMORY_TOOL_TIME_DESCRIPTION, _lang),
                    },
                },
                # query / time 至少给一个：只给 time 就按时间回溯（不依赖
                # 内容），只给 query 就语义检索。两者都空时 handler 早退回
                # "没有找到相关记忆"。故 required 留空，靠 handler 兜底。
                "required": [],
            },
            handler=self._handle_recall_memory_call,
            metadata={"source": "builtin"},
        )
        self.tool_registry.register(recall_tool, replace=True)

    async def _handle_recall_memory_call(self, arguments: dict) -> str:
        """Handler for ``recall_memory`` — calls memory_server's
        ``/query_memory/{lanlan_name}`` over HTTP to run hybrid BM25 + cosine
        recall, formats the results as markdown bullets and returns them to the
        model.

        Logging is split into two tiers (privacy):
        - **INFO**: only reports name / mode / session / lang / hit count /
          elapsed ms. *No raw query text / no raw recalled text*.
          INFO persists to ``D:/Documents/N.E.K.O/logs/N.E.K.O_Main_<date>.log``
          and may be bundled and shipped out; raw memory text (containing user
          privacy) must not appear there.
        - **DEBUG**: only here do the raw query / recalled id list / full args
          land. DEBUG is hidden from the console by default and only goes to the
          project-dir _debug_ file, never shipped out.

        Failure fallback: if HTTP dies at any stage → return "no relevant
        memories found" as an empty result, letting the model continue the
        conversation flow. Never raise to the upstream wire, or one failed tool
        call would stall the model's whole turn.
        """
        _lang = _normalize_memory_prompt_lang(self.user_language)
        args_dict = arguments if isinstance(arguments, dict) else {}
        query = ""
        raw_query = args_dict.get("query")
        if isinstance(raw_query, str):
            query = raw_query.strip()
        time_arg = ""
        raw_time = args_dict.get("time")
        if isinstance(raw_time, str):
            time_arg = raw_time.strip()
        session_kind = type(self.session).__name__ if self.session is not None else "no-session"

        # 空入参早退：模型偶尔会用空 args 调一下"探探工具是否可用"，省一次
        # HTTP。但只要带了 time（按时间回溯，不依赖 query），就不算空入参。
        if not query and not time_arg:
            logger.info(
                "[recall_memory] called by name=%s mode=%s session=%s lang=%s "
                "→ empty query, no fetch",
                self.lanlan_name, self.input_mode, session_kind, _lang,
            )
            logger.debug("[recall_memory] empty-query args=%s", args_dict)
            return _loc(RECALL_MEMORY_TOOL_NO_RESULT, _lang)

        # POST 到 memory_server。query 始终原样下传，不能因为带了 time 就清空
        # —— 下游路由：query + time → hybrid_recall(query, time_window=...) 做
        # "语义 + 时间"联合检索（窗口内按 query 排序，语义匹配保留）；只有 time
        # → 纯时间邻近回溯；time 解析失败还要靠 query 回落语义检索。
        post_body = {"query": query, "language": self.user_language}
        if time_arg:
            post_body["time"] = time_arg
        result_payload: dict = {}
        recall_request_ok = False  # 仅当 memory server 真正成功返回时才置真
        try:
            from utils.internal_http_client import get_internal_http_client
            client = get_internal_http_client()
            resp = await client.post(
                f"http://127.0.0.1:{self.memory_server_port}/query_memory/{self.lanlan_name}",
                json=post_body,
                timeout=5.0,
            )
            if not resp.is_success:
                # WARNING 只带 status + body 长度（非敏感元数据）；body 原文
                # 含跨进程边界返回的字符串，可能夹带 query 回显 / 错误细节
                # 等含上下文内容，按 PR #1384 立的隐私分层规矩落 DEBUG。
                body_text = resp.text or ""
                logger.warning(
                    "[recall_memory] memory_server returned status=%s body_len=%d",
                    resp.status_code, len(body_text),
                )
                logger.debug(
                    "[recall_memory] non-success response body=%r",
                    body_text[:500],
                )
            else:
                result_payload = resp.json()
                recall_request_ok = True
        except Exception as exc:
            logger.warning(
                "[recall_memory] memory_server call failed (%s: %s); "
                "returning empty result",
                type(exc).__name__, exc,
            )

        results = result_payload.get("results") if isinstance(result_payload, dict) else None
        results = results if isinstance(results, list) else []
        elapsed_ms = result_payload.get("elapsed_ms", 0) if isinstance(result_payload, dict) else 0

        # INFO 只记 has_time（布尔），不落 time_arg 原值——time_arg 是用户
        # 原始输入，按本函数 docstring 立的隐私分层规矩（INFO 可能被打包外送）
        # 原文只进下面的 DEBUG。
        logger.info(
            "[recall_memory] called by name=%s mode=%s session=%s lang=%s "
            "has_time=%s → hits=%d elapsed=%.0fms",
            self.lanlan_name, self.input_mode, session_kind, _lang,
            bool(time_arg), len(results), elapsed_ms,
        )
        logger.debug(
            "[recall_memory] args=%s query=%r time=%r ids=%s",
            args_dict, query, time_arg,
            [r.get("id") for r in results],
        )

        if not results:
            # 同时带了 query 和 time 却 0 命中：八成是两个过滤条件叠加太窄
            # （时间窗口里没有语义匹配的条目）。别直接报"没有记忆"让模型放弃，
            # 提示它放宽——只留 time 或只留 query 再查一次。
            # 仅在请求**真正成功返回**时才给放宽提示：non-2xx / 异常也会落到
            # results=[]，那是 memory server 临时故障，不该误导模型"换条件重试"
            # 白烧刚收紧的工具迭代预算。
            if recall_request_ok and query and time_arg:
                return _loc(RECALL_MEMORY_TOOL_NO_RESULT_LOOSEN, _lang).format(query=query)
            return _loc(RECALL_MEMORY_TOOL_NO_RESULT, _lang)

        # 整段渲染扔进 worker 线程：truncate_to_tokens 编码的是**截断前**的
        # 原文，而这条链路存在的理由正是"上游可能返回一条超长的合并
        # reflection"。tiktoken 对切不开的超长 chunk 是二次退化，同步跑在
        # 事件循环上会连带卡住语音会话。渲染本身保持同步函数（与插件侧
        # memory_bridge.render_relevant_memory 同构、测试可直调），offload
        # 放在这个唯一的 async 调用点。
        return await asyncio.to_thread(self._render_recall_block, results, _lang)

    @staticmethod
    def _render_recall_block(results: list, _lang: str) -> str:
        """Render recall hits through the shared entry point, with a header.

        Format: ``1. [事实/关于用户] text  (2026-05-01, 23 天前)``, under an
        i18n overview line.

        No line building happens here. ``memory.recall_render`` is the one
        place recall results become prompt text — it owns the label table,
        the time anchor and the token budgets, and it owns them once so
        this side and the QQ plugin's ``render_relevant_memory`` cannot
        drift apart (issue #2588; the two used to be hand-written twins).
        This method supplies the locale and the overview line, which the
        entry point charges to the same budget because it lands in the same
        string the model reads.

        Entry count here comes from hybrid_recall's fusion cap rather than
        the plugin's limit=5, so this is the side where the block cap
        actually binds.

        Deliberately synchronous, and called through ``asyncio.to_thread``
        — see the caller.
        """  # noqa: DOCSTRING_CJK
        from config import RECALL_RENDER_TOTAL_MAX_TOKENS
        from memory.recall_render import render_recall_block

        block = render_recall_block(
            results, _lang,
            header_template=_loc(RECALL_MEMORY_TOOL_FOUND_HEADER, _lang),
        )
        if block.dropped:
            logger.info(
                "[recall_memory] rendered block over %d tok budget; dropped "
                "trailing %d entries", RECALL_RENDER_TOTAL_MAX_TOKENS, block.dropped,
            )
        return block.text

    async def _sync_tools_to_active_session(self, *, raise_on_failure: bool = False) -> None:
        """Sync the registry's current state to all active clients.

        Covers:
        - ``self.session``: the currently active main session
        - ``self.pending_session``: the session prewarming during hot-swap (the
          window where the new catgirl is built but not yet formally swapped).
          Without syncing it, tools registered via register_tool before
          pending_session takes over would be lost after the hot-swap completes.

        ``apply_tools_to_session`` is only meaningful for ``OmniRealtimeClient``
        instances with a live ws connection; offline clients just rely on
        ``set_tools`` picking up the new snapshot at the next ``stream_text``.

        ⚠️ Serialization: ``_tool_sync_lock`` guarantees that concurrent calls
        push session.update one by one in call order. Otherwise the wire events
        from back-to-back ``register_tool / unregister_tool / clear_tools`` could
        arrive out of order, and the last snapshot might not match the registry's
        final state.
        """
        async with self._tool_sync_lock:
            # registry 在 lock 内才读，确保拿到的是 lock 持有期间的真实快照
            # （而不是入队时的旧值）。
            defs = self.tool_registry.all()
            targets = []
            if self.session is not None:
                targets.append(self.session)
            if self.pending_session is not None and self.pending_session is not self.session:
                targets.append(self.pending_session)
            if not targets:
                return
            errors: list[str] = []
            for sess in targets:
                role = "pending" if sess is self.pending_session else "active"
                try:
                    if hasattr(sess, "set_tools"):
                        sess.set_tools(defs)
                    if hasattr(sess, "set_tool_call_handler"):
                        sess.set_tool_call_handler(self._on_tool_call)
                    if isinstance(sess, OmniRealtimeClient) and sess.ws is not None:
                        await sess.apply_tools_to_session()
                except Exception as e:
                    err_text = f"{role}: {type(e).__name__}: {e}"
                    logger.warning("⚠️ Tool sync to %s session failed: %s", role, e)
                    errors.append(err_text)
            if errors and raise_on_failure:
                # 给 ``*_and_sync`` 调用方一个明确信号：wire 上没真生效，
                # 让 HTTP /api/tools 不要回 ok=true 假成功。
                raise RuntimeError("tool sync failed: " + "; ".join(errors))
