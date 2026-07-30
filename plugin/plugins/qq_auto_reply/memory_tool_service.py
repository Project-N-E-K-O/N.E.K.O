from __future__ import annotations

from typing import Any

from config.prompts.prompts_sys import _loc
from config.prompts.prompts_memory import (
    RECALL_MEMORY_TOOL_DESCRIPTION,
    RECALL_MEMORY_TOOL_QUERY_DESCRIPTION,
    RECALL_MEMORY_TOOL_TIME_DESCRIPTION,
    RECALL_MEMORY_TOOL_NO_RESULT,
    RECALL_MEMORY_TOOL_FOUND_HEADER,
)
from main_logic.tool_calling import ToolDefinition
from utils.language_utils import get_global_language, normalize_language_code

from .pipeline_models import is_synthetic_source

RECALL_TOOL_NAME = "recall_memory"
# 召回 HTTP 的单次预算：也是生成服务给工具轮扩超时时计入的量。
RECALL_TOOL_HTTP_TIMEOUT_SECONDS = 5.0


def resolve_group_recall_subjects(
    plugin: Any, *, group_id: str, memory_sender_id: str,
) -> tuple[list[dict[str, str]], bool]:
    """One place for the group read path's subject list.

    Shared by the per-turn fallback recall and the recall_memory tool
    handler: the two paths must authorize exactly the same scopes, or a
    provider switch would silently change what a group turn can read.
    Returns ``(subjects, used_member_subject)``.
    """
    bridge = plugin.memory_bridge
    subjects = [bridge.group_subject(group_id)]
    member_sender = str(memory_sender_id or "").strip()
    if member_sender and bool(
        (getattr(plugin, "_qq_settings", {}) or {}).get(
            "group_member_memory_enabled", False,
        )
    ):
        # 实时复检（对偶群开关的读点复检）：member 记忆关掉后不得再召回
        # participant 域。sender 规范化与写侧一致，避免读写落进不同桶。
        subjects.append(
            bridge.group_participant_subject(group_id, member_sender)
        )
    return subjects, len(subjects) > 1


class QQMemoryToolService:
    """The model-driven recall channel for QQ sessions.

    The recall_memory tool schema exposes ONLY ``query`` / ``time``.
    Subjects are resolved host-side from the turn context — the server
    treats an omitted ``subjects`` field as the legacy PRIVATE corpus, so
    letting any model-controlled input reach the subject list would leak
    the admin's private memories into group replies.
    """

    def __init__(self, plugin: Any):
        self.plugin = plugin

    @staticmethod
    def _short_lang() -> str:
        return normalize_language_code(
            get_global_language(), format="short",
        ) or "en"

    def build_recall_tool_definition(self) -> ToolDefinition:
        """The recall_memory ToolDefinition for QQ generation sessions.

        Same name / schema as the core builtin so the character card's
        "call the recall_memory tool" instruction holds verbatim. No
        ``handler``: dispatch goes through the per-turn closure installed
        via ``set_tool_call_handler`` (the subject scope changes with the
        speaker, so a registry-style static handler would be wrong).
        """
        lang = self._short_lang()
        return ToolDefinition(
            name=RECALL_TOOL_NAME,
            description=_loc(RECALL_MEMORY_TOOL_DESCRIPTION, lang),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": _loc(
                            RECALL_MEMORY_TOOL_QUERY_DESCRIPTION, lang,
                        ),
                    },
                    "time": {
                        "type": "string",
                        "description": _loc(
                            RECALL_MEMORY_TOOL_TIME_DESCRIPTION, lang,
                        ),
                    },
                },
                # query / time 至少给一个：与本体 handler 同约定，两者都空
                # 时 execute_recall 早退回"没有找到相关记忆"。
                "required": [],
            },
            metadata={"source": "qq_auto_reply"},
        )

    def _live_settings(self) -> dict:
        return (getattr(self.plugin, "_qq_settings", {}) or {})

    def _turn_memory_sender(self, context: Any) -> str:
        # 与 reply_context_node.build 的 memory_sender_id 同判据：合成轮的
        # 名义 sender 不是真实发言人；member 快照取消息接收边界，生成期间
        # 才切 ON 的轮不得回溯读成员域。
        if is_synthetic_source(getattr(context, "source_kind", "")):
            return ""
        if not getattr(context, "member_memory_enabled", False):
            return ""
        return str(getattr(context, "sender_id", "") or "").strip()

    async def execute_recall(
        self, *, context: Any, arguments: dict[str, Any],
    ) -> tuple[str, dict[str, bool]]:
        """Run one recall_memory call under this turn's authorization.

        Returns ``(model_facing_output, consumed_consent)``.
        ``consumed_consent`` names the switches this read actually relied
        on (only when scoped content was returned to the model) — the
        generation service merges it into the runtime consent record so
        the post-generation and pre-send revocation gates cover reads
        that happened MID-generation, where the old "is the section still
        in the prompt" judgement no longer exists.
        """
        lang = self._short_lang()
        no_result = _loc(RECALL_MEMORY_TOOL_NO_RESULT, lang)
        args = arguments if isinstance(arguments, dict) else {}
        raw_query = args.get("query")
        query = raw_query.strip() if isinstance(raw_query, str) else ""
        raw_time = args.get("time")
        time_spec = raw_time.strip() if isinstance(raw_time, str) else ""
        if not query and not time_spec:
            # 空入参早退（与本体对偶）：省一次 HTTP。
            return no_result, {}
        if not getattr(context, "use_memory_context", False):
            # 构建时刻的记忆政策关着（不该被挂上工具；fail-closed 兜底）。
            return no_result, {}

        is_group = bool(getattr(context, "is_group", False))
        subjects: list[dict[str, str]] | None = None
        used_member = False
        if is_group:
            if not bool(self._live_settings().get("group_memory_enabled", False)):
                # handler 入口复检：授权在模型决定调用与真正执行之间被撤销
                # 时，一行都不读。
                return no_result, {}
            group_id = str(getattr(context, "group_id", "") or "").strip()
            if not group_id:
                # 畸形群轮缺 group_id：绝不能让 subjects 退化成 None——
                # None 的语义是 legacy 私聊主人语料。
                return no_result, {}
            subjects, used_member = resolve_group_recall_subjects(
                self.plugin,
                group_id=group_id,
                memory_sender_id=self._turn_memory_sender(context),
            )
        # 私聊（use_memory_context 已按政策解析，默认 admin-only）：
        # subjects=None 走 legacy 私聊主人语料，与回落路径一致。

        try:
            result = await self.plugin.memory_bridge.query_relevant_memory(
                getattr(context, "her_name", "") or "",
                query,
                subjects=subjects,
                time_spec=time_spec,
                timeout=RECALL_TOOL_HTTP_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            # 与本体对偶：召回失败绝不向 wire 抛异常（一次失败的 tool call
            # 会把模型整轮卡死），回"没有找到相关记忆"让对话继续。
            self.plugin.logger.warning(
                f"recall_memory 工具召回失败（返回空结果）: {exc}"
            )
            return no_result, {}

        if is_group:
            live = self._live_settings()
            if used_member and not bool(
                live.get("group_member_memory_enabled", False)
            ):
                # member 侧读后复检：结果混合群域与 participant 域、事后无法
                # 拆分，opt-out 落在 HTTP 飞行期间时整体丢弃，不交给模型。
                return no_result, {}
            if not bool(live.get("group_memory_enabled", False)):
                # 群侧读后复检：同上，数据已读回也要丢弃。
                return no_result, {}

        # INFO 只落元数据（命中数/耗时/是否带 time），原始 query 与召回原文
        # 走 DEBUG——与本体 handler 的隐私分层一致。
        self.plugin.logger.info(
            "recall_memory 工具召回完成: group=%s hits=%s elapsed=%.0fms has_time=%s",
            is_group, result.hit_count, result.elapsed_ms, bool(time_spec),
        )
        self.plugin.logger.debug(
            "recall_memory args=%r query=%r time=%r", args, query, time_spec,
        )

        if not result.text:
            # 本体在 query+time 双条件 0 命中时回"放宽条件重试"的提示，但
            # 插件会话 max_tool_iterations=1：本轮的工具预算已经用完，封顶
            # 后的 forced-finalize 会摘掉 tools——提示模型去做一件做不到的
            # 事只会逼它输出"我再查查"之类的空头承诺。统一回"没有找到"。
            return no_result, {}

        consumed: dict[str, bool] = {}
        if is_group:
            # 只有真的把 scoped 内容交给模型时才记依赖：空结果不构成
            # 消费，撤销与它无关。
            consumed["group_memory_enabled"] = True
            if used_member:
                consumed["group_member_memory_enabled"] = True
        rendered_lines = result.text.count("\n") + 1
        header = _loc(RECALL_MEMORY_TOOL_FOUND_HEADER, lang).format(
            n=rendered_lines,
        )
        return f"{header}\n{result.text}", consumed
