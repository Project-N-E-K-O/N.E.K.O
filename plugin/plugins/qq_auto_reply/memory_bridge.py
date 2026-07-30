from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class QQMemoryQueryResult:
    text: str = ""
    hit_count: int = 0
    elapsed_ms: float = 0.0
    raw_results: list[dict[str, Any]] = field(default_factory=list)


class QQMemoryBridge:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    @staticmethod
    def _client():
        """The process-wide client for internal 127.0.0.1 services.

        Every endpoint used to build and tear down its own AsyncClient, and
        each construction eagerly initializes an SSLContext even for plain
        http to localhost — the reason utils/http/internal_client.py exists.
        A busy group does at least two of these per turn and a member drain
        fires eight at once. Its lifetime is the process's (main_server's
        shutdown hook closes it), so nothing here may close it.

        The timeout has to be passed **per request**: it differs by endpoint
        (scoped history waits on an LLM extraction, the rest are local
        reads), and the shared client carries an unrelated default."""
        from utils.internal_http_client import get_internal_http_client

        return get_internal_http_client()

    def _base_url(self) -> str:
        from config import MEMORY_SERVER_PORT

        return f"http://127.0.0.1:{MEMORY_SERVER_PORT}"

    @staticmethod
    def group_subject(group_id: object) -> dict[str, str]:
        return {
            "subject_kind": "group_chat",
            "subject_id": f"qq:{str(group_id or '').strip()}",
        }

    @staticmethod
    def group_participant_subject(group_id: object, sender_id: object) -> dict[str, str]:
        return {
            "subject_kind": "group_participant",
            "subject_id": (
                f"qq:{str(group_id or '').strip()}:{str(sender_id or '').strip()}"
            ),
        }

    async def fetch_bootstrap_memory(self, her_name: str, *, timeout: float = 5.0) -> str:
        client = self._client()
        response = await client.get(
            f"{self._base_url()}/new_dialog/{her_name}", timeout=timeout,
        )
        response.raise_for_status()
        return response.text.strip()

    async def fetch_scoped_bootstrap_memory(
        self,
        her_name: str,
        *,
        subjects: list[dict[str, str]],
        timeout: float = 5.0,
    ) -> str:
        if not subjects:
            return ""
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/memory/{her_name}/scoped_context",
            json={"subjects": subjects},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.text.strip()

    async def post_scoped_mentions(
        self,
        her_name: str,
        response_text: str,
        *,
        subjects: list[dict[str, str]],
        timeout: float = 5.0,
    ) -> None:
        if not subjects or not response_text:
            return
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/memory/{her_name}/scoped_mentions",
            json={"response_text": response_text, "subjects": subjects},
            timeout=timeout,
        )
        response.raise_for_status()

    async def query_relevant_memory(
        self,
        her_name: str,
        query: str,
        *,
        timeout: float = 5.0,
        limit: int = 5,
        subjects: list[dict[str, str]] | None = None,
    ) -> QQMemoryQueryResult:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return QQMemoryQueryResult()
        # ``None`` means the legacy private caller omitted an authorization
        # boundary. An explicit empty list means the caller has no authorized
        # subject and must never fall back to that legacy corpus.
        if subjects == []:
            return QQMemoryQueryResult()
        request_payload: dict[str, Any] = {"query": normalized_query}
        if subjects is not None:
            request_payload["subjects"] = subjects
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/query_memory/{her_name}",
            json=request_payload,
            timeout=timeout,
        )
        response.raise_for_status()
        response_payload = response.json()
        results = response_payload.get("results") if isinstance(response_payload, dict) else None
        memory_items = [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []
        # 整段渲染扔进 worker 线程：render_relevant_memory 里的
        # truncate_to_tokens 编码的是**截断前**的原文，而这条链路存在的
        # 理由正是"上游可能返回一条超长的合并 reflection"。tiktoken 对
        # 切不开的超长 chunk 是二次退化，同步跑在事件循环上会连带卡住这
        # 个进程里其它群的回复。渲染函数本身保持同步（本体侧同构、测试
        # 直调），offload 放在唯一的 async 调用点。
        rendered = await asyncio.to_thread(
            self.render_relevant_memory, memory_items[:limit],
        )
        elapsed_ms = response_payload.get("elapsed_ms", 0.0) if isinstance(response_payload, dict) else 0.0
        try:
            normalized_elapsed = float(elapsed_ms or 0.0)
        except (TypeError, ValueError):
            normalized_elapsed = 0.0
        return QQMemoryQueryResult(
            text=rendered,
            hit_count=len(memory_items),
            elapsed_ms=normalized_elapsed,
            raw_results=memory_items,
        )

    def render_relevant_memory(self, results: list[dict[str, Any]]) -> str:
        # tier / entity 是内部枚举（scoped 条目的 entity 恒等于 subject.kind），
        # 裸拼会让 `[fact/group_chat]` 出现在中文 prompt 里。与本体侧
        # main_logic/core/tool_calling.py 的召回渲染同一张标签表。
        #
        # 预算：这段此前只有"取前 5 条"，单条零上限——一条被合并出来的超长
        # reflection 就能把召回段撑到几千 token。单条按 token 截断（不丢弃：
        # 召回按相关度排，命中的那条留半段也比整条消失有用），整段按
        # take_lines_within_token_budget 收口，与本体侧同一个 helper。
        from config import (
            RECALL_RENDER_ENTRY_MAX_TOKENS,
            RECALL_RENDER_LINE_OVERHEAD_TOKENS,
            RECALL_RENDER_TOTAL_MAX_TOKENS,
        )
        from config.prompts.prompts_memory import render_recall_entry_tag
        from utils.language_utils import get_global_language
        from utils.tokenize import take_lines_within_token_budget, truncate_to_tokens

        lang = get_global_language()
        lines: list[str] = []
        for index, item in enumerate(results, start=1):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            text = truncate_to_tokens(text, RECALL_RENDER_ENTRY_MAX_TOKENS)
            tag = render_recall_entry_tag(
                item.get("tier"), item.get("entity"), lang,
            )
            anchor = str(
                item.get("event_end_at")
                or item.get("event_start_at")
                or item.get("created_at")
                or ""
            ).strip()
            suffix = f" ({anchor[:10]})" if anchor else ""
            # 整行再兜一次底。截断只管 text，而 tag 里的 tier / entity 是
            # 未知枚举原样透出的（见 render_recall_entry_tag），手改过的
            # facts.json 能塞进任意长的 entity——而整段预算的"至少留一条"
            # 规则会无条件留下第一行。行上限用「单条 + 行装饰」的口径，
            # 正常条目够不着，只有畸形数据会被它切。
            lines.append(truncate_to_tokens(
                f"{index}. {tag} {text}{suffix}",
                RECALL_RENDER_ENTRY_MAX_TOKENS + RECALL_RENDER_LINE_OVERHEAD_TOKENS,
            ))
        kept, dropped = take_lines_within_token_budget(
            lines, RECALL_RENDER_TOTAL_MAX_TOKENS,
        )
        logger = getattr(self.plugin, "logger", None)
        if dropped and logger is not None:
            # 诊断行不该成为渲染的硬依赖：这个函数此前对 plugin 对象零依赖，
            # 抛 AttributeError 会被上游 _build_recalled_memory_text 的
            # except 吞掉，整段召回为了一条日志凭空消失。
            logger.info(
                f"QQ 长期记忆召回段超出 {RECALL_RENDER_TOTAL_MAX_TOKENS} tok 预算，"
                f"丢弃末尾 {dropped} 条"
            )
        return "\n".join(kept)

    async def post_memory_history(self, endpoint: str, her_name: str, messages: list[dict[str, Any]], *, timeout: float = 5.0) -> dict[str, Any]:
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/{endpoint}/{her_name}",
            json={"input_history": json.dumps(messages, ensure_ascii=False)},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    async def post_scoped_memory_history(
        self,
        her_name: str,
        messages: list[dict[str, Any]],
        *,
        subject: dict[str, str],
        speaker_label: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        # speaker_label 只在单发言人批次（成员 bucket）传：提取 prompt 用它
        # 替代私聊主人名渲染 user 轮，避免成员发言被抽成"关于主人"的事实。
        # 群 digest 不传——内容里每条消息已带发言人头。
        payload: dict[str, Any] = {
            "input_history": json.dumps(messages, ensure_ascii=False),
            "subject": subject,
        }
        if speaker_label:
            payload["speaker_label"] = speaker_label
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/memory/{her_name}/scoped_history",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
