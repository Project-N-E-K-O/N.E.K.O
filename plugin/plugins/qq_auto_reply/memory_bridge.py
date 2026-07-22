from __future__ import annotations

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
        async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as client:
            response = await client.get(f"{self._base_url()}/new_dialog/{her_name}")
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
        async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as client:
            response = await client.post(
                f"{self._base_url()}/internal/memory/{her_name}/scoped_context",
                json={"subjects": subjects},
            )
            response.raise_for_status()
            return response.text.strip()

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
        request_payload: dict[str, Any] = {"query": normalized_query}
        if subjects:
            request_payload["subjects"] = subjects
        async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as client:
            response = await client.post(
                f"{self._base_url()}/query_memory/{her_name}",
                json=request_payload,
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
        lines: list[str] = []
        for index, item in enumerate(results, start=1):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            tier = str(item.get("tier") or "memory").strip()
            entity = str(item.get("entity") or "-").strip()
            anchor = str(
                item.get("event_end_at")
                or item.get("event_start_at")
                or item.get("created_at")
                or ""
            ).strip()
            suffix = f" ({anchor[:10]})" if anchor else ""
            lines.append(f"{index}. [{tier}/{entity}] {text}{suffix}")
        return "\n".join(lines)

    async def post_memory_history(self, endpoint: str, her_name: str, messages: list[dict[str, Any]], *, timeout: float = 5.0) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as client:
            response = await client.post(
                f"{self._base_url()}/{endpoint}/{her_name}",
                json={"input_history": json.dumps(messages, ensure_ascii=False)},
            )
            response.raise_for_status()
            return response.json()

    async def post_scoped_memory_history(
        self,
        her_name: str,
        messages: list[dict[str, Any]],
        *,
        subject: dict[str, str],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as client:
            response = await client.post(
                f"{self._base_url()}/internal/memory/{her_name}/scoped_history",
                json={
                    "input_history": json.dumps(messages, ensure_ascii=False),
                    "subject": subject,
                },
            )
            response.raise_for_status()
            return response.json()
