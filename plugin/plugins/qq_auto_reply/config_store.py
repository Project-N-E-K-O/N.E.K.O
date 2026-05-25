from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json_async, read_json_async


class QQAutoReplyConfigStore:
    FILE_NAME = "business_config.json"

    def __init__(self, base_dir: Path):
        self._path = Path(base_dir) / self.FILE_NAME
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @staticmethod
    def default_backlog_labels() -> list[dict[str, Any]]:
        return [
            {
                "id": "mention",
                "label": "点名",
                "keywords": [r"@用户\d+", r"@全体成员"],
                "priority": 60,
            },
        ]

    @staticmethod
    def normalize_backlog_labels(labels: Any) -> list[dict[str, Any]]:
        if labels is None:
            return QQAutoReplyConfigStore.default_backlog_labels()
        if not isinstance(labels, list):
            return []
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in labels:
            if not isinstance(item, dict):
                continue
            label_id = str(item.get("id") or "").strip()
            label_text = str(item.get("label") or "").strip()
            if not label_id or not label_text or label_id in seen_ids:
                continue
            keywords = item.get("keywords")
            if not isinstance(keywords, list):
                keywords = []
            normalized_keywords = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
            priority = item.get("priority", 0)
            try:
                normalized_priority = int(priority)
            except Exception:
                normalized_priority = 0
            normalized.append({
                "id": label_id,
                "label": label_text,
                "keywords": normalized_keywords,
                "priority": normalized_priority,
            })
            seen_ids.add(label_id)
        return normalized

    def default_config(self) -> dict[str, Any]:
        return {
            "onebot_url": "ws://127.0.0.1:3001",
            "token": "",
            "trusted_users": [],
            "trusted_groups": [],
            "normal_relay_probability": 0.1,
            "open_reply_probability": 0.1,
            "show_onboarding": True,
            "guide_step_config_done": False,
            "guide_step_runtime_done": False,
            "max_concurrent_messages": 3,
            "ai_connect_timeout_seconds": 10.0,
            "ai_turn_timeout_seconds": 60.0,
            "handler_shutdown_timeout_seconds": 10.0,
            "napcat_directory": "",
            "show_napcat_window": True,
            "backlog_retention_limit": 200,
            "backlog_summary_threshold": 10,
            "backlog_notify_cooldown_seconds": 900,
            "backlog_issue_notify_threshold": 1,
            "backlog_labels": self.default_backlog_labels(),
        }

    async def exists(self) -> bool:
        return self._path.is_file()

    async def load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return self.default_config()
        payload = await read_json_async(self._path)
        if not isinstance(payload, dict):
            return self.default_config()
        merged = self.default_config()
        merged.update(payload)
        merged["trusted_users"] = payload.get("trusted_users") if isinstance(payload.get("trusted_users"), list) else []
        merged["trusted_groups"] = payload.get("trusted_groups") if isinstance(payload.get("trusted_groups"), list) else []
        merged["backlog_labels"] = self.normalize_backlog_labels(payload.get("backlog_labels"))
        return merged

    async def create_empty(self) -> dict[str, Any]:
        config = self.default_config()
        await self.save(config)
        return config

    async def save(self, config: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            normalized = self.default_config()
            normalized.update(dict(config or {}))
            normalized["trusted_users"] = list(normalized.get("trusted_users") or [])
            normalized["trusted_groups"] = list(normalized.get("trusted_groups") or [])
            normalized["backlog_labels"] = self.normalize_backlog_labels(normalized.get("backlog_labels"))
            await atomic_write_json_async(self._path, normalized)
            return normalized
