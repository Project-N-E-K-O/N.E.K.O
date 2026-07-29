from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass(slots=True)
class QQMemoryQueryResult:
    text: str = ""
    hit_count: int = 0
    elapsed_ms: float = 0.0
    raw_results: list[dict[str, Any]] = field(default_factory=list)


class QQMemoryBridge:
    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        """One client for the whole plugin lifetime.

        Every endpoint used to build and tear down its own client, so each
        call opened a fresh connection — a busy group does at least two per
        turn, and a member drain fires eight at once. The timeout therefore
        has to be passed **per request**: it differs by endpoint (scoped
        history waits on an LLM extraction, the rest are local reads), so
        baking one into the shared client would level them all."""
        client = self._client
        if client is not None and not getattr(client, "is_closed", False):
            return client
        async with self._client_lock:
            client = self._client
            if client is None or getattr(client, "is_closed", False):
                client = httpx.AsyncClient(proxy=None, trust_env=False)
                self._client = client
            return client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None and not getattr(client, "is_closed", False):
            await client.aclose()

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
        client = await self._get_client()
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
        client = await self._get_client()
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
        client = await self._get_client()
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
        client = await self._get_client()
        response = await client.post(
            f"{self._base_url()}/query_memory/{her_name}",
            json=request_payload,
            timeout=timeout,
        )
        response.raise_for_status()
        response_payload = response.json()
        results = response_payload.get("results") if isinstance(response_payload, dict) else None
        memory_items = [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []
        rendered = self.render_relevant_memory(memory_items[:limit])
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
        from config.prompts.prompts_memory import render_recall_entry_tag
        from utils.language_utils import get_global_language

        lang = get_global_language()
        lines: list[str] = []
        for index, item in enumerate(results, start=1):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
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
            lines.append(f"{index}. {tag} {text}{suffix}")
        return "\n".join(lines)

    async def post_memory_history(self, endpoint: str, her_name: str, messages: list[dict[str, Any]], *, timeout: float = 5.0) -> dict[str, Any]:
        client = await self._get_client()
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
        client = await self._get_client()
        response = await client.post(
            f"{self._base_url()}/internal/memory/{her_name}/scoped_history",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
