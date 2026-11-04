"""Internal v0 recovery fence reads without production scheduling authority.

The caller supplies an authenticated transport. This module creates no HTTP
client, plaintext fallback, account binding, production scheduler or
restored-database authorization. Applying a checked page is the caller's
separately guarded transaction; downloaded originals never invoke models.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

from services.chat_sync.ledger import ChatDomain
from services.chat_sync.protocol import MAX_ORIGIN_SEQ


PrototypeRequest = Callable[[str, str, bytes | None], Awaitable[dict]]


def _cursor(value):
    """Exclude bools and values that cannot round-trip through mobile JSON."""
    return type(value) is int and 0 <= value <= MAX_ORIGIN_SEQ


def _ids(values, *, minimum=0):
    """Recovery is bounded and exact, including empty deletion terminal pages."""
    return (isinstance(values, list) and minimum <= len(values) <= 200
            and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) for value in values)
            and len(set(values)) == len(values))


class PrototypeChatSyncClient:
    """Read one role's recovery fences using durable identities."""

    def __init__(self, domain: ChatDomain, request: PrototypeRequest):
        """Bind an explicit domain to the caller's authenticated transport."""
        self.domain = domain
        self.request = request
        self.base_path = f"/prototype/chat-sync/characters/{domain.character_uid}"

    def _recovery_domain(self, value, fields):
        """A restored owner/role claim never authorizes the metadata response."""
        if (not isinstance(value, dict) or set(value) != set(fields)
                or value.get("ownerId") != self.domain.owner_id
                or value.get("characterSyncId") != self.domain.character_uid):
            raise ValueError("invalid_chat_recovery")

    async def check_recovery(self, records: list[dict]) -> dict:
        """Check exact canonical coverage without accepting a copied ACK or reading OSS."""
        if (not isinstance(records, list) or not _ids([item.get("messageId") if isinstance(item, dict) else None
                                                     for item in records], minimum=1)
                or any(set(item) != {"messageId", "sha256"} or not isinstance(item["sha256"], str)
                       or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in records)):
            raise ValueError("invalid_chat_recovery")
        # Freeze caller-owned records before awaiting transport or session checks.
        message_ids = [item["messageId"] for item in records]
        body = json.dumps({"records": records}, separators=(",", ":"), sort_keys=True).encode("utf-8")
        value = await self.request("POST", f"{self.base_path}/recovery/check", body)
        self._recovery_domain(value, {"ownerId", "characterSyncId", "cursor", "results"})
        results = value["results"]
        if (not _cursor(value["cursor"]) or not isinstance(results, list) or len(results) != len(message_ids)
                or any(not isinstance(item, dict) or set(item) != {"messageId", "state"}
                       or item["messageId"] != message_ids[index]
                       or item["state"] not in ("received", "staged", "deleted", "missing", "conflict")
                       for index, item in enumerate(results))):
            raise ValueError("invalid_chat_recovery")
        return value

    async def recovery_deletions(self, *, after: int, through: int | None = None) -> dict:
        """Read one fixed deletion page; applying it is a separate guarded transaction."""
        if not _cursor(after) or (through is not None and (not _cursor(through) or after > through)):
            raise ValueError("invalid_chat_recovery")
        path = f"{self.base_path}/recovery/deletions?after={after}&limit=200"
        if through is not None:
            path += f"&through={through}"
        value = await self.request("GET", path, None)
        self._recovery_domain(value, {"ownerId", "characterSyncId", "through", "cursor", "hasMore", "deletedMessageIds"})
        end, cursor, more = value["through"], value["cursor"], value["hasMore"]
        if (not _cursor(end) or not _cursor(cursor) or not after <= cursor <= end
                or (through is not None and end != through) or type(more) is not bool
                or (more and not after < cursor < end) or (not more and cursor != end)
                or not _ids(value["deletedMessageIds"], minimum=1 if more else 0)
                or len(value["deletedMessageIds"]) > cursor - after):
            raise ValueError("invalid_chat_recovery")
        return value
