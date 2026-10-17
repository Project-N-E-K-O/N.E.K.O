"""Durable, consented static profiles; chat originals and memory stay independent."""

from __future__ import annotations

import asyncio
from contextlib import closing
import copy
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from services.chat_sync.writer import store_connection
from uuid import uuid4

from services.chat_sync.profile_protocol import (
    RESERVED_PROFILE_FIELD_KEYS, decode_character_profile, encode_character_profile,
)
from utils.config_manager.reserved_schema import get_character_uid


def _canonical(value):
    """Keep pending operation bytes fixed across response loss and process restarts."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(raw):
    """Compare original settings without storing effective model prompts."""
    return hashlib.sha256(raw).hexdigest()


def _field_text(value):
    """Existing card forms save strings; retain legacy finite JSON scalar spelling."""
    if isinstance(value, str):
        return value
    if type(value) in {int, bool} or (type(value) is float and math.isfinite(value)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    # Structured values need a separate reviewed mapping, never str(dict)/joins.
    raise ValueError("character_profile_unsupported_value")


def export_profile(name: str, raw: dict) -> dict:
    """Project only portable raw fields; never infer aliases or copy reserved state."""
    from utils.config_manager import get_reserved

    def fields(payload, order=()):
        """Preserve saved order and exact strings instead of normalizing prompt text."""
        if not isinstance(payload, dict) or any(not isinstance(key, str) for key in [*order, *payload]):
            raise ValueError("character_profile_unsupported_value")
        keys = list(dict.fromkeys([*order, *payload]))
        result = []
        for key in keys:
            if key not in payload or key.startswith("_") or key in RESERVED_PROFILE_FIELD_KEYS or key == "档案名":
                continue
            result.append({"key": key, "value": _field_text(payload[key])})
        return result

    ordinary = {key: value for key, value in raw.items() if key != "昵称"}
    order = get_reserved(raw, "field_order", default=[])
    override = get_reserved(raw, "persona_override", default=None)
    persona = None
    if isinstance(override, dict):
        persona = {"presetId": override.get("preset_id", ""), "promptGuidance": override.get("prompt_guidance", ""),
                   "fields": fields(override.get("profile", {}))}
    profile = {"schemaVersion": 1, "name": name, "nickname": raw.get("昵称", ""),
               "systemPrompt": get_reserved(raw, "system_prompt", default="", legacy_keys=("system_prompt",)),
               "fields": fields(ordinary, order if isinstance(order, list) else ()), "persona": persona}
    # Validation is shared byte-for-byte with the independent service codec.
    return decode_character_profile(encode_character_profile(profile))


async def read_character_directory(request, owner):
    """Read bounded owner-scoped pages without silently truncating cloud characters."""
    from services.chat_sync.ledger import ChatDomain

    # Beta accounts have no role quota. Pagination bounds each response; any future
    # paid-plan quota is separate server policy, never an accidental page-size limit.
    roles, seen, after = [], set(), None
    while True:
        path = '/prototype/chat-sync/characters?limit=100' + (f'&after={after}' if after else '')
        result = await request('GET', path, None)
        if (not isinstance(result, dict) or result.keys() not in ({'characters'}, {'characters', 'nextCursor'})
                or not isinstance(result['characters'], list)):
            raise ValueError('invalid_character_profile_response')
        paginated = 'nextCursor' in result
        page = result['characters']
        # A legacy server can ignore the query only on the first, complete read.
        # Accepting that shape midway through paging could lose or duplicate roles.
        if (not paginated and after is not None) or (paginated and len(page) > 100):
            raise ValueError('invalid_character_profile_response')
        previous = after
        for role in page:
            if not isinstance(role, dict) or role.keys() != {'characterSyncId', 'displayName'}:
                raise ValueError('invalid_character_profile_response')
            uid, name = role['characterSyncId'], role['displayName']
            try:
                ChatDomain(owner, uid)
                if not isinstance(name, str) or not name or len(name) > 200 or len(name.encode('utf-8')) > 800:
                    raise ValueError('invalid_character_profile_response')
            except ValueError:
                raise ValueError('invalid_character_profile_response') from None
            if uid in seen or (paginated and previous is not None and uid <= previous):
                raise ValueError('invalid_character_profile_response')
            roles.append(role)
            seen.add(uid)
            previous = uid
        if not paginated or result['nextCursor'] is None:
            return {'characters': roles}
        if not page or result['nextCursor'] != page[-1]['characterSyncId']:
            raise ValueError('invalid_character_profile_response')
        after = result['nextCursor']


class ProfileStore:
    """Retain local/remote versions and approved immutable requests per bound owner."""

    def __init__(self, path: Path):
        """Use a separate SQLite file outside Steam-managed character/memory trees."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS profile_state (
                    domain TEXT PRIMARY KEY, local BLOB NOT NULL, local_hash TEXT NOT NULL,
                    base_revision INTEGER NOT NULL DEFAULT 0, base_hash TEXT,
                    head_revision INTEGER NOT NULL DEFAULT 0, head_hash TEXT, head BLOB,
                    dirty INTEGER NOT NULL DEFAULT 1, apply_pending INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS profile_versions (
                    domain TEXT NOT NULL, hash TEXT NOT NULL, profile BLOB NOT NULL,
                    PRIMARY KEY(domain,hash));
                CREATE TABLE IF NOT EXISTS profile_outbox (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT UNIQUE NOT NULL,
                    domain TEXT NOT NULL, body BLOB NOT NULL, profile_hash TEXT NOT NULL,
                    base_revision INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
                CREATE TABLE IF NOT EXISTS profile_imports (
                    domain TEXT PRIMARY KEY, intent_id TEXT NOT NULL,
                    head BLOB NOT NULL, completed INTEGER NOT NULL DEFAULT 0);
            """)

    def _connect(self):
        """Close every handle after a short FULL-synchronous local transaction."""
        conn = store_connection(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def observe(self, domain: str, profile: dict):
        """A declined upload still becomes dirty, protecting it from remote replacement."""
        raw = encode_character_profile(profile)
        digest = _digest(raw)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO profile_versions VALUES (?,?,?)", (domain, digest, raw))
            conn.execute("INSERT OR IGNORE INTO profile_state(domain,local,local_hash) VALUES (?,?,?)", (domain, raw, digest))
            conn.execute("UPDATE profile_state SET local=?,local_hash=?,dirty=1,apply_pending=0 WHERE domain=? AND local_hash!=?",
                         (raw, digest, domain, digest))
        return self.status(domain)

    def status(self, domain):
        """Expose two retained candidates without merging or duplicating role identities."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM profile_state WHERE domain=?", (domain,)).fetchone()
            if row is None:
                return None
            pending = conn.execute("SELECT COUNT(*) FROM profile_outbox WHERE domain=? AND status='pending'", (domain,)).fetchone()[0]
        value = dict(row)
        value["local"] = decode_character_profile(bytes(value["local"]))
        value["head"] = decode_character_profile(bytes(value["head"])) if value["head"] is not None else None
        value["pending"] = pending
        value["conflict"] = bool(value["dirty"] and value["head_revision"] > value["base_revision"]
                                 and value["head_hash"] != value["local_hash"])
        value["renameRequired"] = bool(value["head"] and value["head"]["name"] != value["local"]["name"])
        return value

    def remote(self, domain, head):
        """Persist a verified head first; dirty local settings never become disposable."""
        raw = encode_character_profile(head["profile"]) if head["profile"] is not None else None
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT * FROM profile_state WHERE domain=?", (domain,)).fetchone()
            if previous is None or head["revision"] < previous["head_revision"]:
                raise ValueError("invalid_character_profile_response")
            if head["revision"] == previous["head_revision"] and head["profileHash"] != previous["head_hash"]:
                raise ValueError("invalid_character_profile_response")
            if raw is not None:
                conn.execute("INSERT OR IGNORE INTO profile_versions VALUES (?,?,?)", (domain, head["profileHash"], raw))
            conn.execute("UPDATE profile_state SET head_revision=?,head_hash=?,head=? WHERE domain=?",
                         (head["revision"], head["profileHash"], raw, domain))
            if raw is not None and head["profileHash"] == previous["local_hash"]:
                conn.execute("UPDATE profile_state SET base_revision=?,base_hash=?,dirty=0,apply_pending=0 WHERE domain=?",
                             (head["revision"], head["profileHash"], domain))
                # Identical concurrent publications converge without a content
                # choice. Retain obsolete request bytes but unblock new consent.
                conn.execute("UPDATE profile_outbox SET status='superseded' WHERE domain=? AND status IN ('pending','conflict')", (domain,))
            elif raw is not None and not previous["dirty"]:
                conn.execute("UPDATE profile_state SET apply_pending=1 WHERE domain=?", (domain,))

    def approve(self, domain, expected_hash, expected_revision, *, resolve=False, operation_id=None):
        """Consent pins one exact local snapshot and CAS base; retries never re-read it."""
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if operation_id is not None:
                previous = conn.execute("SELECT domain,profile_hash FROM profile_outbox WHERE operation_id=?", (operation_id,)).fetchone()
                if previous:
                    if tuple(previous) != (domain, expected_hash):
                        raise ValueError("character_profile_changed")
                    return operation_id
            row = conn.execute("SELECT * FROM profile_state WHERE domain=?", (domain,)).fetchone()
            if row is None or row["local_hash"] != expected_hash or row["head_revision"] != expected_revision:
                raise ValueError("character_profile_changed")
            if not resolve and row["head_revision"] > row["base_revision"] and row["head_hash"] != expected_hash:
                raise ValueError("character_profile_conflict")
            if resolve:
                # Superseded requests remain archived rather than being rewritten.
                conn.execute("UPDATE profile_outbox SET status='superseded' WHERE domain=? AND status IN ('pending','conflict')", (domain,))
            previous = conn.execute("SELECT base_revision,profile_hash,operation_id FROM profile_outbox WHERE domain=? AND status='pending' ORDER BY seq DESC LIMIT 1", (domain,)).fetchone()
            if previous and previous["profile_hash"] == expected_hash:
                return previous["operation_id"]
            base = previous[0] + 1 if previous else expected_revision
            operation_id = operation_id or uuid4().hex
            body = _canonical({"operationId": operation_id, "baseRevision": base, "profile": decode_character_profile(bytes(row["local"]))})
            conn.execute("INSERT INTO profile_outbox(operation_id,domain,body,profile_hash,base_revision) VALUES (?,?,?,?,?)",
                         (operation_id, domain, body, expected_hash, base))
        return operation_id

    def next_operation(self, domain):
        """Only the oldest approved request may advance its predicted CAS chain."""
        with closing(self._connect()) as conn:
            if conn.execute("SELECT 1 FROM profile_outbox WHERE domain=? AND status='conflict'", (domain,)).fetchone():
                return None
            row = conn.execute("SELECT * FROM profile_outbox WHERE domain=? AND status='pending' ORDER BY seq LIMIT 1", (domain,)).fetchone()
        return dict(row) if row else None

    def conflict(self, operation_id):
        """Keep a stale approved request intact until the user chooses a new operation."""
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE profile_outbox SET status='conflict' WHERE operation_id=? AND status='pending'", (operation_id,))

    def ack(self, domain, operation, receipt):
        """Acknowledge only the fixed operation hash and exact successor revision."""
        expected = {"ownerId": json.loads(domain)[0], "characterSyncId": json.loads(domain)[1],
                    "operationId": operation["operation_id"], "revision": operation["base_revision"] + 1,
                    "profileHash": operation["profile_hash"]}
        if receipt != expected:
            raise ValueError("invalid_character_profile_receipt")
        profile = json.loads(operation["body"])["profile"]
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM profile_state WHERE domain=?", (domain,)).fetchone()
            conn.execute("UPDATE profile_outbox SET status='ack' WHERE operation_id=? AND body=?",
                         (operation["operation_id"], operation["body"]))
            # A repeated old receipt cannot split a newer revision/hash pair.
            if row["base_revision"] <= receipt["revision"]:
                conn.execute("UPDATE profile_state SET base_revision=?,base_hash=? WHERE domain=?",
                             (receipt["revision"], receipt["profileHash"], domain))
            if row["head_revision"] <= receipt["revision"]:
                conn.execute("UPDATE profile_state SET head_revision=?,head_hash=?,head=? WHERE domain=?",
                             (receipt["revision"], receipt["profileHash"], encode_character_profile(profile), domain))
            if row["local_hash"] == receipt["profileHash"] and row["head_revision"] <= receipt["revision"]:
                conn.execute("UPDATE profile_state SET dirty=0,apply_pending=0 WHERE domain=?", (domain,))

    def choose_remote(self, domain, expected_hash, expected_revision):
        """Choosing the retained remote candidate is explicit and generation-specific."""
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM profile_state WHERE domain=?", (domain,)).fetchone()
            if row is None or row["local_hash"] != expected_hash or row["head_revision"] != expected_revision or row["head"] is None:
                raise ValueError("character_profile_changed")
            conn.execute("UPDATE profile_outbox SET status='superseded' WHERE domain=? AND status IN ('pending','conflict')", (domain,))
            conn.execute("UPDATE profile_state SET dirty=0,apply_pending=1 WHERE domain=?", (domain,))

    def applied(self, domain, expected_hash, revision):
        """Commit the selected remote baseline after configuration persistence succeeds."""
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM profile_state WHERE domain=?", (domain,)).fetchone()
            if row["local_hash"] != expected_hash or row["head_revision"] != revision or row["dirty"]:
                raise ValueError("character_profile_changed")
            conn.execute("UPDATE profile_state SET local=head,local_hash=head_hash,base_revision=head_revision,base_hash=head_hash,apply_pending=0 WHERE domain=?", (domain,))

    def import_intent(self, domain, head):
        """Pin a reviewed import before config creation; retries retain its exact identity."""
        with closing(self._connect()) as conn, conn:
            # Completed imports no longer pin a later explicit recreation to an
            # obsolete version. Only an unfinished crash recovery keeps its head.
            conn.execute("DELETE FROM profile_imports WHERE domain=? AND completed=1", (domain,))
            conn.execute("INSERT OR IGNORE INTO profile_imports(domain,intent_id,head) VALUES (?,?,?)", (domain, uuid4().hex, _canonical(head)))
            row = conn.execute("SELECT * FROM profile_imports WHERE domain=?", (domain,)).fetchone()
        return json.loads(row["head"])

    def unfinished_import(self, domain):
        """A previous reviewed snapshot remains recoverable after the cloud head moves."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT head FROM profile_imports WHERE domain=? AND completed=0", (domain,)).fetchone()
        return json.loads(row[0]) if row else None

    def complete_import(self, domain):
        """Mark completion only after configuration and binding persistence succeed."""
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE profile_imports SET completed=1 WHERE domain=?", (domain,))


class DesktopProfileAdapter:
    """Read saved raw profiles and apply approved revisions without memory edit APIs."""

    def __init__(self, config_manager):
        """Keep the chosen storage root; no runtime data is loaded at construction."""
        self.config = config_manager

    async def read(self, uid):
        """Resolve by immutable UID, never a copied name or card-local identifier."""
        characters = await self.config.aload_characters()
        for name, raw in characters.get("猫娘", {}).items():
            if isinstance(raw, dict) and get_character_uid(raw) == uid:
                return export_profile(name, raw)
        raise ValueError("chat_sync_character_not_found")

    def busy(self, name):
        """An active/starting conversation keeps its current prompt until it is idle."""
        from main_routers.shared_state import get_session_manager
        from main_logic.session_state import ProactivePhase, session_reply_in_progress
        manager = (get_session_manager() or {}).get(name)
        state = getattr(manager, "state", None)
        # The existing state machine owns streaming/playback; a connected idle
        # session alone is not a reason to defer settings indefinitely.
        return bool(manager and (getattr(manager, "is_starting", False)
                                 or session_reply_in_progress(getattr(manager, "session", None))
                                 or getattr(manager, "_is_voice_playing", lambda: False)()
                                 or getattr(state, "phase", ProactivePhase.IDLE) is not ProactivePhase.IDLE))

    async def apply(self, uid, status, commit, *, confirm_rename=False, before_apply=None):
        """Preserve identity/avatar/preferences and bypass greeting/recent-clear paths."""
        from utils.character_memory import character_config_mutation_lock
        from utils.config_manager import delete_reserved, set_reserved

        async with character_config_mutation_lock:
            if before_apply is not None:
                await before_apply()
            current = await self.read(uid)
            if _digest(encode_character_profile(current)) != status["local_hash"]:
                raise ValueError("character_profile_changed")
            if self.busy(current["name"]):
                return False
            target = status["head"]
            if any(field["key"] in {"昵称", "档案名"} for field in target["fields"]):
                # These literal wire fields cannot coexist with the desktop's
                # single native nickname/name slots; retain both versions safely.
                raise ValueError("character_profile_unsupported_value")
            if target["name"] != current["name"]:
                if not confirm_rename:
                    return False
                from main_routers.characters_router._shared import _validate_profile_name
                if _validate_profile_name(target["name"]) or target["name"] != target["name"].strip():
                    # The portable wire name does not relax desktop filename or
                    # reserved-route constraints used by the normal rename UI.
                    raise ValueError("character_profile_name_invalid")
                # The user's explicit choice reuses the existing complete rename
                # transaction; a dictionary-key edit would orphan local histories.
                from main_routers.characters_router.crud import _rename_catgirl_serialized
                from utils.external_route_registry import end_character_mutation
                try:
                    result = await _rename_catgirl_serialized(current["name"], target["name"], sync_commit=commit)
                finally:
                    end_character_mutation(current["name"], target["name"])
                if not isinstance(result, dict) or not result.get("success"):
                    raise ValueError("character_profile_rename_failed")
            characters = await self.config.aload_characters()
            raw = characters.get("猫娘", {}).get(target["name"])
            if not isinstance(raw, dict) or get_character_uid(raw) != uid:
                raise ValueError("chat_sync_character_not_found")
            previous_raw = raw
            raw = copy.deepcopy(raw)
            for key in list(raw):
                if not key.startswith("_") and key not in RESERVED_PROFILE_FIELD_KEYS and key != "档案名":
                    raw.pop(key)
            for field in target["fields"]:
                key, value = field["key"], field["value"]
                # Preserve existing legacy scalar types when their canonical
                # spelling already matches, avoiding unrelated local rewrites.
                previous = previous_raw.get(key)
                raw[key] = previous if type(previous) in {int, bool, float} and _field_text(previous) == value else value
            raw["昵称"] = target["nickname"]
            set_reserved(raw, "field_order", [field["key"] for field in target["fields"]] + ["昵称"])
            set_reserved(raw, "system_prompt", target["systemPrompt"])
            raw.pop("system_prompt", None)
            if target["persona"] is None:
                delete_reserved(raw, "persona_override")
            else:
                persona = target["persona"]
                set_reserved(raw, "persona_override", {"preset_id": persona["presetId"], "prompt_guidance": persona["promptGuidance"],
                             "profile": {item["key"]: item["value"] for item in persona["fields"]}, "source": "chat_sync", "selected_at": ""})
            characters["猫娘"][target["name"]] = raw
            await commit(self.config.save_characters, characters)
        # The existing fast edit refresh does not call a model, mark a greeting,
        # clear recent history, or restart an existing active session.
        from main_routers.shared_state import get_init_one_catgirl
        await get_init_one_catgirl()(target["name"], is_new=False)
        return True

    async def import_new(self, uid, profile, commit):
        """Explicit imports create once with the cloud UID and local avatar/voice defaults."""
        from config.character_defaults import DEFAULT_LANLAN_TEMPLATE
        from main_routers.characters_router.crud import _get_new_catgirl_default_voice_id
        from main_routers.characters_router._shared import _validate_profile_name
        from utils.character_memory import character_config_mutation_lock
        from utils.config_manager import set_reserved
        from utils.config_manager.reserved_schema import ensure_catgirl_character_id

        async with character_config_mutation_lock:
            characters = await self.config.aload_characters()
            for name, raw in characters.get("猫娘", {}).items():
                if isinstance(raw, dict) and get_character_uid(raw) == uid:
                    return name, True
            name = profile["name"]
            if _validate_profile_name(name) or name != name.strip():
                raise ValueError("character_profile_name_invalid")
            if name in characters.get("猫娘", {}):
                raise ValueError("character_profile_name_conflict")
            if any(field["key"] in {"昵称", "档案名"} for field in profile["fields"]):
                raise ValueError("character_profile_unsupported_value")
            raw = copy.deepcopy(next(iter(DEFAULT_LANLAN_TEMPLATE.values())))
            for key in list(raw):
                if not key.startswith("_"):
                    raw.pop(key)
            raw.update({field["key"]: field["value"] for field in profile["fields"]})
            raw["昵称"] = profile["nickname"]
            set_reserved(raw, "character_uid", uid)
            ensure_catgirl_character_id(raw)
            set_reserved(raw, "voice_id", _get_new_catgirl_default_voice_id())
            set_reserved(raw, "system_prompt", profile["systemPrompt"])
            set_reserved(raw, "field_order", [field["key"] for field in profile["fields"]] + ["昵称"])
            if profile["persona"] is not None:
                persona = profile["persona"]
                set_reserved(raw, "persona_override", {"preset_id": persona["presetId"], "prompt_guidance": persona["promptGuidance"],
                             "profile": {item["key"]: item["value"] for item in persona["fields"]}, "source": "chat_sync", "selected_at": ""})
            characters.setdefault("猫娘", {})[name] = raw
            await commit(self.config.save_characters, characters)
        # Do not mark a new-character greeting, select this role, or touch memory.
        from main_routers.shared_state import get_init_one_catgirl
        await get_init_one_catgirl()(name, is_new=False)
        return name, False


class DesktopProfileSync:
    """Share HTTPS/account/consent fences while keeping profile versions independent."""

    def __init__(self, runtime, path, adapter=None):
        """Synthetic clients opt in to an injected adapter, never real saved profiles."""
        self.runtime, self.adapter = runtime, adapter
        self.store = ProfileStore(path)
        self.lock = asyncio.Lock()

    async def local(self, uid):
        """Capture current saved settings before the UI asks about uploading them."""
        from services.chat_sync.recovery import role_pending
        if await asyncio.to_thread(role_pending, self.runtime, uid):
            raise ValueError("chat_sync_writer_recovery_required")
        binding = await asyncio.to_thread(self.runtime.state.binding, uid)
        if not binding or self.adapter is None:
            raise ValueError("chat_sync_not_bound")
        from services.chat_sync.ledger import ChatDomain
        domain = ChatDomain(binding["owner_id"], uid).key()
        profile = await self.adapter.read(uid)
        return await asyncio.to_thread(self.store.observe, domain, profile)

    async def approve(self, uid, local_hash, revision, *, resolve=False):
        """Durably accept explicit consent even offline; the worker sends it later."""
        state = await self.local(uid)
        if state["local_hash"] != local_hash:
            raise ValueError("character_profile_changed")
        operation_id = await asyncio.to_thread(self.store.approve, state["domain"], local_hash, revision, resolve=resolve)
        self.runtime.wake_scheduler()
        return {"operationId": operation_id, "pending": True}

    async def cloud_roles(self):
        """List only roles belonging to the service-verified currently logged-in owner."""
        token, fingerprint = await self.runtime._session()
        owner = await self.runtime._identity(token)

        async def request(method, path, body):
            # Every page keeps the original credential snapshot, including before send.
            return await self.runtime._request(method, path, token, body=body,
                before_send=lambda: self.runtime._same_session(fingerprint))

        result = await read_character_directory(request, owner)
        await self.runtime._same_session(fingerprint)
        return result

    def _head(self, uid, owner, head):
        """Validate profile identity, canonical hash and the empty-head contract."""
        expected_keys = {"ownerId", "characterSyncId", "revision", "profileHash", "profile"}
        if (head.keys() != expected_keys or head["ownerId"] != owner or head["characterSyncId"] != uid
                or type(head["revision"]) is not int or not 0 <= head["revision"] <= 2**53 - 1):
            raise ValueError("invalid_character_profile_response")
        if head["revision"] == 0:
            if head["profile"] is not None or head["profileHash"] is not None:
                raise ValueError("invalid_character_profile_response")
        elif head["profile"] is None or _digest(encode_character_profile(head["profile"])) != head["profileHash"]:
            raise ValueError("invalid_character_profile_response")
        return head

    def _version(self, uid, owner, head):
        """Validate small poll metadata independently of any retained/full definition."""
        if (not isinstance(head, dict) or head.keys() != {"ownerId", "characterSyncId", "revision", "profileHash"}
                or head["ownerId"] != owner or head["characterSyncId"] != uid
                or type(head["revision"]) is not int or not 0 <= head["revision"] <= 2**53 - 1
                or (head["revision"] == 0 and head["profileHash"] is not None)
                or (head["revision"] > 0 and (not isinstance(head["profileHash"], str)
                    or len(head["profileHash"]) != 64 or any(c not in "0123456789abcdef" for c in head["profileHash"])))):
            raise ValueError("invalid_character_profile_response")
        return head

    async def preview(self, uid):
        """Read a cloud profile for explicit import; this never creates or binds a role."""
        from services.chat_sync.ledger import ChatDomain
        token, fingerprint = await self.runtime._session()
        owner = await self.runtime._identity(token)
        ChatDomain(owner, uid)
        head = await self.runtime._request("GET", f"/prototype/chat-sync/characters/{uid}/profile", token)
        await self.runtime._same_session(fingerprint)
        head = self._head(uid, owner, head)
        # A resumed explicit import shows its already approved immutable intent,
        # rather than silently changing the configuration midway through recovery.
        retained = await asyncio.to_thread(self.store.unfinished_import, ChatDomain(owner, uid).key())
        return retained or head

    async def import_role(self, uid, revision, profile_hash):
        """A fixed local intent recovers the config/binding crash gap without duplicate roles."""
        if self.adapter is None:
            raise ValueError("chat_sync_unavailable")
        writer_epoch = await asyncio.to_thread(self.runtime.writer.epoch)
        async with self.lock:
            head = await self.preview(uid)
            await asyncio.to_thread(self.runtime.writer.require_epoch, writer_epoch)
            if head["revision"] != revision or head["profileHash"] != profile_hash or head["profile"] is None:
                raise ValueError("character_profile_changed")
            from services.chat_sync.ledger import ChatDomain
            domain = ChatDomain(head["ownerId"], uid).key()
            token, fingerprint = await self.runtime._session()
            if await self.runtime._identity(token) != head["ownerId"]:
                raise ValueError("chat_sync_account_mismatch")
            binding = await asyncio.to_thread(self.runtime.state.binding, uid)
            if binding and binding["owner_id"] != head["ownerId"]:
                raise ValueError("chat_sync_account_mismatch")
            unfinished = await asyncio.to_thread(self.store.unfinished_import, domain)
            try:
                existing_profile = await self.adapter.read(uid)
            except ValueError as exc:
                if str(exc) != "chat_sync_character_not_found":
                    raise
                existing_profile = None
            if existing_profile is not None and unfinished is None:
                # Opening an existing UID shows its current consent state. It
                # never duplicates the role or silently resumes a local pause.
                return {"characterSyncId": uid, "displayName": existing_profile["name"], "existing": True,
                        "enabled": bool(binding and binding["enabled"]), "profileHash": _digest(encode_character_profile(existing_profile))}
            async def commit(operation, *args):
                """Before binding exists, pin creation to the same locally guarded account."""
                await self.runtime._same_session(fingerprint)
                def apply():
                    with self.runtime.session_commit_guard(fingerprint), self.runtime._commit_lock, self.runtime.writer.locked():
                        self.runtime.writer.require_epoch(writer_epoch)
                        if self.runtime._closing:
                            raise ValueError("chat_sync_closing")
                        binding = self.runtime.state.binding(uid)
                        if binding and binding["owner_id"] != head["ownerId"]:
                            raise ValueError("chat_sync_account_mismatch")
                        return operation(*args)
                return await asyncio.to_thread(apply)
            # prepare_binding provides the existing durable request ID and rejects
            # old-owner reuse before config creation or any cloud write.
            await commit(self.runtime.state.prepare_binding, ChatDomain(head["ownerId"], uid))
            retained = await commit(self.store.import_intent, domain, head)
            name, existing = await self.adapter.import_new(uid, retained["profile"], commit)
            await asyncio.to_thread(self.runtime.writer.require_epoch, writer_epoch)
            local = await self.local(uid)
            await commit(self.store.remote, domain, retained)
            await self.runtime.enable(uid, name, expected_writer_epoch=writer_epoch)
            await commit(self.store.complete_import, domain)
            return {"characterSyncId": uid, "displayName": name, "existing": existing, "enabled": True,
                    "profileHash": local["local_hash"]}

    async def step(self, uid, *, presence=None, manual=False, choose_remote=False, local_hash=None, revision=None, confirm_rename=False):
        """Poll at the existing two-minute cadence and retry approved requests exactly."""
        if self.adapter is None:
            return None
        async with self.lock:
            if not manual:
                await self.runtime._require_automatic_character(uid)
            state = await self.local(uid)
            binding = await asyncio.to_thread(self.runtime.state.binding, uid)
            if not binding or not binding["enabled"] or not binding["binding_id"]:
                raise ValueError("chat_sync_not_enabled")
            authenticated = await self.runtime._session() if presence is None else None
            presence = presence or authenticated[1]
            scheduler = self.runtime.scheduler
            if not await asyncio.to_thread(scheduler.claim, state["domain"], "profile", binding["generation"], presence, manual=manual):
                if choose_remote:
                    # The UI must retain its reviewed candidates when service
                    # backpressure prevents an explicit resolution right now.
                    raise ValueError("chat_sync_retry_later")
                return state
            try:
                authenticated = authenticated or await self.runtime._session()
                if authenticated[1] != presence:
                    raise ValueError("chat_sync_session_changed")
                _, request, commit = await self.runtime._bound_transport(uid, authenticated=authenticated, expected_binding=binding,
                    automatic_only=not manual)
                path = f"/prototype/chat-sync/characters/{uid}/profile"
                operation = await asyncio.to_thread(self.store.next_operation, state["domain"])
                if operation is not None and not choose_remote:
                    try:
                        receipt = await request("PUT", path, bytes(operation["body"]))
                        await commit(self.store.ack, state["domain"], operation, receipt)
                    except ValueError as exc:
                        if not getattr(exc, "profile_conflict", False):
                            raise
                        await commit(self.store.conflict, operation["operation_id"])
                state = await asyncio.to_thread(self.store.status, state["domain"])
                head = await request("GET", path + "?metadataOnly=true", None)
                if "profile" in head:
                    # A pre-upgrade service ignores the new query and returns its old full response.
                    self._head(uid, binding["owner_id"], head)
                    await commit(self.store.remote, state["domain"], head)
                else:
                    version = self._version(uid, binding["owner_id"], head)
                    if (version["revision"] < state["head_revision"] or (version["revision"] == state["head_revision"]
                            and version["profileHash"] != state["head_hash"])):
                        raise ValueError("invalid_character_profile_response")
                    if version["revision"] != state["head_revision"] or (version["revision"] and state["head"] is None):
                        head = self._head(uid, binding["owner_id"], await request("GET", path, None))
                        # A concurrent publication may advance the full read, never roll it back or change an existing hash.
                        if (head["revision"] < version["revision"] or (head["revision"] == version["revision"]
                                and head["profileHash"] != version["profileHash"])):
                            raise ValueError("invalid_character_profile_response")
                        await commit(self.store.remote, state["domain"], head)
                    elif (state["dirty"] or state["pending"]) and state["head"] is not None and state["local_hash"] == state["head_hash"]:
                        # Reconcile from verified cached bytes after a config-save crash or an ACK of the
                        # final local definition. ACK can already clear dirty while older approved entries
                        # remain; remote() supersedes them so they cannot republish an obsolete persona.
                        await commit(self.store.remote, state["domain"], {**version, "profile": state["head"]})
                if choose_remote:
                    await commit(self.store.choose_remote, state["domain"], local_hash, revision)
                state = await asyncio.to_thread(self.store.status, state["domain"])
                if state["apply_pending"] and not state["dirty"]:
                    # A crash after config save but before baseline commit is safe:
                    # the next raw observation recognizes the same remote hash.
                    async def before_apply():
                        await self.runtime._require_automatic_character(uid)
                    if await self.adapter.apply(uid, state, commit, confirm_rename=confirm_rename,
                                                before_apply=before_apply if not manual else None):
                        await commit(self.store.applied, state["domain"], state["local_hash"], state["head_revision"])
                    elif choose_remote:
                        # A busy conversation or unconfirmed rename keeps the
                        # reviewed dialog open; it is not a completed apply.
                        raise ValueError("chat_sync_retry_later")
                await asyncio.to_thread(scheduler.success, state["domain"], "profile")
                return await asyncio.to_thread(self.store.status, state["domain"])
            except (ValueError, OSError) as exc:
                await asyncio.to_thread(scheduler.failure, state["domain"], "profile", binding["generation"], presence, exc)
                raise
