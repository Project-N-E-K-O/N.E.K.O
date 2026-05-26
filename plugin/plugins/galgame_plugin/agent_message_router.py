from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403


class AgentMessageRouter:
    def __init__(self, *, now_factory: Callable[[], str], limit: int = 100) -> None:
        self._now_factory = now_factory
        self._limit = max(1, int(limit))
        self.inbound_messages: list[dict[str, Any]] = []
        self.outbound_messages: list[dict[str, Any]] = []
        self.push_delivery_history: list[dict[str, Any]] = []
        self.last_interruption: dict[str, Any] = {}
        self._message_seq = 0
        self.dropped_message_count = 0

    def reset(self) -> None:
        self.inbound_messages.clear()
        self.outbound_messages.clear()
        self.push_delivery_history.clear()
        self.last_interruption = {}
        self.dropped_message_count = 0

    def new_message_id(self, *, direction: str, kind: str) -> str:
        self._message_seq += 1
        safe_direction = "".join(ch for ch in direction.lower() if ch.isalnum()) or "msg"
        safe_kind = "".join(ch for ch in kind.lower() if ch.isalnum()) or "event"
        return f"gamellm-{safe_direction}-{safe_kind}-{self._message_seq}"

    def enqueue_inbound(
        self,
        *,
        kind: str,
        content: str,
        priority: int,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        created_at = self._now_factory()
        message = {
            "message_id": self.new_message_id(direction="inbound", kind=kind),
            "direction": "inbound",
            "kind": kind,
            "content": content,
            "status": "queued",
            "priority": int(priority),
            "created_at": created_at,
            "delivered_at": "",
            "acked_at": "",
            "metadata": dict(metadata or {}),
        }
        self.inbound_messages.append(message)
        self._trim(self.inbound_messages)
        return message

    def enqueue_outbound(
        self,
        *,
        kind: str,
        content: str,
        scene_id: str,
        route_id: str,
        priority: int,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        created_at = self._now_factory()
        message_metadata = {
            "kind": kind,
            "scene_id": scene_id,
            "route_id": route_id,
            "ts": created_at,
        }
        if metadata:
            message_metadata.update(dict(metadata))
        message = {
            "message_id": self.new_message_id(direction="outbound", kind=kind),
            "direction": "outbound",
            "kind": kind,
            "content": content,
            "status": "queued",
            "priority": int(priority),
            "created_at": created_at,
            "delivered_at": "",
            "acked_at": "",
            "metadata": message_metadata,
        }
        self.outbound_messages.append(message)
        self._trim(self.outbound_messages)
        self._upsert_push_delivery_record(message, status="queued")
        return message

    def mark_message(
        self,
        message: dict[str, Any],
        *,
        status: str,
        delivered: bool = False,
        acked: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        message["status"] = status
        now = self._now_factory()
        if delivered:
            message["delivered_at"] = now
        if acked:
            message["acked_at"] = now
        if metadata:
            existing = message.get("metadata")
            if not isinstance(existing, dict):
                existing = {}
            existing.update(metadata)
            message["metadata"] = existing
        if str(message.get("direction") or "") == "outbound":
            self._upsert_push_delivery_record(message, status=status)

    def ack_message(self, message_id: str) -> dict[str, Any] | None:
        target_id = str(message_id or "").strip()
        for message in [*self.inbound_messages, *self.outbound_messages]:
            if str(message.get("message_id") or "") == target_id:
                self.mark_message(message, status="acked", acked=True)
                return message
        return None

    def recent_push_records(self) -> list[dict[str, Any]]:
        return json_copy(self.push_delivery_history[-20:])

    def _upsert_push_delivery_record(self, message: dict[str, Any], *, status: str) -> None:
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        message_id = str(message.get("message_id") or "")
        if not message_id:
            return
        existing = None
        for record in reversed(self.push_delivery_history):
            if str(record.get("message_id") or "") == message_id:
                existing = record
                break
        record = existing if isinstance(existing, dict) else {}
        retry_count = int(record.get("retry_count") or 0)
        if status == "failed":
            retry_count = max(retry_count, 1 if metadata.get("retried") else 0)
        elif bool(metadata.get("retried")):
            retry_count = max(retry_count, 1)
        record.update(
            {
                "message_id": message_id,
                "ts": str(message.get("delivered_at") or message.get("created_at") or ""),
                "kind": str(message.get("kind") or metadata.get("kind") or ""),
                "content": str(message.get("content") or ""),
                "scene_id": str(metadata.get("scene_id") or ""),
                "route_id": str(metadata.get("route_id") or ""),
                "status": str(status or message.get("status") or ""),
                "delivered": bool(message.get("delivered_at")),
                "suppressed": bool(metadata.get("suppress_delivery") or metadata.get("suppressed")),
                "retry_count": retry_count,
                "error": str(metadata.get("error") or ""),
                "created_at": str(message.get("created_at") or ""),
                "delivered_at": str(message.get("delivered_at") or ""),
                "acked_at": str(message.get("acked_at") or ""),
                "metadata": json_copy(metadata),
            }
        )
        if existing is None:
            self.push_delivery_history.append(record)
        self._trim(self.push_delivery_history)

    def snapshot(self, *, direction: str = "", limit: int = 50) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit or 50), self._limit))
        normalized_direction = str(direction or "").strip().lower()
        if normalized_direction == "inbound":
            messages = self.inbound_messages
        elif normalized_direction == "outbound":
            messages = self.outbound_messages
        else:
            messages = [*self.inbound_messages, *self.outbound_messages]
        return {
            "messages": json_copy(messages[-bounded_limit:]),
            "inbound_queue_size": len(self.inbound_messages),
            "outbound_queue_size": len(self.outbound_messages),
            "last_interruption": json_copy(self.last_interruption),
            "last_outbound_message": json_copy(self.outbound_messages[-1])
            if self.outbound_messages
            else None,
            "dropped_message_count": self.dropped_message_count,
        }

    def _trim(self, messages: list[dict[str, Any]]) -> None:
        if len(messages) > self._limit:
            self.dropped_message_count += len(messages) - self._limit
            del messages[:-self._limit]
