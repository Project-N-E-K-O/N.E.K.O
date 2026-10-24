"""Durable local outbox delivery to the authenticated community account."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time

import httpx
from portalocker.exceptions import LockException

from services.proactive_feedback import host
from services.proactive_feedback.protocol import COMMUNITY_ORIGIN, MAX_RESPONSE_BYTES, canonical_bytes, valid_receipt


class SyncFailure(Exception):
    def __init__(self, code: str, *, permanent: bool = False):
        super().__init__(code)
        self.code, self.permanent = code, permanent


class SettingsAcknowledgementPending(OSError):
    """V2 custody exists, but its independent source-journal ACK did not finish."""


class ProactiveFeedbackRuntime:
    """One worker; a frozen batch is retried until its exact receipt is durable."""

    def __init__(self, store, *, snapshot_provider=host.saved_snapshot, session_provider=host.resolve_session,
                 client: httpx.AsyncClient | None = None, settings_drain=None, clock=time.monotonic,
                 attribution_context=host.attribution_context, trusted_origin=COMMUNITY_ORIGIN,
                 settings_pending_provider=None, upload_paused=False,
                 fenced_snapshot_provider=host.credentials.saved_session_snapshot):
        self.store = store
        self.snapshot_provider, self.session_provider = snapshot_provider, session_provider
        self.http = client or httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False)
        self._owns_client = client is None
        self.settings_drain = settings_drain
        self.settings_pending_provider = settings_pending_provider
        self.upload_paused = bool(upload_paused)
        # Called only while attribution_context holds the credential writer
        # fence. Reacquiring saved_snapshot's non-reentrant file lock is unsafe.
        self.fenced_snapshot_provider = fenced_snapshot_provider
        self.clock = clock
        # Only constructor injection is permitted for isolated server fixtures;
        # neither browser input nor environment variables select token targets.
        self.origin, self.attribution_context = trusted_origin, attribution_context
        self._lock = asyncio.Lock()
        self._settings_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task = None
        self._closing = False
        self._failures = {}
        self._retry_at = {}
        self._errors = {}
        self._settings_import_error = ""
        self._verified_sessions = {}

    async def _snapshot(self):
        return await asyncio.to_thread(self.snapshot_provider)

    async def _guard(self, owner, token=None):
        if self._closing:
            raise SyncFailure("closing")
        snapshot = await self._snapshot()
        if self._closing:
            raise SyncFailure("closing")
        if host.owner_from_snapshot(snapshot) != owner or (token is not None and (snapshot or {}).get("access_token") != token):
            raise SyncFailure("session_changed")

    async def _json_request(self, method, path, token, owner, *, body=None):
        await self._guard(owner, token)
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        # Stream with a bounded accumulator: an oversized/malformed receipt never
        # counts as an acknowledgement, even when HTTP reports success.
        async with asyncio.timeout(15), self.http.stream(method, self.origin + path, headers=headers, content=body, follow_redirects=False) as response:
            if response.status_code >= 300:
                status = response.status_code
                await self._guard(owner, token)
                if status == 426:
                    raise SyncFailure("upgrade_required", permanent=True)
                if status in (409, 422):
                    raise SyncFailure("cloud_conflict" if status == 409 else "cloud_rejected", permanent=True)
                if status in (401, 403):
                    raise SyncFailure("authentication_required")
                raise SyncFailure("cloud_unavailable")
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise SyncFailure("invalid_receipt")
                raw.extend(chunk)
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                raise SyncFailure("invalid_receipt") from None
        await self._guard(owner, token)
        return payload

    def _commit_receipt(self, owner, receipt):
        # No await between the final credential identity check and SQLite ACK.
        # Credential writers use this same cross-process fence.
        with self.attribution_context() as current_owner:
            if self._closing or current_owner != owner:
                raise SyncFailure("session_changed")
            return self.store.acknowledge_batch(owner, receipt)

    async def sync_once(self) -> dict:
        async with self._lock:
            if self._closing:
                return {"state": "stopped"}
            saved = await self._snapshot()
            owner = host.owner_from_snapshot(saved)
            batch = None
            try:
                if self.settings_drain is not None:
                    async with self._settings_lock:
                        try:
                            await asyncio.to_thread(self.settings_drain, self.store)
                            self._settings_import_error = ""
                        except (OSError, sqlite3.Error, ValueError, LockException):
                            # A full outbox must still be uploaded to make room
                            # for the retained atomic settings journal entries.
                            self._settings_import_error = "settings_import_unavailable"
                    await self._guard(owner)
                if self.upload_paused:
                    return {"state": "paused", "error_code": "upload_paused"}
                if not owner["user_id"]:
                    return {"state": "signed_out"}
                if owner["community"] != self.origin:
                    raise SyncFailure("community_origin_mismatch", permanent=True)
                status = await asyncio.to_thread(self.store.sync_status, owner)
                await self._guard(owner)
                if status.get("blocked"):
                    return {"state": "blocked", "error_code": status.get("last_error") or "cloud_rejected"}
                if self._retry_at.get(owner["key"], 0) > self.clock():
                    return {"state": "retry_wait", "error_code": self._errors.get(owner["key"], "retry_wait")}
                batch = await asyncio.to_thread(self.store.prepare_batch, owner, 100)
                await self._guard(owner)
                if batch is None and self._verified_sessions.get(owner["key"]) == (saved or {}).get("access_token"):
                    return {"state": "idle"}
                async with asyncio.timeout(30):
                    resolved = await self.session_provider()
                await self._guard(owner)
                snapshot = resolved.get("snapshot") if isinstance(resolved, dict) else None
                if not isinstance(resolved, dict) or not resolved.get("logged_in") or not snapshot:
                    raise SyncFailure("authentication_required")
                if host.owner_from_snapshot(snapshot) != owner:
                    raise SyncFailure("session_changed")
                token = snapshot.get("access_token")
                if not isinstance(token, str) or not token:
                    raise SyncFailure("authentication_required")
                identity = await self._json_request("GET", "/api/users/me", token, owner)
                user = identity.get("user") if isinstance(identity, dict) else None
                if not isinstance(user, dict) or str(user.get("id")) != str(owner["user_id"]):
                    raise SyncFailure("account_mismatch", permanent=True)
                await self._guard(owner, token)
                self._verified_sessions[owner["key"]] = token
                if batch is None:
                    self._failures.pop(owner["key"], None)
                    self._retry_at.pop(owner["key"], None)
                    self._errors.pop(owner["key"], None)
                    return {"state": "idle"}
                body = await asyncio.to_thread(canonical_bytes, batch)
                await self._guard(owner, token)
                receipt = await self._json_request("POST", "/api/proactive/feedback/batches", token, owner, body=body)
                if not await asyncio.to_thread(valid_receipt, batch, receipt):
                    raise SyncFailure("invalid_receipt")
                await self._guard(owner, token)
                if not await asyncio.to_thread(self._commit_receipt, owner, receipt):
                    raise SyncFailure("invalid_receipt")
                await self._guard(owner)
                self._failures.pop(owner["key"], None)
                self._retry_at.pop(owner["key"], None)
                self._errors.pop(owner["key"], None)
                return {"state": "synced", "batch_id": batch["batch_id"], "items": len(batch["items"])}
            except (httpx.HTTPError, OSError, sqlite3.Error, TimeoutError):
                failure = SyncFailure("cloud_unavailable")
            except SyncFailure as exc:
                failure = exc
            if failure.code in ("closing", "session_changed"):
                return {"state": "stopped" if self._closing else "session_changed"}
            if failure.code in ("authentication_required", "account_mismatch"):
                self._verified_sessions.pop(owner["key"], None)
            if batch is not None:
                await asyncio.to_thread(self.store.mark_batch_error, owner, batch["batch_id"], failure.code, permanent=failure.permanent)
            self._errors[owner["key"]] = failure.code
            count = self._failures.get(owner["key"], 0) + 1
            self._failures[owner["key"]] = count
            self._retry_at[owner["key"]] = self.clock() + min(900, 30 * 2 ** min(count - 1, 5))
            return {"state": "blocked" if failure.permanent else "retry_wait", "error_code": failure.code}

    def _resume_commit(self, owner, token, batch_id, batch_hash, cancelled, *, expected_error=None):
        with self.attribution_context() as current_owner:
            snapshot = self.fenced_snapshot_provider()
            if self._closing or cancelled.is_set():
                raise SyncFailure("closing" if self._closing else "operation_cancelled")
            if (current_owner != owner or host.owner_from_snapshot(snapshot) != owner
                    or (snapshot or {}).get("access_token") != token):
                raise SyncFailure("session_changed")
            if expected_error is not None:
                return self.store.resume_blocked_batch(owner, batch_id, batch_hash, expected_error=expected_error)
            return self.store.resume_blocked_batch(owner, batch_id, batch_hash)

    async def resume_blocked_batch(self, *, owner_key, batch_id, batch_hash):
        """Explicitly release exactly one diagnosed batch; never rewrite its facts."""
        async with self._lock:
            if self._closing:
                raise SyncFailure("closing")
            snapshot = await self._snapshot()
            owner = host.owner_from_snapshot(snapshot)
            if owner["key"] != owner_key:
                raise SyncFailure("session_changed")
            if not owner["user_id"]:
                raise SyncFailure("authentication_required")
            if self.upload_paused:
                raise SyncFailure("upload_paused")
            if owner["community"] != self.origin:
                raise SyncFailure("community_origin_mismatch")
            upgrade_status = await asyncio.to_thread(self.store.sync_status, owner)
            if upgrade_status.get("last_error") == "upgrade_required":
                raise SyncFailure("upgrade_required", permanent=True)
            async with asyncio.timeout(30):
                resolved = await self.session_provider()
            await self._guard(owner)
            current = resolved.get("snapshot") if isinstance(resolved, dict) else None
            if not isinstance(resolved, dict) or not resolved.get("logged_in") or not current:
                raise SyncFailure("authentication_required")
            if host.owner_from_snapshot(current) != owner:
                raise SyncFailure("session_changed")
            token = current.get("access_token")
            if not isinstance(token, str) or not token:
                raise SyncFailure("authentication_required")
            identity = await self._json_request("GET", "/api/users/me", token, owner)
            user = identity.get("user") if isinstance(identity, dict) else None
            if not isinstance(user, dict) or str(user.get("id")) != str(owner["user_id"]):
                raise SyncFailure("account_mismatch")
            await self._guard(owner, token)
            cancelled = threading.Event()
            try:
                resumed = await asyncio.to_thread(self._resume_commit, owner, token, batch_id, batch_hash, cancelled)
            except asyncio.CancelledError:
                # A thread waiting for the credential fence must not apply an
                # already cancelled request later. A committed CAS is not undone.
                cancelled.set()
                raise
            if not resumed:
                raise SyncFailure("feedback_resume_conflict")
            await self._guard(owner, token)
            self._verified_sessions[owner["key"]] = token
            self._failures.pop(owner["key"], None)
            self._retry_at.pop(owner["key"], None)
            self._errors.pop(owner["key"], None)
            self._wake.set()
            return {"state": "resumed", "batch_id": batch_id, "batch_hash": batch_hash}

    async def status(self) -> dict:
        async with self._settings_lock:
            snapshot = await self._snapshot()
            owner = host.owner_from_snapshot(snapshot)
            journal_pending, journal_error = 0, ""
            if self.settings_pending_provider is not None:
                try:
                    entries = await asyncio.to_thread(self.settings_pending_provider)
                    journal_pending = sum(entry["owner"] == owner for entry in entries)
                except (OSError, sqlite3.Error, ValueError, LockException):
                    journal_pending, journal_error = None, "settings_journal_unavailable"
            # Read the source journal before its destination. This short handoff
            # lock never waits for OAuth or HTTP, keeping diagnostics available.
            result = await asyncio.to_thread(self.store.sync_status, owner)
            if (self.settings_pending_provider is not None and journal_pending == 0
                    and not journal_error and not self._settings_import_error
                    and result["pending"] == 0 and not result.get("blocked")):
                # An older HTTP receipt can empty the outbox after a new settings
                # write. Confirm the source is still empty rather than combining
                # its earlier snapshot with the later destination snapshot. The
                # handoff lock prevents our worker from moving new entries away.
                try:
                    entries = await asyncio.to_thread(self.settings_pending_provider)
                    journal_pending = sum(entry["owner"] == owner for entry in entries)
                except (OSError, sqlite3.Error, ValueError, LockException):
                    journal_pending, journal_error = None, "settings_journal_unavailable"
            await self._guard(owner, (snapshot or {}).get("access_token"))
            error = journal_error or self._settings_import_error
            queue_empty = result["pending"] == 0 and journal_pending == 0 and not error and not result.get("blocked")
            signed_in = bool(owner["user_id"])
            verified = (signed_in and owner["community"] == self.origin
                        and self._verified_sessions.get(owner["key"]) == (snapshot or {}).get("access_token"))
            has_receipt = result.get("acked", 0) > 0
            upgrade_required = (self._errors.get(owner["key"]) == "upgrade_required"
                                or result.get("last_error") == "upgrade_required")
            return {**result, "protocol_version": self.store.protocol_version,
                    "signed_in": signed_in, "owner_key": owner["key"],
                    "running": self._task is not None and not self._task.done() and not self._closing,
                    "settings_import_error": error, "settings_journal_pending": journal_pending,
                    "local_queue_empty": queue_empty, "identity_verified": verified,
                    "has_cloud_receipt": has_receipt, "upload_paused": self.upload_paused,
                    "last_sync_error": error or result.get("last_error") or self._errors.get(owner["key"], ""),
                    "fully_synced": queue_empty and verified and has_receipt and not self.upload_paused and not upgrade_required,
                    "upgrade_required": upgrade_required,
                    "minimum_protocol": 2 if upgrade_required else None,
                    "upgrade_url": "https://github.com/Project-N-E-K-O/N.E.K.O/releases",
                    "retry_after_seconds": max(0, self._retry_at.get(owner["key"], 0) - self.clock())}

    async def _run(self):
        while not self._closing:
            self._wake.clear()
            try:
                await self.sync_once()
            except (OSError, sqlite3.Error, RuntimeError, ValueError, LockException):
                # Never emit token-bearing exception text; retain durable data.
                pass
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=60)
            except TimeoutError:
                pass

    def start(self):
        if self._closing:
            raise RuntimeError("feedback_runtime_closed")
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="proactive-feedback-sync")

    async def close(self):
        self._closing = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._owns_client:
            await self.http.aclose()


def drain_settings(store):
    """Transfer exact journal members before acknowledging the atomic settings file."""
    from utils.preferences import acknowledge_settings_changes, pending_settings_changes
    for entry in pending_settings_changes(limit=100):
        if store.protocol_version == 2:
            custody = store.enqueue_settings(entry["owner"], entry["id"], entry["payload"],
                                             context=entry.get("context"))
            if custody.get("status") not in {"durable", "discarded"}:
                continue
        else:
            store.enqueue_settings(entry["owner"], entry["id"], entry["payload"])
        try:
            acknowledge_settings_changes([entry["id"]])
        except Exception as exc:
            if store.protocol_version == 2 and custody.get("status") == "durable":
                # Uploading and retiring this fact before its source ACK can
                # recreate it with a new sequence on the next journal import.
                raise SettingsAcknowledgementPending("settings_source_acknowledgement_pending") from exc
            raise


def pending_settings():
    from utils.preferences import pending_settings_changes
    return pending_settings_changes(limit=4096)
