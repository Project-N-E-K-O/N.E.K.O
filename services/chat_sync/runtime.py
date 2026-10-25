"""Opt-in desktop binding and durable automatic sync, separate from Steam memory.

Only the authenticated service can establish an owner. Local account hints,
character names and copied histories never authorize a request. This adapter
starts its scheduler only after the application's storage barrier is released.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, closing, contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import sqlite3

from services.chat_sync.writer import store_connection
import ssl
import threading
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from services.chat_sync import host as _host
from services.chat_sync.ledger import MAX_SYNC_ORIGINAL_BYTES, ChatDomain, ChatOriginal, ChatSyncLedger, SourceLocator
from services.chat_sync.protocol import decode_chat_original, validate_sync_domain
from services.chat_sync.prototype import PrototypeChatSyncClient
from services.chat_sync.scheduler import ChatSyncScheduler, body_eligible_at, now_ms
from services.chat_sync.trace import get_trace, route_class
from services.chat_sync.profile_sync import DesktopProfileAdapter, DesktopProfileSync
from utils import community_session as _community_session


SessionProvider = Callable[[], Awaitable[dict]]
_HEX_ID = re.compile(r"[0-9a-f]{32}\Z")
_COMMUNITY_ORIGIN = "https://community.project-neko.cn"
_BUILTIN_SERVICE_URL = "https://sync.nekotech.cn"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_LOCAL_CA_ENV = "NEKO_CHAT_SYNC_LOCAL_CA_FILE"
_LOCAL_WAKE_SECONDS = 1
_RECENT_FALLBACK_SECONDS = 30
_AUTO_BYTE_BUDGET = MAX_SYNC_ORIGINAL_BYTES
_AUTO_PULL_PAGES = 10
_PULL_NETWORK_SECONDS = 3


class AutomaticRoleInactive(Exception):
    """End an automatic pass without revoking consent or recording a failure."""


class ChatSyncServiceError(ValueError):
    """Keep safe HTTP retry metadata without retaining an untrusted error body."""

    def __init__(self, status_code: int, retry_after_ms: int | None = None):
        """Preserve the existing fixed public error code for manual callers."""
        super().__init__("chat_sync_service_rejected")
        self.status_code, self.retry_after_ms = status_code, retry_after_ms
        # Only a whitelisted profile CAS conflict is distinguishable to callers;
        # arbitrary response details remain inaccessible to UI/logging.
        self.profile_conflict = False
        self.character_deleted = False


def _retry_after_ms(value: str | None) -> int | None:
    """Accept delta seconds or an HTTP date without logging the header's text."""
    if value is None:
        return None
    try:
        if value.isascii() and value.isdigit():
            return min(int(value), 2**31 - 1) * 1000
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        return max(0, int((date - datetime.now(timezone.utc)).total_seconds() * 1000))
    except (ValueError, TypeError, OverflowError):
        return None


async def _saved_session_presence() -> str | None:
    """Observe local login only; short scheduler ticks never refresh or do HTTP."""
    def read():
        """Read the existing authoritative snapshot without persisting its bearer."""
        try:
            snapshot = _host.saved_session_snapshot()
            if not snapshot or not snapshot.get("access_token") or snapshot.get("base_url") != _COMMUNITY_ORIGIN:
                return None
            return hashlib.sha256(_canonical({
                "token": snapshot["access_token"], "origin": snapshot["base_url"],
            })).hexdigest()
        except (OSError, TimeoutError, _host.CapabilityNotInstalled):
            # Without the credential store owner no login is observable.
            return None
    return await asyncio.to_thread(read)


def _canonical(value: dict) -> bytes:
    """Keep retry envelopes fixed without using a model or projection codec."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _service_url(value: object) -> str:
    """Accept an explicit HTTPS origin, never an HTTP or credential fallback."""
    if not isinstance(value, str) or value != value.strip() or any(ord(c) < 33 for c in value):
        raise ValueError("invalid_chat_sync_service_url")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
        or parsed.password is not None or parsed.path not in ("", "/")
        or parsed.query or parsed.fragment
    ):
        raise ValueError("invalid_chat_sync_service_url")
    try:
        parsed.port
    except ValueError:
        raise ValueError("invalid_chat_sync_service_url") from None
    return value.rstrip("/")


def _is_loopback_service(value: str) -> bool:
    """Limit explicit local-CA testing to localhost or literal loopback IPs."""
    host = urlsplit(_service_url(value)).hostname
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (getattr(address, "ipv4_mapped", None) or address).is_loopback


def _load_local_ca_context(path: str) -> ssl.SSLContext:
    """Append one explicitly selected test CA without weakening normal TLS checks."""
    try:
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=path)
    except (OSError, ValueError):
        # Paths and OpenSSL diagnostics can contain local configuration details.
        raise ValueError("chat_sync_local_ca_invalid") from None
    return context


@contextmanager
def _saved_session_commit_guard(expected: str):
    """Fence short local commits with the credential writers' existing file locks."""
    try:
        with _host.saved_session_writer_fence():
            snapshot = _host.saved_session_snapshot()
            if not snapshot or not snapshot.get("access_token"):
                raise ValueError("chat_sync_login_required")
            fingerprint = hashlib.sha256(_canonical({
                "token": snapshot["access_token"], "origin": snapshot["base_url"],
            })).hexdigest()
            if fingerprint != expected:
                raise ValueError("chat_sync_session_changed")
            # The existing logout/refresh/login writers hold these same locks.
            # No HTTP, refresh or model work occurs while they are held here.
            yield
    except (OSError, TimeoutError, _host.CapabilityNotInstalled):
        # A process without the credential store owner never commits blind.
        raise ValueError("chat_sync_session_unavailable") from None


class DesktopSyncState:
    """Persist local consent and fixed binding requests outside Steam trees."""

    def __init__(self, path: Path):
        """Create only dedicated sync state; no user memory migration occurs."""
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sync_settings (
                    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                    device_id TEXT NOT NULL,
                    service_url TEXT
                );
                CREATE TABLE IF NOT EXISTS sync_new_roles (
                    character_uid TEXT PRIMARY KEY, request TEXT NOT NULL, next_at INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS sync_choices (
                    character_uid TEXT PRIMARY KEY, enabled INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sync_bindings (
                    character_uid TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    binding_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    generation INTEGER NOT NULL DEFAULT 1
                );
            """)
            conn.execute("INSERT OR IGNORE INTO sync_settings VALUES (1, ?, NULL)", (uuid4().hex,))
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        """Open one durable connection per operation for thread-safe callers."""
        conn = store_connection(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def settings(self) -> dict:
        """Return public local settings; no bearer or credentials are stored."""
        with closing(self._connect()) as conn:
            settings = dict(conn.execute("SELECT device_id, service_url FROM sync_settings").fetchone())
            settings["service_url"] = settings["service_url"] or _BUILTIN_SERVICE_URL
            return settings

    def configure(self, service_url: str) -> None:
        """Pin the service origin once binding starts, including failed retries."""
        service_url = _service_url(service_url)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT service_url FROM sync_settings").fetchone()[0] or _BUILTIN_SERVICE_URL
            if previous and previous != service_url and conn.execute("SELECT 1 FROM sync_bindings LIMIT 1").fetchone():
                raise ValueError("chat_sync_service_already_bound")
            conn.execute("UPDATE sync_settings SET service_url=?", (service_url,))
            conn.commit()

    def queue_new_role(self, uid, request, *, replace=False):
        """Retries retain a fixed snapshot; a later explicit card save can replace its own intent."""
        with closing(self._connect()) as conn, conn:
            previous = conn.execute("SELECT request FROM sync_new_roles WHERE character_uid=?", (uid,)).fetchone()
            if not replace and previous and json.loads(previous[0])["canonical"] != request["canonical"]:
                raise ValueError("character_profile_changed")
            if replace:
                conn.execute("DELETE FROM sync_new_roles WHERE character_uid=?", (uid,))
            conn.execute("INSERT OR IGNORE INTO sync_new_roles(character_uid,request) VALUES (?,?)", (uid, json.dumps(request)))

    def choices(self):
        with closing(self._connect()) as conn:
            return {row[0]: bool(row[1]) for row in conn.execute("SELECT character_uid,enabled FROM sync_choices")}

    def save_choice(self, uid, enabled):
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO sync_choices VALUES (?,?)", (uid, int(enabled)))

    def new_role(self, uid):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT request,next_at FROM sync_new_roles WHERE character_uid=?", (uid,)).fetchone()
            return {**json.loads(row[0]), "next_at": row[1]} if row else None

    def has_new_roles(self):
        with closing(self._connect()) as conn:
            return conn.execute("SELECT 1 FROM sync_new_roles LIMIT 1").fetchone() is not None

    def next_wake_at(self, now):
        with closing(self._connect()) as conn:
            return conn.execute("SELECT MIN(next_at) FROM sync_new_roles WHERE next_at>?", (now,)).fetchone()[0]

    def new_role_errors(self):
        with closing(self._connect()) as conn:
            return {row[0]: code for row in conn.execute("SELECT character_uid,request FROM sync_new_roles")
                    if (code := (request := json.loads(row[1])).get("error") or request.get("lastError"))}

    def record_new_role_error(self, uid, request_id, code):
        """Keep a retryable fixed diagnostic without changing its admission deadline."""
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT request FROM sync_new_roles WHERE character_uid=?", (uid,)).fetchone()
            if row and (request := json.loads(row[0]))["requestId"] == request_id:
                if code is None:
                    request.pop("lastError", None)
                else:
                    request["lastError"] = code
                conn.execute("UPDATE sync_new_roles SET request=? WHERE character_uid=?", (json.dumps(request), uid))

    @staticmethod
    def choice_matches_domain(request, binding):
        """Separate changed cloud authority from a retryable local generation fence."""
        pinned = request.get("historyBinding")
        return not pinned or (binding is not None and all(pinned[key] == binding[key]
                                for key in ("owner_id", "character_uid", "binding_id", "request_id")))

    @staticmethod
    def choice_matches_binding(request, binding):
        """A retained On grants only its pinned domain and explicit paused generation."""
        if not DesktopSyncState.choice_matches_domain(request, binding):
            return False
        return not (binding and not binding["enabled"] and (binding["binding_id"] or binding["generation"] > 1)
                    and request.get("resumeGeneration") != binding["generation"])

    def pending_choices(self, epoch):
        """Report durable affirmative choices without presenting copied or revoked grants as On."""
        with closing(self._connect()) as conn:
            bindings = {row["character_uid"]: dict(row) for row in conn.execute("SELECT * FROM sync_bindings")}
            return {uid: True for uid, raw in conn.execute(
                "SELECT r.character_uid,r.request FROM sync_new_roles r JOIN sync_choices c "
                "ON c.character_uid=r.character_uid WHERE c.enabled=1")
                if tuple((request := json.loads(raw))["epoch"]) == epoch
                and self.choice_matches_binding(request, binding := bindings.get(uid))
                and not (binding and binding["enabled"] and request.get("resumeGeneration") == binding["generation"])}

    def defer_new_role(self, uid, deadline):
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE sync_new_roles SET next_at=? WHERE character_uid=?", (deadline, uid))

    def finish_new_role(self, uid):
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM sync_new_roles WHERE character_uid=?", (uid,))

    def binding(self, character_uid: str) -> dict | None:
        """Read the immutable account association independently of login state."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM sync_bindings WHERE character_uid=?", (character_uid,)).fetchone()
            return dict(row) if row else None

    def bindings(self) -> list[dict]:
        """Expose opt-in and pause state without claiming remote recoverability."""
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM sync_bindings ORDER BY character_uid")]

    def prepare_binding(self, domain: ChatDomain) -> dict:
        """Persist the request ID before network I/O so response loss is safe."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT * FROM sync_bindings WHERE character_uid=?", (domain.character_uid,)).fetchone()
            if prior and prior["owner_id"] != domain.owner_id:
                raise ValueError("chat_sync_account_mismatch")
            if not prior:
                conn.execute(
                    "INSERT INTO sync_bindings(character_uid, owner_id, request_id) VALUES (?, ?, ?)",
                    (domain.character_uid, domain.owner_id, uuid4().hex),
                )
            conn.commit()
        return self.binding(domain.character_uid)

    def activate(self, prepared: dict, binding_id: str) -> dict:
        """Publish consent only if no pause or replacement won during binding."""
        if not isinstance(binding_id, str) or not _HEX_ID.fullmatch(binding_id):
            raise ValueError("invalid_chat_sync_binding")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                "UPDATE sync_bindings SET binding_id=?, enabled=1, generation=generation+1 "
                "WHERE character_uid=? AND owner_id=? AND request_id=? AND generation=?",
                (binding_id, prepared["character_uid"], prepared["owner_id"], prepared["request_id"], prepared["generation"]),
            )
            if result.rowcount != 1:
                raise ValueError("chat_sync_binding_changed")
            conn.commit()
        return self.binding(prepared["character_uid"])

    def pause(self, character_uid: str) -> None:
        """Revoke saved On atomically while retaining the owner, originals and pending deletions."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE sync_bindings SET enabled=0, generation=generation+1 WHERE character_uid=?", (character_uid,),
            )
            conn.execute("DELETE FROM sync_new_roles WHERE character_uid=?", (character_uid,))
            conn.execute("INSERT OR REPLACE INTO sync_choices VALUES (?,0)", (character_uid,))
            conn.commit()


class DesktopChatSyncRuntime:
    """Use the current OAuth snapshot only after remote owner verification."""

    def __init__(
        self, directory: Path, session_provider: SessionProvider, *,
        http_client: httpx.AsyncClient | None = None, session_commit_guard=None,
        session_presence_provider=None, clock=now_ms, uniform=None, witness_path: Path | None = None,
        notification_connect=None, history_changed=None, current_character_uid_provider=None, chat_history_provider=None,
        fixed_service_url: str | None = None,
        recent_memory_adapter=None, recent_character_name=None, long_term_memory_adapter=None,
    ):
        """Allow synthetic HTTP transports in tests; production verifies HTTPS."""
        from services.chat_sync.writer import ChatSyncWriter
        from services.chat_sync.source import ChatOriginalSource
        self._clock = clock
        self._fixed_service_url = fixed_service_url
        self.trace = get_trace()
        self.writer = ChatSyncWriter(directory, witness_path)
        with self.writer.initialize():
            self.state = DesktopSyncState(directory / "state.sqlite3")
            self.ledger = ChatSyncLedger(directory / "originals.sqlite3")
            self.scheduler = ChatSyncScheduler(directory / "scheduler.sqlite3", clock=clock, **({"uniform": uniform} if uniform else {}))
            # The source is an independently checked participant, even before
            # the first raw producer creates a source object for this role.
            ChatOriginalSource(directory / "sources.sqlite3")
            self.profiles = DesktopProfileSync(self, directory / "profiles.sqlite3")
            from services.chat_sync.recent import RecentMemorySync
            self.recent_memory = (RecentMemorySync(directory / "state.sqlite3", recent_memory_adapter)
                                  if recent_memory_adapter is not None else None)
            from services.chat_sync.long_term import LongTermMemorySync
            self.long_term_memory = (LongTermMemorySync(directory / 'state.sqlite3', long_term_memory_adapter)
                                     if long_term_memory_adapter is not None else None)
        self.recent_character_name = recent_character_name
        self._recent_lock = asyncio.Lock()
        self._long_term_lock = asyncio.Lock()
        self.session_provider = session_provider
        async def synthetic_presence():
            """Injected transports never read the developer's real login files."""
            status = await session_provider()
            snapshot = status.get("snapshot") if isinstance(status, dict) else None
            if not isinstance(status, dict) or not status.get("logged_in") or not isinstance(snapshot, dict):
                return None
            token, origin = snapshot.get("access_token"), snapshot.get("base_url")
            if not isinstance(token, str) or not token or origin != _COMMUNITY_ORIGIN:
                return None
            return hashlib.sha256(_canonical({"token": token, "origin": origin})).hexdigest()
        self.session_presence_provider = session_presence_provider or (synthetic_presence if http_client is not None else _saved_session_presence)
        self.session_commit_guard = session_commit_guard or _saved_session_commit_guard
        # Production construction can finish in a thread after boot cancellation.
        # Defer client allocation until this runtime owns an admitted async send;
        # injected test clients retain their explicit constructor ownership.
        self.http = http_client
        # The process-only option affects this sync client, never community TLS.
        # Injected synthetic clients stay independent of the developer's machine.
        self._local_ca_file = os.environ.get(_LOCAL_CA_ENV) if http_client is None else None
        self._tls_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._deletion_lock = asyncio.Lock()
        self._pull_lock = asyncio.Lock()
        self._marker_flight_lock = asyncio.Lock()
        # Manual setup and automatic recovery consume the same fixed consent;
        # only one may prepare/finish its history snapshot at a time.
        self._sync_choice_lock = asyncio.Lock()
        # This short gate never covers HTTP: pause and local response commits
        # have one order even when a worker starts after its async session check.
        self._commit_lock = threading.Lock()
        self._background: set[asyncio.Task] = set()
        self._scheduler_task: asyncio.Task | None = None
        self._flights: dict[str, asyncio.Task] = {}
        self._wake = asyncio.Event()
        self._recent_unsubscribe = None
        self._body_eligible_at: int | None = None
        self.history_changed = history_changed
        self.chat_history_provider = chat_history_provider
        self.current_character_uid_provider = current_character_uid_provider
        self._automatic_character_uid = None
        self._closing = False
        self._login_present: bool | None = None
        self._retirement_check_at = 0
        # Injected HTTP tests stay offline unless they also supply a socket.
        self._notification_connect = notification_connect or (self._connect_notifications if http_client is None else None)
        self._notification_task: asyncio.Task | None = None
        self._notification_key = None
        self._notification_retry_at = 0
        self._notification_failures = 0
        self._notification_blocked_key = None
        # Static profiles use the same owner/HTTPS fences but their own durable
        # consent and conflict ledger. Tests never acquire a real profile adapter.


    def _service_settings(self):
        """Old service permissions require recovery before the production origin can be used."""
        from services.chat_sync.writer import WriterQuarantined
        settings = self.state.settings()
        if self._fixed_service_url and settings["service_url"] != self._fixed_service_url:
            raise WriterQuarantined()
        return settings

    def prepare_writer_recovery(self):
        """An explicit recovery action revokes all copied tasks before fresh binding."""
        from services.chat_sync.recovery import stage_authority

        def stage():
            stage_authority(self)
            if self._fixed_service_url:
                with closing(store_connection(self.state.path)) as conn, conn:
                    conn.execute("UPDATE sync_settings SET service_url=?", (self._fixed_service_url,))
        with self._commit_lock:
            self.writer.recover(stage)

    def save_outage_original(self, uid, record_id, original):
        """Keep accepted local chat available when optional writer authority fails."""
        from services.chat_sync.recovery import save_outage
        return save_outage(self.state.path.parent, uid, record_id, original)

    async def recover_writer_role(self, character_uid, local_name, *, service_url=None):
        """Verify current cloud fences before reusing any copied history candidate."""
        from services.chat_sync.writer import WriterQuarantined
        from services.chat_sync.recovery import (import_outage, pending_candidates, apply_cloud_deletions,
                                                  apply_checked_candidates, finish_role)
        from services.chat_sync.profile_sync import read_character_directory
        from services.chat_sync.source import ChatOriginalSource
        try:
            await asyncio.to_thread(self.writer.check)
            await asyncio.to_thread(self._service_settings)
        except WriterQuarantined:
            await asyncio.to_thread(self.prepare_writer_recovery)
        await asyncio.to_thread(import_outage, self)
        if service_url is not None:
            await self.configure(service_url)
        # A new device/request and explicit service-authenticated binding revoke
        # old flights. Re-enabling alone does not approve any staged body/delete.
        await self.enable(character_uid, local_name)
        async with self._lock:
            binding = await asyncio.to_thread(self.state.binding, character_uid)
            authenticated = await self._session()
            domain, request, commit = await self._bound_transport(character_uid, authenticated=authenticated,
                                                                  expected_binding=binding, recovery=True)
            client = PrototypeChatSyncClient(self.ledger, domain, request, commit=commit)
            after, through = 0, None
            while True:
                page = await client.recovery_deletions(after=after, through=through)
                await commit(apply_cloud_deletions, self, domain, page['deletedMessageIds'])
                through, after = page['through'], page['cursor']
                if not page['hasMore']:
                    break
            candidates = await asyncio.to_thread(pending_candidates, self, domain)
            offset, batch_limit = 0, 200
            while offset < len(candidates):
                selected = candidates[offset:offset + batch_limit]
                try:
                    result = await client.check_recovery([{'messageId': row['record_id'], 'sha256': hashlib.sha256(row['payload']).hexdigest()} for row in selected])
                except ChatSyncServiceError as error:
                    if error.status_code != 413 or len(selected) == 1:
                        raise
                    # The server bounds stored-body verification, not just this
                    # digest envelope. Shrink the same unchecked prefix; retain
                    # the learned limit and the bound transport's normal fences.
                    batch_limit = max(1, len(selected) // 2)
                    continue
                await commit(apply_checked_candidates, self, domain, selected, result['results'])
                offset += len(selected)
            roles = await read_character_directory(request, binding['owner_id'])
            cloud_name = next((role['displayName'] for role in roles['characters'] if role['characterSyncId'] == character_uid), None)
            if cloud_name is None:
                raise ValueError('chat_sync_character_not_found')
            recovered = {'history': None}
            if await self._refresh_chat_history(character_uid):
                settings = await asyncio.to_thread(self._service_settings)
                source = await asyncio.to_thread(ChatOriginalSource, self.state.path.parent / 'sources.sqlite3')
                task = await commit(source.prepare_history, binding, settings['service_url'], authenticated[1], local_name, cloud_name,
                                    device_id=settings['device_id'])
                recovered['history'] = source.metadata(task)
            else:
                # An unavailable archive cannot grant history, but verified deletions remain reviewable.
                recovered['historyError'] = 'chat_sync_unavailable'
            await self._guard(binding, authenticated[1])
            # The recovery transport prepares previews while the role gate is still pending.
            # Release it only after every fallible preview/authority check has completed.
            deletions = await commit(finish_role, self, domain, binding, authenticated[1])
            deletions.update(localName=local_name, cloudName=cloud_name)
            recovered['deletions'] = deletions
        return recovered

    async def approve_recovery_deletions(self, character_uid, task_id):
        """Register renewed exact deletion consent through the existing marker lane."""
        from services.chat_sync.recovery import approve_deletions
        binding = await asyncio.to_thread(self.state.binding, character_uid)
        authenticated = await self._session()
        _, _, commit = await self._bound_transport(character_uid, authenticated=authenticated, expected_binding=binding)
        result = await commit(approve_deletions, self, character_uid, task_id, binding, authenticated[1])
        self.wake_scheduler()
        return result

    async def close(self) -> None:
        """Release this adapter's HTTP transport during orderly shutdown."""
        self._closing = True
        if self._recent_unsubscribe is not None:
            self._recent_unsubscribe()
            self._recent_unsubscribe = None
        tasks = list(self._background)
        for task in tasks:
            task.cancel()
        # Shutdown never seals new work or waits for a network flush. Fixed
        # pending requests and pre-send attempt fences already live on disk.
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.http is not None:
            await self.http.aclose()

    def wake_scheduler(self) -> None:
        """Signal local work without placing HTTP in the chat persistence path."""
        self._wake.set()

    def start_scheduler(self) -> None:
        """Start once after storage selection; constructing a runtime is passive."""
        if not self._closing and (self._scheduler_task is None or self._scheduler_task.done()):
            if self.recent_memory is not None and self._recent_unsubscribe is None:
                from utils.recent_file import subscribe_recent_changes
                loop = asyncio.get_running_loop()
                self._recent_unsubscribe = subscribe_recent_changes(
                    lambda path: loop.call_soon_threadsafe(self.wake_scheduler),
                )
            self._scheduler_task = asyncio.create_task(self._run_scheduler())
            self._track(self._scheduler_task)

    def _track(self, task: asyncio.Task) -> None:
        """Retain worker tasks so cancellation closes only this feature's clients."""
        self._background.add(task)
        def finished(done):
            """Observe failures without leaking SQL, filesystem paths or credentials."""
            self._background.discard(done)
            if not done.cancelled() and done.exception() is not None:
                print("[chat-sync] background_step_unavailable")
        task.add_done_callback(finished)

    async def _run_scheduler(self) -> None:
        """Wake bounded local workers; slow body I/O never blocks marker/pull flights."""
        armed = False
        while not self._closing:
            if self.recent_memory is not None:
                # A burst of saved messages or an append followed by compression
                # needs one inspection, not one full read per write callback.
                await asyncio.sleep(0.2)
            self._wake.clear()
            if self._body_eligible_at is not None and self._body_eligible_at <= self.scheduler.clock():
                self._body_eligible_at = None
            try:
                if not armed:
                    await asyncio.to_thread(self.scheduler.arm_wake)
                    armed = True
                await self.scheduler_tick()
                deadline = (await asyncio.to_thread(self.scheduler.next_wake_at)
                            if self.recent_memory is not None else None)
                if self.recent_memory is not None:
                    now = self.scheduler.clock()
                    role_deadline = await asyncio.to_thread(self.state.next_wake_at, now)
                    deadline = min((value for value in (deadline, role_deadline, self._notification_retry_at)
                                    if value is not None and value > now), default=None)
            except (ValueError, OSError, sqlite3.Error):
                # A local recovery problem does not escape into normal chat or
                # produce a tight retry loop. No exception text is logged here.
                await self._stop_notifications()
                deadline = None
            timeout = _RECENT_FALLBACK_SECONDS if self.recent_memory is not None else _LOCAL_WAKE_SECONDS
            if deadline is not None:
                timeout = min(timeout, max(0, (deadline - self.scheduler.clock()) / 1000))
            if self._body_eligible_at is not None:
                remaining = (self._body_eligible_at - self.scheduler.clock()) / 1000
                # Only future eligibility shortens the tick. An eligible queue
                # blocked by retry/auth still uses ordinary bounded admission.
                if remaining > 0:
                    timeout = min(timeout, remaining)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except TimeoutError:
                pass

    async def scheduler_tick(self) -> None:
        """Check local consent/login before launching any remote activity."""
        if self._closing:
            return
        bindings = await asyncio.to_thread(self.state.bindings)
        has_new_roles = await asyncio.to_thread(self.state.has_new_roles)
        if not has_new_roles and not bindings:
            await self._stop_notifications()
            return
        presence = await self.session_presence_provider()
        if presence and bindings and self.profiles.adapter is not None and self.scheduler.clock() >= self._retirement_check_at:
            flight = self._flights.get("retirement")
            if flight is None or flight.done():
                self._retirement_check_at = self.scheduler.clock() + 30_000
                task = asyncio.create_task(self.reconcile_deleted_characters())
                self._flights["retirement"] = task
                self._track(task)
        if presence and has_new_roles:
            try:
                await self._start_new_role_sync(presence)
            except (ValueError, OSError, sqlite3.Error):
                # A failed new-role intent must not stop existing authorized roles.
                pass
        if not any(item["enabled"] and item["binding_id"] for item in bindings):
            await self._stop_notifications()
            return
        if not presence:
            self._login_present = False
            await self._stop_notifications()
            return
        if self._login_present is False:
            await asyncio.to_thread(self.scheduler.arm_wake)
        self._login_present = True
        await self._recover_originals()
        current_uid = await self._current_character_uid()
        if current_uid != self._automatic_character_uid:
            self._automatic_character_uid = current_uid
            epoch = await asyncio.to_thread(self.writer.epoch)
            for binding in bindings:
                if binding["character_uid"] == current_uid and binding["enabled"] and binding["binding_id"]:
                    for channel in ("pull", "profile"):
                        await self._hint_check(binding, presence, epoch, channel)
        await self._manage_notifications(bindings, presence)
        # Each channel has one owned flight. Body admission is shared by every
        # role; downloads and marker retries can progress during a slow upload.
        channels = ("pull",) if self.recent_memory is not None else ("deletion", "body", "pull")
        if self.long_term_memory is not None:
            channels += ('long_term',)
        for channel in channels:
            previous = self._flights.get(channel)
            if previous is None or previous.done():
                task = asyncio.create_task(self._automatic_channel(channel, presence))
                self._flights[channel] = task
                self._track(task)
        if self.profiles.adapter is not None:
            # Profile downloads are independent of a slow or failed chat batch.
            previous = self._flights.get("profile")
            if previous is None or previous.done():
                task = asyncio.create_task(self._automatic_profiles(bindings, presence))
                self._flights["profile"] = task
                self._track(task)

    @asynccontextmanager
    async def _connect_notifications(self, service_url):
        """Use verified WSS with no bearer in URLs, proxies or diagnostic text."""
        from websockets.asyncio.client import connect
        self._check_local_ca_origin(service_url)
        context = await asyncio.to_thread(_load_local_ca_context, self._local_ca_file) if self._local_ca_file else ssl.create_default_context()
        async with connect("wss" + service_url[5:] + "/v1/chat-sync/notifications", ssl=context,
                           proxy=None, open_timeout=3, close_timeout=1, max_size=4096,
                           ping_interval=2, ping_timeout=2) as socket:
            yield socket

    async def _stop_notifications(self):
        """Close only the owned subscription when consent or login changes."""
        task, self._notification_task = self._notification_task, None
        self._notification_key = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _manage_notifications(self, bindings, presence):
        """Replace an entire subscription on token, binding or writer changes."""
        if self._notification_connect is None:
            return
        current_uid = await self._current_character_uid()
        settings = await asyncio.to_thread(self._service_settings)
        selected = sorted((dict(item) for item in bindings if item["enabled"] and item["binding_id"]
                           and item["character_uid"] == current_uid),
                          key=lambda item: item["character_uid"])
        if not selected:
            await self._stop_notifications()
            return
        epoch = await asyncio.to_thread(self.writer.epoch)
        key = (settings["service_url"], presence, epoch, tuple(
            (item["character_uid"], item["binding_id"], item["generation"], item["owner_id"]) for item in selected))
        if key != self._notification_key:
            await self._stop_notifications()
            self._notification_key = key
            self._notification_retry_at = 0
            self._notification_failures = 0
        if self._notification_task is not None and not self._notification_task.done():
            return
        if key == self._notification_blocked_key or self.scheduler.clock() < self._notification_retry_at:
            return
        if not await asyncio.to_thread(self.scheduler.origin_ready):
            return
        self._notification_task = asyncio.create_task(self._run_notifications(key, selected, presence, epoch))
        self._track(self._notification_task)

    async def _hint_check(self, binding, presence, epoch, channel, *, login_verified=False, wake=True):
        """An update only invalidates poll due time under existing commit fences."""
        await self._guard(binding, presence)
        await asyncio.to_thread(self.writer.require_epoch, epoch)
        await asyncio.to_thread(self._commit_bound, binding, self.scheduler.request_check,
                                ChatDomain(binding["owner_id"], binding["character_uid"]).key(), channel,
                                verified_login=(binding["generation"], presence) if login_verified else None,
                                fingerprint=presence)
        if channel == 'pull' and self.long_term_memory is not None:
            await asyncio.to_thread(self._commit_bound, binding, self.scheduler.request_check,
                                    ChatDomain(binding['owner_id'], binding['character_uid']).key(), 'long_term',
                                    verified_login=(binding['generation'], presence) if login_verified else None,
                                    fingerprint=presence)
        if wake:
            self.wake_scheduler()

    async def request_catchup(self):
        """Wake the selected opted-in role without doing HTTP in the UI call."""
        if self._closing:
            return
        presence = await self.session_presence_provider()
        if not presence:
            return
        epoch = await asyncio.to_thread(self.writer.epoch)
        current_uid = await self._current_character_uid()
        for binding in await asyncio.to_thread(self.state.bindings):
            if binding["enabled"] and binding["binding_id"] and binding["character_uid"] == current_uid:
                for channel in ("pull", "profile"):
                    await self._hint_check(binding, presence, epoch, channel)
        self.wake_scheduler()

    async def _run_notifications(self, key, bindings, presence, epoch):
        """Treat subscription and changed frames as hints, never delivery evidence."""
        from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
        by_uid = {item["character_uid"]: item for item in bindings}
        try:
            await self._require_automatic_character(bindings[0]["character_uid"])
            token, fingerprint = await self._session()
            if fingerprint != presence:
                raise ValueError("chat_sync_session_changed")
            await self._require_automatic_character(bindings[0]["character_uid"])
            async with self._notification_connect(key[0]) as socket:
                await asyncio.to_thread(self.writer.require_epoch, epoch)
                await self._same_session(presence)
                await self._require_automatic_character(bindings[0]["character_uid"])
                await socket.send(json.dumps({"type": "authenticate", "token": token}))
                if json.loads(await asyncio.wait_for(socket.recv(), timeout=10)) != {"type": "ready"}:
                    raise ValueError("invalid_chat_sync_response")
                # Recheck every captured grant before subscribing. Any changed
                # generation replaces the complete connection on the next tick.
                for binding in bindings:
                    await self._guard(binding, presence)
                    await self._require_automatic_character(binding["character_uid"])
                await socket.send(json.dumps({"type": "subscribe", "characters": [
                    {"characterSyncId": item["character_uid"], "bindingId": item["binding_id"]} for item in bindings]}))
                if json.loads(await asyncio.wait_for(socket.recv(), timeout=10)) != {"type": "subscribed"}:
                    raise ValueError("invalid_chat_sync_response")
                self._notification_failures = 0
                for binding in bindings:
                    for channel in ("pull", "profile"):
                        # A service-accepted subscription proves this exact
                        # bearer/grant; ordinary hints cannot repair login gates.
                        await self._hint_check(binding, presence, epoch, channel, login_verified=True)
                while not self._closing:
                    raw = await socket.recv()
                    event = json.loads(raw)
                    if (not isinstance(event, dict) or set(event) != {"type", "characterSyncId", "channel"}
                            or event["type"] != "changed" or event["channel"] not in {"chat", "profile"}
                            or not isinstance(event["characterSyncId"], str) or event["characterSyncId"] not in by_uid):
                        raise ValueError("invalid_chat_sync_response")
                    await self._hint_check(by_uid[event["characterSyncId"]], presence, epoch,
                                           "pull" if event["channel"] == "chat" else "profile")
        except asyncio.CancelledError:
            raise
        except AutomaticRoleInactive:
            return
        except (OSError, ValueError, TypeError, RecursionError, sqlite3.Error, WebSocketException) as exc:
            # Rejected authentication/binding waits for changed local authority;
            # fallback HTTP keeps its own durable errors and never borrows WS.
            rejected = isinstance(exc, ConnectionClosed) and exc.rcvd is not None and exc.rcvd.code in {1008, 4001, 4003, 4401, 4403}
            rejected = rejected or (isinstance(exc, InvalidStatus) and exc.response.status_code in {401, 403})
            if rejected:
                self._notification_blocked_key = key
            self._notification_failures += 1
            retry = min(60_000, 5_000 * 2 ** min(self._notification_failures - 1, 4))
            # Only transport loss gets a short reconnect deadline. Rejected
            # grants and invalid protocol responses retain their existing gates.
            transport = isinstance(exc, OSError) or (isinstance(exc, ConnectionClosed)
                         and ((exc.rcvd is None and (exc.sent is None or exc.sent.code in {1000, 1001, 1011}))
                              or (exc.rcvd is not None and exc.rcvd.code in {1000, 1001})))
            self._notification_retry_at = self.scheduler.clock() + (min(retry, 3000) if transport else retry)
            # A dropped hint channel asks for a normal fenced check. It cannot
            # bypass HTTP errors, and exceptions contain no logged token/body.
            if not self._closing:
                for binding in bindings:
                    for channel in ("pull", "profile"):
                        try:
                            await self._hint_check(binding, presence, epoch, channel)
                        except (OSError, ValueError, sqlite3.Error):
                            pass
    async def _automatic_profiles(self, bindings, presence):
        """Finish initial consent independently; ordinary profile work remains current-role only."""
        for binding in bindings:
            if self._closing:
                return
            if binding["enabled"] and binding["binding_id"]:
                try:
                    # Explicitly approved fixed bytes should not wait for the
                    # periodic read fallback. Local drafts never enter outbox.
                    domain = ChatDomain(binding["owner_id"], binding["character_uid"]).key()
                    operation = await asyncio.to_thread(self.profiles.store.next_operation, domain)
                    initial = await asyncio.to_thread(self.profiles.store.status, domain) if operation and operation["base_revision"] == 0 else None
                    initial_upload_only = bool(initial and initial["base_revision"] == initial["head_revision"] == 0)
                    if not initial_upload_only:
                        await self._require_automatic_character(binding["character_uid"])
                    if operation is not None:
                        epoch = await asyncio.to_thread(self.writer.epoch)
                        await self._hint_check(binding, presence, epoch, "profile", wake=self.recent_memory is None)
                    await self.profiles.step(binding["character_uid"], presence=presence, initial_upload_only=initial_upload_only)
                except (AutomaticRoleInactive, ValueError, OSError, sqlite3.Error):
                    # Approved pending bytes and both conflict versions survive.
                    continue

    async def reconcile_deleted_characters(self):
        """Accept only the pinned owner's explicit retirement, even for paused/noncurrent roles."""
        if self.profiles.adapter is None:
            return
        for binding in await asyncio.to_thread(self.state.bindings):
            uid = binding["character_uid"]
            domain = ChatDomain(binding["owner_id"], uid).key()
            presence = await self.session_presence_provider()
            try:
                epoch = await asyncio.to_thread(self.writer.epoch)
                if not await asyncio.to_thread(self.scheduler.claim, domain, "retirement", binding["generation"], presence):
                    continue
                token, fingerprint = await self._session()
                if await self._identity(token) != binding["owner_id"]:
                    await asyncio.to_thread(self.scheduler.success, domain, "retirement")
                    continue
                await self._same_session(fingerprint)
                try:
                    await self._request("GET", f"/prototype/chat-sync/characters/{uid}/profile?metadataOnly=true", token)
                    await asyncio.to_thread(self.scheduler.success, domain, "retirement")
                    continue
                except ChatSyncServiceError as error:
                    if not error.character_deleted:
                        raise
                await self._same_session(fingerprint)
                paused = await asyncio.to_thread(self._pause_retired_character, binding, fingerprint, epoch)
                if paused is None:
                    await asyncio.to_thread(self.scheduler.success, domain, "retirement")
                    continue

                async def active():
                    await self._same_session(fingerprint)
                    await asyncio.to_thread(self.writer.require_epoch, epoch)
                    if self._closing or await asyncio.to_thread(self.state.binding, uid) != paused:
                        raise ValueError("chat_sync_binding_changed")

                if await self.profiles.adapter.delete(uid, active):
                    await active()
                    await asyncio.to_thread(self._purge_retired_character, paused, fingerprint, epoch)
                await asyncio.to_thread(self.scheduler.success, domain, "retirement")
            except (ValueError, OSError, sqlite3.Error) as error:
                # The remote fence survives failures; an interrupted local purge retries.
                try:
                    await asyncio.to_thread(self.scheduler.failure, domain, "retirement", binding["generation"], presence, error)
                except (ValueError, OSError, sqlite3.Error):
                    pass
                continue

    def _pause_retired_character(self, binding, fingerprint, epoch):
        """Pause only the association whose remote retirement was verified."""
        uid = binding["character_uid"]
        with self.session_commit_guard(fingerprint), self._commit_lock, self.writer.locked():
            self.writer.require_epoch(epoch)
            if self._closing or self.state.binding(uid) != binding:
                return None
            self.state.pause(uid)
            return self.state.binding(uid)

    def _purge_retired_character(self, binding, fingerprint, epoch):
        """Erase this role's sync copies before forgetting its retryable association."""
        uid = binding["character_uid"]
        domain = ChatDomain(binding["owner_id"], uid).key()
        directory = self.state.path.parent
        with self.session_commit_guard(fingerprint), self._commit_lock, self.writer.locked():
            self.writer.require_epoch(epoch)
            if self.state.binding(uid) != binding:
                raise ValueError("chat_sync_binding_changed")
            with closing(store_connection(directory / "sources.sqlite3")) as conn, conn:
                for members, tasks in (("source_history_members", "source_history_tasks"),
                                       ("source_recovery_delete_members", "source_recovery_delete_tasks")):
                    conn.execute(f"DELETE FROM {members} WHERE task_id IN (SELECT task_id FROM {tasks} WHERE uid=?)", (uid,))
                for table in ("source_originals", "source_unbound", "source_local_replies", "source_origin_streams",
                              "source_history_tasks", "source_recovery_candidates", "source_recovery_roles",
                              "source_recovery_delete_ids", "source_recovery_delete_tasks"):
                    conn.execute(f"DELETE FROM {table} WHERE uid=?", (uid,))
                conn.execute("DELETE FROM source_role_reassignments WHERE old_uid=? OR new_uid=?", (uid, uid))
            for filename, tables in (
                ("originals.sqlite3", ("chat_pending", "chat_batches", "chat_sources", "chat_originals", "chat_downloaded",
                                      "chat_tombstones", "chat_cursors", "chat_deletions", "chat_lane_rotation",
                                      "chat_identity_backfills", "chat_oversized", "chat_recovery_deletion_pages",
                                      "chat_origin_identities", "chat_deleted_originals")),
                ("profiles.sqlite3", ("profile_state", "profile_versions", "profile_outbox", "profile_imports")),
                ("scheduler.sqlite3", ("scheduler_tasks",)),
            ):
                with closing(store_connection(directory / filename)) as conn, conn:
                    for table in tables:
                        conn.execute(f"DELETE FROM {table} WHERE domain=?", (domain,))
            outage = directory / "outage.sqlite3"
            if outage.exists():
                with closing(sqlite3.connect(outage, timeout=10)) as conn, conn:
                    for table in ("outage_originals", "outage_deletions"):
                        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                            conn.execute(f"DELETE FROM {table} WHERE uid=?", (uid,))
            with closing(self.state._connect()) as conn, conn:
                for table in ("sync_new_roles", "sync_choices", "sync_bindings"):
                    conn.execute(f"DELETE FROM {table} WHERE character_uid=?", (uid,))

    async def _automatic_channel(self, channel: str, presence: str) -> None:
        """Sync the selected opted-in role; independent deletes still visit all grants."""
        bindings = await asyncio.to_thread(self.state.bindings)
        by_domain = {ChatDomain(item["owner_id"], item["character_uid"]).key(): item for item in bindings if item["enabled"] and item["binding_id"]}
        if channel != "deletion":
            current_uid = await self._current_character_uid()
            by_domain = {key: item for key, item in by_domain.items() if item["character_uid"] == current_uid}
        domains = await asyncio.to_thread(self.scheduler.ordered, list(by_domain))
        for key in domains:
            if self._closing:
                return
            binding = by_domain[key]
            try:
                await self._channel_pass(binding["character_uid"], channel, presence=presence)
            except (AutomaticRoleInactive, ValueError, OSError, sqlite3.Error):
                # The channel recorded a fixed error and its durable retry or
                # explicit-resume fence. Another tick cannot bypass that fence.
                continue

    async def _current_character_uid(self):
        """A missing selection never opts other saved bindings into automatic work."""
        if self.current_character_uid_provider is None:
            return None
        try:
            return await self.current_character_uid_provider()
        except (ValueError, OSError, sqlite3.Error):
            return None

    async def _require_automatic_character(self, uid):
        """Selection limits new automatic requests, never an already-issued receipt."""
        if await self._current_character_uid() != uid:
            raise AutomaticRoleInactive()

    async def _session(self) -> tuple[str, str]:
        """Fingerprint the resolver's exact validated bearer without persisting it."""
        if self._closing:
            raise ValueError("chat_sync_closing")
        span = self.trace.span_id()
        self.trace.emit("session_begin", span_id=span)
        try:
            status = await self.session_provider()
        except BaseException as exc:
            self.trace.emit("session_end", span_id=span, outcome="cancelled" if isinstance(exc, asyncio.CancelledError) else "error")
            raise
        self.trace.emit("session_end", span_id=span, outcome="success")
        snapshot = status.get("snapshot") if isinstance(status, dict) else None
        if not isinstance(status, dict) or not isinstance(snapshot, dict):
            raise ValueError("chat_sync_login_required")
        token, origin = snapshot.get("access_token"), snapshot.get("base_url")
        if not isinstance(token, str) or not token or origin != _COMMUNITY_ORIGIN:
            raise ValueError("chat_sync_login_required")
        if not status.get("logged_in"):
            # The existing resolver retains saved credentials on temporary
            # verification failures; retry without claiming authenticated access.
            if _community_session.status_session_saved(status):
                raise ValueError("chat_sync_session_unavailable")
            raise ValueError("chat_sync_login_required")
        return token, hashlib.sha256(_canonical({"token": token, "origin": origin})).hexdigest()

    async def _same_session(self, fingerprint: str) -> None:
        """Fence responses locally without repeating remote validation per guard."""
        if await self.session_presence_provider() != fingerprint:
            raise ValueError("chat_sync_session_changed")

    def _check_local_ca_origin(self, service_url: str) -> None:
        """Never extend test-CA trust to a saved or newly entered remote service."""
        if self._local_ca_file and not _is_loopback_service(service_url):
            raise ValueError("chat_sync_local_ca_loopback_required")

    async def _http_client(self) -> httpx.AsyncClient:
        """Load optional CA files in a worker before creating the local TLS client."""
        async with self._tls_lock:
            if self._closing:
                raise ValueError("chat_sync_closing")
            if self.http is None:
                context = await asyncio.to_thread(_load_local_ca_context, self._local_ca_file) if self._local_ca_file else True
                # Close may finish during CA I/O. An unowned late thread result
                # must never allocate an HTTP pool after the closing fence.
                if self._closing:
                    raise ValueError("chat_sync_closing")
                self.http = httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False, verify=context)
            return self.http

    async def _request(self, method: str, path: str, token: str, *, body: bytes | None = None, binding_id: str | None = None, before_send=None) -> dict:
        """Observe local preparation and the complete HTTP wait without payloads."""
        span = self.trace.span_id()
        with self.trace.scope(span_id=span, route=route_class(path)):
            self.trace.emit("http_begin")
            try:
                payload = await self._perform_request(method, path, token, body=body, binding_id=binding_id, before_send=before_send)
            except BaseException as exc:
                self.trace.emit("http_end", outcome="cancelled" if isinstance(exc, asyncio.CancelledError) else "error")
                raise
            self.trace.emit("http_end", outcome="success")
            return payload

    async def _perform_request(self, method: str, path: str, token: str, *, body: bytes | None = None, binding_id: str | None = None, before_send=None) -> dict:
        """Send only to the pinned HTTPS service; redirects cannot forward tokens."""
        if self._closing:
            raise ValueError("chat_sync_closing")
        request_epoch = await asyncio.to_thread(self.writer.epoch)
        settings = await asyncio.to_thread(self._service_settings)
        if not settings["service_url"]:
            raise ValueError("chat_sync_service_not_configured")
        if not await asyncio.to_thread(self.scheduler.origin_ready):
            # Scope-free server backpressure also covers identity and enable,
            # even when a user presses a manual control during Retry-After.
            raise ValueError("chat_sync_retry_later")
        self._check_local_ca_origin(settings["service_url"])
        client = await self._http_client()
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if binding_id is not None:
            headers["X-Neko-Chat-Binding"] = binding_id
        await asyncio.to_thread(self.writer.require_epoch, request_epoch)
        try:
            if before_send is not None:
                await before_send()
            self.trace.emit("http_send")
            async with client.stream(
                method, settings["service_url"] + path, content=body, headers=headers,
                follow_redirects=False,
            ) as response:
                self.trace.emit("http_headers", status=response.status_code)
                if response.status_code >= 300:
                    error = ChatSyncServiceError(response.status_code, _retry_after_ms(response.headers.get("Retry-After")))
                    if response.status_code == 410 or (response.status_code == 409 and path.endswith("/profile")):
                        # Bound and discard error bodies; an unrelated 409 must
                        # never authorize replacing a retained profile version.
                        detail = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=1024):
                            if len(detail) + len(chunk) > 4096:
                                detail.clear()
                                break
                            detail.extend(chunk)
                        try:
                            parsed = json.loads(detail)
                            error.profile_conflict = response.status_code == 409 and parsed == {"detail": "profile_revision_conflict"}
                            error.character_deleted = response.status_code == 410 and parsed == {"detail": "character_deleted"}
                        except (ValueError, UnicodeError, RecursionError):
                            pass
                    if response.status_code == 429:
                        # Identity/enable requests have no channel claim. Record
                        # origin pressure here so no other route can bypass it.
                        await asyncio.to_thread(self.scheduler.failure, "control", "control", 0, None, error)
                    raise error
                raw = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if len(raw) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise ValueError("invalid_chat_sync_response")
                    raw.extend(chunk)
                self.trace.emit("http_body_complete", status=response.status_code)
                # Internal prototype response bound, not a final per-message cap.
                payload = json.loads(raw)
        except httpx.HTTPError:
            raise ValueError("chat_sync_unavailable") from None
        except (TypeError, json.JSONDecodeError, UnicodeError, RecursionError):
            raise ValueError("invalid_chat_sync_response") from None
        if not isinstance(payload, dict):
            raise ValueError("invalid_chat_sync_response")
        await asyncio.to_thread(self.writer.require_epoch, request_epoch)
        return payload

    async def _identity(self, token: str, *, before_send=None) -> str:
        """Obtain the service-authoritative owner, ignoring local_user_id hints."""
        payload = await self._request("GET", "/v1/chat-sync/identity", token, before_send=before_send)
        owner = payload.get("ownerId")
        validate_sync_domain(owner, "0" * 32, "chat", "private-chat")
        return owner

    async def status(self) -> dict:
        """Describe local opt-in separately from authentication and cloud progress."""
        from services.chat_sync.writer import WriterQuarantined
        from services.chat_sync.recovery import role_pending
        try:
            await asyncio.to_thread(self.writer.check)
            settings, bindings = await asyncio.gather(
                asyncio.to_thread(self._service_settings), asyncio.to_thread(self.state.bindings),
            )
        except WriterQuarantined:
            return await asyncio.to_thread(self._quarantined_status)
        for binding in bindings:
            binding["writerRecoveryRequired"] = await asyncio.to_thread(role_pending, self, binding["character_uid"])
            binding.update(await asyncio.to_thread(
                self._remaining, ChatDomain(binding["owner_id"], binding["character_uid"]),
            ))
            binding["lastSyncError"] = await asyncio.to_thread(
                self.scheduler.last_error, ChatDomain(binding["owner_id"], binding["character_uid"]).key(),
            )
            profile = await asyncio.to_thread(self.profiles.store.status, ChatDomain(binding["owner_id"], binding["character_uid"]).key())
            # Local UI polling sees only revision flags; full retained candidates
            # are returned by the explicit per-character profile controls.
            binding["profile"] = None if profile is None else {
                key: profile[key] for key in ("local_hash", "head_revision", "head_hash", "dirty", "pending", "conflict", "renameRequired", "apply_pending")
            }
            binding["historyBackfill"] = await self._history_status(binding["character_uid"])
        return {"serviceUrl": settings["service_url"], "deviceId": settings["device_id"], "bindings": bindings,
                "syncSetupErrors": await asyncio.to_thread(self.state.new_role_errors),
                "syncPendingChoices": await asyncio.to_thread(self.state.pending_choices, await asyncio.to_thread(self.writer.epoch)),
                "syncChoices": await asyncio.to_thread(self.state.choices), "writerRecoveryRequired": False}

    def _quarantined_status(self):
        """Expose local setup hints without treating copied consent as authority."""
        settings, bindings = {"service_url": None, "device_id": None}, []
        try:
            with closing(sqlite3.connect(self.state.path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                settings = dict(conn.execute("SELECT device_id,service_url FROM sync_settings").fetchone())
                for row in conn.execute("SELECT * FROM sync_bindings"):
                    bindings.append({**dict(row), "enabled": 0, "pendingOriginals": 0, "pendingDeletions": 0,
                                     "cursor": 0, "hasSealedBatch": False, "localOnlyOriginals": 0,
                                     "writerRecoveryRequired": True})
        except sqlite3.Error:
            pass
        return {"serviceUrl": self._fixed_service_url or settings["service_url"], "deviceId": settings["device_id"], "bindings": bindings,
                "writerRecoveryRequired": True}

    async def _history_status(self, character_uid):
        """Read consent/progress only when a dedicated source database exists."""
        if self.recent_memory is not None:
            binding = await asyncio.to_thread(self.state.binding, character_uid)
            if not binding:
                return None
            state = await asyncio.to_thread(self.recent_memory.state, ChatDomain(binding['owner_id'], character_uid).key())
            return state['preview']['metadata'] if state['preview'] else None
        from services.chat_sync.source import ChatOriginalSource
        path = self.state.path.parent / "sources.sqlite3"
        if not await asyncio.to_thread(path.exists):
            return None
        source = await asyncio.to_thread(ChatOriginalSource, path)
        metadata = source.metadata(await asyncio.to_thread(source.task, character_uid))
        if metadata is not None:
            metadata["awaitingRegistration"] = await asyncio.to_thread(source.awaiting_registration, character_uid)
        return metadata

    def _cancel_history_preview(self, uid, task_id, epoch):
        """Only the creator's fixed unapproved task may be cancelled under the same writer authority."""
        from services.chat_sync.source import ChatOriginalSource
        with self._commit_lock, self.writer.locked():
            self.writer.require_epoch(epoch)
            source = ChatOriginalSource(self.state.path.parent / "sources.sqlite3")
            source.cancel_preview(uid, task_id)

    async def preview_history(self, character_uid, local_name, *, after_origin_seq=0, through_origin_seq=None,
                              supersede_preview=True):
        """Pin authenticated cloud identity and a fixed trusted local member set."""
        from services.chat_sync.source import ChatOriginalSource
        if not await self._refresh_chat_history(character_uid):
            raise ValueError("chat_sync_unavailable")
        async with self._lock:
            binding = await asyncio.to_thread(self.state.binding, character_uid)
            authenticated = await self._session()
            _, request, commit = await self._bound_transport(character_uid, authenticated=authenticated, expected_binding=binding)
            from services.chat_sync.profile_sync import read_character_directory
            roles = await read_character_directory(request, binding["owner_id"])
            names = {role["characterSyncId"]: role["displayName"] for role in roles["characters"]}
            if character_uid not in names:
                raise ValueError("chat_sync_character_not_found")
            if self.recent_memory is not None:
                domain = ChatDomain(binding['owner_id'], character_uid).key()
                if await self.recent_character_name(character_uid) != local_name:
                    raise ValueError('chat_sync_character_not_found')
                local = await commit(self.recent_memory.adapter.read, local_name)
                metadata = {"taskId": uuid4().hex, "characterSyncId": character_uid, "localName": local_name,
                            "cloudName": names[character_uid], "count": len(local['entries']), "unverifiedCount": 0,
                            "status": "preview", "registeredCount": 0, "cancelledCount": 0}
                await commit(self.recent_memory.preview, domain, {"metadata": metadata, "binding": binding,
                             "fingerprint": authenticated[1], "ids": [row['id'] for row in local['entries']]})
                return metadata
            settings = await asyncio.to_thread(self._service_settings)
            source = await asyncio.to_thread(ChatOriginalSource, self.state.path.parent / "sources.sqlite3")
            epoch = await asyncio.to_thread(self.writer.epoch) if not supersede_preview else None
            preparing = commit(source.prepare_history, binding, settings["service_url"], authenticated[1], local_name, names[character_uid],
                               device_id=settings["device_id"], after_origin_seq=after_origin_seq, through_origin_seq=through_origin_seq,
                               supersede_preview=supersede_preview)
            if supersede_preview:
                task = await preparing
            else:
                # Cancellation cannot stop an already running SQLite thread.
                # Drain only this local commit so its exact ID is available for
                # cleanup, never shield the preceding login or network requests.
                preparing = asyncio.create_task(preparing)
                try:
                    task = await asyncio.shield(preparing)
                except asyncio.CancelledError:
                    try:
                        task = await asyncio.shield(preparing)
                        await asyncio.to_thread(self._cancel_history_preview, character_uid, task["task_id"], epoch)
                    except (ValueError, OSError, sqlite3.Error):
                        # Ambiguous writes still require the existing writer recovery.
                        pass
                    raise
            return source.metadata(task)

    @staticmethod
    def _history_binding_matches(task, binding, settings):
        """Allow explicit pause/resume while preserving every approved domain pin."""
        pinned = json.loads(task["binding"])
        return (task["service_url"] == settings["service_url"] and task["device_id"] == settings["device_id"] and all(pinned[key] == binding[key]
                for key in ("character_uid", "owner_id", "binding_id", "request_id")))

    async def approve_history(self, character_uid, task_id, *, expected_request_id=None):
        """Accept one reviewed task ID; the client supplies no names, counts or owner."""
        from services.chat_sync.source import ChatOriginalSource
        if not isinstance(task_id, str) or not _HEX_ID.fullmatch(task_id):
            raise ValueError("chat_sync_history_changed")
        async with self._lock:
            binding = await asyncio.to_thread(self.state.binding, character_uid)
            authenticated = await self._session()
            _, _, commit = await self._bound_transport(character_uid, authenticated=authenticated, expected_binding=binding)
            if self.recent_memory is not None:
                domain = ChatDomain(binding['owner_id'], character_uid).key()
                def approve_recent():
                    state = self.recent_memory.state(domain)
                    preview = state['preview']
                    if (not preview or preview['metadata']['taskId'] != task_id or preview['binding'] != binding
                            or preview['fingerprint'] != authenticated[1]):
                        raise ValueError('chat_sync_history_changed')
                    self.recent_memory.exclude(domain, set(state['excluded']) - set(preview['ids']))
                    preview['metadata'].update(status='registered', registeredCount=preview['metadata']['count'])
                    self.recent_memory.preview(domain, preview)
                    return preview['metadata']
                result = await commit(approve_recent)
                self.wake_scheduler()
                return result
            source = await asyncio.to_thread(ChatOriginalSource, self.state.path.parent / "sources.sqlite3")
            settings = await asyncio.to_thread(self._service_settings)
            task = await asyncio.to_thread(source.task, character_uid, task_id)
            if task is None or not self._history_binding_matches(task, binding, settings):
                raise ValueError("chat_sync_history_changed")
            if task["status"] == "preview":
                if json.loads(task["binding"]) != binding or task["fingerprint"] != authenticated[1]:
                    raise ValueError("chat_sync_history_changed")
            elif task["status"] not in ("approved", "registered"):
                raise ValueError("chat_sync_history_changed")
            def approve():
                # The bound commit already holds writer/session/generation
                # gates. A later card save must also win over an old worker
                # that waited after its last asynchronous request check.
                if expected_request_id is not None:
                    latest = self.state.new_role(character_uid)
                    if not latest or latest["requestId"] != expected_request_id:
                        raise ValueError("chat_sync_binding_changed")
                return source.approve_history(character_uid, task_id)
            approved = await commit(approve)
            self.wake_scheduler()
            return source.metadata(approved)

    async def _advance_history(self, character_uid, *, presence=None):
        """Register one bounded local page before ordinary queue eligibility is read."""
        from services.chat_sync.source import ChatOriginalSource
        path = self.state.path.parent / "sources.sqlite3"
        if not await asyncio.to_thread(path.exists):
            return 0
        binding = await asyncio.to_thread(self.state.binding, character_uid)
        if not binding or not binding["enabled"] or not binding["binding_id"]:
            return 0
        source = await asyncio.to_thread(ChatOriginalSource, path)
        tasks = await asyncio.to_thread(source.approved_tasks, character_uid)
        if not tasks:
            return 0
        presence = presence or (await self._session())[1]
        await self._guard(binding, presence)
        settings = await asyncio.to_thread(self._service_settings)
        for task in tasks:
            if not self._history_binding_matches(task, binding, settings):
                continue
            return await asyncio.to_thread(self._commit_bound, binding, self._advance_history_page, source, task, binding, fingerprint=presence)
        return 0

    def _advance_history_page(self, source, task, binding):
        """Bound raw recovery plus ledger history to one unacknowledged page."""
        domain = ChatDomain(binding["owner_id"], binding["character_uid"])
        with closing(sqlite3.connect(self.ledger.path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("ATTACH DATABASE ? AS source_wal", (source.path.resolve().as_uri() + "?mode=ro",))
            connection.execute("BEGIN")
            # Recovery commits ledger first, then clears source.pending. Taking
            # this order of snapshots and deduping IDs also covers that crash gap.
            connection.execute("SELECT 1 FROM source_wal.source_originals LIMIT 1").fetchone()
            pending = connection.execute(
                "SELECT count(*) FROM (SELECT message_id FROM chat_pending WHERE domain=? AND queue_lane='history' "
                "UNION SELECT record_id FROM source_wal.source_originals WHERE owner=? AND uid=? AND pending=1 "
                "AND deleted=0 AND history_task_id IS NOT NULL) p WHERE NOT EXISTS "
                "(SELECT 1 FROM chat_tombstones t WHERE t.domain=? AND t.message_id=p.message_id)",
                (domain.key(), domain.owner_id, domain.character_uid, domain.key()),
            ).fetchone()[0]
        capacity = max(0, 200 - pending)
        return source.advance_history(task, binding, maximum=capacity) if capacity else 0

    def _remaining(self, domain: ChatDomain) -> dict:
        """Count pending identities in both durable stores without reading raw text."""
        if self.recent_memory is not None:
            state = self.recent_memory.state(domain.key())
            pending = state["pending"]
            return {"pendingOriginals": len(pending["body"]["upserts"]) if pending else 0,
                    "pendingDeletions": len(pending["body"]["removedIds"]) if pending else 0,
                    "cursor": state["revision"], "hasSealedBatch": bool(pending), "localOnlyOriginals": 0}
        stats = self.ledger.stats(domain)
        source_path = self.state.path.parent / "sources.sqlite3"
        if not source_path.exists():
            return stats
        with closing(sqlite3.connect(self.ledger.path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("ATTACH DATABASE ? AS source_wal", (source_path.resolve().as_uri() + "?mode=ro",))
            conn.execute("BEGIN")
            # Snapshot the source before the ledger: capture commits the ledger
            # before clearing source.pending. The union dedupes that crash gap.
            conn.execute("SELECT 1 FROM source_wal.source_originals LIMIT 1").fetchone()
            stats["pendingOriginals"] = conn.execute(
                "SELECT COUNT(*) FROM (SELECT message_id FROM chat_pending WHERE domain=? "
                "UNION SELECT record_id FROM source_wal.source_originals "
                "WHERE owner=? AND uid=? AND pending=1 AND deleted=0) pending "
                "WHERE NOT EXISTS (SELECT 1 FROM chat_tombstones t WHERE t.domain=? AND t.message_id=pending.message_id)",
                (domain.key(), domain.owner_id, domain.character_uid, domain.key()),
            ).fetchone()[0]
        return stats

    async def configure(self, service_url: str) -> None:
        """Serialize service setup with binding and manual sync operations."""
        if self._fixed_service_url and _service_url(service_url) != self._fixed_service_url:
            raise ValueError("invalid_chat_sync_service_url")
        self._check_local_ca_origin(service_url)
        async with self._lock:
            await asyncio.to_thread(self.state.configure, service_url)

    def _queue_sync_choice(self, uid, name, profile, binding, epoch, *, include_history, replace):
        """Pin one saved definition/range under the caller's writer lock before dependent work."""
        from services.chat_sync.profile_protocol import encode_character_profile
        request = {"requestId": uuid4().hex, "epoch": list(epoch), "displayName": name,
                   "canonical": encode_character_profile(profile).decode("utf-8")}
        if replace and binding:
            request["resumeGeneration"] = binding["generation"]
        if self.recent_memory is not None:
            previous = self.state.new_role(uid)
            request['recentExcludedIds'] = ([] if include_history else
                (previous.get('recentExcludedIds', []) if previous and tuple(previous['epoch']) == epoch else
                 [row['id'] for row in self.recent_memory.adapter.read(name)['entries']]))
            self.state.queue_new_role(uid, request, replace=replace)
            return
        with closing(store_connection(self.state.path.parent / "sources.sqlite3")) as conn:
            row = conn.execute("SELECT next_seq FROM source_origin_streams WHERE uid=?", (uid,)).fetchone()
        cutoff = row[0] - 1 if row else 0
        request["afterSeq"] = 0 if include_history else cutoff
        if binding and binding["binding_id"]:
            # Bound saves approve a fixed old range; later originals keep their existing increment lane.
            request.update(throughSeq=cutoff, historyBinding=binding)
        previous = self.state.new_role(uid)
        if previous and tuple(previous["epoch"]) == epoch:
            request["afterSeq"] = min(previous.get("afterSeq", 0), request["afterSeq"])
        self.state.queue_new_role(uid, request, replace=replace)

    async def request_new_role_sync(self, character_uid, display_name, *, include_history=True, replace=False):
        """Persist card consent locally; login and network never block saving a card."""
        refreshed = await self._refresh_chat_history(character_uid)
        binding = await asyncio.to_thread(self.state.binding, character_uid)
        if not refreshed and (not include_history or binding and binding["binding_id"]):
            # A fixed range cannot use a partial archive, including an exclusion cutoff.
            raise ValueError("chat_sync_unavailable")
        if self.profiles.adapter is None:
            raise ValueError("character_profile_unavailable")
        epoch = await asyncio.to_thread(self.writer.epoch)
        profile = await self.profiles.adapter.read(character_uid)
        def commit():
            with self.writer.locked():
                self.writer.require_epoch(epoch)
                if self.state.binding(character_uid) != binding:
                    raise ValueError("chat_sync_binding_changed")
                self._queue_sync_choice(character_uid, display_name, profile, binding, epoch,
                                        include_history=include_history, replace=replace)
                self.state.save_choice(character_uid, True)
        await asyncio.to_thread(commit)
        self.wake_scheduler()
        return {"pending": True, "enabled": False}

    async def save_character_sync_choice(self, uid, name, enabled, *, include_history=True):
        """A card save is explicit consent; no login or cloud request blocks local saving."""
        if enabled:
            refreshed = await self._refresh_chat_history(uid)
        epoch = await asyncio.to_thread(self.writer.epoch)
        binding = await asyncio.to_thread(self.state.binding, uid)
        if enabled and include_history and binding and binding["binding_id"] and not refreshed:
            raise ValueError("chat_sync_unavailable")
        if enabled and (not binding or not binding["binding_id"] or not binding["enabled"]):
            return await self.request_new_role_sync(uid, name, include_history=include_history, replace=True)
        candidate = await self.profiles.local(uid) if enabled and binding and binding["binding_id"] else None
        def commit():
            with self._commit_lock, self.writer.locked():
                self.writer.require_epoch(epoch)
                if self.state.binding(uid) != binding:
                    raise ValueError("chat_sync_binding_changed")
                if enabled and include_history:
                    # Queue first: a crash/failure during profile approval must retain the combined consent.
                    self._queue_sync_choice(uid, name, candidate["local"], binding, epoch, include_history=True, replace=True)
                if enabled and candidate and candidate["dirty"]:
                    self.profiles.store.approve(candidate["domain"], candidate["local_hash"], candidate["head_revision"])
                self.state.save_choice(uid, enabled)
                if not enabled:
                    if binding:
                        self.state.pause(uid)
                    self.state.finish_new_role(uid)
                elif not include_history and (pending := self.state.new_role(uid)) and tuple(pending["epoch"]) == epoch:
                    # Keep the original chat cutoff while explicitly approving the newer saved definition.
                    pending.update(requestId=uuid4().hex, displayName=name)
                    pending.pop("error", None)
                    pending.pop("lastError", None)
                    from services.chat_sync.profile_protocol import encode_character_profile
                    pending["canonical"] = encode_character_profile(candidate["local"]).decode("utf-8")
                    self.state.queue_new_role(uid, pending, replace=True)
        await asyncio.to_thread(commit)
        self.wake_scheduler()
        return {"pending": enabled, "enabled": enabled}

    async def _start_new_role_sync(self, presence):
        """Complete initial profiles off-selection; only the current role consumes history consent."""
        previous = self._flights.get("new-role")
        if previous and not previous.done():
            return
        current_uid = await self._current_character_uid()
        choices = await asyncio.to_thread(self.state.choices)
        epoch = await asyncio.to_thread(self.writer.epoch)
        candidates = ([current_uid] if current_uid else []) + [uid for uid, enabled in choices.items() if enabled and uid != current_uid]
        for uid in candidates:
            request = await asyncio.to_thread(self.state.new_role, uid)
            if not request or request.get("error") or request["next_at"] > self._clock() or tuple(request["epoch"]) != epoch:
                continue
            initial_profile_only = uid != current_uid
            if initial_profile_only and not await self._initial_profile_unpublished(uid):
                continue
            break
        else:
            return
        def defer():
            with self.writer.locked():
                self.writer.require_epoch(tuple(request["epoch"]))
                self.state.defer_new_role(uid, self._clock() + 30_000)
        await asyncio.to_thread(defer)
        task = asyncio.create_task(self._run_new_role_sync(uid, request, presence, initial_profile_only=initial_profile_only))
        self._flights["new-role"] = task
        self._track(task)

    async def run_character_sync_choice(self, uid):
        """Consume a saved grant for an explicit target and advance one bounded manual pass."""
        _, presence = await self._session()
        async with self._sync_choice_lock:
            # The scheduler may have completed this grant while the manual
            # request waited. Read after admission rather than replaying a
            # stale snapshot and falsely reporting a successful setup as failed.
            request = await asyncio.to_thread(self.state.new_role, uid)
            if request:
                await self._consume_sync_choice(uid, request, presence, manual=True)
        # These are the existing manual exceptions to current-role scheduling.
        # Both channels retain their durable retry, owner and writer checks.
        await self.profiles.step(uid, manual=True)
        await self.sync(uid, byte_budget=_AUTO_BYTE_BUDGET, max_pull_pages=1)
        pending = await asyncio.to_thread(self.state.new_role, uid)
        return {"completed": pending is None, "pending": pending is not None}

    async def _initial_profile_unpublished(self, uid):
        """Only a never-published definition can finish the initial setup outside current-role scope."""
        binding = await asyncio.to_thread(self.state.binding, uid)
        state = await asyncio.to_thread(self.profiles.store.status, ChatDomain(binding["owner_id"], uid).key()) if binding else None
        return state is None or state["base_revision"] == state["head_revision"] == 0

    async def _run_new_role_sync(self, uid, request, presence, *, manual=False, initial_profile_only=False):
        """Serialize manual setup with the scheduler's consumer of the same grant."""
        async with self._sync_choice_lock:
            return await self._consume_sync_choice(uid, request, presence, manual=manual, initial_profile_only=initial_profile_only)

    async def _consume_sync_choice(self, uid, request, presence, *, manual, initial_profile_only=False):
        """Reuse binding, fixed profile approval and trusted-history tasks under writer fences."""
        from services.chat_sync.profile_protocol import encode_character_profile
        preview = None
        async def active():
            if self._closing:
                raise ValueError("chat_sync_closing")
            if not manual and not initial_profile_only:
                await self._require_automatic_character(uid)
            await asyncio.to_thread(self.writer.require_epoch, tuple(request["epoch"]))
            if await self.session_presence_provider() != presence:
                raise ValueError("chat_sync_session_changed")
            latest = await asyncio.to_thread(self.state.new_role, uid)
            if not latest or latest["requestId"] != request["requestId"]:
                raise ValueError("chat_sync_binding_changed")
        try:
            await active()
            profile = await self.profiles.adapter.read(uid)
            if encode_character_profile(profile).decode("utf-8") != request["canonical"]:
                raise ValueError("character_profile_changed")
            binding = await asyncio.to_thread(self.state.binding, uid)
            # A user pause always wins over a create-form default, including after a restart.
            if not self.state.choice_matches_binding(request, binding):
                raise ValueError("chat_sync_binding_changed")
            if initial_profile_only and not await self._initial_profile_unpublished(uid):
                return
            if not binding or not binding["binding_id"] or not binding["enabled"]:
                await self.enable(uid, request["displayName"], expected_writer_epoch=tuple(request["epoch"]), before_send=active)
            await active()
            candidate = await self.profiles.local(uid)
            expected_hash = hashlib.sha256(request["canonical"].encode("utf-8")).hexdigest()
            if candidate["local_hash"] != expected_hash:
                raise ValueError("character_profile_changed")
            def approve():
                with self._commit_lock, self.writer.locked():
                    self.writer.require_epoch(tuple(request["epoch"]))
                    current = self.state.binding(uid)
                    if not current or not current["enabled"]:
                        raise ValueError("chat_sync_binding_inactive")
                    latest = self.state.new_role(uid)
                    if (not latest or latest["requestId"] != request["requestId"]
                            or not self.state.choice_matches_binding(request, current)):
                        raise ValueError("chat_sync_binding_changed")
                    if candidate["dirty"]:
                        self.profiles.store.approve(candidate["domain"], expected_hash, candidate["head_revision"],
                                                    operation_id=request["requestId"])
            await active()
            await asyncio.to_thread(approve)
            if initial_profile_only:
                # The saved card already approved these exact initial bytes. Finish only
                # this publication; leave its existing history task for the selected role.
                await active()
                published = await self.profiles.step(uid, presence=presence, initial_upload_only=True)
                if published and published["head_revision"] > 0 and not published["pending"]:
                    await active()
                    def clear_retry_error():
                        with self._commit_lock, self.writer.locked():
                            self.writer.require_epoch(tuple(request["epoch"]))
                            self.state.record_new_role_error(uid, request["requestId"], None)
                    await asyncio.to_thread(clear_retry_error)
                return
            # Saved while first login was unavailable: these are this new role's already approved chats.
            current = await asyncio.to_thread(self.state.binding, uid)
            if not self.state.choice_matches_binding(request, current):
                raise ValueError("chat_sync_binding_changed")
            if self.recent_memory is not None:
                _, _, commit_recent = await self._bound_transport(uid, expected_binding=current)
                await commit_recent(self.recent_memory.exclude, ChatDomain(current['owner_id'], uid).key(),
                                    request.get('recentExcludedIds', []))
            else:
                preview = await self.preview_history(uid, request["displayName"], after_origin_seq=request.get("afterSeq", 0),
                                                     through_origin_seq=request.get("throughSeq"), supersede_preview=False)
                await active()
                await self.approve_history(uid, preview["taskId"], expected_request_id=request["requestId"])
            await active()
            def finish():
                with self.writer.locked():
                    self.writer.require_epoch(tuple(request["epoch"]))
                    latest = self.state.new_role(uid)
                    if not latest or latest["requestId"] != request["requestId"]:
                        raise ValueError("chat_sync_binding_changed")
                    self.state.finish_new_role(uid)
            await asyncio.to_thread(finish)
        except ValueError as error:
            code = str(error)
            if code in {"character_profile_changed", "character_profile_name_conflict", "character_profile_unsupported_value",
                        "character_profile_conflict", "profile_revision_conflict", "prototype_idempotency_conflict",
                        "chat_sync_binding_changed"}:
                def block():
                    with self._commit_lock, self.writer.locked():
                        self.writer.require_epoch(tuple(request["epoch"]))
                        latest = self.state.new_role(uid)
                        if latest and latest["requestId"] == request["requestId"]:
                            current = self.state.binding(uid)
                            if code == "chat_sync_binding_changed" and self.state.choice_matches_domain(request, current):
                                # A pause revokes a retained pre-upgrade On; an enabled
                                # same-domain generation race simply retries. Neither is
                                # an account/identity change requiring another card save.
                                if not self.state.choice_matches_binding(request, current):
                                    self.state.finish_new_role(uid)
                                return
                            self.state.queue_new_role(uid, {**request, "error": code}, replace=True)
                await asyncio.to_thread(block)
            elif code in {"chat_sync_account_mismatch", "chat_sync_login_required", "chat_sync_session_changed",
                          "chat_sync_session_unavailable", "chat_sync_unavailable", "chat_sync_retry_later",
                          "chat_sync_service_rejected", "chat_sync_writer_recovery_required"}:
                def remember():
                    with self.writer.locked():
                        self.writer.require_epoch(tuple(request["epoch"]))
                        self.state.record_new_role_error(uid, request["requestId"], code)
                try:
                    await asyncio.to_thread(remember)
                except (ValueError, OSError, sqlite3.Error):
                    # A diagnostic must not replace the original failure or bypass writer recovery.
                    pass
            if manual:
                raise
        except (AutomaticRoleInactive, OSError, sqlite3.Error):
            # The protected intent and its durable retry deadline remain; local chat stays available.
            if manual:
                raise
        finally:
            if preview is not None:
                # A failed combined grant owns only this background preview.
                # Session loss may cancel it, but cannot revoke a manual preview
                # or a history snapshot whose approval already committed.
                try:
                    await asyncio.to_thread(self._cancel_history_preview, uid, preview["taskId"], tuple(request["epoch"]))
                except (ValueError, OSError, sqlite3.Error):
                    # Writer recovery still owns any ambiguous local commit.
                    pass
            self.wake_scheduler()

    async def enable(self, character_uid: str, display_name: str, *, expected_writer_epoch=None, before_send=None) -> dict:
        """Register the persisted desktop UID, then establish explicit consent."""
        if not isinstance(display_name, str) or not display_name:
            raise ValueError("invalid_chat_sync_display_name")
        display_name.encode("utf-8")
        writer_epoch = expected_writer_epoch or await asyncio.to_thread(self.writer.epoch)
        await asyncio.to_thread(self.writer.require_epoch, writer_epoch)
        async with self._lock:
            token, fingerprint = await self._session()
            owner = await self._identity(token, before_send=before_send)
            binding = await asyncio.to_thread(self.state.binding, character_uid)
            if binding and binding['owner_id'] != owner:
                raise ValueError('chat_sync_account_mismatch')
            await self._same_session(fingerprint)
            await asyncio.to_thread(self.writer.require_epoch, writer_epoch)
            prepared = await asyncio.to_thread(self.state.prepare_binding, ChatDomain(owner, character_uid))
            base = f"/prototype/chat-sync/characters/{character_uid}"
            registration = await self._request("PUT", base, token, body=_canonical({"displayName": display_name}), before_send=before_send)
            if (registration.keys() != {"ownerId", "characterSyncId", "displayName"}
                    or registration["ownerId"] != owner or registration["characterSyncId"] != character_uid
                    or not isinstance(registration["displayName"], str) or not registration["displayName"]
                    or len(registration["displayName"]) > 200 or len(registration["displayName"].encode("utf-8")) > 800):
                # A published profile owns its approved cloud display name. A
                # rejected local rename must not break an existing chat binding.
                raise ValueError("invalid_chat_sync_registration")
            await self._same_session(fingerprint)
            settings = await asyncio.to_thread(self._service_settings)
            receipt = await self._request("POST", base + "/bindings", token, body=_canonical({
                "deviceId": settings["device_id"], "requestId": prepared["request_id"],
            }), before_send=before_send)
            await self._same_session(fingerprint)
            expected = {"ownerId": owner, "characterSyncId": character_uid, "deviceId": settings["device_id"], "bindingId": receipt.get("bindingId")}
            if receipt != expected:
                raise ValueError("invalid_chat_sync_binding")
            if before_send is not None:
                await before_send()
            binding = await asyncio.to_thread(self._activate, prepared, receipt.get("bindingId"), fingerprint, writer_epoch)
            # Trusted deletions saved before first binding enter the existing
            # pure-marker lane without opting their original bodies into sync.
            await self._recover_originals()
            self.wake_scheduler()
            return binding

    def _activate(self, prepared: dict, binding_id: str, fingerprint: str, writer_epoch) -> dict:
        """Order consent changes with the same gate as local response commits."""
        with self.session_commit_guard(fingerprint), self._commit_lock, self.writer.locked():
            self.writer.check()
            if self._closing:
                raise ValueError("chat_sync_closing")
            self.writer.require_epoch(writer_epoch)
            return self.state.activate(prepared, binding_id)

    async def pause(self, character_uid: str) -> None:
        """Pause immediately without revoking the preserved account association."""
        # Do not wait for a slow sync network call before invalidating its generation.
        await asyncio.to_thread(self._pause, character_uid)
        self.wake_scheduler()

    def _pause(self, character_uid: str) -> None:
        """Complete pause before any later worker can accept an old generation."""
        with self._commit_lock, self.writer.locked():
            self.state.pause(character_uid)

    def _commit_bound(self, binding: dict, operation, *args, fingerprint: str, **kwargs) -> None:
        """Check the fixed response snapshot and commit under one short local gate."""
        with self.session_commit_guard(fingerprint), self._commit_lock, self.writer.locked():
            self.writer.check()
            if self._closing:
                raise ValueError("chat_sync_closing")
            current = self.state.binding(binding["character_uid"])
            if current != binding or not current["enabled"]:
                raise ValueError("chat_sync_binding_changed")
            if operation == self.ledger.apply_changes:
                self._recover_download_deletions(binding, args[1])
            result = operation(*args, **kwargs)
        # Writer witnesses and the local transaction have both finished here.
        # Optional inspection must never convert an accepted commit into failure.
        if self.trace.enabled:
            try:
                at = self.trace.instant()
                if operation == self.ledger.apply_changes:
                    page, domain = args[1], args[0]
                    ids = list(dict.fromkeys(decode_chat_original(raw.encode("utf-8"))["messageId"] for raw in page["originals"]))
                    if ids:
                        with closing(self.ledger._connect()) as conn:
                            placeholders = ",".join("?" for _ in ids)
                            accepted = conn.execute(
                                "SELECT message_id FROM chat_downloaded WHERE domain=? AND message_id IN (" + placeholders + ") "
                                "AND NOT EXISTS (SELECT 1 FROM chat_tombstones t WHERE t.domain=chat_downloaded.domain "
                                "AND t.message_id=chat_downloaded.message_id)", (domain.key(), *ids),
                            ).fetchall()
                        for row in accepted:
                            self.trace.emit("download_committed", at=at, character_uid=binding["character_uid"],
                                            message_id=row[0], local_message_id=row[0], cursor=page["cursor"])
                elif operation == self.ledger.ack_batch:
                    for identity in args[1]["messageIds"]:
                        self.trace.emit("upload_ack_committed", at=at, character_uid=binding["character_uid"], message_id=identity)
            except Exception:
                pass
        return result

    def _recover_download_deletions(self, binding, page):
        """Fence a late page with real markers even beyond the source recovery page."""
        from services.chat_sync.source import ChatOriginalSource, register_source_deletions
        path = self.state.path.parent / "sources.sqlite3"
        if not path.exists() or not isinstance(page, dict) or not isinstance(page.get("originals"), list):
            return
        # The transport requests at most 200 originals. Decode canonical IDs and
        # verify their domain before consulting source metadata; apply_changes
        # still performs every page, hash, cursor and immutable identity check.
        if len(page["originals"]) > 200:
            raise ValueError("invalid_chat_changes")
        identities = []
        for raw in page["originals"]:
            try:
                record = decode_chat_original(raw.encode("utf-8"))
                if record["ownerId"] != binding["owner_id"] or record["characterSyncId"] != binding["character_uid"]:
                    raise ValueError
            except (AttributeError, TypeError, ValueError, RecursionError):
                raise ValueError("invalid_chat_changes") from None
            identities.append(record["messageId"])
        source = ChatOriginalSource(path)
        deleted = source.deleted_ids(binding["character_uid"], list(dict.fromkeys(identities)))
        if deleted:
            register_source_deletions(self.ledger, source, ChatDomain(binding["owner_id"], binding["character_uid"]), deleted)

    async def _guard(self, binding: dict, fingerprint: str) -> None:
        """Recheck consent and session before sending or applying any response."""
        if self._closing:
            raise ValueError("chat_sync_closing")
        current = await asyncio.to_thread(self.state.binding, binding["character_uid"])
        if current != binding or not current["enabled"]:
            raise ValueError("chat_sync_binding_changed")
        await self._same_session(fingerprint)

    async def sync(self, character_uid: str, *, byte_budget: int, max_pull_pages: int) -> dict:
        """Request eligible work now while respecting device intervals and retries."""
        result = {"deletions": 0, "uploaded": 0, "pullPages": 0, "hasMore": False}
        failure = None
        channels = ('deletion', 'body', 'pull') + (('long_term',) if self.long_term_memory is not None else ())
        for channel in channels:
            # A failed marker attempt leaves body delivery for a later pass;
            # independent download can still merge already-published changes.
            if failure is not None and channel == "body":
                continue
            try:
                receipt = await self._channel_pass(character_uid, channel, manual=True,
                                                   byte_budget=byte_budget, max_pull_pages=max_pull_pages)
                if receipt is not None:
                    for key in ("deletions", "uploaded", "pullPages"):
                        result[key] += receipt[key]
                    result["hasMore"] = result["hasMore"] or receipt["hasMore"]
            except (ValueError, OSError) as exc:
                failure = failure or exc
                if str(exc) in {"chat_sync_account_mismatch", "chat_sync_login_required", "chat_sync_session_changed",
                                "chat_sync_not_enabled", "chat_sync_binding_changed"}:
                    break
        if failure is not None:
            raise failure
        binding = await asyncio.to_thread(self.state.binding, character_uid)
        if not binding or not binding["enabled"]:
            raise ValueError("chat_sync_not_enabled")
        remaining = await asyncio.to_thread(self._remaining, ChatDomain(binding["owner_id"], character_uid))
        return {**result, **remaining, "hasMore": result["hasMore"] or bool(
            remaining["pendingOriginals"] or remaining["pendingDeletions"] or remaining["hasSealedBatch"],
        )}

    async def _channel_pass(
        self, character_uid: str, channel: str, *, presence: str | None = None,
        manual: bool = False, byte_budget: int = _AUTO_BYTE_BUDGET, max_pull_pages: int = _AUTO_PULL_PAGES,
    ) -> dict | None:
        """Keep one correlation scope across lock admission, session and requests."""
        with self.trace.scope(span_id=self.trace.span_id(), character_uid=character_uid, operation=channel):
            self.trace.emit("channel_wait")
            try:
                result = await self._channel_pass_inner(character_uid, channel, presence=presence, manual=manual,
                                                        byte_budget=byte_budget, max_pull_pages=max_pull_pages)
            except BaseException as exc:
                self.trace.emit("channel_end", outcome="cancelled" if isinstance(exc, asyncio.CancelledError) else "error")
                raise
            self.trace.emit("channel_end", outcome="success")
            return result

    async def _channel_pass_inner(
        self, character_uid: str, channel: str, *, presence: str | None = None,
        manual: bool = False, byte_budget: int = _AUTO_BYTE_BUDGET, max_pull_pages: int = _AUTO_PULL_PAGES,
    ) -> dict | None:
        """Admit one independently retried channel before sealing or sending data."""
        if channel == 'long_term' and self.long_term_memory is not None:
            return await self._long_term_memory_pass(character_uid, presence=presence, manual=manual)
        if self.recent_memory is not None:
            return await self._recent_memory_pass(character_uid, channel, presence=presence, manual=manual)
        if type(byte_budget) is not int or byte_budget <= 0:
            raise ValueError("invalid_batch_byte_budget")
        if type(max_pull_pages) is not int or max_pull_pages <= 0:
            raise ValueError("invalid_pull_page_budget")
        lock = {"body": self._lock, "pull": self._pull_lock, "deletion": self._marker_flight_lock}[channel]
        async with lock:
            self.trace.emit("channel_enter")
            if self._closing:
                raise ValueError("chat_sync_closing")
            automatic_only = not manual and channel != "deletion"
            if automatic_only:
                await self._require_automatic_character(character_uid)
            if channel == "deletion":
                from services.chat_sync.recovery import approved_deletion_tasks
                for task_id in await asyncio.to_thread(approved_deletion_tasks, self, character_uid):
                    await self.approve_recovery_deletions(character_uid, task_id)
            if channel == "body":
                # Task registration is local and precedes the empty-queue gate:
                # deleting the first page cannot strand later approved pages.
                await self._advance_history(character_uid, presence=presence)
            await self._recover_originals()
            binding = await asyncio.to_thread(self.state.binding, character_uid)
            if not binding or not binding["enabled"] or not binding["binding_id"]:
                raise ValueError("chat_sync_not_enabled")
            domain = ChatDomain(binding["owner_id"], character_uid)
            queue = await asyncio.to_thread(self.ledger.queue_state, domain)
            if channel == "body" and not manual:
                now = self.scheduler.clock()
                eligible_at = body_eligible_at(queue, now)
                if eligible_at is not None and eligible_at > now and (
                    self._body_eligible_at is None or eligible_at < self._body_eligible_at
                ):
                    # Reuse this already-required queue snapshot. A new earlier
                    # deadline interrupts the current wait without rescanning.
                    self._body_eligible_at = eligible_at
                    self.wake_scheduler()
            if (channel == "body" and not queue["pendingOriginals"]) or (channel == "deletion" and not queue["pendingDeletions"]):
                return None
            if channel == "body" and queue["pendingDeletions"]:
                # Do not race a queued marker with body sealing. An independent
                # marker flight can acknowledge it without waiting on this lock.
                return None
            if not await asyncio.to_thread(self.scheduler.origin_ready):
                return None
            authenticated = None
            if presence is None:
                authenticated = await self._session()
                presence = authenticated[1]
            if automatic_only:
                await self._require_automatic_character(character_uid)
            if not await asyncio.to_thread(self.scheduler.claim, domain.key(), channel, binding["generation"], presence,
                                           queue=queue, manual=manual):
                return None
            try:
                authenticated = authenticated or await self._session()
                if authenticated[1] != presence:
                    raise ValueError("chat_sync_session_changed")
                _, request, commit = await self._bound_transport(character_uid, authenticated=authenticated, expected_binding=binding,
                    pull_deadline=asyncio.get_running_loop().time() + _PULL_NETWORK_SECONDS if channel == "pull" else None,
                    automatic_only=automatic_only,
                )
                client = PrototypeChatSyncClient(self.ledger, domain, request, commit=commit, deletion_lock=self._deletion_lock)
                receipt = await client.step(byte_budget=byte_budget, max_pull_pages=max_pull_pages,
                                            upload=channel == "body", pull=channel == "pull", send_deletions=channel == "deletion")
                await asyncio.to_thread(self.scheduler.success, domain.key(), channel)
                if channel == "pull" and receipt["hasMore"]:
                    # Yield at the page budget, then continue on the next owned
                    # sweep instead of waiting for a periodic fallback interval.
                    await commit(self.scheduler.request_check, domain.key(), "pull")
                    self.wake_scheduler()
                return receipt
            except (ValueError, OSError) as exc:
                await asyncio.to_thread(self.scheduler.failure, domain.key(), channel, binding["generation"], presence, exc)
                raise
            finally:
                # Even a failed later page may have committed tombstones. Repeat
                # source cleanup without recapturing a downloaded original.
                await self._recover_originals()

    async def _recent_memory_pass(self, uid, channel, *, presence=None, manual=False):
        """Use the established pull admission for the bounded recent projection."""
        if channel in {"deletion", "body"}:
            return None
        from services.chat_sync.recent_protocol import changes
        async with self._recent_lock:
            if not manual:
                await self._require_automatic_character(uid)
            binding = await asyncio.to_thread(self.state.binding, uid)
            if not binding or not binding["enabled"] or not binding["binding_id"]:
                raise ValueError("chat_sync_not_enabled")
            name = await self.recent_character_name(uid)
            if name is None:
                raise ValueError("chat_sync_character_not_found")
            domain = ChatDomain(binding["owner_id"], uid)

            def inspect():
                with self._commit_lock, self.writer.locked():
                    self.writer.check()
                    if self.state.binding(uid) != binding:
                        raise ValueError("chat_sync_binding_changed")
                    local = self.recent_memory.adapter.read(name)
                    state = self.recent_memory.state(domain.key())
                    excluded = set(state['excluded'])
                    change = changes(state["base"], [row for row in local["entries"] if row['id'] not in excluded])
                    return bool(state["pending"] or change["upserts"] or change["removedIds"])

            dirty = await asyncio.to_thread(inspect)
            if not dirty and self.profiles.adapter is not None and self.profiles.adapter.busy(name):
                return None
            if dirty:
                await asyncio.to_thread(self.scheduler.request_check, domain.key(), "pull")
            if not await asyncio.to_thread(self.scheduler.origin_ready):
                return None
            authenticated = await self._session()
            presence = presence or authenticated[1]
            if presence != authenticated[1]:
                raise ValueError("chat_sync_session_changed")
            if not await asyncio.to_thread(self.scheduler.claim, domain.key(), "pull", binding["generation"], presence, manual=manual):
                return None
            try:
                _, request, commit = await self._bound_transport(uid, authenticated=authenticated,
                    expected_binding=binding, automatic_only=not manual)
                can_apply = lambda: self.profiles.adapter is None or not self.profiles.adapter.busy(name)
                result = await self.recent_memory.sync(domain, name, request, commit, can_apply=can_apply)
                await asyncio.to_thread(self.scheduler.success, domain.key(), "pull")
                if result['hasMore']:
                    await commit(self.scheduler.request_check, domain.key(), 'pull')
                    self.wake_scheduler()
                if result["changed"] and self.history_changed:
                    await self._notify_history_changed(binding, authenticated[1])
                return result
            except (ValueError, OSError, sqlite3.Error) as error:
                await asyncio.to_thread(self.scheduler.failure, domain.key(), "pull", binding["generation"], presence, error)
                raise
            finally:
                # Recompute the persisted retry/poll deadline after this flight;
                # declined admissions do not wake again and cannot form a loop.
                self.wake_scheduler()

    async def _long_term_memory_pass(self, uid, *, presence=None, manual=False):
        async with self._long_term_lock:
            if not manual:
                await self._require_automatic_character(uid)
            binding = await asyncio.to_thread(self.state.binding, uid)
            if not binding or not binding['enabled'] or not binding['binding_id']:
                raise ValueError('chat_sync_not_enabled')
            name = await self.recent_character_name(uid)
            if name is None:
                raise ValueError('chat_sync_character_not_found')
            epoch = await asyncio.to_thread(self.writer.epoch)
            settings = await asyncio.to_thread(self._service_settings)
            grant = {**binding, 'service_url': settings['service_url']}
            domain = ChatDomain(binding['owner_id'], uid)
            local = await asyncio.to_thread(self.long_term_memory.adapter.read_local, grant, epoch)
            state = await asyncio.to_thread(self.long_term_memory.state, domain.key())
            dirty = bool(local and not local['operationId'].startswith('download:') and local['operationId'] != state['acknowledged'])
            can_apply = lambda: self.profiles.adapter is None or not self.profiles.adapter.busy(name)
            if can_apply():
                applied = await self.long_term_memory.adapter.flush(uid)
                if applied and self.history_changed and presence:
                    await self._notify_history_changed(binding, presence)
            if not dirty and not can_apply():
                return None
            if dirty:
                await asyncio.to_thread(self.scheduler.request_check, domain.key(), 'long_term')
            if not await asyncio.to_thread(self.scheduler.origin_ready):
                return None
            if presence and not await asyncio.to_thread(self.scheduler.ready, domain.key(), 'long_term',
                                                        binding['generation'], presence, manual=manual):
                return None
            authenticated = await self._session()
            presence = presence or authenticated[1]
            if presence != authenticated[1]:
                raise ValueError('chat_sync_session_changed')
            if not await asyncio.to_thread(self.scheduler.claim, domain.key(), 'long_term', binding['generation'], presence, manual=manual):
                return None
            try:
                _, request, commit = await self._bound_transport(uid, authenticated=authenticated,
                    expected_binding=binding, automatic_only=not manual)
                result = await self.long_term_memory.sync(domain, grant, epoch, request, commit, can_apply=can_apply,
                                                          session_fingerprint=presence)
                await asyncio.to_thread(self.scheduler.success, domain.key(), 'long_term')
                if result['changed'] and can_apply():
                    applied = await self.long_term_memory.adapter.flush(uid)
                    if applied and self.history_changed:
                        await self._notify_history_changed(binding, presence)
                if result['hasMore']:
                    await commit(self.scheduler.request_check, domain.key(), 'long_term')
                return {'uploaded': 0, 'deletions': 0, 'pullPages': 0, 'hasMore': result['hasMore']}
            except (ValueError, OSError, sqlite3.Error) as error:
                await asyncio.to_thread(self.scheduler.failure, domain.key(), 'long_term', binding['generation'], presence, error)
                raise
            finally:
                self.wake_scheduler()

    async def _recover_originals(self) -> None:
        """Recover raw WAL gaps and repeat tombstone cleanup after interrupted deletes."""
        await asyncio.to_thread(self.writer.check)
        if self.recent_memory is not None:
            return
        from services.chat_sync.source import recover_sources
        await recover_sources(self)
        for binding in await asyncio.to_thread(self.state.bindings):
            await asyncio.to_thread(
                self.ledger.purge_deleted_originals, ChatDomain(binding["owner_id"], binding["character_uid"]),
            )

    async def _bound_transport(self, character_uid: str, *, authenticated=None, expected_binding=None, recovery=False, pull_deadline=None, automatic_only=False):
        """Pin one verified account, consent generation and bearer for an operation."""
        binding = await asyncio.to_thread(self.state.binding, character_uid)
        if not recovery:
            from services.chat_sync.recovery import role_pending
            if await asyncio.to_thread(role_pending, self, character_uid):
                raise ValueError("chat_sync_writer_recovery_required")
        if self._closing:
            raise ValueError("chat_sync_closing")
        if expected_binding is not None and binding != expected_binding:
            # A paused and explicitly re-enabled role owns a new generation.
            # An old claim must never borrow that later grant to resume itself.
            raise ValueError("chat_sync_binding_changed")
        if not binding or not binding["enabled"] or not binding["binding_id"]:
            raise ValueError("chat_sync_not_enabled")
        token, fingerprint = authenticated or await self._session()
        async def download_io(operation, *args, **kwargs):
            """Bound only download network waits, never an already-started commit."""
            if pull_deadline is None:
                return await operation(*args, **kwargs)
            remaining = pull_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ValueError("chat_sync_pull_timeout")
            deadline = asyncio.timeout(remaining)
            try:
                async with deadline:
                    return await operation(*args, **kwargs)
            except TimeoutError:
                if not deadline.expired():
                    raise
                raise ValueError("chat_sync_pull_timeout") from None

        async def before_send():
            await self._require_automatic_character(character_uid)

        automatic_check = before_send if automatic_only else None
        if await download_io(self._identity, token, before_send=automatic_check) != binding["owner_id"]:
            raise ValueError("chat_sync_account_mismatch")

        async def guard():
            """Fence ledger acknowledgements and downloaded pages as well as HTTP."""
            await self._guard(binding, fingerprint)

        async def request(method, path, body):
            """Use the same bound owner/token for the complete fixed operation."""
            await guard()
            receipt = await download_io(self._request, method, path, token, body=body, binding_id=binding["binding_id"], before_send=automatic_check)
            await guard()
            return receipt

        async def commit(operation, *args, **kwargs):
            """Check the session immediately before a generation-fenced worker."""
            await self._same_session(fingerprint)
            result = await asyncio.to_thread(self._commit_bound, binding, operation, *args, fingerprint=fingerprint, **kwargs)
            if (operation == self.ledger.apply_changes and args[1]["cursor"] > kwargs["after"]
                    and self.history_changed and not self._closing):
                # Only accepted page commits hint local windows. Cloud socket
                # hints never enter this path or advance display progress.
                task = asyncio.create_task(self._notify_history_changed(binding, fingerprint))
                self._track(task)
            return result

        return ChatDomain(binding["owner_id"], character_uid), request, commit

    async def _notify_history_changed(self, binding, fingerprint):
        """Keep optional window I/O outside page commits and own it until shutdown."""
        span = self.trace.span_id()
        self.trace.emit("history_notify_begin", span_id=span, character_uid=binding["character_uid"])
        try:
            await self._guard(binding, fingerprint)
            await asyncio.wait_for(self.history_changed(binding["character_uid"]), timeout=1)
        except asyncio.CancelledError:
            self.trace.emit("history_notify_end", span_id=span, character_uid=binding["character_uid"], outcome="cancelled")
            raise
        except Exception:
            # A closed/unavailable window does not turn a committed download
            # into a failed cloud operation. Its five-second fallback remains.
            self.trace.emit("history_notify_end", span_id=span, character_uid=binding["character_uid"], outcome="error")
        else:
            self.trace.emit("history_notify_end", span_id=span, character_uid=binding["character_uid"], outcome="success")

    async def flush_deletions(self, character_uid: str) -> int:
        """Request markers now without bypassing their persisted retry deadline."""
        receipt = await self._channel_pass(character_uid, "deletion", manual=True)
        return receipt["deletions"] if receipt else 0

    def start_deletion_flush(self, character_uid: str) -> None:
        """Attempt network deletion after local commit without delaying local hiding."""
        async def attempt():
            """Keep fixed deletion requests intact on expected offline/auth failures."""
            try:
                await self.flush_deletions(character_uid)
            except (ValueError, OSError, sqlite3.Error):
                # Pending counts stay visible; retry keeps the same bytes and ID.
                pass

        # The immediate marker lane shares admission with automatic retries;
        # repeated button presses cannot create extra accepted attempts.
        self._track(asyncio.create_task(attempt()))
        self.wake_scheduler()

    async def local_replies(self, character_uid: str) -> list[dict]:
        """Restore legacy records not yet enrolled in the shared chat history."""
        from services.chat_sync.source import local_replies
        return await local_replies(self, character_uid)

    async def _refresh_chat_history(self, uid):
        """Refresh optional display history and report whether a grant can pin its range."""
        if self.recent_memory is not None:
            return True
        if self.chat_history_provider is None:
            return True
        from services.chat_sync.source import ChatOriginalSource, pending_original_ids
        from services.chat_sync.writer import WriterQuarantined
        epoch = await asyncio.to_thread(self.writer.epoch)
        try:
            records = await self.chat_history_provider(uid)
        except WriterQuarantined:
            raise
        except (ValueError, OSError, sqlite3.Error) as error:
            logging.getLogger(__name__).warning("Chat history archive unavailable (%s)", type(error).__name__)
            return False
        pending = pending_original_ids()
        def commit():
            with self._commit_lock, self.writer.locked():
                self.writer.require_epoch(epoch)
                source = ChatOriginalSource(self.state.path.parent / "sources.sqlite3")
                known = source.stored_ids(uid)
                binding = self.state.binding(uid)
                hidden = set(self.ledger.deleted_message_ids(ChatDomain(binding["owner_id"], uid))) if binding else set()
                batch = []
                invalid = 0
                for record in records:
                    identity = record["messageId"]
                    # Memory caching can finish before the producer's late-text grace.
                    # Its active original owns these bytes and the existing live upload grant.
                    if identity in known or identity in hidden or identity in pending:
                        continue
                    try:
                        raw = ChatOriginal(record["role"], record["text"], record["originalTime"], record["timezone"],
                                           record["conversationId"], record["replyToMessageId"])
                    except ValueError:
                        invalid += 1
                        continue
                    origin = record["origin"] if record["role"] == "assistant" and record["origin"] in ("agent", "tool") else None
                    batch.append((identity, raw, origin))
                if invalid:
                    logging.getLogger(__name__).warning("Chat history archive ignored invalid originals=%d", invalid)
                refreshed = True
                if batch:
                    try:
                        source.commit_history(uid, batch)
                    except WriterQuarantined:
                        raise
                    except ValueError as error:
                        logging.getLogger(__name__).warning("Chat history archive import unavailable (%s)", type(error).__name__)
                        refreshed = False
                source.include_local_replies(uid, hidden=hidden)
                return refreshed
        return await asyncio.to_thread(commit)

    async def history(self, character_uid: str) -> list[dict]:
        """Read stored originals without feeding cache, models or reactions."""
        if self.recent_memory is not None:
            name = await self.recent_character_name(character_uid)
            if name is None:
                return []
            def read_recent_history():
                with self._commit_lock, self.writer.locked():
                    self.writer.check()
                    return self.recent_memory.adapter.read(name)
            local = await asyncio.to_thread(read_recent_history)
            return [{"messageId": row["id"], "characterSyncId": character_uid, "role": row["role"], "text": row["text"],
                     "originalTime": row["originalTime"], "originSeq": None, "originStreamId": None,
                     "conversationId": None, "replyToMessageId": None}
                    for row in local["entries"] if row["role"] != "memo"]
        from services.chat_sync.writer import WriterQuarantined
        try:
            await self._refresh_chat_history(character_uid)
            await self._recover_originals()
            binding = await asyncio.to_thread(self.state.binding, character_uid)
            if not binding and self.chat_history_provider is None:
                return []
            domain = ChatDomain(binding["owner_id"], character_uid) if binding else None
            payloads = await asyncio.to_thread(self.ledger.visible_originals, domain) if domain else []
            result = {record["messageId"]: record for record in map(decode_chat_original, payloads)}
            if self.chat_history_provider is not None:
                from services.chat_sync.source import ChatOriginalSource
                source = await asyncio.to_thread(ChatOriginalSource, self.state.path.parent / "sources.sqlite3")
                local = await asyncio.to_thread(source.local_history, character_uid, binding["owner_id"] if binding else None)
                hidden = set(await asyncio.to_thread(self.ledger.deleted_message_ids, domain)) if domain else set()
                for record in local:
                    if record["messageId"] not in hidden:
                        result.setdefault(record["messageId"], record)
            return list(result.values())
        except WriterQuarantined:
            if not await asyncio.to_thread(self.writer.local_visibility_trusted):
                # On first upgrade/restart, copied authority is unproven. Keep
                # underlying bytes but hide managed history until cloud fences.
                return []
            return await asyncio.to_thread(self._trusted_local_history, character_uid)

    def _trusted_local_history(self, uid):
        """A witness-only outage cannot erase this live process's existing chat view."""
        bindings = self._quarantined_status()["bindings"]
        binding = next((item for item in bindings if item["character_uid"] == uid), None)
        if binding is None:
            return []
        domain = ChatDomain(binding["owner_id"], uid)
        with closing(sqlite3.connect(self.ledger.path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            rows = conn.execute("SELECT message_id,payload FROM chat_originals WHERE domain=? UNION "
                                "SELECT message_id,payload FROM chat_downloaded WHERE domain=?", (domain.key(), domain.key())).fetchall()
            hidden = {row[0] for row in conn.execute("SELECT message_id FROM chat_tombstones WHERE domain=?", (domain.key(),))}
        outage = self.state.path.parent / "outage.sqlite3"
        if outage.exists():
            with closing(sqlite3.connect(outage.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='outage_deletions'").fetchone():
                    hidden.update(row[0] for row in conn.execute("SELECT message_id FROM outage_deletions WHERE uid=?", (uid,)))
        return [decode_chat_original(payload) for identity, payload in rows if identity not in hidden]

    async def capture_original(
        self, character_uid: str, source: SourceLocator, original: ChatOriginal,
        *, message_id: str | None = None,
    ) -> dict | None:
        """Keep verified new originals under the persisted owner, even while paused.

        Source-side recovery must retain records until this idempotent capture
        succeeds. No bearer lookup or network request participates in saving.
        """
        binding = await asyncio.to_thread(self.state.binding, character_uid)
        if binding is None:
            return None
        record = await asyncio.to_thread(
            self.ledger.capture, ChatDomain(binding["owner_id"], character_uid), source, original,
            message_id=message_id,
        )
        self.wake_scheduler()
        return record

    async def delete(self, character_uid: str, message_ids: list[str], *, expected_binding=...) -> dict:
        """Durably hide exact IDs and queue deletion even while network is unavailable."""
        from services.chat_sync.writer import WriterQuarantined
        from services.chat_sync.recovery import save_outage_deletions
        try:
            binding = await asyncio.to_thread(self.state.binding, character_uid)
            await self._recover_originals()
            deletions = await asyncio.to_thread(self._delete_sources, character_uid, binding, message_ids,
                                                expected_binding=expected_binding)
        except WriterQuarantined:
            def save_reviewed_outage():
                with self._commit_lock:
                    if expected_binding is not ...:
                        if not self.writer.local_visibility_trusted():
                            raise ValueError('chat_sync_binding_changed')
                        current = next((item for item in self._quarantined_status()['bindings']
                                        if item['character_uid'] == character_uid), None)
                        fence = None if current is None else {key: current[key] for key in ('owner_id', 'binding_id', 'generation')}
                        if fence != expected_binding:
                            raise ValueError('chat_sync_binding_changed')
                    save_outage_deletions(self.state.path.parent, character_uid, message_ids)
            await asyncio.to_thread(save_reviewed_outage)
            return {"deletionIds": [], "messageIds": list(message_ids), "pending": False, "recoveryRequired": True}
        self.wake_scheduler()
        return {"deletionIds": [item.deletion_id for item in deletions], "messageIds": list(message_ids), "pending": bool(deletions)}

    def _delete_sources(self, character_uid, binding, message_ids, *, expected_binding=...):
        """Serialize local deletion with source-page transfer and preserve crash fences."""
        from services.chat_sync.source import ChatOriginalSource
        from services.chat_sync.ledger import _message_ids
        _message_ids(message_ids)
        with self._commit_lock:
            if expected_binding is not ...:
                # Memory Browser's Save may await recovery while a role is
                # rebound. Pin its reviewed owner/generation at the actual
                # marker commit, under the same gate used by activation/pause.
                current = self.state.binding(character_uid)
                fence = None if current is None else {
                    key: current[key] for key in ('owner_id', 'binding_id', 'generation')
                }
                if fence != expected_binding:
                    raise ValueError('chat_sync_binding_changed')
                binding = current
            domain = ChatDomain(binding["owner_id"], character_uid) if binding else None
            path = self.state.path.parent / "sources.sqlite3"
            source = ChatOriginalSource(path) if path.exists() else None
            deletions = self.ledger.queue_deletions(domain, message_ids) if domain else []
            if source:
                source.purge(binding["owner_id"] if binding else None, character_uid, message_ids)
            # Ledger tombstones commit first. Interrupted source cleanup is
            # repeated before recovery, so a late page cannot resurrect raw text.
            if domain:
                self.ledger.purge_deleted_originals(domain)
            return deletions

    async def clear(self, character_uid: str) -> dict:
        """Clear known sync originals and separately erase this role's local replies."""
        from services.chat_sync.source import clear_local_replies, flush_terminated_originals, known_source_ids, local_reply_ids
        from services.chat_sync.writer import WriterQuarantined
        from services.chat_sync.recovery import quarantined_local_ids
        # A reply that just finished may still wait out its late-text grace;
        # save it first so the fixed snapshot below includes it.
        await flush_terminated_originals(character_uid)
        local_ids = await local_reply_ids(self, character_uid)
        try:
            originals = await self.history(character_uid)
            ids = list(dict.fromkeys(
                [item["messageId"] for item in originals] + await known_source_ids(self, character_uid),
            ))
        except WriterQuarantined:
            ids = await asyncio.to_thread(quarantined_local_ids, self, character_uid)
        receipt = await self.delete(character_uid, ids) if ids else {"deletionIds": [], "messageIds": [], "pending": False}
        receipt["localMessageIds"] = await clear_local_replies(self, character_uid, local_ids)
        return receipt


_runtimes: dict[Path, DesktopChatSyncRuntime] = {}
_runtime_lock = asyncio.Lock()
_scheduler_bootstrap: asyncio.Task | None = None
_runtime_epoch = 0


def _finish_scheduler_bootstrap(done: asyncio.Task) -> None:
    """Observe startup completion without scheduling work on its owning loop."""
    global _scheduler_bootstrap
    # A delayed callback from an earlier start must not discard its replacement.
    if _scheduler_bootstrap is done:
        _scheduler_bootstrap = None
    if not done.cancelled() and done.exception() is not None:
        print("[chat-sync] scheduler_start_unavailable")


async def get_desktop_chat_sync_runtime(config_manager=None) -> DesktopChatSyncRuntime:
    """Resolve ConfigManager's current root lazily, outside Steam-managed subtrees."""
    if config_manager is None:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
    directory = Path(config_manager.app_docs_dir) / "chat_sync"
    async with _runtime_lock:
        runtime = _runtimes.get(directory)
        if runtime is None:
            async def session_provider():
                """Reuse OAuth refresh and its exact verified snapshot on demand."""
                try:
                    status = _host.resolve_saved_oauth_status()
                except _host.CapabilityNotInstalled:
                    # Retry later; a missing resolver is not a logout.
                    raise ValueError("chat_sync_session_unavailable") from None
                return await status

            async def history_changed(character_uid):
                """Hint only the current local socket for this exact persisted UID."""
                from utils.config_manager.reserved_schema import get_character_uid
                data = await config_manager.aload_characters()
                entries = data.get("猫娘", {}) if isinstance(data, dict) else {}
                for name, profile in entries.items():
                    if isinstance(profile, dict) and get_character_uid(profile) == character_uid:
                        manager = _host.get_session_manager().get(name)
                        socket = getattr(manager, "websocket", None)
                        if socket is not None:
                            await socket.send_json({"type": "chat_sync_history_changed", "characterSyncId": character_uid,
                                                    **({"memoryEditedName": name} if runtime.recent_memory is not None else {})})
                        return

            async def current_character_uid():
                """Read the existing local selection without creating or approving a role."""
                from utils.config_manager.reserved_schema import get_character_uid
                data = await config_manager.aload_characters()
                if not isinstance(data, dict):
                    return None
                entries = data.get("猫娘", {})
                profile = entries.get(data.get("当前猫娘")) if isinstance(entries, dict) else None
                return get_character_uid(profile) if isinstance(profile, dict) else None

            async def recent_character_name(uid):
                """Resolve saved identity without reading the complete chat archive."""
                from utils.config_manager.reserved_schema import get_character_uid
                data = await config_manager.aload_characters()
                for name, profile in data.get("猫娘", {}).items():
                    if isinstance(profile, dict) and get_character_uid(profile) == uid:
                        return name
                return None

            epoch = _runtime_epoch
            def construct():
                from memory.recent_sync import RecentMemoryAdapter
                from memory.long_term_sync import DesktopLongTermAdapter

                return DesktopChatSyncRuntime(directory, session_provider,
                    witness_path=Path(config_manager.local_state_dir) / "chat_sync_writer.json",
                    fixed_service_url=_BUILTIN_SERVICE_URL,
                    history_changed=history_changed, current_character_uid_provider=current_character_uid,
                    recent_memory_adapter=RecentMemoryAdapter(config_manager),
                    long_term_memory_adapter=DesktopLongTermAdapter(config_manager),
                    recent_character_name=recent_character_name)
            # Constructor SQLite handles close synchronously and default HTTP is
            # lazy. A cancelled thread result owns no pool, task or open database
            # handle, so shutdown need not wait for optional local construction.
            runtime = await asyncio.to_thread(construct)
            if epoch != _runtime_epoch:
                await runtime.close()
                raise ValueError("chat_sync_closing")
            _runtimes[directory] = runtime
            # Attach only the storage-selected production manager. Synthetic
            # runtimes explicitly inject their own isolated raw profile adapter.
            runtime.profiles.adapter = DesktopProfileAdapter(config_manager)
        return runtime


async def close_desktop_chat_sync_runtimes() -> None:
    """Close only adapters created in this process, without deleting local state."""
    global _scheduler_bootstrap, _runtime_epoch
    # Invalidate in-progress local construction before awaiting cancellation.
    # Registry snapshots below contain no await, so another factory cannot insert
    # between copying and clearing it on this event loop.
    _runtime_epoch += 1
    bootstrap, _scheduler_bootstrap = _scheduler_bootstrap, None
    if bootstrap is not None:
        if bootstrap.done():
            # Even gather(done_task) registers callbacks on a possibly closed loop.
            _finish_scheduler_bootstrap(bootstrap)
        else:
            bootstrap.cancel()
            await asyncio.gather(bootstrap, return_exceptions=True)
    runtimes = list(_runtimes.values())
    _runtimes.clear()
    for runtime in runtimes:
        await runtime.close()


async def start_desktop_chat_sync_scheduler(config_manager=None) -> None:
    """Resolve the final storage root only after the main-server startup gate."""
    runtime = await get_desktop_chat_sync_runtime(config_manager)
    runtime.start_scheduler()


def schedule_desktop_chat_sync_start(config_manager=None) -> None:
    """Defer optional SQLite/client setup so main-server boot never waits on sync."""
    global _scheduler_bootstrap
    if _scheduler_bootstrap is None or _scheduler_bootstrap.done():
        async def bootstrap():
            """Keep optional setup failures out of the normal chat startup path."""
            try:
                await start_desktop_chat_sync_scheduler(config_manager)
            except (ValueError, OSError, sqlite3.Error):
                # Fixed diagnostic only: local paths and login state stay private.
                print("[chat-sync] scheduler_start_unavailable")
        _scheduler_bootstrap = asyncio.create_task(bootstrap())
        _scheduler_bootstrap.add_done_callback(_finish_scheduler_bootstrap)
