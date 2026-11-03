"""Internal v0 transport loop without production scheduling or recovery authority.

The caller supplies an authenticated transport. This module creates no HTTP
client, plaintext fallback, account binding, production scheduler, fragmentation
or restored-database authorization. Source capture and runtime consent remain
separate; downloaded originals never invoke models.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable

from services.chat_sync.ledger import ChatDomain, ChatSyncLedger
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
    """Drive one bounded prototype pass using durable identities and receipts."""

    def __init__(
        self, ledger: ChatSyncLedger, domain: ChatDomain, request: PrototypeRequest,
        *, before_commit: Callable[[], Awaitable[None]] | None = None,
        commit: Callable[..., Awaitable[None]] | None = None,
        deletion_lock: asyncio.Lock | None = None,
    ):
        """Bind an explicit ledger/domain to the caller's authenticated transport."""
        self.ledger = ledger
        self.domain = domain
        self.request = request
        # Runtime adapters can fence account/consent changes before local commits.
        self.before_commit = before_commit
        self.commit = commit
        self.base_path = f"/prototype/chat-sync/characters/{domain.character_uid}"
        # Serialize this instance's steps; SQLite protects independent writers.
        self._lock = asyncio.Lock()
        # Runtime manual passes and detached flushes share only this marker
        # flight lock; slow original requests never delay the marker channel.
        self._deletion_lock = deletion_lock or asyncio.Lock()

    async def _commit(self, operation, *args, **kwargs) -> None:
        """Let runtime adapters atomically fence consent and local ledger commits."""
        if self.before_commit is not None:
            await self.before_commit()
        if self.commit is not None:
            await self.commit(operation, *args, **kwargs)
        else:
            await asyncio.to_thread(operation, *args, **kwargs)

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
        if self.before_commit is not None:
            await self.before_commit()
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
        if self.before_commit is not None:
            await self.before_commit()
        self._recovery_domain(value, {"ownerId", "characterSyncId", "through", "cursor", "hasMore", "deletedMessageIds"})
        end, cursor, more = value["through"], value["cursor"], value["hasMore"]
        if (not _cursor(end) or not _cursor(cursor) or not after <= cursor <= end
                or (through is not None and end != through) or type(more) is not bool
                or (more and not after < cursor < end) or (not more and cursor != end)
                or not _ids(value["deletedMessageIds"], minimum=1 if more else 0)
                or len(value["deletedMessageIds"]) > cursor - after):
            raise ValueError("invalid_chat_recovery")
        return value

    async def step(
        self, *, byte_budget: int, max_pull_pages: int,
        upload: bool = True, pull: bool = True, send_deletions: bool = True,
    ) -> dict:
        """Send deletion markers first, acknowledge one fixed batch, then pull.

        Receipt cursors never advance the download cursor. Exhausting the page
        budget returns hasMore=True, leaving the next page for a later pass.
        Failures propagate with fixed pending requests retained for retry.
        """
        if type(byte_budget) is not int or byte_budget <= 0:
            raise ValueError("invalid_batch_byte_budget")
        if type(max_pull_pages) is not int or max_pull_pages <= 0:
            raise ValueError("invalid_pull_page_budget")
        async with self._lock:
            # Runtime admission controls each channel independently. A pull-only
            # check must neither seal young originals nor consume marker retries.
            deletions = []
            if send_deletions:
                async with self._deletion_lock:
                    deletions = await asyncio.to_thread(self.ledger.pending_deletions, self.domain)
                    for deletion in deletions:
                        receipt = await self.request(
                            "POST", f"{self.base_path}/deletions/{deletion.deletion_id}", deletion.payload,
                        )
                        await self._commit(self.ledger.ack_deletion, self.domain, receipt)
            batch = await asyncio.to_thread(self.ledger.seal_batch, self.domain, byte_budget=byte_budget) if upload else None
            if batch is not None:
                # Convert the fixed local snapshot to the temporary v0 envelope;
                # retries use the same canonical original strings and batch ID.
                payload = json.dumps(
                    {"originals": [member[1].decode("utf-8") for member in batch.members]},
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")
                receipt = await self.request("PUT", f"{self.base_path}/batches/{batch.batch_id}", payload)
                await self._commit(self.ledger.ack_batch, self.domain, receipt)
            cursor = await asyncio.to_thread(self.ledger.cursor, self.domain)
            has_more = False
            pull_pages = 0
            for _ in range(max_pull_pages if pull else 0):
                page = await self.request("GET", f"{self.base_path}/changes?after={cursor}&limit=200", None)
                await self._commit(self.ledger.apply_changes, self.domain, page, after=cursor)
                pull_pages += 1
                cursor, has_more = page["cursor"], page["hasMore"]
                if not has_more:
                    break
            return {
                "deletions": len(deletions), "uploaded": len(batch.members) if batch is not None else 0,
                "pullPages": pull_pages, "cursor": cursor, "hasMore": has_more,
            }
