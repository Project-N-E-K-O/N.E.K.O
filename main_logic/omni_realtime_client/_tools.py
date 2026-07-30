# -- coding: utf-8 --
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

from ._shared import (
    Any,
    Dict,
    List,
    OnToolCallCallback,
    Optional,
    ToolCall,
    ToolDefinition,
    ToolResult,
    logger,
    time,
)



class _ToolingMixin:
    def set_tools(self, tool_definitions: Optional[List[ToolDefinition]]) -> None:
        """Replace the active tool list. Takes effect the next time the
        client builds its session config (next ``connect`` call). For an
        already-connected session, callers can also call
        ``apply_tools_to_session`` to push the new list mid-conversation
        (only providers whose protocol allows mid-session tool updates
        will honour it; OpenAI Realtime and Step accept ``session.update``
        with new ``tools``)."""
        self._tool_definitions = list(tool_definitions or [])

    def set_tool_call_handler(self, handler: Optional[OnToolCallCallback]) -> None:
        self.on_tool_call = handler

    def has_tools(self) -> bool:
        return bool(self._tool_definitions) and self.on_tool_call is not None

    def _tools_for_openai_realtime(self) -> List[Dict[str, Any]]:
        """OpenAI Realtime / GLM Realtime schema — flat (type/name/
        description/parameters at the same level)."""
        return [t.to_openai_realtime() for t in self._tool_definitions] if self.has_tools() else []

    def _tools_for_step(self) -> List[Dict[str, Any]]:
        """StepFun Realtime schema — nested under ``function``."""
        return [t.to_openai_chat() for t in self._tool_definitions] if self.has_tools() else []

    def _tools_for_qwen(self) -> List[Dict[str, Any]]:
        """Qwen-Omni-Realtime schema — nested under ``function``, same shape
        as StepFun (see the example in the Aliyun client-events docs)."""
        return [t.to_openai_chat() for t in self._tool_definitions] if self.has_tools() else []

    async def apply_tools_to_session(self) -> None:
        """Push the current tools list to the connected session
        mid-conversation. Caller is responsible for calling this only
        after the session is connected."""
        if not self.ws and not self._gemini_session:
            return
        if self._is_gemini:
            # Gemini Live API does not support session.update mid-session;
            # tool list is fixed at connect time. Log + ignore.
            logger.info("apply_tools_to_session: Gemini Live does not support mid-session tools update — ignoring")
            return
        api = self._api_type.lower()
        if api == 'step' or api == 'free':
            # stepaudio-2.5-realtime 不再支持内置 web_search，与
            # update_session 初始化路径保持一致：只发 caller 注册的
            # function tools。
            tools_payload: List[Dict[str, Any]] = self._tools_for_step()
            await self.update_session({"tools": tools_payload})
        elif api == 'gpt':
            payload: Dict[str, Any] = {"tools": self._tools_for_openai_realtime()}
            if self.has_tools():
                payload["tool_choice"] = "auto"
            await self.update_session(payload)
        elif api == 'grok':
            # xAI Grok 走 OpenAI Realtime 协议，schema 与 GPT 同构。
            payload: Dict[str, Any] = {"tools": self._tools_for_openai_realtime()}
            if self.has_tools():
                payload["tool_choice"] = "auto"
            await self.update_session(payload)
        elif api == 'glm':
            # GLM 文档要求："ServerVAD 时更新 tools 需同时传入 turn_detection"。
            # 此方法的调用前提是已 connect()，连接时已把 turn_detection 设成
            # server_vad —— 这里复发同样的值即可，免得服务端 reset 成默认。
            await self.update_session({
                "tools": self._tools_for_openai_realtime(),
                "turn_detection": {"type": "server_vad"},
            })
        elif api == 'qwen':
            # Qwen-Omni-Realtime: tools 与 enable_search 互斥；当我们
            # 注册了自定义工具，强制关掉 enable_search 防止 server 拒绝。
            qwen_payload: Dict[str, Any] = {"tools": self._tools_for_qwen()}
            if self.has_tools():
                qwen_payload["enable_search"] = False
            await self.update_session(qwen_payload)
        else:
            logger.info("apply_tools_to_session: api_type=%s does not support custom tools — ignoring", api)

    async def _execute_tool_call(self, call: ToolCall) -> ToolResult:
        """Run the user-supplied ``on_tool_call`` callback and trap any
        exception so we still return a structured ``ToolResult`` the
        provider can ingest (model usually recovers from a tool error
        gracefully)."""
        if self.on_tool_call is None:
            msg = "no on_tool_call handler bound"
            return ToolResult(
                call_id=call.call_id, name=call.name,
                output={"error": msg}, is_error=True, error_message=msg,
            )

        # [ISSUE4c] Sliding-window tool-call flood guard. Count tool executions
        # in the last _TOOL_CALL_WINDOW_S; once it exceeds _TOOL_CALL_WINDOW_MAX,
        # do NOT execute — return a hard STOP warning as the function_call_output
        # so the model (which has no per-turn tool cap of its own) is told to
        # stop calling tools and respond by voice instead. The function_call and
        # this warning output both stay in the conversation via the normal
        # function_call_output path, so the model still "sees" that it tried.
        _TOOL_CALL_WINDOW_S = 15.0
        _TOOL_CALL_WINDOW_MAX = 4
        _now_tc = time.time()
        self._recent_tool_call_times = [
            t for t in self._recent_tool_call_times if _now_tc - t < _TOOL_CALL_WINDOW_S
        ]
        if len(self._recent_tool_call_times) >= _TOOL_CALL_WINDOW_MAX:
            logger.warning(
                "OmniRealtimeClient: tool-call flood guard tripped (%d calls in %.0fs) — "
                "refusing '%s', telling model to stop",
                len(self._recent_tool_call_times), _TOOL_CALL_WINDOW_S, call.name,
            )
            return ToolResult(
                call_id=call.call_id, name=call.name,
                output={
                    "stop": True,
                    "warning": (
                        f"本轮短时间内已调用工具 {len(self._recent_tool_call_times)} 次，已达上限。"
                        f"停止调用任何工具（包括 {call.name}），不要重试、不要换措辞再调。"
                        "直接用语音回应，等需要时再调用。本次未执行。"
                    ),
                },
                is_error=True, error_message="tool-call rate limit reached",
            )
        self._recent_tool_call_times.append(_now_tc)

        try:
            return await self.on_tool_call(call)
        except Exception as e:
            logger.exception("OmniRealtimeClient: on_tool_call '%s' raised", call.name)
            return ToolResult(
                call_id=call.call_id, name=call.name,
                output={"error": f"{type(e).__name__}: {e}"},
                is_error=True, error_message=str(e),
            )

    async def _send_tool_result_openai_realtime(self, result: ToolResult) -> None:
        """OpenAI Realtime / GLM Realtime / StepFun / Qwen / Free —
        send tool result via ``conversation.item.create`` of type
        ``function_call_output``, then ``response.create``.

        ⚠️ Provider differences:
        - OpenAI gpt / StepFun / Qwen / Free: ``call_id`` is required;
          the server uses it to bind the result back to the corresponding
          function_call.
        - GLM: the documented example shows function_call_output with
          **only an output field**, and the server's
          ``function_call_arguments.done`` carries no call_id either. The
          ``glm_<rid>_<idx>`` we synthesize at the done event is solely for
          internal registry tracking and must never be sent back to the
          server, or the request is likely to be rejected.
        """
        item: Dict[str, Any] = {
            "type": "function_call_output",
            "output": result.output_as_json_string(),
        }
        api = self._api_type.lower()
        if api == 'glm':
            # GLM 协议不接受 call_id。哪怕我们内部合成了，也不外传。
            pass
        elif result.call_id:
            item["call_id"] = result.call_id
        item_event = {
            "type": "conversation.item.create",
            "item": item,
        }
        ticket = await self._ensure_response_arbiter().enqueue(
            source="tool_result",
            events_before_response=(item_event,),
            response_event={"type": "response.create"},
            priority=5,
        )
        await ticket.sent
