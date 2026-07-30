from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Optional

from main_logic.omni_offline_client import route_supports_tool_calls
from main_logic.tool_calling import ToolResult
from utils.llm_client import SystemMessage, create_chat_llm_async
from utils.token_tracker import set_call_type

from .memory_tool_service import RECALL_TOOL_HTTP_TIMEOUT_SECONDS
from .pipeline_models import (
    QQInstructionBundle,
    QQModelResult,
    QQPipelineStageTrace,
    QQReplyContext,
    is_synthetic_source,
)


class QQReplyGenerationService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def generate_reply_fallback_direct_llm(
        self,
        *,
        context: QQReplyContext,
    ) -> Optional[str]:
        try:
            from utils.config_manager import get_config_manager

            if self.plugin._should_skip_direct_llm_fallback_for_images(message=context.message, attachments=context.attachments):
                self.plugin.logger.warning("QQ 图片消息跳过纯文本 fallback，避免假装已看图")
                return None
            model_config = get_config_manager().get_model_api_config("conversation")
            base_url = str(model_config.get("base_url") or "").strip()
            model = str(model_config.get("model") or "").strip()
            api_key = str(model_config.get("api_key") or "").strip()
            if not base_url or not model:
                self.plugin.logger.warning("Fallback 生成跳过：agent 模型未配置")
                return None
            llm = await create_chat_llm_async(
                model=model,
                base_url=base_url,
                api_key=api_key,
                max_completion_tokens=120,
                timeout=float(self.plugin._ai_turn_timeout_seconds or 60.0) + 0.5,
                provider_type=model_config.get("provider_type"),
            )
            try:
                set_call_type("conversation")
                fb_prompt, fb_recalled = self._sanitize_for_live_consent(
                    context, context.system_prompt, context.recalled_memory_text,
                )
                # 与主会话路径对偶：清洗只保证"调用发起时"的授权，调用期间
                # 撤销的话返回文本里仍带着那些内容。
                consent_before = self._consent_dependency_snapshot(context)
                self._store_consent_snapshot(context, consent_before)
                response = await llm.ainvoke([
                    {"role": "system", "content": self._compose_turn_instructions(fb_prompt, fb_recalled)},
                    {"role": "user", "content": context.prompt_message},
                ])
                fallback_reply = getattr(response, "content", "") or ""
                if fallback_reply and self._consent_dependency_revoked(
                    context, consent_before,
                ):
                    self.plugin.logger.warning(
                        "生成期间记忆授权被撤销，丢弃 fallback 回复"
                    )
                    return None
                if fallback_reply:
                    self.plugin.logger.info(f"Fallback 直连 LLM 生成成功 (length: {len(fallback_reply)})")
                    return fallback_reply
                self.plugin.logger.warning("Fallback 直连 LLM 未生成内容")
                return None
            finally:
                aclose = getattr(llm, "aclose", None)
                if callable(aclose):
                    try:
                        await aclose()
                    except Exception:
                        pass
        except Exception as e:
            self.plugin.logger.warning(f"Fallback 直连 LLM 生成失败: {e}")
            return None

    async def generate_fallback_from_context(self, context: QQReplyContext) -> Optional[str]:
        return await self.generate_reply_fallback_direct_llm(context=context)

    async def run_primary_session_call(self, context: QQReplyContext) -> QQModelResult:
        session_key = self.plugin.session_runtime_service.build_generation_session_key(context)
        stage_trace = QQPipelineStageTrace(
            stage="model_primary",
            status="started",
            metadata={
                "session_key": session_key,
                "is_group": context.is_group,
                "group_id": str(context.group_id or ""),
                "ephemeral_session": context.ephemeral_session,
                "group_scene_mode": context.group_scene_mode,
            },
        )
        synthetic_hist_before = None
        try:
            user_data = await self.plugin.session_bootstrap_service.ensure_generation_session(context, session_key)
            if not user_data:
                stage_trace.status = "no_session"
                return QQModelResult(reply_text=None, source="none", traces=[stage_trace])

            user_session, reply_chunks = self.plugin.session_runtime_service.prime_generation_session_state(
                user_data,
                session_key=session_key,
                context=context,
            )

            # 合成轮的 prompt 行在生成过程中进入历史：超时 salvage（下面
            # except 里的 discard→finalize）可能在 pipeline 的 finally 记录
            # 排除之前就把历史结算掉——生成前先记长度，超时时先排除再丢。
            synthetic_hist_before = (
                len(getattr(user_session, "_conversation_history", []) or [])
                if is_synthetic_source(getattr(context, "source_kind", ""))
                else None
            )

            try:
                ai_reply = await self._run_session_generation(
                    context=context,
                    session_key=session_key,
                    user_data=user_data,
                    user_session=user_session,
                    reply_chunks=reply_chunks,
                )
            finally:
                # 成员发言的收集绑定"会话已接受该 human 行"（stream_text 在
                # 发起网络流之前就把它追加进历史），不绑回复非空、也不绑
                # 生成成功：空回复轮与流异常/超时轮里成员的话都已进共享
                # 历史、会进群 digest，却会从 participant bucket 永久缺席。
                # 单点记录（成功钩子不再重复记）；recorder 自身按 sender
                # 追加，重复调用会重复入桶，故只此一处。
                if user_data.get("memory_enabled") and user_data.pop(
                    "human_row_accepted", False,
                ):
                    try:
                        self.plugin.session_memory_service.record_group_member_turn(
                            user_data, context,
                        )
                    except Exception as record_error:
                        # 绝不掩盖原始异常（finally 里抛出会替换掉
                        # TimeoutError，超时抢救与 trace 全部走偏）。
                        self.plugin.logger.warning(
                            f"成员发言记录失败: {record_error}"
                        )
            stage_trace.metadata["recalled_memory_used"] = context.recalled_memory_used
            stage_trace.metadata["recalled_memory_length"] = len(context.recalled_memory_text)
            if not ai_reply:
                self.plugin.logger.warning("AI 未生成回复，准备进入 fallback")
                stage_trace.status = "empty"
                stage_trace.metadata["reply_length"] = 0
                # 静默轮也要跑记忆管家：排空调度挂在这条路径上，一个模型
                # 一直选择沉默（或 fallback 也为空）的活跃群，否则群积压
                # 与成员队列永远不会被排空——队列到硬顶开始丢，历史被复读
                # 守卫重置时也没人抢救过。
                await self._run_memory_housekeeping(session_key, user_data)
                return QQModelResult(reply_text=None, source="session", allow_fallback=True, traces=[stage_trace])

            await self._sync_memory_after_success(
                session_key=session_key, user_data=user_data, context=context,
                reply_text=ai_reply,
            )
            self.plugin.logger.info(f"AI 生成回复完成 (会话: {session_key}, length: {len(ai_reply)})")
            stage_trace.status = "success"
            stage_trace.metadata["reply_length"] = len(ai_reply)
            return QQModelResult(reply_text=ai_reply, source="session", traces=[stage_trace])

        except asyncio.TimeoutError:
            # discard_session 内部会先结算群 scoped 缓冲再丢弃（集中抢救）。
            self.plugin.logger.warning(f"会话 {session_key} 处理超时，关闭并丢弃该会话")
            if synthetic_hist_before is not None:
                # 抢救会立即 finalize：合成控制 prompt 行必须先进排除名单，
                # 否则 pipeline 层跑完后的记录来不及、控制指令被提取成
                # 参与者历史。
                try:
                    self.plugin.session_memory_service.record_synthetic_prompt_rows(
                        session_key, synthetic_hist_before,
                    )
                except Exception as salvage_error:
                    # 抢救标记失败不能连累丢弃：会话刚被强制取消，留着它
                    # 下一轮必再超时，且未结算状态会一直挂着。
                    self.plugin.logger.warning(
                        f"超时轮合成 prompt 行标记失败: {salvage_error}"
                    )
            discarded = await self.plugin.session_runtime_service.discard_session(session_key, reason="generation_timeout")
            if discarded is False:
                # 结算失败被有意保留：但本会话的 stream 刚被 wait_for 强制
                # 取消，直接复用会再次超时、陷入死循环。打粘性标记让下轮
                # bootstrap 先重试 discard（含集中抢救），与登录身份变化
                # 的 pending_identity_discard 模式对齐。
                kept = self.plugin._user_sessions.get(session_key)
                if kept is not None:
                    kept["pending_identity_discard"] = True
            stage_trace.status = "timeout"
            return QQModelResult(reply_text=None, source="session", timed_out=True, traces=[stage_trace])
        except Exception as e:
            self.plugin.logger.exception(f"AI 生成回复失败: {e}")
            stage_trace.status = "error"
            stage_trace.detail = str(e)
            return QQModelResult(reply_text=None, source="none", traces=[stage_trace])
        finally:
            if context.ephemeral_session:
                await self.plugin.session_runtime_service.discard_session(session_key, reason="ephemeral_cleanup")

    def _compose_turn_instructions(self, system_prompt: str, recalled_memory_text: str) -> str:
        return "\n\n".join(part for part in [system_prompt, recalled_memory_text] if part)

    async def _run_session_generation(
        self,
        *,
        context: QQReplyContext,
        session_key: str,
        user_data: dict[str, Any],
        user_session: Any,
        reply_chunks: list[str],
    ) -> str | None:
        async with user_data["lock"]:
            reply_chunks.clear()

            queued_images = await self.plugin._queue_attachment_images(user_session, context.attachments)
            self.plugin.logger.info(f"发送消息到 AI (会话: {session_key}, length: {len(context.prompt_message)}, images: {queued_images})")
            # 群会话是全群共享的：创建时烙进 system prompt 的是首个发言者的
            # member persona / 身份行。群轮必须无条件换上本轮刚构建好的
            # prompt（含当前发言人的 scoped persona），否则召回为空的轮次
            # （早期常态）会一直用创建者快照回答所有人。
            # 生成前最后一道复检（集中在一处，读点/构建后/锁内三段窗口
            # 共用同一判据）：共享会话锁与附件排队可能让本轮等很久，其间
            # 任一授权被撤销，已注入 prompt 的对应段都不得用于生成。
            turn_system_prompt, turn_recalled_text = self._sanitize_for_live_consent(
                context, context.system_prompt, context.recalled_memory_text,
            )
            # 生成前的依赖快照：模型已经读到 scoped/跨群内容后，撤销才落
            # 下的话，回复本身仍带着那些内容——生成结束要再比一次。
            consent_before = self._consent_dependency_snapshot(context)
            self._store_consent_snapshot(context, consent_before)
            history_before = len(
                getattr(user_session, "_conversation_history", []) or []
            )
            # 成员发言的收集绑定"共享历史真的收下了这条 human 行"。锁等待
            # 与附件排队都在 stream_text 之前，它们异常/取消/超时时这条消息
            # 根本没进历史——此时入 participant bucket 会造出会话里不存在的
            # 成员记忆。
            user_data["human_row_accepted"] = False
            restore_session_prompt = self._apply_turn_memory_context(
                user_session, turn_system_prompt, turn_recalled_text,
                # 私聊会话的 prompt 是建会话时烙进去的：跨群授权打开时建的
                # 那条里带着别的群/联系人的清单，opt-out 之后本轮虽然构建了
                # 剥离版，不换上去 stream_text 用的还是旧的；而新 context 的
                # cross_session_section 已被剥空，两道 consent 闸也看不出
                # 依赖。开关关着时私聊也强制换。
                always_refresh=(
                    context.is_group
                    or bool(getattr(context, "cross_session_section", ""))
                    or not bool(
                        (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                            "allow_cross_group_context", False,
                        )
                    )
                ),
            )
            # recall_memory 工具按轮挂载：群会话是全群共享一个 client，而
            # participant subject 随发言人变——handler 闭包必须在会话锁内按
            # 本轮 context 重建，绝不能在建会话时冻结（那会让所有人都用首
            # 个发言者的 subject）。consent_before 传给闭包：工具读发生在
            # 生成中途，运行时记录要能被生成结束的撤销比对看到。
            armed_recall_tool = self._arm_recall_tool(
                context=context,
                user_session=user_session,
                reply_chunks=reply_chunks,
                consent_before=consent_before,
            )
            try:
                turn_timeout = self.plugin._ai_turn_timeout_seconds
                if armed_recall_tool:
                    # 工具轮的最坏路径是 2 次完整 LLM 流（初始流 + 封顶后的
                    # forced-finalize；插件会话 max_tool_iterations=1）加一次
                    # 召回 HTTP。沿用单流预算会把"慢但会成功"的工具轮变成
                    # 超时——而这条路径超时的代价不是丢一轮，是丢弃整个共享
                    # 群会话再打粘性标记。
                    turn_timeout = (
                        turn_timeout * 2 + RECALL_TOOL_HTTP_TIMEOUT_SECONDS
                    )
                await asyncio.wait_for(
                    user_session.stream_text(context.prompt_message),
                    timeout=turn_timeout,
                )

                completed = await self.plugin._wait_session_response_complete(user_session)
                if self._consent_dependency_revoked(context, consent_before):
                    # 生成期间授权被撤销：这条回复的 prompt 里带着已撤销的
                    # 内容，不能送出——清空出站文本只挡住了发送，stream_text
                    # 早已把 ai 行写进共享历史，留着它等于让被撤销的内容既
                    # 进 digest 又进后续轮次的上下文。本轮追加的 ai 行与
                    # tool 轮的裸 dict 行（assistant tool_calls / role=tool，
                    # content 里是召回原文）一并摘掉（human 行是用户自己的
                    # 发言，保留）。
                    self.plugin.logger.warning(
                        f"生成期间记忆授权被撤销，丢弃本轮回复 ({session_key})"
                    )
                    reply_chunks.clear()
                    history = getattr(user_session, "_conversation_history", None)
                    if isinstance(history, list):
                        while (
                            len(history) > history_before
                            and (
                                getattr(history[-1], "type", "") == "ai"
                                or self._is_tool_round_row(history[-1])
                            )
                        ):
                            history.pop()
                if not completed:
                    # 只 raise 不在这里 discard：外层 except TimeoutError 会
                    # 统一走"先抢救群缓冲再丢弃"，这里先 pop 会让 user_data
                    # 在抢救前就没了（原本也是双重 discard）。
                    self.plugin.logger.warning(f"会话 {session_key} 响应超时，关闭并丢弃该会话")
                    raise asyncio.TimeoutError
            finally:
                if armed_recall_tool:
                    # 按轮挂载的对偶收尾：工具与 handler 不得越轮存活——
                    # 同一 client 上的其他生成路径（proactive 的
                    # prompt_ephemeral 等）绝不能带着本轮的 subject 闭包
                    # 发起召回。
                    try:
                        user_session.set_tools(None)
                        user_session.set_tool_call_handler(None)
                    except Exception:
                        # 卸载失败不能连累收尾（下面还有历史清理与成员轮
                        # 记录），下一轮挂载会整体覆盖这两个槽位。
                        pass
                restore_session_prompt()
                history_now = getattr(user_session, "_conversation_history", []) or []
                if isinstance(history_now, list):
                    # tool 轮写进共享历史的裸 dict 行随轮清理：召回原文是按
                    # consent 域临时授权给本轮的，语义与旧管线的"prompt 注入
                    # + restore"一致——留在共享历史里会进 digest、进后续每轮
                    # 的上下文，member 撤销后也无法再摘除。模型的最终回答行
                    # （引用了召回结论的那条 ai 行）照常保留。
                    self._strip_tool_round_rows(history_now, history_before)
                appended = list(history_now)[history_before:]
                user_data["human_row_accepted"] = any(
                    getattr(row, "type", "") == "human" for row in appended
                )
                # 本轮真正写进历史的那条 ai 行（没有就是 None）。未投递打标
                # 按它的身份来：用"raw 输出非空"去推断历史里有行，是推断而
                # 不是证据——推断错了就会把上一条**已投递**的回复标成未投递，
                # 那条回复从此再也进不了 digest。
                user_data["current_turn_ai_row"] = next(
                    (row for row in reversed(appended)
                     if getattr(row, "type", "") == "ai"),
                    None,
                )
                if context.is_group and not user_data.get("memory_enabled"):
                    # 未授权边界在 finally 记：异常/空回复的 human 行也已
                    # 进历史，只在成功路径记会漏（超时路径会话随后被弃，
                    # 多记无害）。
                    user_data["nonconsent_history_end"] = len(
                        getattr(user_session, "_conversation_history", []) or []
                    )

            return "".join(reply_chunks)

    def _arm_recall_tool(
        self,
        *,
        context: Any,
        user_session: Any,
        reply_chunks: list[str],
        consent_before: dict,
    ) -> bool:
        """Install this turn's recall_memory tool + handler on the client.

        Returns whether the tool is actually armed. The capability check
        runs against the SESSION CLIENT's frozen route, not the current
        config: a cached session can outlive a provider switch, and a
        route that silently drops ``tools`` must not count as armed (the
        turn would think it has a recall channel it does not have).
        """
        if not getattr(context, "recall_via_tool", False):
            return False
        if not getattr(context, "use_memory_context", False):
            return False
        set_tools = getattr(user_session, "set_tools", None)
        set_handler = getattr(user_session, "set_tool_call_handler", None)
        if not callable(set_tools) or not callable(set_handler):
            return False
        if not route_supports_tool_calls(
            str(getattr(user_session, "model", "") or ""),
            str(getattr(user_session, "base_url", "") or ""),
        ):
            self.plugin.logger.warning(
                "缓存会话的线路不支持 tool call，本轮无召回（会话重建后恢复）"
            )
            return False
        try:
            set_tools([
                self.plugin.memory_tool_service.build_recall_tool_definition()
            ])
            set_handler(self._build_recall_tool_handler(
                context=context,
                reply_chunks=reply_chunks,
                consent_before=consent_before,
            ))
            return True
        except Exception as exc:
            self.plugin.logger.warning(
                f"recall_memory 工具挂载失败（本轮无召回）: {exc}"
            )
            try:
                set_tools(None)
                set_handler(None)
            except Exception:
                # 挂载半途失败后的兜底清理：清不掉也只影响本轮（返回
                # False 已宣布未挂载），finally 不会再动这两个槽位。
                pass
            return False

    def _build_recall_tool_handler(
        self, *, context: Any, reply_chunks: list[str], consent_before: dict,
    ):
        """This turn's recall_memory execution closure.

        Subjects never come from the model: ``execute_recall`` derives
        them from the turn context (the server reads an omitted subjects
        field as the legacy PRIVATE corpus). The closure also owns two
        pieces of turn plumbing — outbound-text hygiene and the runtime
        consent record.
        """

        # 一轮一次召回的闸在 handler 层：max_tool_iterations=1 只限 LLM/
        # tool 循环轮数，模型在同一个 assistant 回复里可以并排发多个
        # recall_memory 调用（客户端会逐个执行），流内重试也会再次进
        # tool 轮——每次都是一段 5s HTTP，会击穿超时预算里"一次召回"的
        # 假设，而这条路径超时的代价是丢弃整个共享群会话。空参试探
        # （execute_recall 本就不发 HTTP）不烧额度。
        recall_executed = [False]

        async def _handle_recall_tool(tool_call: Any) -> ToolResult:
            # pre-tool 文本（"我查一下"之类）不得外发：走到这里说明本轮
            # 进入了 tool 轮，之前流出的增量已由客户端写进 history 的
            # assistant tool_calls 行（随后与本轮其余 tool 行一并清理），
            # 出站文本只保留 post-tool 的最终回答。
            reply_chunks.clear()
            tool_service = self.plugin.memory_tool_service
            arguments = getattr(tool_call, "arguments", None) or {}
            substantive = tool_service.has_recall_arguments(arguments)
            if substantive and recall_executed[0]:
                self.plugin.logger.info(
                    "recall_memory 本轮已执行过，追加调用返回空结果"
                )
                return ToolResult(
                    call_id=getattr(tool_call, "call_id", "") or "",
                    name=getattr(tool_call, "name", "") or "recall_memory",
                    output=tool_service.no_result_text(),
                )
            if substantive:
                recall_executed[0] = True
            output, consumed = await tool_service.execute_recall(
                context=context,
                arguments=arguments,
            )
            if consumed:
                # consent 判据从"prompt 里有没有那段字"换成"运行时有没有
                # 真的发生这次读"：写进本轮的 consent_before（生成结束的
                # 撤销比对读它），并合入 context.consent_snapshot（发送前
                # 与 buffer 的撤销闸读它）。recalled_memory_used 不在这里
                # 记——它跟的是"召回内容被消费"而非"消费了群授权"，私聊
                # legacy 召回 consumed 恒空，由 execute_recall 在回填点记。
                for key, was_enabled in consumed.items():
                    consent_before[key] = (
                        bool(consent_before.get(key)) or bool(was_enabled)
                    )
                self._store_consent_snapshot(context, consumed)
            return ToolResult(
                call_id=getattr(tool_call, "call_id", "") or "",
                name=getattr(tool_call, "name", "") or "recall_memory",
                output=output,
            )

        return _handle_recall_tool

    @staticmethod
    def _is_tool_round_row(row: Any) -> bool:
        """A bare dict row the client's tool loop appended to history.

        Two shapes (OpenAI-compat and genai paths both append these):
        the assistant turn announcing tool_calls, and the role=tool
        result row carrying the recalled text.
        """
        if not isinstance(row, dict):
            return False
        role = row.get("role")
        return role == "tool" or (
            role == "assistant" and bool(row.get("tool_calls"))
        )

    @classmethod
    def _strip_tool_round_rows(cls, history: list, start_index: int) -> int:
        """Remove this turn's tool-round dict rows from shared history.

        Only rows appended at or after ``start_index`` are considered —
        the rows sit BETWEEN the human row and the final ai row, so this
        scans by index instead of popping from the tail. A repetition
        guard can reset the history to shorter than ``start_index``; the
        range is then empty and nothing is touched.
        """
        removed = 0
        for index in range(len(history) - 1, max(start_index, 0) - 1, -1):
            if cls._is_tool_round_row(history[index]):
                del history[index]
                removed += 1
        return removed

    async def _run_memory_housekeeping(
        self, session_key: str, user_data: dict[str, Any],
    ) -> None:
        """Schedule the backlog / member-bucket drains for this session.

        Shared by the success path and the silent-turn path: the drains are
        the only thing standing between an always-busy group and a queue
        that discards at its hard limit."""
        try:
            await self.plugin._cache_session_delta(session_key, user_data)
        except Exception as exc:
            self.plugin.logger.warning(f"记忆管家调度失败（忽略）: {exc}")

    def _consent_dependency_snapshot(self, context: Any) -> dict:
        """Which consent switches this turn's prompt actually depends on.

        Not group-only: with cross-group consent on, a PRIVATE reply's
        sessions block can name other groups and contacts, so that turn
        depends on the switch too."""
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        snapshot: dict = {}
        if getattr(context, "cross_session_section", ""):
            snapshot["allow_cross_group_context"] = bool(
                settings.get("allow_cross_group_context", False)
            )
        if not getattr(context, "is_group", False):
            return snapshot
        if getattr(context, "core_memory_text", "") or getattr(
            context, "recalled_memory_text", "",
        ):
            snapshot["group_memory_enabled"] = bool(
                settings.get("group_memory_enabled", False)
            )
            if getattr(context, "used_member_subject", False):
                snapshot["group_member_memory_enabled"] = bool(
                    settings.get("group_member_memory_enabled", False)
                )
        if getattr(context, "cross_group_section", ""):
            snapshot["allow_cross_group_context"] = bool(
                settings.get("allow_cross_group_context", False)
            )
        return snapshot

    def _store_consent_snapshot(self, context: Any, snapshot: dict) -> None:
        """Carry the generation-time snapshot to the pre-send gate.

        Unions with whatever the context already carries: a nested
        synthetic turn inherits the buffered drafts' dependencies, and its
        own (clean) prompt must not erase them."""
        try:
            merged = dict(getattr(context, "consent_snapshot", None) or {})
            for key, was_enabled in (snapshot or {}).items():
                merged[key] = bool(merged.get(key)) or bool(was_enabled)
            context.consent_snapshot = merged
        except Exception:
            # 合成调用方可能传的是轻量对象：拿不到就退回"发送前不复检"，
            # 生成后的复检仍在。
            pass

    def _consent_dependency_revoked(self, context: Any, before: dict) -> bool:
        """True when a switch this prompt relied on went off since `before`."""
        if not before:
            return False
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        return any(
            was_enabled and not settings.get(key, False)
            for key, was_enabled in before.items()
        )

    def _sanitize_for_live_consent(
        self, context: Any, system_prompt: str, recalled_text: str,
    ) -> tuple[str, str]:
        """Drop prompt sections whose consent is no longer live.

        One place for all three switches so every generation path (primary
        session call, direct fallback) enforces the same boundary — the
        per-path rechecks kept diverging as new paths appeared."""
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        if not settings.get("allow_cross_group_context", False):
            # 私聊轮也可能带会话清单段（跨群开关打开时它会列出其他群与
            # 联系人）：非群轮不能在这里直接返回，否则那段撤不掉。
            system_prompt = self._strip_section_text(
                system_prompt, getattr(context, "cross_session_section", "") or "",
            )
        if not getattr(context, "is_group", False):
            return system_prompt, recalled_text
        core_text = getattr(context, "core_memory_text", "") or ""
        if not settings.get("group_memory_enabled", False):
            # 群记忆关闭：scoped 召回与 bootstrap 段全部撤除。
            recalled_text = ""
            system_prompt = self._strip_section_text(system_prompt, core_text)
        elif getattr(context, "used_member_subject", False) and not settings.get(
            "group_member_memory_enabled", False,
        ):
            # 仅 member 关闭：召回混合了群域与 participant 域、无法事后
            # 拆分，连同 participant 派生的 bootstrap 段一起撤除。
            recalled_text = ""
            system_prompt = self._strip_section_text(system_prompt, core_text)
        if not settings.get("allow_cross_group_context", False):
            system_prompt = self._strip_section_text(
                system_prompt, getattr(context, "cross_group_section", "") or "",
            )
        return system_prompt, recalled_text

    @staticmethod
    def _strip_section_text(system_prompt: str, section_text: str) -> str:
        """Remove one composed section (with its separator) from a prompt."""
        if not section_text or section_text not in system_prompt:
            return system_prompt
        separator = "\n\n"
        for candidate in (
            separator + section_text, section_text + separator, section_text,
        ):
            if candidate in system_prompt:
                return system_prompt.replace(candidate, "", 1)
        return system_prompt

    @staticmethod
    def _strip_scoped_sections(system_prompt: str, context: Any) -> str:
        """Remove scoped-memory sections from an already-composed prompt.

        Used when group memory is revoked between context construction and
        generation: the bootstrap section is the only scoped block left in
        the prompt (recall is passed separately and simply dropped)."""
        return QQReplyGenerationService._strip_section_text(
            system_prompt, getattr(context, "core_memory_text", "") or "",
        )

    def _apply_turn_memory_context(
        self, user_session: Any, system_prompt: str, recalled_memory_text: str,
        *, always_refresh: bool = False,
    ):
        # always_refresh：群轮即使无召回也要换 prompt——
        # _compose_turn_instructions 会自动省略空的召回段，swap 退化为
        # 纯 system_prompt 替换；restore 保证会话落盘的仍是创建时原文。
        if not recalled_memory_text and not always_refresh:
            return lambda: None
        conversation_history = getattr(user_session, "_conversation_history", None)
        if not conversation_history or not isinstance(conversation_history[0], SystemMessage):
            return lambda: None
        original_system_message = conversation_history[0]
        original_instructions = getattr(user_session, "_instructions", original_system_message.content)
        enhanced_instructions = self._compose_turn_instructions(system_prompt, recalled_memory_text)
        conversation_history[0] = SystemMessage(content=enhanced_instructions)
        user_session._instructions = enhanced_instructions

        def restore() -> None:
            current_history = getattr(user_session, "_conversation_history", None)
            if current_history and current_history[0] is not original_system_message:
                current_history[0] = original_system_message
            user_session._instructions = original_instructions

        return restore

    async def _sync_memory_after_success(
        self,
        *,
        session_key: str,
        user_data: dict[str, Any],
        context: QQReplyContext,
        reply_text: str = "",
    ) -> None:
        if user_data.get("memory_enabled"):
            try:
                # member turn 已在主生成完成点单点记录（含空回复轮）；此处
                # 只做 cache 同步，避免同一发言重复入 bucket。
                count = await self.plugin._cache_session_delta(session_key, user_data)
                if count:
                    self.plugin.logger.info(f"[管理员] 成功同步 {count} 条消息到 Memory Server (会话: {session_key})")
            except Exception as e:
                self.plugin.logger.error(f"记忆同步失败: {e}")
            # mention 计数不在这里记：本钩子跑在生成成功时刻，buffer 可能
            # 把这条回复截停并用 summary 取代——没投递的草稿不得推进
            # suppression 计数。投递点统一调 record_scoped_mentions_on_delivery。
            return

        if user_data.get("memory_context_used"):
            self.plugin.logger.info(f"[临时发送] 已使用记忆上下文但跳过记忆同步 (会话: {session_key})")
            return
        if context.is_group:
            # 未授权边界已在 run_primary_session_call 每次尝试后统一记录。
            self.plugin.logger.info(f"[群聊] 跳过记忆同步 (群: {context.group_id}, 用户: {context.sender_id})")
            return
        self.plugin.logger.info(f"[非管理员] 跳过记忆同步 (用户: {context.sender_id}, 权限: {context.permission_level})")

    def append_fallback_ai_row(
        self, context: QQReplyContext, reply_text: str,
    ) -> None:
        """Put a delivered direct-fallback reply into the shared history.

        The primary session accepted the human row but produced nothing, so
        the fallback's text exists only in the outbound message: without
        this the group digest persists a one-sided conversation and loses
        whatever the bot disclosed. Idempotent — the row is tagged so a
        second delivery hook cannot double-append."""
        if not getattr(context, "is_group", False) or not reply_text:
            return
        if getattr(context, "ephemeral_session", False):
            return
        session_key = self.plugin.session_runtime_service.build_generation_session_key(context)
        user_data = (getattr(self.plugin, "_user_sessions", {}) or {}).get(
            session_key
        )
        if not user_data or not user_data.get("memory_enabled"):
            return
        session = user_data.get("session")
        history = getattr(session, "_conversation_history", None)
        if history is None:
            return
        # 幂等键取本轮消息 ID：context 对象可能被重建（构造时的 turn_uid
        # 就变了），而重复的投递钩子未必紧挨着——扫最近几行也会漏，故全
        # 历史精确匹配。没有消息 ID 的轮次（主动发言/合成轮/平台缺字段）
        # 退到 context 构造时生成的 turn_uid：绝不能用 id(context)，地址
        # 复用会让后续每一条 fallback 行都被误判成重复而永久丢失。
        turn_id = (
            str(getattr(context, "current_message_id", "") or "")
            or str(getattr(context, "turn_uid", "") or "")
        )
        if not turn_id:
            history.append(self._build_fallback_row(reply_text, ""))
            return
        marker = f"fallback:{turn_id}"
        for msg in reversed(history):
            if getattr(msg, "type", "") == "ai" and (
                getattr(msg, "additional_kwargs", None) or {}
            ).get("neko_fallback_row") == marker:
                return
        history.append(self._build_fallback_row(reply_text, marker))

    @staticmethod
    def _build_fallback_row(reply_text: str, marker: str):
        try:
            from langchain_core.messages import AIMessage

            row = AIMessage(content=reply_text)
            row.additional_kwargs["neko_fallback_row"] = marker
            return row
        except Exception:
            return SimpleNamespace(
                type="ai", content=reply_text,
                additional_kwargs={"neko_fallback_row": marker},
            )

    async def record_scoped_mentions_on_delivery(
        self, context: QQReplyContext, reply_text: str,
    ) -> None:
        """Bump scoped mention counters when a reply is ACTUALLY delivered.

        群路径绕开 legacy post_turn，scoped 条目的 mention 计数（防重复注入
        的 suppression 输入）只能在插件侧补记——且必须绑定投递而非生成：
        buffer 合并场景的草稿没人看到，各记一次会把被引用条目推进 suppression
        阈值、错误地从后续上下文消失。best-effort：失败只影响该条目晚几轮
        进入"暂不主动提及"。"""
        if not context.is_group or not reply_text or context.ephemeral_session:
            return
        if not (getattr(self.plugin, "_qq_settings", {}) or {}).get(
            "group_memory_enabled", False,
        ):
            # mention 计数是对群域记忆元数据的写：开关关掉之后不得再改，
            # 哪怕会话侧的 flag 还没被后台结算清掉。
            return
        session_key = self.plugin.session_runtime_service.build_generation_session_key(context)
        user_data = self.plugin._user_sessions.get(session_key)
        if not user_data or not user_data.get("memory_enabled"):
            return
        await self._record_scoped_mentions_best_effort(context, reply_text)

    async def _record_scoped_mentions_best_effort(
        self, context: QQReplyContext, reply_text: str,
    ) -> None:
        """Bump scoped mention counters with the subjects this reply was
        authorized to see, so repeatedly-volunteered scoped entries reach the
        suppression threshold like legacy entries do."""
        group_id = str(context.group_id or "").strip()
        if not group_id:
            return
        bridge = self.plugin.memory_bridge
        subjects = [bridge.group_subject(group_id)]
        sender_id = str(context.sender_id or "").strip()
        synthetic = is_synthetic_source(getattr(context, "source_kind", ""))
        member_authorized = bool(
            getattr(context, "member_memory_enabled", False)
            and (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                "group_member_memory_enabled", False,
            )
        )
        if sender_id and not synthetic and member_authorized:
            # 合成轮的名义 sender 不是真实发言人——mention 计数只按群域记，
            # 与召回/写入侧的合成轮过滤对齐。member 未授权时也不记：该域
            # 本轮没被召回，扫描/改写留存条目会把没展示过的事实压进
            # suppression、之后 opt-in 也不再出现。
            subjects.append(bridge.group_participant_subject(group_id, sender_id))
        try:
            await bridge.post_scoped_mentions(
                context.her_name, reply_text, subjects=subjects,
            )
        except Exception as e:
            self.plugin.logger.warning(f"scoped mention 记录失败（忽略）: {e}")

    async def run_fallback_memory_hooks(
        self, context: QQReplyContext, fallback_reply: str,
    ) -> None:
        """fallback 成功也要跑 scoped 记忆钩子：成员发言入 bucket、被展示
        的 scoped 条目计 mention——主会话空回复不代表这轮没发生。生产
        pipeline 走 QQReplyModelNode.generate()，legacy 入口走
        generate_from_context()——两条 fallback 成功路径都必须调这里。
        会话可能已被超时丢弃（user_data 不在了则跳过）。"""
        if not context.is_group or context.ephemeral_session:
            # ephemeral 键含 time_ns，重新生成必 miss；且 ephemeral 会话
            # persist=False、finally 即丢弃，记忆钩子本就无意义。
            return
        session_key = self.plugin.session_runtime_service.build_generation_session_key(context)
        user_data = self.plugin._user_sessions.get(session_key)
        if user_data is not None:
            await self._sync_memory_after_success(
                session_key=session_key, user_data=user_data,
                context=context, reply_text=fallback_reply,
            )

    async def generate_from_context(self, context: QQReplyContext) -> QQModelResult:
        if not context.is_group and context.permission_level not in ["admin", "trusted"]:
            return QQModelResult(reply_text=None, source="none")

        primary_result = await self.run_primary_session_call(context)
        if not primary_result.allow_fallback:
            return primary_result

        fallback_reply = await self.generate_fallback_from_context(context)
        if fallback_reply:
            primary_result.traces.append(
                QQPipelineStageTrace(
                    stage="model_fallback",
                    status="success",
                    metadata={"reply_length": len(fallback_reply), "group_scene_mode": context.group_scene_mode},
                )
            )
            await self.run_fallback_memory_hooks(context, fallback_reply)
            return QQModelResult(reply_text=fallback_reply, source="direct_llm_fallback", used_fallback=True, traces=primary_result.traces)
        primary_result.traces.append(
            QQPipelineStageTrace(
                stage="model_fallback",
                status="empty",
                metadata={"reply_length": 0, "group_scene_mode": context.group_scene_mode},
            )
        )
        return QQModelResult(reply_text=None, source="none", used_fallback=True, traces=primary_result.traces)

    async def generate_reply(
        self,
        message: str,
        permission_level: str,
        sender_id: str,
        attachments: list[dict[str, Any]] | None = None,
        is_group: bool = False,
        group_id: str = None,
        user_nickname: Optional[str] = None,
        use_memory_context: Optional[bool] = None,
        persist_memory: Optional[bool] = None,
        ephemeral_session: bool = False,
        group_facing: bool = False,
        group_scene_mode: str = "",
    ) -> Optional[str]:
        context = await self.plugin.reply_context_node.build(
            message=message,
            permission_level=permission_level,
            sender_id=sender_id,
            attachments=attachments,
            is_group=is_group,
            group_id=group_id,
            user_nickname=user_nickname,
            use_memory_context=use_memory_context,
            persist_memory=persist_memory,
            ephemeral_session=ephemeral_session,
            group_facing=group_facing,
            group_scene_mode=group_scene_mode,
        )
        model_result = await self.generate_from_context(context)
        outcome = self.plugin.reply_postprocess_node.finalize(context, model_result)
        return outcome.reply_text
