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

"""Visit identity derivations, recall subjects and the local peer roster.

Design: ``docs/design/visit-infrastructure.md`` OD-05 v2, section 3.7.1 and
3.7.5, PR-06 ``subjects.py``.

Derivations (both machines compute the same values; every hash below is the
lowercase ``sha256(...).hexdigest()`` of the UTF-8 encoded string, and ``|``
is a literal vertical bar)::

    pair_id      = sha256(min(a, b) + '|' + max(a, b))[:24]
    person_id    = 'p_' + sha256(own_uid + '|' + peer_uid)[:24]
    peer_char_id = 'c_' + sha256(peer_uid + '|' + char_tag)[:24]
    vid          = role[0] + '_' + sha256(visit_uid + '|' + visit_id)[:24]
    short_code   = visit_uid[:6].upper()

The three memory subjects of one visit, in recall budget priority order::

    group_chat('neko_visit', pair_id)
    group_participant('neko_visit', pair_id, peer_char_id)
    participant('neko_visit', person_id)

They are produced through :class:`memory.scopes.MemorySubject` so the
``subject_id`` escaping is byte-identical to what memory_server builds from a
``MemorySubjectRequest``; the wire form is ``{subject_kind, subject_id}``.

The roster ``config_dir/visit_peers.json`` is partitioned by the locally
signed-in community account (``accounts[own_uid]``) and, inside one account,
by local character name (``by_char``). :class:`PeerRoster` is bound to one
``own_uid`` and never reads or writes another account's partition, except for
:meth:`PeerRoster.rename_char`, which follows a machine-local character rename
and therefore moves the entry in every partition.

All file access runs in worker threads (``asyncio.to_thread``); the public
roster methods are coroutines. Read-modify-write cycles are serialized per
file by a process-wide thread lock, and every write is an atomic replace.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from config.visit_settings import VISIT_MEMORY_PLATFORM, VISIT_PEERS_FILENAME
from memory.scopes import MemoryScopeError, MemorySubject
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_ROLES = ("host", "guest")


class RosterCorruptError(RuntimeError):
    """Raised when ``visit_peers.json`` cannot be parsed and a write is requested.

    Writing over an unreadable roster would silently drop every account's
    peers, so mutations refuse instead; reads degrade to an empty view.
    """


# ── 纯函数派生 ──────────────────────────────────────────────────────────


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def derive_pair_id(a: str, b: str) -> str:
    """Return ``sha256(min(a, b) + '|' + max(a, b)).hexdigest()[:24]``.

    Symmetric in its arguments, so host and guest derive the same id.
    """
    a = _require_str(a, "uid")
    b = _require_str(b, "uid")
    lo, hi = (a, b) if a <= b else (b, a)
    return _sha256_hex(f"{lo}|{hi}")[:24]


def derive_person_id(own_uid: str, peer_uid: str) -> str:
    """Return ``'p_' + sha256(own_uid + '|' + peer_uid).hexdigest()[:24]``.

    Directional and bound to the local account, so switching community
    accounts on this machine never reads the previous account's memory of
    the same person. Single segment on purpose: ``participant()`` escapes
    ``:`` and a composite id would not match the literal used elsewhere.
    """
    own_uid = _require_str(own_uid, "own_uid")
    peer_uid = _require_str(peer_uid, "peer_uid")
    return "p_" + _sha256_hex(f"{own_uid}|{peer_uid}")[:24]


def derive_peer_char_id(peer_uid: str, char_tag: str) -> str:
    """Return ``'c_' + sha256(peer_uid + '|' + char_tag).hexdigest()[:24]`` (26 chars).

    ``char_tag`` is self-reported by the peer but namespaced by the verified
    ``peer_uid``, so it only ever affects the peer's own namespace.
    """
    peer_uid = _require_str(peer_uid, "peer_uid")
    char_tag = _require_str(char_tag, "char_tag")
    return "c_" + _sha256_hex(f"{peer_uid}|{char_tag}")[:24]


def derive_vid(role: str, visit_uid: str, visit_id: str) -> str:
    """Return ``role[0] + '_' + sha256(visit_uid + '|' + visit_id).hexdigest()[:24]``.

    26 characters drawn from ``[a-z0-9_]``, inside the TRTC userId charset.
    """
    if role not in _ROLES:
        raise ValueError(f"role must be one of {_ROLES}")
    visit_uid = _require_str(visit_uid, "visit_uid")
    visit_id = _require_str(visit_id, "visit_id")
    return role[0] + "_" + _sha256_hex(f"{visit_uid}|{visit_id}")[:24]


def derive_short_code(visit_uid: str) -> str:
    """Return ``visit_uid[:6].upper()``, the only id form ever shown in the UI."""
    return _require_str(visit_uid, "visit_uid")[:6].upper()


def subject_wire(subject: MemorySubject) -> dict[str, str]:
    """Return the ``{subject_kind, subject_id}`` wire form of a subject.

    The scope is omitted: memory_server's ``MemorySubjectRequest`` defaults it
    to ``kind:subject_id``, which is exactly what ``MemorySubject.create``
    produced here.
    """
    return {"subject_kind": subject.kind, "subject_id": subject.subject_id}


def group_chat_subject(pair_id: str) -> dict[str, str]:
    """Wire form of ``group_chat('neko_visit', pair_id)``."""
    return subject_wire(MemorySubject.group_chat(VISIT_MEMORY_PLATFORM, pair_id))


def group_participant_subject(pair_id: str, peer_char_id: str) -> dict[str, str]:
    """Wire form of ``group_participant('neko_visit', pair_id, peer_char_id)``."""
    return subject_wire(
        MemorySubject.group_participant(VISIT_MEMORY_PLATFORM, pair_id, peer_char_id)
    )


def participant_subject(person_id: str) -> dict[str, str]:
    """Wire form of ``participant('neko_visit', person_id)``."""
    return subject_wire(MemorySubject.participant(VISIT_MEMORY_PLATFORM, person_id))


def _field(state: Any, name: str) -> Any:
    if isinstance(state, Mapping):
        return state.get(name)
    return getattr(state, name, None)


def resolve_visit_recall_subjects(state: Any) -> list[dict[str, str]]:
    """Return the three visit subjects of one visit in budget priority order.

    ``state`` is a mapping (or an object with attributes) carrying ``own_uid``,
    ``peer_uid`` and either ``peer_char_tag`` or an already derived
    ``peer_char_id``. ``pair_id`` and ``person_id`` are always derived from the
    two uids, never trusted from the input. Any missing or malformed input
    yields ``[]``: visit memory is then off for this visit and the visit
    itself goes on.
    """
    own_uid = _field(state, "own_uid")
    peer_uid = _field(state, "peer_uid")
    char_tag = _field(state, "peer_char_tag")
    peer_char_id = _field(state, "peer_char_id")
    try:
        if not isinstance(peer_char_id, str) or not peer_char_id:
            peer_char_id = derive_peer_char_id(peer_uid, char_tag)
        pair_id = derive_pair_id(own_uid, peer_uid)
        person_id = derive_person_id(own_uid, peer_uid)
        return [
            group_chat_subject(pair_id),
            group_participant_subject(pair_id, peer_char_id),
            participant_subject(person_id),
        ]
    except (ValueError, MemoryScopeError):
        return []


# ── 名册 visit_peers.json ───────────────────────────────────────────────

_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def path_lock(path: Path) -> threading.Lock:
    """Return the process-wide thread lock guarding read-modify-write of ``path``."""
    key = str(Path(path).resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock


def _dict_at(parent: dict, key: str) -> dict:
    """Return ``parent[key]`` as a dict, creating it when absent.

    A present value of another type is damage, not "missing": rebuilding it as
    ``{}`` would let the next atomic write erase whatever was recoverable, so
    it raises :class:`RosterCorruptError` instead.
    """
    if key not in parent:
        parent[key] = {}
        return parent[key]
    value = parent[key]
    if not isinstance(value, dict):
        raise RosterCorruptError(f"roster {key!r} is not an object")
    return value


def _check_char_entry(entry: Any, where: str) -> dict:
    """Validate one ``by_char`` entry the way strict roster reads do.

    The entry must be an object; ``pairs`` (when present) a list of non-empty
    ids; ``chars`` (when present) an object keyed by non-empty ids. Raises
    :class:`RosterCorruptError`.
    """
    if not isinstance(entry, dict):
        raise RosterCorruptError(f"{where}: by_char entry is not an object")
    pairs = entry.get("pairs", [])
    if not isinstance(pairs, list) or not all(isinstance(p, str) and p for p in pairs):
        raise RosterCorruptError(f"{where}: pairs is not a list of ids")
    chars = entry.get("chars", {})
    if not isinstance(chars, dict) or not all(isinstance(c, str) and c for c in chars):
        raise RosterCorruptError(f"{where}: chars is not an object keyed by ids")
    return entry


def _merge_char_entries(target: dict, source: dict) -> dict:
    """Merge two ``by_char`` entries (used when a rename target already exists)."""
    merged = copy.deepcopy(target)
    pairs = list(merged.get("pairs") or [])
    for pair_id in source.get("pairs") or []:
        if pair_id not in pairs:
            pairs.append(pair_id)
    merged["pairs"] = pairs
    chars = dict(merged.get("chars") or {})
    for char_id, info in (source.get("chars") or {}).items():
        mine = chars.get(char_id)
        if not isinstance(mine, dict) or (
            isinstance(info, dict)
            and (info.get("last_seen") or 0) > (mine.get("last_seen") or 0)
        ):
            chars[char_id] = copy.deepcopy(info)
    merged["chars"] = chars
    src_summary = source.get("last_summary")
    dst_summary = merged.get("last_summary")
    if isinstance(src_summary, dict) and (
        not isinstance(dst_summary, dict)
        or (src_summary.get("ended_at") or 0) > (dst_summary.get("ended_at") or 0)
    ):
        merged["last_summary"] = copy.deepcopy(src_summary)
    return merged


class PeerRoster:
    """The local peer roster of one community account (``accounts[own_uid]``).

    Entry shape under ``accounts[own_uid].peers[peer_uid]``::

        {display_name, short_code, first_seen, last_seen,
         by_char: {<local character name>: {
             pairs: [pair_id],
             chars: {peer_char_id: {char_tag, display_name, last_seen}},
             last_summary?: {visit_id, ended_at, text}}}}

    Unknown top-level keys (for example the rename transaction's
    ``pending_rename``) and every other account's partition are preserved
    byte-for-byte across writes.
    """

    def __init__(self, config_dir: str | Path, *, own_uid: str) -> None:
        self.config_dir = Path(config_dir)
        self.own_uid = _require_str(own_uid, "own_uid")
        self.path = self.config_dir / VISIT_PEERS_FILENAME

    # ── 底层读写（工作线程内）──

    def _load(self, *, strict: bool) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            if strict:
                raise RosterCorruptError(f"cannot read {self.path.name}: {exc}") from exc
            logger.warning("visit roster unreadable, treating as empty: %s", exc)
            return {}
        if not isinstance(data, dict):
            if strict:
                raise RosterCorruptError(f"{self.path.name} is not a JSON object")
            logger.warning("visit roster is not a JSON object, treating as empty")
            return {}
        return data

    def _peers_view(self, data: dict) -> dict:
        accounts = data.get("accounts")
        if not isinstance(accounts, dict):
            return {}
        account = accounts.get(self.own_uid)
        if not isinstance(account, dict):
            return {}
        peers = account.get("peers")
        return peers if isinstance(peers, dict) else {}

    def _peers_mut(self, data: dict) -> dict:
        accounts = _dict_at(data, "accounts")
        account = _dict_at(accounts, self.own_uid)
        return _dict_at(account, "peers")

    def _char_view(self, data: dict, peer_uid: str, own_char: str) -> dict | None:
        peer = self._peers_view(data).get(peer_uid)
        if not isinstance(peer, dict):
            return None
        by_char = peer.get("by_char")
        if not isinstance(by_char, dict):
            return None
        entry = by_char.get(own_char)
        return entry if isinstance(entry, dict) else None

    def _char_view_strict(self, data: dict, peer_uid: str, own_char: str) -> dict | None:
        """Like :meth:`_char_view`, but a damaged structure raises instead of reading as absent.

        A missing key means "no entry"; a present key of the wrong type
        (``accounts`` / account / ``peers`` / peer / ``by_char`` / entry not
        an object, ``pairs`` not a list, ``chars`` not an object) raises
        :class:`RosterCorruptError`.
        """
        node: Any = data
        for key in ("accounts", self.own_uid, "peers", peer_uid, "by_char", own_char):
            if key not in node:
                return None
            node = node[key]
            if not isinstance(node, dict):
                raise RosterCorruptError(f"{self.path.name}: {key!r} is not an object")
        return _check_char_entry(node, self.path.name)

    def _mutate(self, fn) -> Any:
        with path_lock(self.path):
            data = self._load(strict=True)
            result, changed = fn(data)
            if changed:
                atomic_write_json(self.path, data)
            return result

    def _read(self, fn, strict: bool = False) -> Any:
        with path_lock(self.path):
            data = self._load(strict=strict)
        return fn(data)

    # ── 公开 API ──

    async def upsert(
        self,
        peer_uid: str,
        own_char: str,
        *,
        pair_id: str,
        peer_char_id: str,
        char_tag: str,
        char_display_name: str = "",
        display_name: str | None = None,
        now: float,
    ) -> None:
        """Record that ``peer_uid`` visited local character ``own_char``.

        Creates the peer and its ``by_char[own_char]`` entry if needed, adds
        ``pair_id`` to ``pairs`` and ``peer_char_id`` to ``chars``, and bumps
        ``last_seen``. ``display_name`` (the person) is only replaced when
        given; ``short_code`` is derived from ``peer_uid``.
        """
        _require_str(peer_uid, "peer_uid")
        _require_str(own_char, "own_char")
        _require_str(pair_id, "pair_id")
        _require_str(peer_char_id, "peer_char_id")

        def fn(data: dict):
            peers = self._peers_mut(data)
            peer = peers.get(peer_uid)
            if peer_uid in peers and not isinstance(peer, dict):
                # 已存在但类型坏了的行不能当新 peer 覆盖：by_char / 摘要 / pair 史都会丢
                raise RosterCorruptError(f"{self.path.name}: peer entry is not an object")
            if peer is None:
                peer = {
                    "display_name": display_name or "",
                    "short_code": derive_short_code(peer_uid),
                    "first_seen": now,
                    "last_seen": now,
                    "by_char": {},
                }
                peers[peer_uid] = peer
            if display_name is not None:
                peer["display_name"] = display_name
            peer.setdefault("short_code", derive_short_code(peer_uid))
            peer.setdefault("first_seen", now)
            peer["last_seen"] = max(now, peer.get("last_seen") or 0)
            by_char = _dict_at(peer, "by_char")
            entry = _dict_at(by_char, own_char)
            if "pairs" in entry and not isinstance(entry["pairs"], list):
                # 坏掉的 pairs 不能重建成 []：历史 pair 一丢，清除就再也展开不到它们
                raise RosterCorruptError(f"{self.path.name}: pairs is not a list")
            pairs = entry.setdefault("pairs", [])
            if pair_id not in pairs:
                pairs.append(pair_id)
            chars = _dict_at(entry, "chars")
            chars[peer_char_id] = {
                "char_tag": char_tag,
                "display_name": char_display_name,
                "last_seen": now,
            }
            return None, True

        await asyncio.to_thread(self._mutate, fn)

    async def remove_char(self, peer_uid: str, own_char: str) -> bool:
        """Delete ``by_char[own_char]`` of one peer; drop the peer once ``by_char`` is empty.

        Only the named local character is touched: the same person's entries
        under other local characters stay. The ``last_summary`` stored inside
        the entry goes with it. Returns whether anything was removed (False
        when the entry is already absent). A damaged structure on the path
        raises :class:`RosterCorruptError` so a revocation stays pending
        instead of recording the removal as done.
        """

        def fn(data: dict):
            node: Any = data
            for key in ("accounts", self.own_uid, "peers"):
                if key not in node:
                    return False, False
                node = node[key]
                if not isinstance(node, dict):
                    raise RosterCorruptError(f"{self.path.name}: {key!r} is not an object")
            peers = node
            if peer_uid not in peers:
                return False, False
            peer = peers[peer_uid]
            if not isinstance(peer, dict):
                raise RosterCorruptError(f"{self.path.name}: peer entry is not an object")
            # upsert 建 peer 时总带 by_char：已存在的 peer 缺它只能是损坏，不能当「已删除」
            if "by_char" not in peer:
                raise RosterCorruptError(f"{self.path.name}: peer entry has no by_char")
            by_char = peer["by_char"]
            if not isinstance(by_char, dict):
                raise RosterCorruptError(f"{self.path.name}: by_char is not an object")
            if own_char not in by_char:
                return False, False
            del by_char[own_char]
            if not by_char:
                del peers[peer_uid]
            return True, True

        return await asyncio.to_thread(self._mutate, fn)

    async def get_char_entry(self, peer_uid: str, own_char: str, *,
                             strict: bool = False) -> dict | None:
        """Return a deep copy of ``by_char[own_char]`` of one peer, or ``None``.

        ``strict=True`` raises :class:`RosterCorruptError` on an unreadable
        roster instead of reading it as empty (forget planning uses it).
        """

        def fn(data: dict):
            view = self._char_view_strict if strict else self._char_view
            entry = view(data, peer_uid, own_char)
            return copy.deepcopy(entry) if entry is not None else None

        return await asyncio.to_thread(self._read, fn, strict)

    async def get_peer(self, peer_uid: str) -> dict | None:
        """Return a deep copy of one peer entry of this account, or ``None``."""

        def fn(data: dict):
            peer = self._peers_view(data).get(peer_uid)
            return copy.deepcopy(peer) if isinstance(peer, dict) else None

        return await asyncio.to_thread(self._read, fn)

    async def list_peers(self) -> dict[str, dict]:
        """Return a deep copy of every peer entry of this account."""

        def fn(data: dict):
            return {
                uid: copy.deepcopy(peer)
                for uid, peer in self._peers_view(data).items()
                if isinstance(peer, dict)
            }

        return await asyncio.to_thread(self._read, fn)

    async def expand_subjects(
        self,
        peer_uid: str,
        own_char: str,
        current: tuple[str, str] | None = None,
    ) -> list[dict[str, str]]:
        """Expand every visit subject of one person under one local character.

        Order: ``group_chat(pair)`` for each pair, then
        ``group_participant(pair, peer_char)`` for each pair times each known
        peer character, then ``participant(person_id)``. ``current`` is the
        in-flight visit's ``(pair_id, peer_char_id)``, merged in even when it
        has not reached the roster yet. Duplicates are removed and other local
        characters' entries never contribute.

        Reads strictly: an unreadable roster raises :class:`RosterCorruptError`
        instead of expanding to the person subject alone (an incomplete
        revocation log would later finish and leave scoped memories behind).
        """
        _require_str(peer_uid, "peer_uid")

        def fn(data: dict):
            entry = self._char_view_strict(data, peer_uid, own_char) or {}
            pairs = [p for p in entry.get("pairs") or [] if isinstance(p, str) and p]
            chars_map = entry.get("chars")
            chars = [
                c for c in (chars_map.keys() if isinstance(chars_map, dict) else [])
                if isinstance(c, str) and c
            ]
            return pairs, chars

        pairs, chars = await asyncio.to_thread(self._read, fn, True)
        if current is not None:
            cur_pair, cur_char = current
            if cur_pair and cur_pair not in pairs:
                pairs.append(cur_pair)
            if cur_char and cur_char not in chars:
                chars.append(cur_char)
        out: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(subject: dict[str, str]) -> None:
            key = (subject["subject_kind"], subject["subject_id"])
            if key not in seen:
                seen.add(key)
                out.append(subject)

        for pair_id in pairs:
            add(group_chat_subject(pair_id))
        for pair_id in pairs:
            for char_id in chars:
                add(group_participant_subject(pair_id, char_id))
        add(participant_subject(derive_person_id(self.own_uid, peer_uid)))
        return out

    async def rename_char(self, old: str, new: str) -> int:
        """Move ``by_char[old]`` to ``by_char[new]`` for every peer in one atomic write.

        Local character names are machine-wide, so this deliberately walks
        every account partition, not only ``own_uid``: renaming only the active
        account would strand the other accounts' entries under a name that no
        longer exists. ``last_summary`` moves along unchanged. Idempotent: a
        rerun after completion finds no ``old`` entry; if both names exist the
        entries are merged (newer ``last_seen`` / ``ended_at`` wins). Returns
        the number of moved entries. ``rename_char(new, old)`` undoes it.
        """
        _require_str(old, "old")
        _require_str(new, "new")
        if old == new:
            return 0

        def fn(data: dict):
            # 改名是事务的一步：任何分区结构坏了都不能跳过（pending_rename 会被清掉、
            # 那个分区里的条目永远停在旧名下），一律报损坏让事务保留标记
            moved = 0
            if "accounts" not in data:
                return 0, False
            accounts = data["accounts"]
            if not isinstance(accounts, dict):
                raise RosterCorruptError(f"{self.path.name}: accounts is not an object")
            for account in accounts.values():
                if not isinstance(account, dict):
                    raise RosterCorruptError(f"{self.path.name}: account entry is not an object")
                if "peers" not in account:
                    continue
                peers = account["peers"]
                if not isinstance(peers, dict):
                    raise RosterCorruptError(f"{self.path.name}: peers is not an object")
                for peer in peers.values():
                    if not isinstance(peer, dict) or not isinstance(peer.get("by_char"), dict):
                        raise RosterCorruptError(f"{self.path.name}: peer entry is malformed")
                    by_char = peer["by_char"]
                    if old not in by_char:
                        continue
                    # 源或目标条目（含嵌套的 pairs / chars）坏了：覆盖或合并都会丢掉
                    # 可恢复的数据（字符串 pairs 会被拆成单个字符），改名事务保留标记
                    entry = _check_char_entry(by_char[old], self.path.name)
                    target = by_char.get(new)
                    if new in by_char:
                        _check_char_entry(target, self.path.name)
                    del by_char[old]
                    by_char[new] = _merge_char_entries(target, entry) if new in by_char else entry
                    moved += 1
            return moved, moved > 0

        return await asyncio.to_thread(self._mutate, fn)

    async def set_last_summary(
        self,
        peer_uid: str,
        own_char: str,
        *,
        visit_id: str,
        ended_at: float,
        text: str,
        pair_id: str,
    ) -> bool:
        """Store the last-visit summary of one person with one local character.

        Writes only when ``by_char[own_char]`` exists and its ``pairs`` holds
        ``pair_id`` (a completed "forget this person" must not be undone by a
        late summary), and only when no stored summary has a later
        ``ended_at``. Never creates entries. Returns whether it wrote.
        """

        def fn(data: dict):
            entry = self._char_view(data, peer_uid, own_char)
            if entry is None:
                return False, False
            pairs = entry.get("pairs")
            if not isinstance(pairs, list) or pair_id not in pairs:
                return False, False
            existing = entry.get("last_summary")
            if isinstance(existing, dict):
                prev = existing.get("ended_at")
                if isinstance(prev, (int, float)) and prev > ended_at:
                    return False, False
            entry["last_summary"] = {
                "visit_id": visit_id,
                "ended_at": ended_at,
                "text": text,
            }
            return True, True

        return await asyncio.to_thread(self._mutate, fn)

    async def get_last_summary(self, peer_uid: str, own_char: str) -> dict | None:
        """Return ``by_char[own_char].last_summary`` of one person, or ``None``.

        Looked up by exactly this ``(peer_uid, own_char)`` pair in this
        account; never falls back to another character or another person.
        """

        def fn(data: dict):
            entry = self._char_view(data, peer_uid, own_char)
            if entry is None:
                return None
            summary = entry.get("last_summary")
            return copy.deepcopy(summary) if isinstance(summary, dict) else None

        return await asyncio.to_thread(self._read, fn)

    async def clear_last_summary(self, peer_uid: str, own_char: str) -> bool:
        """Delete ``by_char[own_char].last_summary`` of one person (first forget step)."""

        def fn(data: dict):
            entry = self._char_view(data, peer_uid, own_char)
            if entry is None or "last_summary" not in entry:
                return False, False
            del entry["last_summary"]
            return True, True

        return await asyncio.to_thread(self._mutate, fn)
