# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Server-authoritative speaker-trust pool, keyed by platform-neutral account id.

FOUR RULES THIS MODULE EXISTS TO ENFORCE
========================================

1. **The only writer is a memory_server handler.** Plugins, the dashboard and
   the migration path all go through HTTP; nothing else may touch the file.
   Every write is one synchronous critical section handed to a single
   ``asyncio.to_thread``.

2. **The pool must be a FLAT file at the memory_dir root.** A subdirectory such
   as ``memory/_trust/`` is silently ``rmtree``-d by the cloudsave import
   cleanup (``utils/cloudsave_runtime/operations.py``: ``delete_dir_targets``
   takes every child dir not in the imported-character set), while
   ``delete_file_targets`` only ever names ``memory/<character>/<whitelisted
   leaf>``. A root-level flat file dodges both. Precedent:
   ``app/memory_server/gates.py``'s ``idle_maintenance_state.json``. Any future
   sharding must stay flat: ``speaker_trust.<n>.json``, never a directory.

3. **The path carries no ``lanlan_name`` — cross-character sharing is the
   product decision, not a bug.** Trust answers "is this person reliable", which
   is a property of the person, not of their relationship with one character.
   The write happens inside a per-character route, and the target file
   deliberately is not per-character.

4. **The entity layer decides whether an event is PRODUCED, never whose ledger
   it lands in.** ``trust_event_id`` bakes the target account into its SHA256
   and the plugin's activity ids bake the source account in, so an id aimed at
   A is structurally impossible inside B's ring. Unioning rings across an
   entity buys zero deduplication and destroys ``unbind``'s ability to know
   what an account takes with it. Entity resolution is allowed to change which
   signals get generated (the self-attestation ban); once generated, idempotency
   and storage are 100% per-account.

EXPLICIT KILL LIST — never derive an entity edge from any of these
=================================================================
1. Display name / nickname / group card. The two QQ transports read them in
   OPPOSITE priority order, the card is group-scoped, and users can change them.
2. Bootstrap elevation. Its trigger is "the user list is empty", a configuration
   state, not proof of identity.
3. Temporal adjacency — whoever speaks first after a transport switch.
4. Any edit distance / similarity / shape heuristic.
5. ``channel`` itself. It is an observed attribute, not identity evidence.

The number of automatic bind code paths is zero, and that is a structural
property: ``_bind_locked`` / ``_merge_entities_locked`` are reachable only from
the four human-triggered identity endpoints. There is a guard test asserting
that call-site count.

BARRIERS ARE ACCOUNT-LOCAL WHILE ``trust_inputs`` IS ENTITY-GLOBAL
=================================================================
This asymmetry is deliberate and a reader will reasonably assume otherwise.
``barrier_pending(platform(account_id))`` only inspects the requesting side. An
entity holding a cleared ``qq:``  account and a pending ``bili:`` one resolves
normally on QQ (bounded under-count until the bili ledger imports; fail-closed,
self-heals when the barrier opens) and abstains on bili. There is no
double-count path, because the pending platform's write side is skipped too.
The alternative reading — "any pending platform makes the whole person abstain"
— would make onboarding a new platform instantly strip that person's trust
everywhere they were already established.

MINIMAL INTEGRATION SURFACE FOR A NEW PLATFORM
==============================================
Per ``scoped_history`` request, a platform plugin supplies only:

1. a ``platform`` token matching ``[A-Za-z0-9_.-]+`` (lowercased server-side);
2. ``speaker_id = f"{platform}:{actor}"`` where actor matches
   ``[A-Za-z0-9_.:@-]+``, total length <= 96, and actor is a **stable
   platform-side id, never a nickname** (a ``uid if uid > 0 else uname``
   fallback is judged ``None`` by ``stable_speaker_id`` and silently dropped);
3. a base source, exactly one of ``speaker_tier`` (four-tier platforms) or
   ``speaker_base_trust`` (0..1, clamped server-side, for tier-less platforms);
4. ``speaker_is_owner``, derivable only from an explicit stable-id binding with
   ``tier == "admin"`` — the server 422s any other combination;
5. ``speaker_label`` / ``display_name`` (cosmetic);
6. optionally ``speaker_activity_events``: ``[{"id", "count"}]`` with ids that
   stay stable across retries and restarts.

It supplies NO storage, ledger, idempotency ring, writer lock, transaction
lock, event application logic, response handling, or migration.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence

from utils.config_manager import get_config_manager
# Module-level import on purpose (mirrors gates.py): this runs inside the
# critical section, and a function-local import would lock the import
# machinery along with the pool.
from utils.file_utils import atomic_write_json, read_json_tolerating_replace

from memory.identity import (
    account_platform,
    activity_count_cap,
    apply_signal_event,
    dedup_keep_order,
    derive_entity_id,
    effective_trust,
    merge_order_key,
    normalize_account_id,
    normalize_account_record,
    normalize_channel,
    normalize_entity_record,
    normalize_legacy_profile,
    record_activity,
)

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")

_config_manager = get_config_manager()

POOL_VERSION = 2

_ENTITY_RESOLVE_MAX_DEPTH = 8


class TrustIdentityError(Exception):
    """A human-triggered identity operation was rejected. Carries an HTTP status."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seeded_barriers() -> dict[str, dict[str, Any]]:
    from config import SPEAKER_TRUST_LEGACY_BARRIERS

    now = _now_iso()
    return {
        str(platform).strip().lower(): {
            "source": str(source),
            "status": "pending",
            "seeded_at": now,
            "cleared_at": None,
            "chunks": 0,
            "accounts": 0,
            "skipped": 0,
        }
        for platform, source in (SPEAKER_TRUST_LEGACY_BARRIERS or {}).items()
        if str(platform).strip()
    }


def _empty_pool() -> dict[str, Any]:
    """A pool that abstains from everything.

    The barriers are seeded PENDING here, not only in ``_seed_pool``: this is
    the value ``_POOL`` holds before startup finishes and after a load failure,
    and in both states the correct behaviour is to abstain rather than to stamp
    facts from an empty ledger.
    """
    return {
        "version": POOL_VERSION,
        "updated_at": None,
        "legacy_barriers": _seeded_barriers(),
        "account_index": {},
        "entities": {},
        "forgotten": {},
        # Diagnostics only. Never a key, never a predicate input.
        "channel_observations": {},
        "channel_collisions": {},
        # Human-written only; code NEVER infers a value here. Showing "unknown"
        # is the honest state until someone runs the forensics.
        "platform_identity_scope": {},
    }


def _seed_pool() -> dict[str, Any]:
    pool = _empty_pool()
    pool["updated_at"] = _now_iso()
    return pool


# ── module-level single-writer state ────────────────────────────────────────
# Deliberately NOT hung off the runtime globals: ``reload_memory_components``
# rebinds every runtime component, and keeping the pool out of that graph is
# what makes a ``_share_trust_write_state`` shim unnecessary.
_POOL: dict[str, Any] = _empty_pool()
_load_failed: bool = False

# threading.Lock, not asyncio.Lock — the same three arguments as gates.py:
# (1) it is this repo's established disk-write idiom (``memory/event_log.py``);
# (2) a module-level asyncio.Lock binds to whichever loop first contends it,
#     and pytest here is asyncio_mode=auto with a function-scoped loop, so the
#     second contended test RuntimeErrors and leaves the lock stuck held;
# (3) threading.Lock can enclose ``json.dumps`` (atomic_write_json dumps first).
#
# Cancellation safety: the whole critical section runs inside ONE
# ``asyncio.to_thread``. Once handed off it cannot be cancelled — the awaiting
# coroutine may be cancelled but the thread still finishes and still releases
# the lock. Hence no shield / no second cancellation loop / no before-after
# rollback: there is no "hold a lock across await" anywhere in this module.
_pool_lock = threading.Lock()

# Mutators are SYNCHRONOUS on purpose: "await inside the lock" and "take another
# lock inside the lock" become syntactically unwritable. Deliberately not an
# RLock — a nested read-modify-write's inner persist would flush the outer's
# half-applied state to disk.
TrustMutator = Callable[["_Draft"], tuple[bool, Any]]


def pool_path() -> str:
    from config import SPEAKER_TRUST_POOL_FILENAME

    return os.path.join(
        str(_config_manager.memory_dir), SPEAKER_TRUST_POOL_FILENAME,
    )


# ── copy-on-write draft ─────────────────────────────────────────────────────
class _Draft:
    """A pool being edited. Every touched container is a fresh object.

    "A failed disk write must not fork memory from disk" only holds when every
    mutated container is newly allocated, so that discarding the draft discards
    every change. Untouched entity records may be shared by reference because
    the writer never mutates a published object in place.
    """

    __slots__ = ("pool", "_owned_entities")

    def __init__(self, published: dict[str, Any]) -> None:
        self.pool = {
            "version": POOL_VERSION,
            "updated_at": published.get("updated_at"),
            "legacy_barriers": {
                platform: dict(entry) if isinstance(entry, dict) else {}
                for platform, entry in (
                    published.get("legacy_barriers") or {}
                ).items()
            },
            "account_index": dict(published.get("account_index") or {}),
            "entities": dict(published.get("entities") or {}),
            "forgotten": {
                entity_id: dict(entry) if isinstance(entry, dict) else {}
                for entity_id, entry in (
                    published.get("forgotten") or {}
                ).items()
            },
            "channel_observations": {
                platform: {
                    channel: dict(stats) if isinstance(stats, dict) else {}
                    for channel, stats in (entry or {}).items()
                }
                for platform, entry in (
                    published.get("channel_observations") or {}
                ).items()
            },
            "channel_collisions": {
                account_id: dict(entry) if isinstance(entry, dict) else {}
                for account_id, entry in (
                    published.get("channel_collisions") or {}
                ).items()
            },
            "platform_identity_scope": {
                platform: dict(entry) if isinstance(entry, dict) else {}
                for platform, entry in (
                    published.get("platform_identity_scope") or {}
                ).items()
            },
        }
        self._owned_entities: set[str] = set()

    # -- entity/account access -------------------------------------------
    def entity(self, entity_id: str) -> dict[str, Any] | None:
        """Return a writable copy of one entity record, or ``None``."""
        record = self.pool["entities"].get(entity_id)
        if record is None:
            return None
        if entity_id in self._owned_entities:
            return record
        clone = dict(record)
        clone["accounts"] = {
            account_id: _clone_account(account)
            for account_id, account in (record.get("accounts") or {}).items()
        }
        if isinstance(record.get("canonical_accounts"), dict):
            clone["canonical_accounts"] = {
                platform: dict(entry)
                for platform, entry in record["canonical_accounts"].items()
            }
        if isinstance(record.get("superseded_canonicals"), list):
            clone["superseded_canonicals"] = list(
                record["superseded_canonicals"]
            )
        if isinstance(record.get("merged_accounts"), list):
            clone["merged_accounts"] = list(record["merged_accounts"])
        self.pool["entities"][entity_id] = clone
        self._owned_entities.add(entity_id)
        return clone

    def create_entity(self, entity_id: str, *, now: str) -> dict[str, Any]:
        record = {
            "entity_id": entity_id,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "accounts": {},
        }
        self.pool["entities"][entity_id] = record
        self._owned_entities.add(entity_id)
        return record

    def resolve_entity(self, entity_id: Any) -> str | None:
        return _resolve_entity_locked(self.pool, entity_id)

    def account(
        self, account_id: str, *, create: bool = False, now: str | None = None,
    ) -> dict[str, Any] | None:
        """Return a writable account record, optionally auto-vivifying it.

        Auto-vivify is safe on the signal axis: the target comes from a durable
        fact row the server itself wrote, not from attacker-controlled input.
        Same semantics as the pre-migration plugin's ``setdefault``.
        """
        entity_id = self.resolve_entity(
            self.pool["account_index"].get(account_id)
        )
        if entity_id is not None:
            entity = self.entity(entity_id)
            if entity is not None:
                existing = entity["accounts"].get(account_id)
                if existing is not None:
                    return existing
        if not create:
            return None
        stamp = now or _now_iso()
        entity_id = _seed_entity_for_account_locked(
            self.pool, account_id, now=stamp,
        )
        entity = self.entity(entity_id) or self.create_entity(
            entity_id, now=stamp,
        )
        record = normalize_account_record(account_id, {"bound_at": stamp})
        entity["accounts"][account_id] = record
        entity["updated_at"] = stamp
        self.pool["account_index"][account_id] = entity_id
        return record

    def entity_of(self, account_id: str) -> str | None:
        return self.resolve_entity(
            self.pool["account_index"].get(account_id)
        )

    def entity_message_count(self, account_id: str) -> int:
        entity_id = self.entity_of(account_id)
        if entity_id is None:
            return 0
        entity = self.pool["entities"].get(entity_id) or {}
        return sum(
            max(0, int((record or {}).get("message_count", 0) or 0))
            for record in (entity.get("accounts") or {}).values()
        )

    def barrier_pending(self, platform: str) -> bool:
        entry = self.pool["legacy_barriers"].get(str(platform or "").lower())
        return bool(entry) and entry.get("status") != "cleared"


def _clone_account(record: Any) -> dict[str, Any]:
    """Copy one account record including BOTH rings and the channel ledger."""
    if not isinstance(record, dict):
        return {}
    clone = dict(record)
    clone["processed_activity_events"] = list(
        record.get("processed_activity_events") or []
    )
    clone["processed_signal_events"] = list(
        record.get("processed_signal_events") or []
    )
    clone["channels_seen"] = {
        channel: dict(stats) if isinstance(stats, dict) else {}
        for channel, stats in (record.get("channels_seen") or {}).items()
    }
    if isinstance(record.get("legacy_import"), dict):
        clone["legacy_import"] = dict(record["legacy_import"])
    return clone


# ── entity resolution (pure over a pool dict) ───────────────────────────────
def _resolve_entity_locked(
    pool: dict[str, Any], entity_id: Any, *, depth: int = _ENTITY_RESOLVE_MAX_DEPTH,
) -> str | None:
    """Follow ``merged_into`` to the live entity. Too deep ⇒ refuse, never guess."""
    current = str(entity_id or "")
    if not current:
        return None
    entities = pool.get("entities") or {}
    for _ in range(depth):
        record = entities.get(current)
        if not isinstance(record, dict):
            return None
        if record.get("status") != "merged":
            return current
        nxt = str(record.get("merged_into") or "")
        if not nxt or nxt == current:
            return None
        current = nxt
    return None


def _seed_entity_for_account_locked(
    pool: dict[str, Any], account_id: str, *, now: str,
) -> str:
    """Find the first free deterministic seed id for one account."""
    entities = pool.get("entities") or {}
    forgotten = pool.get("forgotten") or {}
    generation = 0
    while True:
        candidate = derive_entity_id(account_id, generation)
        if candidate not in entities and candidate not in forgotten:
            return candidate
        generation += 1
        if generation > 4096:  # pragma: no cover - defensive
            raise TrustIdentityError(
                "unable to allocate an entity id for this account",
                status_code=500,
            )


# ── normalization / load ────────────────────────────────────────────────────
def _normalize_pool(data: Any) -> dict[str, Any]:
    """Rebuild a trustworthy pool from possibly hand-edited JSON.

    ``account_index`` on disk is written but NOT trusted: it is rebuilt from
    ``entities[*].accounts`` so a stale or hand-edited alias cannot make one
    account resolve to an entity that does not hold it.
    """
    pool = _empty_pool()
    if not isinstance(data, dict):
        return pool

    barriers = pool["legacy_barriers"]
    raw_barriers = data.get("legacy_barriers")
    if isinstance(raw_barriers, dict):
        for platform, entry in raw_barriers.items():
            key = str(platform or "").strip().lower()
            if not key or not isinstance(entry, dict):
                continue
            merged = dict(barriers.get(key) or {})
            merged.update({
                "source": str(entry.get("source") or merged.get("source") or ""),
                "status": (
                    "cleared" if entry.get("status") == "cleared" else "pending"
                ),
                "seeded_at": entry.get("seeded_at") or merged.get("seeded_at"),
                "cleared_at": entry.get("cleared_at"),
                "chunks": max(0, int(entry.get("chunks", 0) or 0)),
                "accounts": max(0, int(entry.get("accounts", 0) or 0)),
                "skipped": max(0, int(entry.get("skipped", 0) or 0)),
            })
            barriers[key] = merged

    raw_entities = data.get("entities")
    duplicates: list[str] = []
    if isinstance(raw_entities, dict):
        for raw_entity_id, raw_entity in raw_entities.items():
            entity_id = str(raw_entity_id or "").strip()
            if not entity_id:
                continue
            record = normalize_entity_record(entity_id, raw_entity)
            pool["entities"][entity_id] = record

    # Rebuild the index, folding any account that a hand edit duplicated across
    # two entities. Losing one copy silently would drop a ledger; the merge
    # rule mirrors the legacy-import merge (add adjustment, cap message_count,
    # union both rings in order).
    for entity_id, record in pool["entities"].items():
        if record.get("status") == "merged":
            continue
        for account_id, account in list((record.get("accounts") or {}).items()):
            owner = pool["account_index"].get(account_id)
            if owner is None:
                pool["account_index"][account_id] = entity_id
                continue
            duplicates.append(account_id)
            keeper = pool["entities"][owner]["accounts"][account_id]
            keeper["adjustment"] = float(
                keeper.get("adjustment", 0.0) or 0.0
            ) + float(account.get("adjustment", 0.0) or 0.0)
            keeper["message_count"] = min(
                activity_count_cap(),
                int(keeper.get("message_count", 0) or 0)
                + int(account.get("message_count", 0) or 0),
            )
            keeper["processed_signal_events"] = dedup_keep_order(
                list(keeper.get("processed_signal_events") or [])
                + list(account.get("processed_signal_events") or [])
            )
            from config import SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT
            keeper["processed_activity_events"] = dedup_keep_order(
                list(keeper.get("processed_activity_events") or [])
                + list(account.get("processed_activity_events") or [])
            )[-SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT:]
            # The one-shot import sentinel has to survive the fold. The plugin
            # re-pushes the frozen legacy ledger on EVERY startup by design and
            # `_import_locked` skips only on a matching sentinel — so dropping
            # the loser's marker (when the keeper has none) makes the very next
            # startup add that same legacy adjustment a second time. Double
            # counting is the exact failure the barrier exists to prevent; it
            # must not sneak back in through duplicate folding.
            if not isinstance(keeper.get("legacy_import"), dict):
                loser_import = account.get("legacy_import")
                if isinstance(loser_import, dict):
                    keeper["legacy_import"] = dict(loser_import)
            record["accounts"].pop(account_id, None)
    if duplicates:
        logger.warning(
            "[Trust] 池里有 %d 个 account 同时挂在两个实体下，已合并账本: %s",
            len(duplicates), sorted(set(duplicates))[:10],
        )
    # Re-validate canonical pointers AFTER the fold. ``normalize_entity_record``
    # already drops pointers naming a non-member, but it runs BEFORE the loop
    # above removes duplicate accounts — so a pointer that was valid then can be
    # dangling now.
    #
    # This is not cosmetic: a dangling pointer makes ``canonical_subject`` route
    # the losing entity's REMAINING accounts to an account that now belongs to a
    # DIFFERENT entity, i.e. one person's writes land in another person's
    # subject pile. Cross-user routing is the worst failure this design can
    # have, so the check is repeated rather than assumed. Clearing is safe: the
    # next write re-seals, exactly like the unbind path.
    for record in pool["entities"].values():
        canonical = record.get("canonical_accounts")
        if not isinstance(canonical, dict):
            continue
        for platform in [
            platform for platform, entry in canonical.items()
            if not isinstance(entry, dict)
            or entry.get("account_id") not in (record.get("accounts") or {})
        ]:
            logger.warning(
                "[Trust] 实体 %s 的 %s canonical 指针在去重后悬空，已清除"
                "（下次写入重新封定）",
                record.get("entity_id"), platform,
            )
            canonical.pop(platform, None)

    raw_forgotten = data.get("forgotten")
    if isinstance(raw_forgotten, dict):
        for entity_id, entry in raw_forgotten.items():
            if str(entity_id or "").strip() and isinstance(entry, dict):
                pool["forgotten"][str(entity_id)] = dict(entry)

    for container in ("channel_collisions", "platform_identity_scope"):
        raw = data.get(container)
        if isinstance(raw, dict):
            pool[container] = {
                str(key): dict(value)
                for key, value in raw.items()
                if isinstance(value, dict)
            }
    # ``channel_observations`` nests one level deeper than the containers
    # above, so a shallow copy would leave a hand-edited per-channel value
    # un-normalized. The copy-on-write draft happens to coerce it today, but
    # relying on that makes the published pool and the draft disagree about
    # what is in there — normalize here, at the one place that reads disk.
    raw_observations = data.get("channel_observations")
    if isinstance(raw_observations, dict):
        for platform, entry in raw_observations.items():
            if not isinstance(entry, dict):
                continue
            cleaned = {
                str(channel): dict(stats)
                for channel, stats in entry.items()
                if isinstance(stats, dict)
            }
            if cleaned:
                pool["channel_observations"][str(platform)] = cleaned
    pool["updated_at"] = data.get("updated_at") or _now_iso()
    return pool


def _rebind_locked(pool: dict[str, Any]) -> None:
    global _POOL
    with _pool_lock:
        _POOL = pool


def _set_load_failed(value: bool) -> None:
    global _load_failed
    with _pool_lock:
        _load_failed = value


async def aload_pool() -> None:
    """Load the pool at startup. A read failure degrades this process to read-only."""
    path = pool_path()
    if not await asyncio.to_thread(os.path.exists, path):
        await asyncio.to_thread(_rebind_locked, _seed_pool())
        _set_load_failed(False)
        logger.info("[Trust] 池文件不存在，已新建（迁移闸门 pending）")
        return
    try:
        # Must be the tolerating reader: ``read_json_async`` is a bare
        # ``read_json``, and on Windows a concurrent ``os.replace`` raises
        # PermissionError(WinError 5/32), which an upper layer swallows into
        # "unreadable → fall back to default trust" = silently dropped
        # evolution. Must run in a worker thread: the tolerating reader
        # refuses to back off while on the event loop.
        data = await asyncio.to_thread(read_json_tolerating_replace, path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        # UnicodeDecodeError needs its own name (the gates.py lesson): it is a
        # ValueError subclass, so it is neither a JSONDecodeError nor an
        # OSError, and letting it escape would break runtime initialization.
        logger.error(f"[Trust] 池加载失败，本进程进入只读降级: {exc}")
        _set_load_failed(True)
        return
    try:
        # Normalization is inside the guard too: this is the ONE caller, it runs
        # during runtime init (whose await has no try of its own), and it walks
        # arbitrary hand-editable JSON. A structural surprise here must degrade
        # to read-only, not abort the whole memory_server startup.
        normalized = _normalize_pool(data)
    except Exception as exc:  # noqa: BLE001 - startup must survive bad data
        logger.error(f"[Trust] 池归一失败，本进程进入只读降级: {exc}")
        _set_load_failed(True)
        return
    await asyncio.to_thread(_rebind_locked, normalized)
    _set_load_failed(False)


def reset_for_tests() -> None:
    """Reset module state. Tests only — there is no production caller."""
    global _POOL, _load_failed
    with _pool_lock:
        _POOL = _empty_pool()
        _load_failed = False


# ── read side ───────────────────────────────────────────────────────────────
class TrustSnapshot:
    """A frozen read-only view. The writer never mutates a published object."""

    __slots__ = ("_pool", "_loaded")

    def __init__(self, pool: dict[str, Any], *, loaded: bool) -> None:
        self._pool = pool
        self._loaded = loaded

    @property
    def loaded(self) -> bool:
        return self._loaded

    def entity_of(self, account_id: Any) -> str | None:
        if not self._loaded:
            return None
        normalized = normalize_account_id(account_id)
        if normalized is None:
            return None
        return _resolve_entity_locked(
            self._pool, (self._pool.get("account_index") or {}).get(normalized)
        )

    def same_entity(self, a: Any, b: Any) -> bool:
        """Conservative: unloaded pool ⇒ False. Never auto-vivifies."""
        if not self._loaded:
            return False
        left = self.entity_of(a)
        if left is None:
            return False
        return left == self.entity_of(b)

    def accounts_of(self, account_id: Any) -> tuple[str, ...]:
        """Every account of the entity owning ``account_id``, in a total order.

        Ordered by ``(bound_at, account_id)`` with the canonical first. The
        order MUST NOT depend on a volatile field such as ``channels_seen.last``
        — a per-message-varying sort key makes the same person's older piles
        flicker in and out between turns.
        """
        entity_id = self.entity_of(account_id)
        if entity_id is None:
            normalized = normalize_account_id(account_id)
            return (normalized,) if normalized is not None else ()
        entity = (self._pool.get("entities") or {}).get(entity_id) or {}
        accounts = entity.get("accounts") or {}
        platform = account_platform(normalize_account_id(account_id) or "")
        canonical = self.canonical_account(entity_id, platform)
        ordered = sorted(
            accounts.items(),
            key=lambda item: (
                str((item[1] or {}).get("bound_at") or ""), item[0],
            ),
        )
        ids = [account for account, _ in ordered]
        if canonical in ids:
            ids.remove(canonical)
            ids.insert(0, canonical)
        return tuple(ids)

    def canonical_account(self, entity_id: Any, platform: str) -> str | None:
        entity = (self._pool.get("entities") or {}).get(
            str(entity_id or "")
        ) or {}
        entry = (entity.get("canonical_accounts") or {}).get(
            str(platform or "").lower()
        )
        if not isinstance(entry, dict):
            return None
        return entry.get("account_id") or None

    def barrier_pending(self, platform: Any) -> bool:
        entry = (self._pool.get("legacy_barriers") or {}).get(
            str(platform or "").lower()
        )
        return bool(entry) and entry.get("status") != "cleared"

    def trust_inputs(self, account_id: Any) -> tuple[float, int]:
        """Return the RAW, UNCLAMPED ``(Σ adjustment, Σ message_count)``.

        Clamping is allowed in exactly one place — ``effective_trust`` as called
        by ``resolve_trust``. If a second caller ever clamps here as well there
        will be two different "effective" scores in circulation.

        An unregistered account returns ``(0.0, 0)`` and **never ``None``**:
        ``None`` is ``resolve_trust``'s abstention value and means something
        completely different.
        """
        entity_id = self.entity_of(account_id)
        if entity_id is None:
            return 0.0, 0
        entity = (self._pool.get("entities") or {}).get(entity_id) or {}
        adjustment = 0.0
        counted = 0
        for record in (entity.get("accounts") or {}).values():
            if not isinstance(record, dict):
                continue
            # A hand-edited or half-written value must not take the whole
            # read path down: skip that one component and keep summing the
            # rest. Dropping a component can only SHRINK the sum, so the
            # failure direction is under-count, never inflation.
            try:
                adjustment += float(record.get("adjustment", 0.0) or 0.0)
            except (TypeError, ValueError, OverflowError):
                pass
            try:
                counted += max(0, int(record.get("message_count", 0) or 0))
            except (TypeError, ValueError, OverflowError):
                pass
        return adjustment, counted

    def resolve_trust(
        self, account_id: Any, *,
        tier: str | None = None,
        base: float | None = None,
    ) -> float | None:
        """Resolve one segment's trust, or ``None`` to abstain.

        EXACTLY THREE REQUEST-LEVEL abstention conditions, in this order.
        Adding a fourth REQUEST-LEVEL condition silently changes arbitration
        behaviour and is forbidden:

        1. ``account_id`` is missing or malformed;
        2. that platform's legacy barrier is still pending;
        3. neither ``tier`` nor ``base`` was supplied.

        Specifically NOT abstention conditions: a missing ledger entry (the
        sums are ``(0.0, 0)`` and aggregation proceeds normally) and an empty
        entity (an empty sum is 0.0).

        Plus ONE process-level gate on a different axis: if this process could
        not read the pool at all, every read abstains. That is not a fourth
        request condition — it is the same read-only degradation that already
        makes ``same_entity`` return False, ``canonical_subject`` the identity,
        and ``same_provenance_source`` "unknown". Without it a platform that has
        no seeded barrier would keep stamping base-only scores while the
        adjustments on disk are unreadable, i.e. recording a guess as a fact.

        ``None`` means the handler must not write the ``speaker_trust`` key at
        all, which keeps ``preferred_by_trust`` abstaining. Falling back to 0.5
        would stamp a finite value onto rows that today deliberately carry
        none, turning abstention into an active arbitration vote.
        """
        from config import (
            SPEAKER_TRUST_BY_PERMISSION_LEVEL,
            SPEAKER_TRUST_MAX_REPORTED_BASE,
        )

        if not self._loaded:
            return None
        normalized = normalize_account_id(account_id)
        if normalized is None:
            return None
        if self.barrier_pending(account_platform(normalized)):
            return None
        if tier is None and base is None:
            return None
        adjustment_sum, activity_sum = self.trust_inputs(normalized)
        if tier is not None:
            base_score = SPEAKER_TRUST_BY_PERMISSION_LEVEL[tier]
            return effective_trust(base_score, adjustment_sum, activity_sum)
        # Self-reported base channel. The 0.8 clamp is applied to the FINAL
        # score, not merely to base: clamping base alone leaves
        # 0.8 + 0.30 + 0.02 = 1.0, i.e. admin-equivalent, which makes the
        # documented "0.8 < admin's 1.0 closes off owner-grade arbitration"
        # claim arithmetically false. The resulting asymmetry is deliberate:
        # tier='trusted' (0.8) may earn its way to 1.0 because a platform
        # permission model vouches for it; an unauthenticated self-reported
        # 0.8 may not.
        base_score = max(0.0, min(SPEAKER_TRUST_MAX_REPORTED_BASE, float(base)))
        return min(
            SPEAKER_TRUST_MAX_REPORTED_BASE,
            effective_trust(base_score, adjustment_sum, activity_sum),
        )

    # -- diagnostics only -------------------------------------------------
    def channels_seen(self, account_id: Any) -> tuple[str, ...]:
        entity_id = self.entity_of(account_id)
        normalized = normalize_account_id(account_id)
        if entity_id is None or normalized is None:
            return ()
        entity = (self._pool.get("entities") or {}).get(entity_id) or {}
        record = (entity.get("accounts") or {}).get(normalized) or {}
        return tuple(sorted((record.get("channels_seen") or {}).keys()))

    def channel_collision(self, account_id: Any) -> bool:
        normalized = normalize_account_id(account_id)
        if normalized is None:
            return False
        return normalized in (self._pool.get("channel_collisions") or {})

    def platform_identity_scope(self, platform: Any = None) -> dict[str, Any]:
        scope = self._pool.get("platform_identity_scope") or {}
        if platform is None:
            return {key: dict(value) for key, value in scope.items()}
        return dict(scope.get(str(platform or "").lower()) or {})

    def profile(self, account_id: Any) -> dict[str, Any]:
        """Read-only diagnostic view of one account. Never returns the ledger rings."""
        normalized = normalize_account_id(account_id)
        if normalized is None:
            return {"account_id": None, "known": False}
        entity_id = self.entity_of(normalized)
        adjustment_sum, activity_sum = self.trust_inputs(normalized)
        entity = (self._pool.get("entities") or {}).get(entity_id or "") or {}
        record = (entity.get("accounts") or {}).get(normalized) or {}
        return {
            "account_id": normalized,
            "known": bool(record),
            "entity_id": entity_id,
            "platform": account_platform(normalized),
            "barrier_pending": self.barrier_pending(
                account_platform(normalized)
            ),
            "entity_accounts": list(self.accounts_of(normalized))
            if entity_id else [],
            "canonical_account": self.canonical_account(
                entity_id, account_platform(normalized),
            ) if entity_id else None,
            "adjustment_sum": adjustment_sum,
            "activity_count_sum": activity_sum,
            "account_adjustment": float(record.get("adjustment", 0.0) or 0.0),
            "account_message_count": int(record.get("message_count", 0) or 0),
            "signal_event_count": len(
                record.get("processed_signal_events") or []
            ),
            "activity_event_count": len(
                record.get("processed_activity_events") or []
            ),
            "bound_at": record.get("bound_at"),
            "channels_seen": list(self.channels_seen(normalized)),
            "channel_collision": self.channel_collision(normalized),
            "platform_identity_scope": self.platform_identity_scope(
                account_platform(normalized)
            ),
            "legacy_barriers": {
                platform: dict(entry)
                for platform, entry in (
                    self._pool.get("legacy_barriers") or {}
                ).items()
            },
        }


def trust_snapshot() -> TrustSnapshot:
    """One atomic attribute read. Lock-free by construction, no defensive copy."""
    return TrustSnapshot(_POOL, loaded=not _load_failed)


# ── write side ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ActivityEvent:
    id: str
    count: int = 1


@dataclass(frozen=True)
class TrustMutation:
    """One segment's trust effects.

    TWO INDEPENDENT AXES — never share an account between them:

    * activity is recorded against ``speaker_account_id`` (who spoke);
    * every signal event is routed by ITS OWN ``speaker_id``, which is the
      **target** of the correction, not the speaker. See ``memory/facts.py``'s
      event body ``{'speaker_id': target_id}``.
    """

    speaker_account_id: str | None = None
    activity_events: tuple[ActivityEvent, ...] = ()
    signal_events: tuple[dict, ...] = ()
    channel: str | None = None


@dataclass(frozen=True)
class MutationOutcome:
    """Per-mutation breakdown, aligned index-for-index with the input sequence.

    The whole batch is one file write, but the response reports per segment, so
    the counts have to be attributed back to the mutation that produced them.
    """

    activity_applied: int = 0
    signals_applied: int = 0
    signals_deferred: int = 0
    channel_collision: bool = False


@dataclass(frozen=True)
class TrustApplyResult:
    persisted: bool
    activity_applied: int = 0
    signals_applied: int = 0
    signals_deferred: int = 0
    channel_collisions: tuple[str, ...] = ()
    per_mutation: tuple[MutationOutcome, ...] = ()


def _persist_draft_locked(draft: _Draft) -> bool:
    """Write the draft and publish it. Returns whether the write landed."""
    global _POOL
    draft.pool["updated_at"] = _now_iso()
    try:
        # Deliberately NOT wrapped in ``assert_cloudsave_writable``: that helper
        # is a bare TOCTOU assertion, and this root-level pool file is neither
        # in the cloudsave manifest nor reachable by the import cleanup. Adding
        # the gate here would make a later reader believe a protection exists.
        atomic_write_json(pool_path(), draft.pool, indent=2, ensure_ascii=False)
    except BaseException as exc:  # noqa: BLE001 - never let a pool write escape
        # BaseException, not Exception, ON PURPOSE. This runs inside a worker
        # thread as the handler's post-commit step; letting ANY throwable out —
        # including a KeyboardInterrupt or a MaintenanceModeError — would
        # discard an already-durable fact write's response. The draft is
        # dropped either way, so memory and disk cannot fork.
        #
        # NEVER raise. The pool write happens after the facts are already
        # durable; a MaintenanceModeError here would be turned into a 409 that
        # discards the whole response body (per-segment status / fact_ids /
        # reconciled), forcing the caller to retry the entire batch and pay for
        # another LLM extraction. Post-commit failures degrade to
        # ``persisted=False`` reported per segment.
        logger.warning(f"[Trust] 池写盘失败，本次演化未落盘: {exc}")
        return False
    # Rebind only after the write succeeded, so memory and disk cannot fork.
    _POOL = draft.pool
    return True


def _with_pool_write(mutator: TrustMutator) -> tuple[bool, Any]:
    """Run one mutator as a single critical section. Call me in a worker thread."""
    with _pool_lock:
        if _load_failed:
            # One-vote veto: a read failure must never let a subsequent write
            # overwrite the whole evolution with an empty pool.
            return False, None
        draft = _Draft(_POOL)
        dirty, value = mutator(draft)
        if not dirty:
            return True, value
        return _persist_draft_locked(draft), value


def _record_channel_observation(
    draft: _Draft, account_id: str, channel: str | None, *, now: str,
) -> bool:
    """Record one channel observation and detect collisions. Diagnostics only.

    Returns whether this observation is STRUCTURALLY NEW (first time this
    channel is seen for this account), which is the only case worth an fsync.
    The ``last`` / ``count`` counters are still bumped, but they ride along on
    whatever write the request was going to do anyway; when nothing else is
    dirty the draft is discarded and those counter bumps are lost. That is
    deliberate — marking every observed message dirty would rewrite the whole
    pool JSON on every flush and undo the activity-cap no-op optimisation
    entirely. The detector's actual question ("has this account been seen on
    more than one channel?") is set membership, and set membership is exact.
    """
    if channel is None:
        return False
    record = draft.account(account_id)
    if record is None:
        return False
    platform = account_platform(account_id)
    seen = record.setdefault("channels_seen", {})
    entry = seen.get(channel)
    if entry is not None:
        entry["last"] = now
        entry["count"] = int(entry.get("count", 0) or 0) + 1
        observations = draft.pool["channel_observations"].setdefault(
            platform, {},
        )
        stats = observations.setdefault(
            channel, {"first_seen": now, "last_seen": now, "accounts": 1},
        )
        stats["last_seen"] = now
        return False
    seen[channel] = {"first": now, "last": now, "count": 1}
    observations = draft.pool["channel_observations"].setdefault(platform, {})
    stats = observations.get(channel)
    if stats is None:
        observations[channel] = {
            "first_seen": now, "last_seen": now, "accounts": 1,
        }
    else:
        stats["last_seen"] = now
        stats["accounts"] = int(stats.get("accounts", 0) or 0) + 1
    if len(seen) > 1:
        collision = draft.pool["channel_collisions"].setdefault(
            account_id, {"channels": [], "detected_at": now, "hits": 0},
        )
        collision["channels"] = sorted(seen.keys())
        collision["hits"] = int(collision.get("hits", 0) or 0) + 1
        logger.warning(
            "[Trust] account %s 被两条通道观测到: %s —— 账本本来就是分开的，"
            "这条告警是为了让「是否需要字节级拆分」变成可观测事实",
            account_id, collision["channels"],
        )
    return True


def _apply_trust_mutations_locked(
    draft: _Draft, muts: Sequence[TrustMutation],
) -> tuple[bool, TrustApplyResult]:
    now = _now_iso()
    activity_applied = 0
    signals_applied = 0
    deferred = 0
    collisions: list[str] = []
    outcomes: list[MutationOutcome] = []
    dirty = False
    for mutation in muts:
        seg_activity = 0
        seg_signals = 0
        seg_deferred = 0
        seg_collision = False
        # ① Activity axis: the segment's own speaker.
        speaker = normalize_account_id(mutation.speaker_account_id)
        if speaker is not None and not draft.barrier_pending(
            account_platform(speaker)
        ):
            # Only auto-vivify for the activity ledger. A channel observation
            # alone must not open an account record — the observation ledger is
            # diagnostics, and opening a ledger row for it would put every
            # passing speaker into the pool.
            record = (
                draft.account(speaker, create=True, now=now)
                if mutation.activity_events
                else draft.account(speaker)
            )
            if record is not None:
                # R-CANON-1 lazy sealing, inside the same critical section as
                # the ledger write: an entity with no canonical for this
                # platform seals the account that is speaking right now. This
                # request's own row is unaffected either way — the freshly
                # sealed canonical IS the current speaker, so routing it would
                # be the identity.
                sealed, _ = _seal_canonical_locked(draft, speaker, now=now)
                if sealed:
                    dirty = True
                for event in mutation.activity_events:
                    if record_activity(
                        record, event.count, event.id,
                        entity_message_count=draft.entity_message_count(
                            speaker
                        ),
                    ):
                        seg_activity += 1
                        dirty = True
                if _record_channel_observation(
                    draft, speaker, normalize_channel(mutation.channel),
                    now=now,
                ):
                    dirty = True
                if speaker in draft.pool["channel_collisions"]:
                    collisions.append(speaker)
                    seg_collision = True
        # ② Signal axis: each event routed by its own target.
        for event in mutation.signal_events:
            if not isinstance(event, dict):
                continue
            target = normalize_account_id(event.get("speaker_id"))
            if target is None:
                continue
            if draft.barrier_pending(account_platform(target)):
                seg_deferred += 1
                continue
            record = draft.account(target, create=True, now=now)
            if record is not None and apply_signal_event(record, event):
                seg_signals += 1
                dirty = True
        activity_applied += seg_activity
        signals_applied += seg_signals
        deferred += seg_deferred
        outcomes.append(MutationOutcome(
            activity_applied=seg_activity,
            signals_applied=seg_signals,
            signals_deferred=seg_deferred,
            channel_collision=seg_collision,
        ))
    return dirty, TrustApplyResult(
        persisted=True,
        activity_applied=activity_applied,
        signals_applied=signals_applied,
        signals_deferred=deferred,
        channel_collisions=tuple(dict.fromkeys(collisions)),
        per_mutation=tuple(outcomes),
    )


async def aapply_trust_mutations(
    muts: Sequence[TrustMutation],
) -> TrustApplyResult:
    """The single write entry point for trust evolution.

    LOCK ORDER: the pool lock is a LEAF. While holding it, never take another
    lock, never await, and never call into ``FactStore``. This runs as the last
    durable write of its handler, after every FactStore call, so the two lock
    orders cannot overlap.
    """
    materialized = tuple(muts)
    if not materialized:
        return TrustApplyResult(persisted=True)

    captured: dict[str, TrustApplyResult] = {}

    def _mutator(draft: _Draft) -> tuple[bool, Any]:
        dirty, result = _apply_trust_mutations_locked(draft, materialized)
        captured["result"] = result
        return dirty, result

    persisted, _ = await asyncio.to_thread(_with_pool_write, _mutator)
    base = captured.get("result") or TrustApplyResult(
        persisted=False,
        per_mutation=tuple(MutationOutcome() for _ in materialized),
    )
    if not persisted:
        # Everything except the gating decision is rolled back with the draft:
        # deferral is a statement about the barrier, not about applied state.
        return TrustApplyResult(
            persisted=False,
            signals_deferred=base.signals_deferred,
            channel_collisions=base.channel_collisions,
            per_mutation=tuple(
                MutationOutcome(signals_deferred=outcome.signals_deferred)
                for outcome in base.per_mutation
            ),
        )
    return base


# ── canonical sealing (write-side subject routing) ──────────────────────────
def _seal_canonical_locked(
    draft: _Draft, account_id: str, *, now: str,
) -> tuple[bool, str | None]:
    """R-CANON-1: lazily seal the CURRENT speaker as canonical for its platform.

    Sealing (rather than deriving canonical from state, e.g. ``min(account_id)``
    or the earliest ``bound_at``) is what makes canonical stable across merges:
    a derived canonical flips whenever the union grows, and every flip strands
    the previous period's writes on yet another subject, producing a third pile
    that is harder to converge than the first two.
    """
    entity_id = draft.entity_of(account_id)
    if entity_id is None:
        return False, None
    platform = account_platform(account_id)
    entity = draft.entity(entity_id)
    if entity is None:
        return False, None
    canonical = entity.setdefault("canonical_accounts", {})
    entry = canonical.get(platform)
    if isinstance(entry, dict) and entry.get("account_id"):
        return False, entry["account_id"]
    canonical[platform] = {"account_id": account_id, "sealed_at": now}
    entity["updated_at"] = now
    return True, account_id


async def aseal_canonical(account_id: Any) -> str | None:
    """Seal (if needed) and return the canonical account.

    Production sealing happens inside ``_apply_trust_mutations_locked`` so it
    shares that handler's single file write. This standalone form exists for the
    identity endpoints and for tests that need to seal without traffic.
    """
    normalized = normalize_account_id(account_id)
    if normalized is None:
        return None
    snap = trust_snapshot()
    entity_id = snap.entity_of(normalized)
    if entity_id is None:
        return None
    existing = snap.canonical_account(
        entity_id, account_platform(normalized),
    )
    if existing is not None:
        return existing

    def _mutator(draft: _Draft) -> tuple[bool, Any]:
        return _seal_canonical_locked(draft, normalized, now=_now_iso())

    _, sealed = await asyncio.to_thread(_with_pool_write, _mutator)
    return sealed


def _release_canonical_locked(
    entity: dict[str, Any], account_id: str, *, now: str,
) -> bool:
    """R-CANON-2: unsealing happens only when the canonical member LEAVES."""
    canonical = entity.get("canonical_accounts")
    if not isinstance(canonical, dict):
        return False
    platform = account_platform(account_id)
    entry = canonical.get(platform)
    if not isinstance(entry, dict) or entry.get("account_id") != account_id:
        # Unbinding a non-canonical account leaves the record byte-identical.
        return False
    canonical.pop(platform, None)
    entity["updated_at"] = now
    return True


# ── legacy migration + barriers ─────────────────────────────────────────────
def _import_legacy_locked(
    draft: _Draft, *, platform: str, source: str, profiles: dict, final: bool,
) -> tuple[bool, dict]:
    now = _now_iso()
    imported: list[str] = []
    skipped: list[dict] = []
    dirty = False
    for bare_key, raw in (profiles or {}).items():
        account_id = normalize_account_id(f"{platform}:{str(bare_key).strip()}")
        if account_id is None or not isinstance(raw, dict):
            skipped.append({
                "key": str(bare_key)[:64], "reason": "invalid_account_id",
            })
            continue
        record = draft.account(account_id, create=True, now=now)
        if record is None:  # pragma: no cover - defensive
            skipped.append({"key": str(bare_key)[:64], "reason": "unavailable"})
            continue
        if (record.get("legacy_import") or {}).get("source") == source:
            # Per-account one-shot sentinel keyed by (source, account_id).
            # account_id is immutable, so re-pushing the same frozen snapshot
            # on every startup is a no-op — which is exactly what makes "pool
            # lost → plugin re-pushes → state restored" work without a
            # cross-file double marker that can deadlock.
            continue
        legacy = normalize_legacy_profile(raw)
        # Additive merge, never overwrite. Safe precisely because the barrier
        # guarantees this platform had zero server-side evolution before the
        # import. ``adjustment`` is NOT clamped here (commutativity).
        record["adjustment"] = float(
            record.get("adjustment", 0.0) or 0.0
        ) + legacy["adjustment"]
        record["message_count"] = min(
            activity_count_cap(),
            int(record.get("message_count", 0) or 0) + legacy["message_count"],
        )
        # The two rings get two separate statements on purpose — never fold
        # them into one loop, the signal ring must not inherit a truncation.
        record["processed_signal_events"] = dedup_keep_order(
            list(record.get("processed_signal_events") or [])
            + legacy["processed_signal_events"]
        )
        from config import SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT
        record["processed_activity_events"] = dedup_keep_order(
            list(record.get("processed_activity_events") or [])
            + legacy["processed_activity_events"]
        )[-SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT:]
        record["legacy_import"] = {"source": source, "at": now}
        imported.append(account_id)
        dirty = True
    barrier = draft.pool["legacy_barriers"].setdefault(platform, {
        "source": source, "status": "pending", "seeded_at": now,
        "cleared_at": None, "chunks": 0, "accounts": 0, "skipped": 0,
    })
    if final and barrier.get("status") != "cleared":
        barrier["status"] = "cleared"
        barrier["cleared_at"] = now
        dirty = True
    if imported or skipped:
        dirty = True
    # Counters move ONLY when something actually happened. The plugin re-pushes
    # the same frozen snapshot on every startup by design, so bumping a chunk
    # counter unconditionally would force one full pool rewrite per restart
    # forever — for purely diagnostic bookkeeping.
    if dirty:
        barrier["accounts"] = (
            int(barrier.get("accounts", 0) or 0) + len(imported)
        )
        barrier["skipped"] = int(barrier.get("skipped", 0) or 0) + len(skipped)
        barrier["chunks"] = int(barrier.get("chunks", 0) or 0) + 1
    return dirty, {
        "imported": imported,
        "skipped": skipped,
        "barrier": barrier["status"],
    }


async def aimport_legacy_profiles(
    *, platform: str, source: str, profiles: dict, final: bool,
) -> dict:
    """Import one chunk of a legacy per-platform trust ledger."""
    normalized_platform = str(platform or "").strip().lower()

    def _mutator(draft: _Draft) -> tuple[bool, Any]:
        return _import_legacy_locked(
            draft, platform=normalized_platform, source=str(source),
            profiles=profiles or {}, final=bool(final),
        )

    persisted, value = await asyncio.to_thread(_with_pool_write, _mutator)
    result = dict(value or {})
    result["persisted"] = bool(persisted)
    return result


async def awaive_legacy_barrier(platform: str) -> dict:
    """Manually give up on a platform's legacy import (escape hatch)."""
    normalized = str(platform or "").strip().lower()

    def _mutator(draft: _Draft) -> tuple[bool, Any]:
        now = _now_iso()
        barrier = draft.pool["legacy_barriers"].setdefault(normalized, {
            "source": "", "status": "pending", "seeded_at": now,
            "cleared_at": None, "chunks": 0, "accounts": 0, "skipped": 0,
        })
        if barrier.get("status") == "cleared":
            return False, {"platform": normalized, "barrier": "cleared"}
        barrier["status"] = "cleared"
        barrier["cleared_at"] = now
        barrier["waived"] = True
        return True, {"platform": normalized, "barrier": "cleared"}

    persisted, value = await asyncio.to_thread(_with_pool_write, _mutator)
    result = dict(value or {})
    result["persisted"] = bool(persisted)
    return result


async def areconcile_from_facts(fact_store, character_names) -> dict:
    """Disaster recovery: rebuild folds missing from the pool, idempotent by event id.

    NOT a correctness dependency — correctness comes from the ``trust.persisted``
    round-trip plus the caller's retain-and-retry. It is also NOT a complete
    self-healer: ``ascoped_forget`` deletes the fact rows carrying
    ``_speaker_trust_signal_events``, so events on forgotten subjects have no
    reconstruction source.

    LOCK DISCIPLINE: scanning and delta construction run ENTIRELY outside the
    pool lock (they call FactStore, and the pool lock is a leaf). Only
    "dedupe by event id + fold + persist" enters ``to_thread`` + ``_pool_lock``,
    and the critical section re-reads the live pool rather than a snapshot taken
    before the scan — otherwise the whole scan window's handler evolution would
    be overwritten (textbook lost update).
    """
    scanned: list[dict] = []
    for name in list(character_names or []):
        rows: list = []
        try:
            rows = list(await fact_store.aload_facts(name) or [])
        except Exception as exc:  # noqa: BLE001 - disaster tool, never fatal
            logger.warning(f"[Trust] reconcile 读取 {name} 失败: {exc}")
            continue
        try:
            # Archived rows carry signal events too — ``apersist`` updates the
            # archive as well. Skipping them makes the recovery tool silently
            # under-restore exactly the OLDEST adjustments, which are the ones
            # least likely to be re-earned by an owner repeating themselves.
            rows.extend(
                await fact_store.aload_archived_speaker_trust_signal_facts(name)
                or []
            )
        except Exception as exc:  # noqa: BLE001 - archive is best-effort here
            logger.warning(f"[Trust] reconcile 读取 {name} 归档失败: {exc}")
        for row in rows:
            if not isinstance(row, dict):
                continue
            for event in row.get("_speaker_trust_signal_events") or []:
                if isinstance(event, dict):
                    scanned.append(event)

    def _mutator(draft: _Draft) -> tuple[bool, Any]:
        applied = 0
        gated = 0
        dirty = False
        for event in scanned:
            target = normalize_account_id(event.get("speaker_id"))
            if target is None:
                continue
            # Same barrier as the write path: folding an event that the pending
            # legacy ledger already contains would double-count it once the
            # import lands.
            if draft.barrier_pending(account_platform(target)):
                gated += 1
                continue
            record = draft.account(target, create=True, now=_now_iso())
            if record is not None and apply_signal_event(record, event):
                applied += 1
                dirty = True
        return dirty, {"scanned": len(scanned), "applied": applied,
                       "gated": gated}

    persisted, value = await asyncio.to_thread(_with_pool_write, _mutator)
    result = dict(value or {})
    result["persisted"] = bool(persisted)
    return result


# ── identity lifecycle (human-triggered endpoints only) ─────────────────────
def _account_limit_guard(
    entity: dict[str, Any], platform: str, incoming: Iterable[str],
) -> None:
    from config import IDENTITY_MAX_ACCOUNTS_PER_ENTITY_PER_PLATFORM

    existing = {
        account_id for account_id in (entity.get("accounts") or {})
        if account_platform(account_id) == platform
    }
    existing.update(
        account_id for account_id in incoming
        if account_platform(account_id) == platform
    )
    if len(existing) > IDENTITY_MAX_ACCOUNTS_PER_ENTITY_PER_PLATFORM:
        raise TrustIdentityError(
            f"该实体在 {platform} 已有 "
            f"{IDENTITY_MAX_ACCOUNTS_PER_ENTITY_PER_PLATFORM} 个 account，"
            f"无法再绑定；请先解绑一个",
            status_code=409,
        )


def _merge_entities_locked(
    draft: _Draft, left: str, right: str, *, now: str,
) -> str:
    """Merge two entities. Idempotent, commutative, associative, ledger-lossless.

    The survivor is the ``(created_at, entity_id)`` minimum, so two concurrent
    requests in opposite directions converge on the same result and the eventual
    survivor is a function of the final entity set rather than of merge order.
    Merging is a DISJOINT MOVE of account sub-dicts — there is nothing to
    "combine", which is exactly what per-account ledger partitioning bought.
    """
    a = draft.resolve_entity(left)
    b = draft.resolve_entity(right)
    if a is None or b is None:
        raise TrustIdentityError("unknown entity", status_code=404)
    if a == b:
        return a
    record_a = draft.entity(a)
    record_b = draft.entity(b)
    if record_a is None or record_b is None:  # pragma: no cover - defensive
        raise TrustIdentityError("unknown entity", status_code=404)
    if merge_order_key(record_a) <= merge_order_key(record_b):
        survivor, absorbed = record_a, record_b
    else:
        survivor, absorbed = record_b, record_a
    moving = list((absorbed.get("accounts") or {}).keys())
    for platform in {account_platform(account) for account in moving}:
        _account_limit_guard(survivor, platform, moving)
    for account_id, record in (absorbed.get("accounts") or {}).items():
        # Structurally guaranteed disjoint: ``account_index`` is a function and
        # the key is the immutable ``account_id``, so no path can give one
        # account two keys. An explicit raise rather than ``assert``: ``-O`` /
        # PYTHONOPTIMIZE strips assert statements entirely, and this is exactly
        # the guard whose whole point is to fail loud — under -O it would
        # degrade into the silent, irreversible ledger overwrite it exists to
        # prevent.
        if account_id in survivor["accounts"]:
            raise TrustIdentityError(
                f"merge would clobber an existing ledger for {account_id}",
                status_code=500,
            )
        survivor["accounts"][account_id] = record
        draft.pool["account_index"][account_id] = survivor["entity_id"]
    # R-CANON-3: the survivor keeps its own canonical. It only adopts the
    # absorbed one for a platform where it has none; when both have one, the
    # absorbed pointer is filed as read-only diagnostics.
    survivor_canonical = survivor.setdefault("canonical_accounts", {})
    for platform, entry in (absorbed.get("canonical_accounts") or {}).items():
        if platform not in survivor_canonical:
            survivor_canonical[platform] = dict(entry)
        else:
            survivor.setdefault("superseded_canonicals", []).append({
                "platform": platform,
                "account_id": entry.get("account_id"),
                "at": now,
            })
    absorbed["accounts"] = {}
    absorbed["canonical_accounts"] = {}
    absorbed["status"] = "merged"
    absorbed["merged_into"] = survivor["entity_id"]
    absorbed["merged_at"] = now
    # Full inverse of merge needs to know which accounts came in with it.
    absorbed["merged_accounts"] = moving
    survivor["updated_at"] = now
    return survivor["entity_id"]


def _bind_locked(
    draft: _Draft, account_id: str, entity_id: str, *,
    now: str, bound_by: str | None,
) -> tuple[bool, dict]:
    target = draft.resolve_entity(entity_id)
    if target is None:
        raise TrustIdentityError("unknown entity", status_code=404)
    current = draft.entity_of(account_id)
    if current == target:
        return False, {"entity_id": target, "changed": False}
    if current is not None:
        # The account already belongs to another entity: binding is then
        # exactly a merge, and the ledger follows automatically.
        merged = _merge_entities_locked(draft, current, target, now=now)
        return True, {"entity_id": merged, "changed": True, "merged": True}
    entity = draft.entity(target)
    if entity is None:  # pragma: no cover - defensive
        raise TrustIdentityError("unknown entity", status_code=404)
    # Unregistered account: attach it to the target entity directly. Going
    # through the seed entity first and then merging would produce an extra
    # tombstone for an entity that never held anything.
    _account_limit_guard(entity, account_platform(account_id), [account_id])
    entity["accounts"][account_id] = normalize_account_record(
        account_id, {"bound_at": now},
    )
    entity["updated_at"] = now
    draft.pool["account_index"][account_id] = target
    return True, {"entity_id": target, "changed": True, "bound_by": bound_by}


def _unbind_locked(
    draft: _Draft, account_id: str, *, now: str,
) -> tuple[bool, dict]:
    """Move one account out into a fresh entity. Ledger loss is exactly zero.

    ``ledger_delta`` and ``effective_delta`` are BOTH returned and are usually
    different numbers. Under a clamped aggregate "how much did it take with it"
    has no unique answer: with an entity raw sum of −2.0 and ``adj_A = −0.5``
    both sides read −0.30, so the account takes away 0; with a raw sum of −0.35
    the same removal moves the effective score by +0.45, more than the clamp
    itself, because it also released the other side's saturation. An operator
    handed only the ledger number will never be able to explain the score.
    """
    entity_id = draft.entity_of(account_id)
    if entity_id is None:
        return False, {"entity_id": None, "changed": False}
    entity = draft.entity(entity_id)
    if entity is None:  # pragma: no cover - defensive
        return False, {"entity_id": None, "changed": False}
    record = entity["accounts"].get(account_id)
    if record is None:
        return False, {"entity_id": entity_id, "changed": False}
    before_adjustment = sum(
        float((row or {}).get("adjustment", 0.0) or 0.0)
        for row in (entity.get("accounts") or {}).values()
    )
    before_activity = sum(
        max(0, int((row or {}).get("message_count", 0) or 0))
        for row in (entity.get("accounts") or {}).values()
    )
    ledger_delta = float(record.get("adjustment", 0.0) or 0.0)
    entity["accounts"].pop(account_id, None)
    _release_canonical_locked(entity, account_id, now=now)
    entity["updated_at"] = now
    generation = int(record.get("generation", 0) or 0) + 1
    entities = draft.pool["entities"]
    forgotten = draft.pool["forgotten"]
    while True:
        candidate = derive_entity_id(account_id, generation)
        if candidate not in entities and candidate not in forgotten:
            break
        generation += 1
    fresh = draft.create_entity(candidate, now=now)
    record["generation"] = generation
    record["bound_at"] = now
    fresh["accounts"][account_id] = record
    draft.pool["account_index"][account_id] = candidate
    remaining_adjustment = sum(
        float((row or {}).get("adjustment", 0.0) or 0.0)
        for row in (entity.get("accounts") or {}).values()
    )
    remaining_activity = sum(
        max(0, int((row or {}).get("message_count", 0) or 0))
        for row in (entity.get("accounts") or {}).values()
    )
    from config import SPEAKER_TRUST_BY_PERMISSION_LEVEL
    probe = SPEAKER_TRUST_BY_PERMISSION_LEVEL["normal"]
    effective_delta = (
        effective_trust(probe, remaining_adjustment, remaining_activity)
        - effective_trust(probe, before_adjustment, before_activity)
    )
    return True, {
        "entity_id": candidate,
        "previous_entity_id": entity_id,
        "changed": True,
        "ledger_delta": ledger_delta,
        # Deliberately two different numbers; a future "fix" making them agree
        # would be wrong (see the docstring).
        "effective_delta": effective_delta,
    }


def _forget_entity_locked(
    draft: _Draft, entity_id: str, *, now: str,
) -> tuple[bool, dict]:
    target = draft.resolve_entity(entity_id)
    if target is None:
        return False, {"entity_id": None, "changed": False}
    entity = draft.entity(target)
    if entity is None:  # pragma: no cover - defensive
        return False, {"entity_id": None, "changed": False}
    accounts = list((entity.get("accounts") or {}).keys())
    for account_id in accounts:
        draft.pool["account_index"].pop(account_id, None)
    draft.pool["entities"].pop(target, None)
    draft.pool["forgotten"][target] = {
        "forgotten_at": now, "accounts": accounts,
    }
    for account_id in accounts:
        draft.pool["channel_collisions"].pop(account_id, None)
    return True, {
        "entity_id": target, "changed": True, "accounts": accounts,
    }


async def abind_account(
    account_id: Any, entity_id: Any, *, bound_by: str | None = None,
) -> dict:
    normalized = normalize_account_id(account_id)
    if normalized is None:
        raise TrustIdentityError("invalid account_id")
    target = str(entity_id or "").strip()
    if not target:
        raise TrustIdentityError("entity_id is required")

    def _mutator(draft: _Draft) -> tuple[bool, Any]:
        return _bind_locked(
            draft, normalized, target, now=_now_iso(), bound_by=bound_by,
        )

    persisted, value = await asyncio.to_thread(_with_pool_write, _mutator)
    result = dict(value or {})
    result["persisted"] = bool(persisted)
    result["account_id"] = normalized
    return result


async def aunbind_account(account_id: Any) -> dict:
    normalized = normalize_account_id(account_id)
    if normalized is None:
        raise TrustIdentityError("invalid account_id")

    def _mutator(draft: _Draft) -> tuple[bool, Any]:
        return _unbind_locked(draft, normalized, now=_now_iso())

    persisted, value = await asyncio.to_thread(_with_pool_write, _mutator)
    result = dict(value or {})
    result["persisted"] = bool(persisted)
    result["account_id"] = normalized
    return result


async def amerge_entities(left: Any, right: Any) -> dict:
    a = str(left or "").strip()
    b = str(right or "").strip()
    if not a or not b:
        raise TrustIdentityError("both entity ids are required")

    def _mutator(draft: _Draft) -> tuple[bool, Any]:
        now = _now_iso()
        before = draft.resolve_entity(a), draft.resolve_entity(b)
        survivor = _merge_entities_locked(draft, a, b, now=now)
        # Idempotent: already the same entity ⇒ no-op, no disk write.
        return before[0] != before[1], {"entity_id": survivor}

    persisted, value = await asyncio.to_thread(_with_pool_write, _mutator)
    result = dict(value or {})
    result["persisted"] = bool(persisted)
    return result


async def aforget_entity(entity_id: Any) -> dict:
    target = str(entity_id or "").strip()
    if not target:
        raise TrustIdentityError("entity_id is required")

    def _mutator(draft: _Draft) -> tuple[bool, Any]:
        return _forget_entity_locked(draft, target, now=_now_iso())

    persisted, value = await asyncio.to_thread(_with_pool_write, _mutator)
    result = dict(value or {})
    result["persisted"] = bool(persisted)
    return result


async def aensure_account(account_id: Any, *, channel: Any = None) -> str | None:
    """Register one account if unseen (used by the identity endpoints)."""
    normalized = normalize_account_id(account_id)
    if normalized is None:
        return None
    snap = trust_snapshot()
    if snap.entity_of(normalized) is not None:
        return snap.entity_of(normalized)

    def _mutator(draft: _Draft) -> tuple[bool, Any]:
        now = _now_iso()
        draft.account(normalized, create=True, now=now)
        _record_channel_observation(
            draft, normalized, normalize_channel(channel), now=now,
        )
        return True, draft.entity_of(normalized)

    _, value = await asyncio.to_thread(_with_pool_write, _mutator)
    return value
