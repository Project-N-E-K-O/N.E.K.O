"""Provider-neutral, local recommendation evidence. No dialogue or credentials.

Call this synchronous store on a worker thread. Decisions and their evidence
commit in one SQLite transaction; the insertion cursor also orders late events.
"""
from __future__ import annotations

import json
import base64
import binascii
import hashlib
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, get_args
from urllib.parse import urlsplit


RecommendationEventKind = Literal["shown", "accepted", "skipped", "playing", "paused", "ended", "stopped", "failed"]
RECOMMENDATION_EVENT_KINDS = frozenset(get_args(RecommendationEventKind))
FEEDBACK_SCHEMA = "desktop-proactive-feedback-v1"
PROACTIVE_SETTING_KEYS = frozenset({
    "proactiveChatEnabled", "proactiveVisionEnabled", "proactiveVisionChatEnabled",
    "proactiveNewsChatEnabled", "proactiveCommunityChatEnabled", "proactiveVideoChatEnabled",
    "proactivePersonalChatEnabled", "proactiveMusicEnabled", "proactiveMemeEnabled",
    "proactiveMiniGameInviteEnabled", "proactiveChatInterval", "proactiveVisionInterval",
})


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _settings_snapshot(value):
    return {key: val for key, val in value.items()
            if key in PROACTIVE_SETTING_KEYS and (
                (key.endswith("Enabled") and type(val) is bool)
                or (key.endswith("Interval") and type(val) is int and 1 <= val <= 3600))}


class FeedbackConflict(ValueError):
    pass


class FeedbackDecisionConflict(FeedbackConflict):
    """Reject an opposite action while explicitly ending its exact telemetry."""
    def __init__(self, event_id, payload_hash):
        super().__init__("recommendation_decision_conflict")
        self.telemetry = {"event_id": event_id, "payload_hash": payload_hash,
                          "status": "discarded", "reason": "decision_conflict"}


class RecommendationStore:
    MAX_RECOMMENDATIONS = 10_000
    MAX_EVENTS = 100_000
    TTL_SECONDS = 1800
    MAX_PENDING_UPLOADS = 100_000
    MAX_BATCH_BYTES = 1_048_576
    HISTORY_WARNING_BYTES = 512 * 1024 * 1024
    MAX_V2_PENDING_BYTES = 64 * 1024 * 1024
    MAX_V2_PENDING_AGE_SECONDS = 7 * 86400
    V2_SCHEMA = "desktop-proactive-feedback-v2"
    _BINDING_FIELDS = ("account_incarnation", "clear_generation", "registration_id", "registration_version")

    def __init__(self, path: Path, *, protocol_version: int = 1):
        self.path = path
        if protocol_version not in (1, 2):
            raise ValueError("unsupported_feedback_protocol")
        self.protocol_version = protocol_version

    @contextmanager
    def _transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            if self.protocol_version == 2:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise FeedbackConflict("unsupported_feedback_schema")
            db.execute("""CREATE TABLE IF NOT EXISTS recommendations (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, user_id TEXT,
                community TEXT, character TEXT NOT NULL, turn_id TEXT NOT NULL,
                track TEXT NOT NULL, settings TEXT NOT NULL, created_at REAL NOT NULL,
                expires_at REAL NOT NULL, decision TEXT, decision_event TEXT,
                playback_id TEXT, state TEXT NOT NULL DEFAULT 'pending')""")
            db.execute("""CREATE TABLE IF NOT EXISTS recommendation_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                recommendation_id TEXT NOT NULL REFERENCES recommendations(id),
                kind TEXT NOT NULL, occurred_at REAL NOT NULL, received_at REAL NOT NULL,
                playback_id TEXT, listened_seconds REAL NOT NULL,
                position_seconds REAL NOT NULL, reason TEXT NOT NULL)""")
            self._sync_schema(db)
            if self.protocol_version == 2:
                self._v2_schema(db)
            db.execute("PRAGMA user_version=2")
            db.execute("CREATE INDEX IF NOT EXISTS recommendation_owner ON recommendations(owner)")
            db.execute("CREATE INDEX IF NOT EXISTS recommendation_event_card ON recommendation_events(recommendation_id, kind)")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _sync_schema(db):
        # Existing v1 facts are preserved. Only newly committed facts are
        # enqueued; migration does not silently upload anonymous/history data.
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_sync_sources (
            owner TEXT PRIMARY KEY, source_id TEXT UNIQUE NOT NULL,
            user_id TEXT NOT NULL, community TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_sync_outbox (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
            item_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
            payload_hash TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
            UNIQUE(owner, kind, item_id))""")
        db.execute("""CREATE INDEX IF NOT EXISTS feedback_sync_pending
            ON feedback_sync_outbox(owner,state,seq)""")
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_sync_batches (
            owner TEXT PRIMARY KEY, batch_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL,
            blocked INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_sync_receipts (
            batch_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
            receipt TEXT NOT NULL, acknowledged_at REAL NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_settings_evidence (
            owner TEXT NOT NULL, entry_id TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(owner,entry_id))""")
        # An additive v2 extension: older compatible v2 clients still use the
        # triggers, so rollback never requires lowering user_version or losing facts.
        columns = {row[1] for row in db.execute("PRAGMA table_info(feedback_sync_outbox)")}
        if "created_at" not in columns:
            db.execute("ALTER TABLE feedback_sync_outbox ADD COLUMN created_at REAL")
        db.execute("""CREATE INDEX IF NOT EXISTS feedback_receipt_owner_time
            ON feedback_sync_receipts(owner,acknowledged_at)""")
        RecommendationStore._storage_schema(db)

    @staticmethod
    def _storage_schema(db):
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback_storage_usage'").fetchone()
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_storage_usage (
            kind TEXT PRIMARY KEY, retained INTEGER NOT NULL, awaiting_ack INTEGER NOT NULL)""")
        facts = (("recommendation", "recommendations", "id", "owner"),
                 ("event", "recommendation_events", "event_id",
                  "(SELECT owner FROM recommendations WHERE id={row}.recommendation_id)"))
        if not exists:
            # One-time accounting includes anonymous and pre-v2 local history.
            # It does not enqueue that history or manufacture cloud consent.
            for kind, table, identity, owner in facts:
                owner_sql = f"f.{owner}" if owner == "owner" else owner.format(row="f")
                retained = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                acked = db.execute(f"""SELECT count(*) FROM {table} f JOIN feedback_sync_outbox o
                    ON o.owner={owner_sql} AND o.item_id=f.{identity}
                    AND o.kind=? AND o.state='acked'""", (kind,)).fetchone()[0]
                db.execute("INSERT INTO feedback_storage_usage VALUES (?,?,?)", (kind, retained, retained - acked))
            totals = db.execute("SELECT count(*),count(CASE WHEN state='pending' THEN 1 END) FROM feedback_sync_outbox").fetchone()
            db.execute("INSERT INTO feedback_storage_usage VALUES ('upload',?,?)", tuple(totals))
        for kind, table, identity, owner in facts:
            owner_sql = "OLD.owner" if owner == "owner" else owner.format(row="OLD")
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS feedback_usage_{kind}_insert
                AFTER INSERT ON {table} BEGIN
                UPDATE feedback_storage_usage SET retained=retained+1,awaiting_ack=awaiting_ack+1 WHERE kind='{kind}'; END""")
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS feedback_usage_{kind}_delete
                AFTER DELETE ON {table} BEGIN
                UPDATE feedback_storage_usage SET retained=retained-1,awaiting_ack=awaiting_ack-
                  (NOT EXISTS (SELECT 1 FROM feedback_sync_outbox WHERE owner={owner_sql}
                   AND kind='{kind}' AND item_id=OLD.{identity} AND state='acked')) WHERE kind='{kind}'; END""")
            # State transitions, not receipt counts, release quota. Duplicate
            # acknowledgments and aborted transactions cannot create credits.
            fact_owner = "f.owner" if owner == "owner" else owner.format(row="f")
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS feedback_usage_{kind}_ack
                AFTER UPDATE OF state ON feedback_sync_outbox
                WHEN NEW.kind='{kind}' AND OLD.state<>NEW.state AND EXISTS
                (SELECT 1 FROM {table} f WHERE f.{identity}=NEW.item_id AND {fact_owner}=NEW.owner)
                BEGIN UPDATE feedback_storage_usage SET awaiting_ack=awaiting_ack+
                (OLD.state='acked')-(NEW.state='acked') WHERE kind='{kind}'; END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS feedback_usage_upload_insert
            AFTER INSERT ON feedback_sync_outbox BEGIN UPDATE feedback_storage_usage
            SET retained=retained+1,awaiting_ack=awaiting_ack+(NEW.state='pending') WHERE kind='upload'; END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS feedback_usage_upload_state
            AFTER UPDATE OF state ON feedback_sync_outbox BEGIN UPDATE feedback_storage_usage
            SET awaiting_ack=awaiting_ack+(NEW.state='pending')-(OLD.state='pending') WHERE kind='upload'; END""")

    @staticmethod
    def _awaiting_ack(db, kind):
        return db.execute("SELECT awaiting_ack FROM feedback_storage_usage WHERE kind=?", (kind,)).fetchone()[0]

    @staticmethod
    def _source(db, owner):
        if not owner.get("user_id") or not owner.get("community"):
            raise FeedbackConflict("feedback_anonymous_not_uploadable")
        row = db.execute("SELECT * FROM feedback_sync_sources WHERE owner=?", (owner["key"],)).fetchone()
        if row is not None:
            if row["user_id"] != str(owner["user_id"]) or row["community"] != owner["community"]:
                raise FeedbackConflict("feedback_owner_conflict")
            return row["source_id"]
        source_id = str(uuid.uuid4())
        db.execute("INSERT INTO feedback_sync_sources VALUES (?,?,?,?)",
                   (owner["key"], source_id, str(owner["user_id"]), owner["community"]))
        return source_id

    def _enqueue(self, db, owner, kind, item_id, payload):
        if not owner.get("user_id") or not owner.get("community"):
            return
        self._source(db, owner)
        encoded = _canonical(payload)
        old = db.execute("SELECT payload FROM feedback_sync_outbox WHERE owner=? AND kind=? AND item_id=?",
                         (owner["key"], kind, item_id)).fetchone()
        if old is not None:
            if old["payload"] != encoded:
                raise FeedbackConflict("feedback_upload_identity_collision")
            return
        if len(encoded.encode("utf-8")) > 65_536:
            raise FeedbackConflict("feedback_upload_payload_too_large")
        if self._awaiting_ack(db, "upload") >= self.MAX_PENDING_UPLOADS:
            raise FeedbackConflict("feedback_upload_capacity_reached")
        db.execute("""INSERT INTO feedback_sync_outbox(owner,item_id,kind,payload,payload_hash,created_at)
            VALUES (?,?,?,?,?,?)""", (owner["key"], item_id, kind, encoded, _digest(payload), time.time()))

    @staticmethod
    def _recommendation_payload(row):
        track = json.loads(row["track"])
        category = track.get("category", "music")
        content = {key: str(track.get(key) or "") for key in ("name", "artist", "source", "content_id")}
        if category in {"vision", "window"}:
            content = dict.fromkeys(content, "")
        content["identifier_kind"] = "legacy_metadata_hash_v1"
        return {"category": category, "content": content, "recommended_at": row["created_at"],
                "expires_at": row["expires_at"], "settings": _settings_snapshot(json.loads(row["settings"]))}

    @staticmethod
    def _event_payload(saved):
        reason = saved["reason"]
        if reason and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason) is None:
            reason = "media_error"
        return {"recommendation_id": saved["recommendation_id"], "kind": saved["kind"],
                "occurred_at": saved["occurred_at"], "local_received_at": saved["received_at"],
                "playback_id": saved["playback_id"], "listened_seconds": saved["listened_seconds"],
                "position_seconds": saved["position_seconds"], "reason": reason,
                "measurement_version": "media-played-union-v1"}

    def create(self, owner: dict, character: str, turn_id: str, track: dict, settings: dict, *, category: str = "music", control: dict | None = None):
        if self.protocol_version == 2:
            return self._create_v2(owner, character, turn_id, track, settings, category=category, control=control)
        now = time.time()
        recommendation_id = str(uuid.uuid4())
        # Media URLs may be signed. Evidence stores content metadata, never
        # query credentials or ephemeral playback URLs.
        source = urlsplit(track.get("url", "")).hostname or ""
        metadata = {"name": track["name"], "artist": track["artist"], "source": source,
                    "content_id": hashlib.sha256(f"{source}\n{track['name']}\n{track['artist']}".encode()).hexdigest()}
        if category != "music":
            metadata["category"] = category
        with self._transaction() as db:
            if self._awaiting_ack(db, "recommendation") >= self.MAX_RECOMMENDATIONS:
                raise FeedbackConflict("feedback_capacity_reached")
            db.execute(
                """INSERT INTO recommendations
                (id, owner, user_id, community, character, turn_id, track, settings, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (recommendation_id, owner["key"], owner["user_id"], owner["community"],
                 character, turn_id, json.dumps(metadata, ensure_ascii=False),
                 json.dumps(settings), now, now + self.TTL_SECONDS),
            )
            row = db.execute("SELECT * FROM recommendations WHERE id=?", (recommendation_id,)).fetchone()
            self._enqueue(db, owner, "recommendation", recommendation_id, self._recommendation_payload(row))
        return {"id": recommendation_id, "track": track, "state": "pending",
                "expires_at": now + self.TTL_SECONDS}

    def record(self, owner: dict, recommendation_id: str, event: dict, *, disabled_categories: frozenset[str] = frozenset(), control: dict | None = None, canonical_header: str | None = None):
        if self.protocol_version == 2:
            return self._record_v2(owner, recommendation_id, event, disabled_categories=disabled_categories, control=control, canonical_header=canonical_header)
        now = time.time()
        kind = event["kind"]
        if kind not in RECOMMENDATION_EVENT_KINDS:
            raise FeedbackConflict("invalid_feedback_kind")
        for field in ("listened_seconds", "position_seconds"):
            value = event.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 86400:
                raise FeedbackConflict("invalid_feedback_duration")
        if kind in {"shown", "accepted", "skipped"} and (
                event.get("playback_id") is not None or event.get("listened_seconds", 0) != 0
                or event.get("position_seconds", 0) != 0):
            raise FeedbackConflict("invalid_observation_fields")
        with self._transaction() as db:
            row = db.execute("SELECT * FROM recommendations WHERE id=? AND owner=?",
                             (recommendation_id, owner["key"])).fetchone()
            if row is None:
                raise FeedbackConflict("recommendation_not_found")
            category = json.loads(row["track"]).get("category", "music")
            if kind == "accepted" and category in disabled_categories:
                raise FeedbackConflict("recommendation_disabled")
            if category != "music" and kind not in {"shown", "accepted", "skipped"}:
                raise FeedbackConflict("invalid_feedback_kind")
            old = db.execute("SELECT * FROM recommendation_events WHERE event_id=?",
                             (event["event_id"],)).fetchone()
            if old:
                if old["recommendation_id"] != recommendation_id or old["kind"] != kind:
                    raise FeedbackConflict("feedback_event_collision")
                return self._result(row, event, False)
            if kind in {"accepted", "skipped"}:
                if row["decision"] is not None:
                    return self._result(row, event, False)
                if now > row["expires_at"]:
                    raise FeedbackConflict("recommendation_expired")
                db.execute("UPDATE recommendations SET decision=?, decision_event=?, state=? WHERE id=?",
                           (kind, event["event_id"], kind, recommendation_id))
            elif kind != "shown":
                if row["decision"] != "accepted":
                    raise FeedbackConflict("recommendation_not_accepted")
                playback_id = event.get("playback_id")
                if not playback_id:
                    raise FeedbackConflict("missing_playback_id")
                if row["playback_id"] and row["playback_id"] != playback_id:
                    raise FeedbackConflict("playback_owner_changed")
                if row["state"] in {"ended", "stopped", "failed"}:
                    return self._result(row, event, False)
                db.execute("UPDATE recommendations SET state=?, playback_id=? WHERE id=?",
                           (kind, playback_id, recommendation_id))
            elif db.execute("SELECT 1 FROM recommendation_events WHERE recommendation_id=? AND kind='shown'",
                            (recommendation_id,)).fetchone():
                return self._result(row, event, False)
            if self._awaiting_ack(db, "event") >= self.MAX_EVENTS:
                raise FeedbackConflict("feedback_capacity_reached")
            db.execute("""INSERT INTO recommendation_events
                (event_id, recommendation_id, kind, occurred_at, received_at, playback_id,
                 listened_seconds, position_seconds, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event["event_id"], recommendation_id, kind, event.get("occurred_at") if event.get("occurred_at") is not None else now,
                 now, event.get("playback_id"), event.get("listened_seconds", 0),
                 event.get("position_seconds", 0), event.get("reason", "")),
            )
            # Older local recommendations may receive a new decision after the
            # v2 upgrade. Register its parent, but never rebuild an already
            # frozen original payload using today's settings/state.
            if db.execute("SELECT 1 FROM feedback_sync_outbox WHERE owner=? AND kind='recommendation' AND item_id=?",
                          (owner["key"], recommendation_id)).fetchone() is None:
                self._enqueue(db, owner, "recommendation", recommendation_id, self._recommendation_payload(row))
            if kind not in {"shown", "accepted", "skipped"}:
                # A v1 playback may continue after upgrading. Transfer its real
                # committed acceptance as a required dependency, with its old ID
                # and timestamp; do not fabricate a new acceptance or all history.
                decision = db.execute("SELECT * FROM recommendation_events WHERE event_id=?",
                                      (row["decision_event"],)).fetchone()
                if decision is not None:
                    self._enqueue(db, owner, "event", decision["event_id"], self._event_payload(decision))
            saved = db.execute("SELECT * FROM recommendation_events WHERE event_id=?", (event["event_id"],)).fetchone()
            self._enqueue(db, owner, "event", saved["event_id"], self._event_payload(saved))
            row = db.execute("SELECT * FROM recommendations WHERE id=?", (recommendation_id,)).fetchone()
            return self._result(row, event, True)

    @staticmethod
    def _result(row, event, applied):
        granted = (row["decision"] == "accepted" and row["state"] == "accepted"
                   and row["decision_event"] == event["event_id"] and event["kind"] == "accepted")
        category = json.loads(row["track"]).get("category", "music")
        return {"applied": applied, "state": row["state"],
                "playback_grant": granted and category == "music",
                "action_grant": granted and category != "music"}

    def events(self, owner: dict, after: int, limit: int):
        with self._transaction() as db:
            rows = db.execute("""SELECT e.*, r.user_id, r.community, r.character, r.turn_id,
                r.track, r.settings, r.created_at AS recommended_at, r.expires_at FROM recommendation_events e
                JOIN recommendations r ON r.id=e.recommendation_id
                WHERE r.owner=? AND e.seq>? ORDER BY e.seq LIMIT ?""",
                (owner["key"], after, limit)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            original = item.pop("v2_event", None)
            item.pop("v2_hash", None)
            item.pop("v2_custody", None)
            if original is not None:
                item["original_event"] = json.loads(original)
            item["track"] = json.loads(item["track"])
            item["settings"] = json.loads(item["settings"])
            item["category"] = item["track"].get("category", "music")
            result.append(item)
        return {"events": result, "next_cursor": result[-1]["seq"] if result else after}

    def enqueue_settings(self, owner: dict, entry_id: str, payload: dict, *, context: dict | None = None):
        """Import an immutable JSON settings journal entry before acknowledging it.

        This transaction and JSON acknowledgment may be interrupted independently:
        retry imports the same ID/body and cannot lose or duplicate the mutation.
        """
        allowed = {"revision", "origin", "occurred_at", "previous", "settings"}
        # Journals survive process restarts and local-root migration. Validate
        # their JSON fields before hashing, set lookup or SQLite binding: a bad
        # local member must use the existing unavailable/preserve path rather
        # than terminate the upload worker with TypeError or KeyError.
        if (not isinstance(owner, dict) or not isinstance(owner.get("key"), str) or not owner["key"]
                or not {"user_id", "community"} <= owner.keys()
                or any(value is not None and not isinstance(value, str)
                       for value in (owner["user_id"], owner["community"]))
                or not isinstance(entry_id, str) or not 1 <= len(entry_id) <= 100 or "\x00" in entry_id
                or not isinstance(payload, dict) or set(payload) != allowed
                or not isinstance(payload["origin"], str)
                or payload["origin"] not in {"user", "system", "baseline"}
                or type(payload["revision"]) is not int or not 1 <= payload["revision"] <= 2**53 - 1
                or type(payload["occurred_at"]) not in (int, float)):
            raise FeedbackConflict("invalid_settings_evidence")
        try:
            finite_timestamp = math.isfinite(payload["occurred_at"])
        except OverflowError:
            # A JSON integer may exceed the finite timestamp representation.
            finite_timestamp = False
        if not finite_timestamp or not 0 <= payload["occurred_at"] <= 1e12:
            raise FeedbackConflict("invalid_settings_evidence")
        for name in ("previous", "settings"):
            if not isinstance(payload[name], dict) or _settings_snapshot(payload[name]) != payload[name]:
                raise FeedbackConflict("invalid_settings_evidence")
        if self.protocol_version == 2:
            self._validate_v2_context(context)
            intent = {"event_id": entry_id, "kind": "settings", "context": context, "settings_payload": payload}
            original, proofs, digest = self.event_identity(intent)
            with self._transaction() as db:
                row = db.execute("SELECT snapshot FROM feedback_v2_controls WHERE owner=?", (owner["key"],)).fetchone()
                control = json.loads(row[0]) if row else None
                return self._v2_custody(db, owner, original, proofs, digest, "settings", control, "")
        with self._transaction() as db:
            old = db.execute("SELECT payload FROM feedback_settings_evidence WHERE owner=? AND entry_id=?",
                             (owner["key"], entry_id)).fetchone()
            encoded = _canonical(payload)
            if old is not None and old["payload"] != encoded:
                raise FeedbackConflict("feedback_upload_identity_collision")
            db.execute("INSERT OR IGNORE INTO feedback_settings_evidence VALUES (?,?,?)",
                       (owner["key"], entry_id, encoded))
            self._enqueue(db, owner, "settings", entry_id, payload)

    def prepare_batch(self, owner: dict, limit: int = 100):
        """Freeze exact members/body before network I/O; restart retries the same batch."""
        if self.protocol_version == 2:
            return self._prepare_batch_v2(owner, limit)
        if not owner.get("user_id") or not owner.get("community"):
            return None
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FeedbackConflict("invalid_feedback_batch_limit")
        with self._transaction() as db:
            active = db.execute("SELECT * FROM feedback_sync_batches WHERE owner=?", (owner["key"],)).fetchone()
            if active is not None:
                self._source(db, owner)
                return None if active["blocked"] else json.loads(active["body"])
            rows = db.execute("""SELECT * FROM feedback_sync_outbox
                WHERE owner=? AND state='pending' ORDER BY seq LIMIT ?""", (owner["key"], limit)).fetchall()
            if not rows:
                return None
            batch = {"schema_version": FEEDBACK_SCHEMA, "batch_id": str(uuid.uuid4()),
                     "source_id": self._source(db, owner), "items": []}
            for row in rows:
                item = {"id": row["item_id"], "kind": row["kind"], "sequence": row["seq"],
                        "payload": json.loads(row["payload"])}
                batch["items"].append(item)
                if len(_canonical(batch).encode("utf-8")) > self.MAX_BATCH_BYTES:
                    batch["items"].pop()
                    break
            if not batch["items"]:
                raise FeedbackConflict("feedback_upload_payload_too_large")
            db.execute("INSERT INTO feedback_sync_batches(owner,batch_id,body,created_at) VALUES (?,?,?,?)",
                       (owner["key"], batch["batch_id"], _canonical(batch), time.time()))
            return batch

    def acknowledge_batch(self, owner: dict, receipt: dict):
        """Only a complete exact receipt can acknowledge this owner's frozen members."""
        if self.protocol_version == 2:
            return self._acknowledge_batch_v2(owner, receipt)
        if not isinstance(receipt, dict):
            raise FeedbackConflict("invalid_feedback_receipt")
        with self._transaction() as db:
            self._source(db, owner)
            old = db.execute("SELECT receipt FROM feedback_sync_receipts WHERE owner=? AND batch_id=?",
                             (owner["key"], receipt.get("batch_id"))).fetchone()
            if old is not None:
                if old["receipt"] != _canonical(receipt):
                    raise FeedbackConflict("feedback_receipt_collision")
                return True
            active = db.execute("SELECT * FROM feedback_sync_batches WHERE owner=?", (owner["key"],)).fetchone()
            if active is None:
                raise FeedbackConflict("feedback_batch_not_found")
            batch = json.loads(active["body"])
            keys = {"schema_version", "batch_id", "source_id", "batch_hash", "results"}
            if (set(receipt) != keys or receipt.get("schema_version") != FEEDBACK_SCHEMA
                    or receipt.get("batch_id") != batch["batch_id"]
                    or receipt.get("source_id") != batch["source_id"]
                    or receipt.get("batch_hash") != _digest(batch)
                    or not isinstance(receipt["results"], list)):
                raise FeedbackConflict("invalid_feedback_receipt")
            expected = {(item["kind"], item["id"]): _digest(item["payload"]) for item in batch["items"]}
            seen = set()
            for result in receipt["results"]:
                if not isinstance(result, dict) or set(result) != {"id", "kind", "payload_hash", "status"}:
                    raise FeedbackConflict("invalid_feedback_receipt")
                key = (result["kind"], result["id"])
                if (key in seen or expected.get(key) != result["payload_hash"]
                        or result["status"] not in {"stored", "duplicate"}):
                    raise FeedbackConflict("invalid_feedback_receipt")
                seen.add(key)
            if seen != set(expected):
                raise FeedbackConflict("incomplete_feedback_receipt")
            for kind, item_id in expected:
                db.execute("UPDATE feedback_sync_outbox SET state='acked' WHERE owner=? AND kind=? AND item_id=?",
                           (owner["key"], kind, item_id))
            db.execute("INSERT INTO feedback_sync_receipts VALUES (?,?,?,?)",
                       (batch["batch_id"], owner["key"], _canonical(receipt), time.time()))
            db.execute("DELETE FROM feedback_sync_batches WHERE owner=? AND batch_id=?",
                       (owner["key"], batch["batch_id"]))
            return True

    def mark_batch_error(self, owner: dict, batch_id: str, error_code: str, permanent: bool = False):
        # Store only bounded machine codes, never remote bodies or credentials.
        safe_code = re.sub(r"[^a-zA-Z0-9_:-]", "_", str(error_code))[:80]
        with self._transaction() as db:
            self._source(db, owner)
            return db.execute("""UPDATE feedback_sync_batches SET attempts=attempts+1,last_error=?,
                blocked=CASE WHEN ? THEN 1 ELSE blocked END WHERE owner=? AND batch_id=?""",
                       (safe_code, bool(permanent), owner["key"], batch_id)).rowcount == 1

    def resume_blocked_batch(self, owner: dict, batch_id: str, batch_hash: str, *, expected_error=None):
        """Explicit retry of exactly the inspected immutable batch, never a replacement.

        The caller fences the logged-in account. BEGIN IMMEDIATE serializes this
        CAS with late acknowledgments, account writes and other recovery requests.
        """
        with self._transaction() as db:
            self._source(db, owner)
            active = db.execute("SELECT * FROM feedback_sync_batches WHERE owner=? AND batch_id=?",
                                (owner["key"], batch_id)).fetchone()
            if (active is None or not active["blocked"]
                    or (expected_error is not None and active["last_error"] != expected_error)
                    or _digest(json.loads(active["body"])) != batch_hash):
                raise FeedbackConflict("feedback_resume_conflict")
            changed = db.execute("""UPDATE feedback_sync_batches SET blocked=0,last_error=''
                WHERE owner=? AND batch_id=? AND body=? AND last_error=? AND blocked=1""",
                (owner["key"], batch_id, active["body"], active["last_error"])).rowcount
            if changed != 1:
                raise FeedbackConflict("feedback_resume_conflict")
        return True

    def sync_status(self, owner: dict):
        if self.protocol_version == 2:
            return self._sync_status_v2(owner)
        with self._transaction() as db:
            now = time.time()
            counts = dict(db.execute("SELECT state,count(*) FROM feedback_sync_outbox WHERE owner=? GROUP BY state",
                                     (owner["key"],)).fetchall())
            active = db.execute("SELECT * FROM feedback_sync_batches WHERE owner=?",
                                (owner["key"],)).fetchone()
            age = db.execute("""SELECT min(created_at),count(CASE WHEN created_at IS NULL THEN 1 END)
                FROM feedback_sync_outbox WHERE owner=? AND state='pending'""", (owner["key"],)).fetchone()
            oldest_pending_at = age[0] if age[1] == 0 else None
            usage = {row["kind"]: dict(row) for row in db.execute("SELECT * FROM feedback_storage_usage")}
            db_bytes = db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]
            latest = db.execute("""SELECT * FROM feedback_sync_receipts WHERE owner=?
                ORDER BY acknowledged_at DESC,rowid DESC LIMIT 1""", (owner["key"],)).fetchone()
            last_acknowledged_batch = None
            if latest:
                original = json.loads(latest["receipt"])
                last_acknowledged_batch = {"batch_id": latest["batch_id"], "batch_hash": original["batch_hash"],
                                           "source_id": original["source_id"], "acknowledged_at": latest["acknowledged_at"],
                                           "receipt": original}
        return {"pending": counts.get("pending", 0), "acked": counts.get("acked", 0),
                "batch_id": active["batch_id"] if active else None,
                "batch_hash": _digest(json.loads(active["body"])) if active else None,
                "batch_created_at": active["created_at"] if active else None,
                "batch_age_seconds": max(0, now - active["created_at"]) if active else None,
                "blocked": bool(active["blocked"]) if active else False,
                "last_error": active["last_error"] if active else "",
                "attempts": active["attempts"] if active else 0,
                "oldest_pending_at": oldest_pending_at,
                "oldest_pending_age_seconds": max(0, now - oldest_pending_at) if oldest_pending_at is not None else None,
                "pending_age_unknown_count": age[1],
                "last_acknowledged_batch": last_acknowledged_batch,
                "capacity": {"scope": "all_local_accounts", "quota": "facts_awaiting_cloud_ack",
                             "recommendations": usage["recommendation"], "events": usage["event"],
                             "uploads": usage["upload"],
                             "limits": {"recommendations": self.MAX_RECOMMENDATIONS, "events": self.MAX_EVENTS,
                                        "pending_uploads": self.MAX_PENDING_UPLOADS},
                             "db_bytes": db_bytes, "history_warning_bytes": self.HISTORY_WARNING_BYTES,
                             "history_warning": db_bytes >= self.HISTORY_WARNING_BYTES,
                             "local_history_retained": True, "automatic_gc": False}}

    @staticmethod
    def _v2_schema(db):
        # Operational slots remain separate from the bounded telemetry spool.
        # They do not masquerade as uploaded facts or recommendation counts.
        first_upgrade = not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback_v2_meta'").fetchone()
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_v2_meta (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), storage_incarnation TEXT NOT NULL)""")
        db.execute("INSERT OR IGNORE INTO feedback_v2_meta VALUES (1,?)", (str(uuid.uuid4()),))
        event_columns = {row[1] for row in db.execute("PRAGMA table_info(recommendation_events)")}
        migrate_events = "v2_event" not in event_columns
        for column in ("v2_event", "v2_hash", "v2_custody"):
            if column not in event_columns:
                db.execute(f"ALTER TABLE recommendation_events ADD COLUMN {column} TEXT")
        if first_upgrade:
            # The agreed first-upgrade rule invalidates the old pending
            # transport. Retained facts/current preferences are untouched.
            db.execute("DELETE FROM feedback_sync_batches")
            db.execute("DELETE FROM feedback_sync_outbox WHERE state='pending'")
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_v2_controls (
            owner TEXT PRIMARY KEY, snapshot TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_v2_baseline (
            owner TEXT PRIMARY KEY, binding_key TEXT NOT NULL, event_id TEXT,
            state TEXT NOT NULL CHECK(state IN ('pending','queued','complete')))""")
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_v2_status (
            owner TEXT PRIMARY KEY, acknowledged INTEGER NOT NULL DEFAULT 0,
            latest_receipt TEXT, acknowledged_at REAL, incomplete INTEGER NOT NULL DEFAULT 0)""")
        if "incomplete" not in {row[1] for row in db.execute("PRAGMA table_info(feedback_v2_status)")}:
            db.execute("ALTER TABLE feedback_v2_status ADD COLUMN incomplete INTEGER NOT NULL DEFAULT 0")
        db.execute("DELETE FROM feedback_v2_status WHERE acknowledged_at<=?", (time.time() - 180 * 86400,))
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_v2_offers (
            offer_id TEXT PRIMARY KEY REFERENCES recommendations(id), context TEXT NOT NULL,
            shown_id TEXT, shown_hash TEXT, shown_status TEXT,
            decision_hash TEXT, decision_status TEXT,
            dispatch_state TEXT NOT NULL DEFAULT 'unclaimed',
            meter_epoch TEXT, meter_prefix INTEGER NOT NULL DEFAULT 0)""")
        if "expires_at" not in {row[1] for row in db.execute("PRAGMA table_info(feedback_v2_offers)")}:
            db.execute("ALTER TABLE feedback_v2_offers ADD COLUMN expires_at REAL")
        db.execute("CREATE INDEX IF NOT EXISTS feedback_v2_offer_expiry ON feedback_v2_offers(expires_at)")
        db.execute("UPDATE feedback_v2_offers SET expires_at=(SELECT expires_at FROM recommendations WHERE id=offer_id) WHERE expires_at IS NULL")
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_v2_spool (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, event_id TEXT NOT NULL,
            recommendation_id TEXT NOT NULL, kind TEXT NOT NULL, category TEXT NOT NULL, event TEXT NOT NULL,
            payload_hash TEXT NOT NULL, proof TEXT NOT NULL, state TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
            UNIQUE(owner,event_id))""")
        db.execute("""CREATE INDEX IF NOT EXISTS feedback_v2_spool_ready
            ON feedback_v2_spool(owner,state,seq)""")
        if "retry_after" not in {row[1] for row in db.execute("PRAGMA table_info(feedback_v2_spool)")}:
            db.execute("ALTER TABLE feedback_v2_spool ADD COLUMN retry_after REAL NOT NULL DEFAULT 0")
        db.execute("""CREATE INDEX IF NOT EXISTS feedback_v2_deferred_due
            ON feedback_v2_spool(owner,state,retry_after,seq)""")
        db.execute("CREATE INDEX IF NOT EXISTS feedback_v2_spool_offer ON feedback_v2_spool(recommendation_id)")
        db.execute("CREATE INDEX IF NOT EXISTS feedback_v2_spool_age ON feedback_v2_spool(created_at,seq)")
        if migrate_events:
            # Only still-retained v2 originals can be migrated; never invent
            # history already released by an older implementation.
            for row in db.execute("SELECT * FROM feedback_v2_spool WHERE kind<>'settings'"):
                RecommendationStore._v2_save_event(db, row["recommendation_id"], json.loads(row["event"]),
                    row["payload_hash"], {"event_id": row["event_id"], "payload_hash": row["payload_hash"],
                    "status": "durable", "reason": row["reason"]}, row["created_at"])
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_v2_usage (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), rows_used INTEGER NOT NULL, bytes_used INTEGER NOT NULL)""")
        if not db.execute("SELECT 1 FROM feedback_v2_usage WHERE singleton=1").fetchone():
            db.execute("""INSERT INTO feedback_v2_usage SELECT 1,count(*),
                coalesce(sum(length(CAST(event AS BLOB))+length(CAST(proof AS BLOB))),0) FROM feedback_v2_spool""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS feedback_v2_usage_insert AFTER INSERT ON feedback_v2_spool
            BEGIN UPDATE feedback_v2_usage SET rows_used=rows_used+1,
            bytes_used=bytes_used+length(CAST(NEW.event AS BLOB))+length(CAST(NEW.proof AS BLOB)); END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS feedback_v2_usage_delete AFTER DELETE ON feedback_v2_spool
            BEGIN UPDATE feedback_v2_usage SET rows_used=rows_used-1,
            bytes_used=bytes_used-length(CAST(OLD.event AS BLOB))-length(CAST(OLD.proof AS BLOB)); END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS feedback_v2_usage_proof AFTER UPDATE OF proof ON feedback_v2_spool
            BEGIN UPDATE feedback_v2_usage SET bytes_used=bytes_used+
            length(CAST(NEW.proof AS BLOB))-length(CAST(OLD.proof AS BLOB)); END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS feedback_v2_immutable
            BEFORE UPDATE OF owner,event_id,recommendation_id,kind,category,event,payload_hash,created_at ON feedback_v2_spool
            BEGIN SELECT RAISE(ABORT,'immutable_feedback_fact'); END""")

    def storage_incarnation(self, owner=None):
        with self._transaction() as db:
            self._v2_schema(db)
            return db.execute("SELECT storage_incarnation FROM feedback_v2_meta WHERE singleton=1").fetchone()[0]

    def load_control(self, owner):
        with self._transaction() as db:
            self._v2_schema(db)
            row = db.execute("SELECT snapshot FROM feedback_v2_controls WHERE owner=?", (owner["key"],)).fetchone()
            return json.loads(row[0]) if row else None

    def apply_control(self, owner, snapshot):
        if not isinstance(snapshot, dict):
            raise FeedbackConflict("invalid_feedback_control")
        if snapshot.get("owner_key", owner["key"]) != owner["key"]:
            raise FeedbackConflict("feedback_owner_conflict")
        revision = snapshot.get("binding_revision")
        if type(revision) is not int or revision < 0:
            raise FeedbackConflict("invalid_feedback_control")
        with self._transaction() as db:
            self._v2_schema(db)
            old = db.execute("SELECT snapshot FROM feedback_v2_controls WHERE owner=?", (owner["key"],)).fetchone()
            previous = json.loads(old[0]) if old else None
            if previous and revision < previous.get("binding_revision", 0):
                return False
            if previous and revision == previous.get("binding_revision"):
                if any(snapshot.get(key) != previous.get(key) for key in self._BINDING_FIELDS):
                    raise FeedbackConflict("feedback_binding_revision_collision")
            db.execute("INSERT OR REPLACE INTO feedback_v2_controls VALUES (?,?)", (owner["key"], _canonical(snapshot)))
            binding_changed = previous and any(snapshot.get(key) != previous.get(key) for key in self._BINDING_FIELDS)
            if previous and any(snapshot.get(key) != previous.get(key)
                                for key in ("account_incarnation", "clear_generation")):
                # Account history clearing also covers the local export ledger;
                # device replacement alone preserves its retained history.
                db.execute("""DELETE FROM recommendation_events WHERE recommendation_id IN
                    (SELECT id FROM recommendations WHERE owner=?)""", (owner["key"],))
            for row in db.execute("SELECT * FROM feedback_v2_spool WHERE owner=?", (owner["key"],)).fetchall():
                event = json.loads(row["event"])
                original_context = event.get("context") or {}
                if binding_changed and any(original_context.get(key) != snapshot.get(key) for key in self._BINDING_FIELDS):
                    # Clear/removal also invalidates pre-existing partial
                    # captures; uncertainty is not a way around the clear.
                    db.execute("DELETE FROM feedback_v2_spool WHERE seq=?", (row["seq"],))
                    continue
                if row["state"] == "frozen" and not binding_changed:
                    continue
                if (not binding_changed and row["state"] == "held"
                        and row["reason"] in {"time_deferred", "dependency_deferred"}):
                    continue
                status, reason = self._v2_classify(owner, event, snapshot, json.loads(row["proof"]))
                if status == "discarded":
                    db.execute("DELETE FROM feedback_v2_spool WHERE seq=?", (row["seq"],))
                else:
                    db.execute("UPDATE feedback_v2_spool SET state=?,reason=? WHERE seq=?", (status, reason, row["seq"]))
            # A binding change must not allow a late receipt to acknowledge
            # replacement facts. Transport is reconstructed from surviving facts.
            if binding_changed:
                db.execute("DELETE FROM feedback_sync_batches WHERE owner=?", (owner["key"],))
                db.execute("DELETE FROM feedback_v2_status WHERE owner=?", (owner["key"],))
                db.execute("""UPDATE feedback_v2_offers SET meter_epoch=NULL,meter_prefix=0
                    WHERE offer_id IN (SELECT id FROM recommendations WHERE owner=?)""", (owner["key"],))
                db.execute("""UPDATE feedback_v2_baseline SET state='pending',event_id=NULL
                    WHERE owner=? AND state='queued' AND NOT EXISTS
                    (SELECT 1 FROM feedback_v2_spool WHERE event_id=feedback_v2_baseline.event_id
                     AND owner=feedback_v2_baseline.owner)""", (owner["key"],))
        return True

    @staticmethod
    def event_identity(event):
        def without_proofs(value):
            if isinstance(value, dict):
                return {key: without_proofs(item) for key, item in value.items() if key != "time_proof"}
            if isinstance(value, list):
                return [without_proofs(item) for item in value]
            return value
        original = without_proofs(json.loads(_canonical(event)))
        context = event.get("context")
        proof = {"event": context.get("time_proof", {}) if isinstance(context, dict) else {},
                 "offer": ((event.get("offer_context") or {}).get("origin_context") or {}).get("time_proof", {}),
                 "measurement": ((event.get("measurement") or {}).get("origin_context") or {}).get("time_proof", {})}
        return original, proof, _digest(original)

    @staticmethod
    def local_handoff_hash(original, canonical_header):
        """Validate the renderer's immutable JSON text before hashing its bytes.

        This local transport identity avoids JS/Python float printer differences;
        cloud fact hashes remain the separate shared Python wire algorithm.
        """
        if canonical_header is None:
            return _digest(original)
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result
        def same(left, right):
            if type(left) in (int, float) and type(right) in (int, float):
                return math.isfinite(left) and math.isfinite(right) and left == right
            if type(left) is not type(right):
                return False
            if isinstance(left, dict):
                return left.keys() == right.keys() and all(same(left[key], right[key]) for key in left)
            if isinstance(left, list):
                return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
            return left == right
        try:
            if not isinstance(canonical_header, str) or len(canonical_header) > 180_000:
                raise ValueError("invalid size")
            encoded = base64.b64decode(canonical_header, validate=True)
            decoded = encoded.decode("utf-8", errors="strict")
            value = json.loads(decoded, object_pairs_hook=pairs,
                               parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite number")))
            if not same(original, value):
                raise ValueError("different event")
        except (ValueError, TypeError, OverflowError, RecursionError, binascii.Error) as exc:
            raise FeedbackConflict("invalid_feedback_canonical") from exc
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _v2_classify(cls, owner, event, control, proof):
        if event.get("context") is None:
            return "discarded", "legacy_capture_missing"
        context = event.get("context") or {}
        if context.get("owner_key", owner["key"]) != owner["key"]:
            return "discarded", "owner_changed"
        if not control:
            return "held", "control_unavailable"
        if any(control.get(key) is None or control.get(key) == "" for key in cls._BINDING_FIELDS):
            return "held", "control_unavailable"
        for key in cls._BINDING_FIELDS:
            if context.get(key) is None or context.get(key) == "":
                return "held", "binding_unavailable"
            if context[key] != control.get(key):
                return "discarded", "binding_changed"
        if control.get("upgrade_required") or control.get("state") == "upgrade_required":
            return "held", "upgrade_required"
        capture = context.get("capture") or {}
        event_proof = proof.get("event", proof) if isinstance(proof, dict) else {}
        def clock_tick(value):
            # Check the wire bound before converting a JSON integer to float;
            # very large integers otherwise raise OverflowError in isfinite.
            return (type(value) in (int, float) and 0 <= value <= 2**53 - 1
                    and math.isfinite(value))
        def text(value, maximum):
            return isinstance(value, str) and 0 < len(value) <= maximum and "\x00" not in value
        def origin(value):
            # The peer accepts permanently partial origins, but every present
            # scalar must still fit its wire type. A malformed origin must not
            # turn one unqualified fact into a permanent rejection of its batch.
            if value is None:
                return True
            if not isinstance(value, dict):
                return False
            if value.get("version") not in (None, "desktop-feedback-capture-v2"):
                return False
            for key, maximum in (("owner_key", 512), ("account_incarnation", 128), ("registration_id", 100)):
                if value.get(key) is not None and not text(value[key], maximum):
                    return False
            for key, minimum in (("clear_generation", 0), ("registration_version", 1), ("binding_revision", 0)):
                number = value.get(key)
                if number is not None and (type(number) is not int or not minimum <= number <= 2**53 - 1):
                    return False
            captured = value.get("capture")
            if captured is not None:
                if not isinstance(captured, dict):
                    return False
                for key, maximum in (("clock_session_id", 128), ("anchor_id", 100)):
                    if captured.get(key) is not None and not text(captured[key], maximum):
                        return False
                if captured.get("tick_ms") is not None and not clock_tick(captured["tick_ms"]):
                    return False
            proved = value.get("time_proof")
            if proved is not None:
                if not isinstance(proved, dict):
                    return False
                if proved.get("anchor_token") is not None and not text(proved["anchor_token"], 8192):
                    return False
                if any(proved.get(key) is not None and not clock_tick(proved[key])
                       for key in ("send_tick_ms", "receive_tick_ms")):
                    return False
            return True
        if (context.get("version") != "desktop-feedback-capture-v2"
                or not origin(context)
                or not isinstance(capture, dict) or not isinstance(event_proof, dict)
                or not text(capture.get("clock_session_id"), 128)
                or not clock_tick(capture.get("tick_ms"))
                or not text(capture.get("anchor_id"), 100)
                or set(event_proof) != {"anchor_token", "send_tick_ms", "receive_tick_ms"}
                or not text(event_proof.get("anchor_token"), 8192)
                or not clock_tick(event_proof.get("send_tick_ms"))
                or not clock_tick(event_proof.get("receive_tick_ms"))
                or event_proof["receive_tick_ms"] < event_proof["send_tick_ms"]):
            return "held", "time_unproven"
        for key, proof_key in (("offer_context", "offer"), ("measurement", "measurement")):
            container = event.get(key)
            if container is None:
                continue
            if not isinstance(container, dict):
                return "held", "time_unproven"
            original_origin = container.get("origin_context")
            # Validate precisely the proof projection used by _v2_wire_item;
            # immutable originals deliberately have their time_proof removed.
            if isinstance(original_origin, dict) and isinstance(proof, dict) and proof.get(proof_key):
                original_origin = {**original_origin, "time_proof": proof[proof_key]}
            if not origin(original_origin):
                return "held", "time_unproven"
        return "ready", ""

    def _create_v2(self, owner, character, turn_id, track, settings, *, category, control):
        now = time.time()
        offer_id = str(uuid.uuid4())
        source = urlsplit(track.get("url", "")).hostname or ""
        metadata = {"name": track["name"], "artist": track["artist"], "source": source,
                    "content_id": hashlib.sha256(f"{source}\n{track['name']}\n{track['artist']}".encode()).hexdigest()}
        metadata["category"] = category
        context = {"owner_key": owner["key"], "offer_id": offer_id}
        if control:
            context["origin_context"] = json.loads(_canonical(control))
        with self._transaction() as db:
            self._v2_gc_offers(db)
            db.execute("""INSERT INTO recommendations
                (id,owner,user_id,community,character,turn_id,track,settings,created_at,expires_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (offer_id, owner["key"], owner.get("user_id"), owner.get("community"),
                    character, turn_id, _canonical(metadata), _canonical(settings), now, now + self.TTL_SECONDS))
            db.execute("INSERT INTO feedback_v2_offers(offer_id,context,expires_at) VALUES (?,?,?)", (offer_id, _canonical(context), now + self.TTL_SECONDS))
        return {"id": offer_id, "track": track, "state": "pending", "expires_at": now + self.TTL_SECONDS,
                "offer_context": context}

    def _v2_custody(self, db, owner, event, proof, payload_hash, category, control, recommendation_id):
        self._v2_retire_pending(db)
        if not owner.get("user_id") or not owner.get("community"):
            return {"event_id": event["event_id"], "payload_hash": payload_hash,
                    "status": "discarded", "reason": "anonymous_not_uploadable"}
        old = db.execute("SELECT * FROM feedback_v2_spool WHERE owner=? AND event_id=?", (owner["key"], event["event_id"])).fetchone()
        if old:
            if old["payload_hash"] != payload_hash:
                raise FeedbackConflict("feedback_event_collision")
            return {"event_id": event["event_id"], "payload_hash": payload_hash, "status": "durable", "reason": old["reason"]}
        state, reason = self._v2_classify(owner, event, control, proof)
        if state == "discarded":
            return {"event_id": event["event_id"], "payload_hash": payload_hash, "status": "discarded", "reason": reason}
        encoded = _canonical(event)
        used = db.execute("SELECT rows_used,bytes_used FROM feedback_v2_usage WHERE singleton=1").fetchone()
        if (used[0] >= self.MAX_PENDING_UPLOADS
                or len(encoded.encode()) + len(_canonical(proof).encode()) > 65_536
                or used[1] + len(encoded.encode()) + len(_canonical(proof).encode()) > self.MAX_V2_PENDING_BYTES):
            db.execute("""INSERT INTO feedback_v2_status(owner,incomplete,acknowledged_at) VALUES (?,1,?)
                ON CONFLICT(owner) DO UPDATE SET incomplete=1""", (owner["key"], time.time()))
            return {"event_id": event["event_id"], "payload_hash": payload_hash, "status": "discarded", "reason": "capacity_reached"}
        db.execute("""INSERT INTO feedback_v2_spool(owner,event_id,recommendation_id,kind,category,event,payload_hash,proof,state,reason,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (owner["key"], event["event_id"], recommendation_id, event["kind"], category, encoded,
                payload_hash, _canonical(proof), state, reason, time.time()))
        return {"event_id": event["event_id"], "payload_hash": payload_hash, "status": "durable", "reason": reason}

    @staticmethod
    def _v2_save_event(db, recommendation_id, event, event_hash, custody, received_at):
        # Reuse the local history ledger, not an additional cloud ACK log. The
        # initial handoff result survives transport retirement and reply loss.
        db.execute("""INSERT OR IGNORE INTO recommendation_events
            (event_id,recommendation_id,kind,occurred_at,received_at,playback_id,
             listened_seconds,position_seconds,reason,v2_event,v2_hash,v2_custody)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (event["event_id"], recommendation_id, event["kind"],
                event.get("occurred_at") if event.get("occurred_at") is not None else received_at,
                received_at, event.get("playback_id"), event.get("listened_seconds", 0),
                event.get("position_seconds", 0), event.get("reason", ""),
                _canonical(event), event_hash, _canonical(custody)))

    def _v2_retire_pending(self, db):
        # Retire in bounded batches across local accounts. Frozen transport is
        # immutable and remains owned until its receipt or a control change.
        now = time.time()
        expired = db.execute("""SELECT * FROM feedback_v2_spool
            WHERE created_at<=? AND state IN ('held','ready') ORDER BY created_at,seq LIMIT 256""",
            (now - self.MAX_V2_PENDING_AGE_SECONDS,)).fetchall()
        for row in expired:
            if row["kind"] != "settings":
                self._v2_save_event(db, row["recommendation_id"], json.loads(row["event"]), row["payload_hash"],
                    {"event_id": row["event_id"], "payload_hash": row["payload_hash"],
                     "status": "durable", "reason": row["reason"]}, row["created_at"])
            db.execute("DELETE FROM feedback_v2_spool WHERE seq=?", (row["seq"],))
            db.execute("""UPDATE feedback_v2_baseline SET state='pending',event_id=NULL
                WHERE owner=? AND event_id=?""", (row["owner"], row["event_id"]))
            db.execute("""INSERT INTO feedback_v2_status(owner,incomplete,acknowledged_at) VALUES (?,1,?)
                ON CONFLICT(owner) DO UPDATE SET incomplete=1""", (row["owner"], now))

    def _record_v2(self, owner, recommendation_id, event, *, disabled_categories, control, canonical_header=None):
        self._validate_v2_event(event)
        try:
            original, proof, event_hash = self.event_identity(event)
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise FeedbackConflict("invalid_feedback_event") from exc
        event_hash = self.local_handoff_hash(original, canonical_header)
        kind = original.get("kind")
        if kind not in RECOMMENDATION_EVENT_KINDS | {"measurement"}:
            raise FeedbackConflict("invalid_feedback_kind")
        if (not isinstance(original.get("event_id"), str) or not 1 <= len(original["event_id"]) <= 100
                or "\x00" in original["event_id"]):
            raise FeedbackConflict("invalid_feedback_identity")
        for field in ("listened_seconds", "position_seconds"):
            value = original.get(field, 0)
            if type(value) not in (int, float) or not 0 <= value <= 86400 or not math.isfinite(value):
                raise FeedbackConflict("invalid_feedback_duration")
        if kind in {"shown", "accepted", "skipped"} and (
                original.get("playback_id") is not None or original.get("listened_seconds", 0) != 0
                or original.get("position_seconds", 0) != 0):
            raise FeedbackConflict("invalid_observation_fields")
        with self._transaction() as db:
            persisted = db.execute("SELECT snapshot FROM feedback_v2_controls WHERE owner=?", (owner["key"],)).fetchone()
            if persisted:
                control = json.loads(persisted[0])
            row = db.execute("SELECT * FROM recommendations WHERE id=? AND owner=?", (recommendation_id, owner["key"])).fetchone()
            if row is None:
                raise FeedbackConflict("recommendation_not_found")
            slot = db.execute("SELECT * FROM feedback_v2_offers WHERE offer_id=?", (recommendation_id,)).fetchone()
            if slot is None:
                # A real new action on a legacy offer may continue. Preserve
                # its original owner; never backfill the legacy generation.
                offer_context = {"owner_key": row["owner"], "offer_id": recommendation_id}
                db.execute("INSERT INTO feedback_v2_offers(offer_id,context,expires_at) VALUES (?,?,?)", (recommendation_id, _canonical(offer_context), row["expires_at"]))
                slot = db.execute("SELECT * FROM feedback_v2_offers WHERE offer_id=?", (recommendation_id,)).fetchone()
            supplied_offer = original.get("offer_context")
            saved_offer, _, _ = self.event_identity(json.loads(slot["context"]))
            if supplied_offer is not None and supplied_offer != saved_offer:
                raise FeedbackConflict("feedback_offer_context_changed")
            if (original.get("context") or {}).get("owner_key", owner["key"]) != owner["key"]:
                raise FeedbackConflict("feedback_owner_conflict")
            category = json.loads(row["track"]).get("category", "music")
            if category != "music" and kind not in {"shown", "accepted", "skipped"}:
                raise FeedbackConflict("invalid_feedback_kind")
            saved = db.execute("SELECT * FROM recommendation_events WHERE event_id=?", (original["event_id"],)).fetchone()
            if saved:
                if saved["recommendation_id"] != recommendation_id or saved["v2_hash"] != event_hash:
                    raise FeedbackConflict("feedback_event_collision")
                result = self._result(row, original, False)
                result["telemetry"] = json.loads(saved["v2_custody"])
                state, reason = self._v2_classify(owner, original, control, proof)
                if state == "discarded":
                    result["telemetry"] = {"event_id": original["event_id"], "payload_hash": event_hash,
                                           "status": "discarded", "reason": reason}
                return result
            existing = db.execute("SELECT payload_hash FROM feedback_v2_spool WHERE owner=? AND event_id=?", (owner["key"], original["event_id"])).fetchone()
            if existing and existing[0] != event_hash:
                raise FeedbackConflict("feedback_event_collision")
            if existing:
                result = self._result(row, original, False)
                result["telemetry"] = self._v2_custody(db, owner, original, proof, event_hash, category, control, recommendation_id)
                received = db.execute("SELECT created_at FROM feedback_v2_spool WHERE owner=? AND event_id=?",
                                      (owner["key"], original["event_id"])).fetchone()
                self._v2_save_event(db, recommendation_id, original, event_hash, result["telemetry"],
                                    received[0] if received else time.time())
                return result
            applied = True
            if kind == "shown":
                if slot["shown_id"]:
                    if slot["shown_id"] == original["event_id"] and slot["shown_hash"] != event_hash:
                        raise FeedbackConflict("feedback_event_collision")
                    applied = False
            elif kind in {"accepted", "skipped"}:
                if row["decision"] is not None:
                    if row["decision"] != kind:
                        raise FeedbackDecisionConflict(original["event_id"], event_hash)
                    if row["decision_event"] == original["event_id"] and slot["decision_hash"] not in (None, event_hash):
                        raise FeedbackConflict("feedback_event_collision")
                    applied = False
                else:
                    if kind == "accepted" and category in disabled_categories:
                        raise FeedbackConflict("recommendation_disabled")
                    if time.time() > row["expires_at"]:
                        raise FeedbackConflict("recommendation_expired")
                    db.execute("UPDATE recommendations SET decision=?,decision_event=?,state=? WHERE id=?", (kind, original["event_id"], kind, recommendation_id))
            else:
                if row["decision"] != "accepted":
                    raise FeedbackConflict("recommendation_not_accepted")
                playback = original.get("playback_id")
                if not playback:
                    raise FeedbackConflict("missing_playback_id")
                if not isinstance(playback, str) or len(playback) > 512 or "\x00" in playback:
                    raise FeedbackConflict("invalid_feedback_identity")
                if row["playback_id"] and row["playback_id"] != playback:
                    raise FeedbackConflict("playback_owner_changed")
                if kind != "measurement" and row["state"] in {"ended", "stopped", "failed"}:
                    applied = False
                if kind == "measurement":
                    measurement = original.get("measurement") or {}
                    start, end = measurement.get("range_start_ms"), measurement.get("range_end_ms")
                    # This fixed slot is a local contiguous-prefix cache, not
                    # an infinite seen-epoch log. A new real epoch starts at
                    # zero; delayed old epochs keep their original identity
                    # and are deduplicated by cloud coverage. They must not
                    # block the current epoch's later ranges or replay audio.
                    if ((slot["meter_epoch"] != measurement["epoch_id"] and start == 0)
                            or (slot["meter_epoch"] == measurement["epoch_id"]
                                and start <= slot["meter_prefix"] < end)):
                        db.execute("UPDATE feedback_v2_offers SET meter_epoch=?,meter_prefix=? WHERE offer_id=?", (measurement["epoch_id"], end, recommendation_id))
                    if not row["playback_id"]:
                        db.execute("UPDATE recommendations SET playback_id=? WHERE id=?", (playback, recommendation_id))
                    applied = False  # Measurement never executes a normal action.
                elif applied:
                    db.execute("UPDATE recommendations SET state=?,playback_id=? WHERE id=?", (kind, playback, recommendation_id))
            if kind == "measurement":
                custody = self._v2_custody(db, owner, original, proof, event_hash, category, control, recommendation_id)
            elif applied:
                custody = self._v2_custody(db, owner, original, proof, event_hash, category, control, recommendation_id)
                if kind == "shown":
                    db.execute("UPDATE feedback_v2_offers SET shown_id=?,shown_hash=?,shown_status=? WHERE offer_id=?", (original["event_id"], event_hash, custody["status"], recommendation_id))
                elif kind in {"accepted", "skipped"}:
                    db.execute("UPDATE feedback_v2_offers SET decision_hash=?,decision_status=? WHERE offer_id=?", (event_hash, custody["status"], recommendation_id))
            elif existing:
                custody = self._v2_custody(db, owner, original, proof, event_hash, category, control, recommendation_id)
            else:
                # A different retry ID cannot turn a skipped first display or
                # already committed decision into a fresh statistic.
                custody = {"event_id": original["event_id"], "payload_hash": event_hash, "status": "discarded", "reason": "already_applied"}
            row = db.execute("SELECT * FROM recommendations WHERE id=?", (recommendation_id,)).fetchone()
            result = self._result(row, original, applied)
            result["telemetry"] = custody
            if ((applied or kind == "measurement")
                    and custody.get("reason") not in {"binding_changed", "capacity_reached"}):
                self._v2_save_event(db, recommendation_id, original, event_hash, custody, time.time())
            return result

    @staticmethod
    def _validate_v2_context(value):
        if value is None:
            return
        if not isinstance(value, dict) or set(value) - {
                "version", "owner_key", "account_incarnation", "clear_generation", "registration_id",
                "registration_version", "binding_revision", "capture", "time_proof"}:
            raise FeedbackConflict("invalid_feedback_context")
        capture = value.get("capture")
        if capture is not None and (not isinstance(capture, dict)
                or set(capture) - {"clock_session_id", "tick_ms", "anchor_id"}):
            raise FeedbackConflict("invalid_feedback_context")
        proof = value.get("time_proof")
        if proof is not None and (not isinstance(proof, dict)
                or set(proof) - {"anchor_token", "send_tick_ms", "receive_tick_ms"}):
            raise FeedbackConflict("invalid_feedback_context")

    @staticmethod
    def _validate_v2_event(event):
        if not isinstance(event, dict) or set(event) - {
                "event_id", "kind", "occurred_at", "playback_id", "listened_seconds", "position_seconds",
                "reason", "context", "offer_context", "measurement"}:
            raise FeedbackConflict("invalid_feedback_event")
        RecommendationStore._validate_v2_context(event.get("context"))
        offer = event.get("offer_context")
        if offer is not None:
            if not isinstance(offer, dict) or set(offer) - {"owner_key", "offer_id", "origin_context"}:
                raise FeedbackConflict("invalid_feedback_offer_context")
            RecommendationStore._validate_v2_context(offer.get("origin_context"))
        measurement = event.get("measurement")
        if event.get("kind") == "measurement" or measurement is not None:
            if not isinstance(measurement, dict) or set(measurement) - {
                    "version", "epoch_id", "range_start_ms", "range_end_ms", "origin_context"}:
                raise FeedbackConflict("invalid_feedback_measurement")
            if event.get("kind") in {"shown", "accepted", "skipped"}:
                raise FeedbackConflict("invalid_observation_fields")
            start, end = measurement.get("range_start_ms"), measurement.get("range_end_ms")
            if (measurement.get("version") != "playback_elapsed_observed_v2"
                    or not isinstance(measurement.get("epoch_id"), str) or not measurement["epoch_id"]
                    or len(measurement["epoch_id"]) > 100 or "\x00" in measurement["epoch_id"]
                    or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= 86400000):
                raise FeedbackConflict("invalid_feedback_measurement")
            RecommendationStore._validate_v2_context(measurement.get("origin_context"))

    def claim_dispatch(self, owner, recommendation_id, event_id):
        """Claim once before dispatch. A lost reply remains unknown, not replayable.

        This is not proof of physical audio output. The player must reconcile
        its original action/playback ID; telemetry recovery never invokes it.
        """
        with self._transaction() as db:
            row = db.execute("SELECT * FROM recommendations WHERE id=? AND owner=?", (recommendation_id, owner["key"])).fetchone()
            if row is None or row["decision"] != "accepted" or row["decision_event"] != event_id:
                raise FeedbackConflict("feedback_dispatch_owner_changed")
            return db.execute("UPDATE feedback_v2_offers SET dispatch_state='claimed_unknown' WHERE offer_id=? AND dispatch_state='unclaimed'", (recommendation_id,)).rowcount == 1

    def _v2_wire_item(self, db, row):
        original, proofs = json.loads(row["event"]), json.loads(row["proof"])
        context = original.get("context") or {}
        if row["kind"] == "settings":
            return {"id": row["event_id"], "kind": "settings", "sequence": row["seq"],
                    "payload": original["settings_payload"], "capture": context.get("capture") or {},
                    "time_proof": proofs.get("event", {})}
        recommendation_id = row["recommendation_id"]
        reason = original.get("reason", "")
        if reason and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason) is None:
            reason = "media_error"
        payload = {"recommendation_id": recommendation_id, "kind": original["kind"],
                   # The local API permits a missing/null wall timestamp. Use
                   # its first durable receipt, as v1 does, without rewriting
                   # the immutable original or using a retry's current clock.
                   "occurred_at": original.get("occurred_at") if original.get("occurred_at") is not None else row["created_at"],
                   "local_received_at": row["created_at"],
                   "playback_id": original.get("playback_id"), "listened_seconds": original.get("listened_seconds", 0),
                   "position_seconds": original.get("position_seconds", 0), "reason": reason,
                   # Normal pause/terminal callbacks also carry a final elapsed
                   # range; its version follows the measurement, not the action.
                   "measurement_version": "playback_elapsed_observed_v2" if original.get("measurement") else "media-played-union-v1",
                   "category": row["category"]}
        for key, proof_key in (("offer_context", "offer"), ("measurement", "measurement")):
            if original.get(key) is not None:
                payload[key] = json.loads(_canonical(original[key]))
                origin = payload[key].get("origin_context")
                if isinstance(origin, dict) and proofs.get(proof_key):
                    origin["time_proof"] = proofs[proof_key]
        return {"id": row["event_id"], "kind": "event", "sequence": row["seq"],
                "payload": payload, "capture": context.get("capture") or {}, "time_proof": proofs.get("event", {})}

    @staticmethod
    def _v2_gc_offers(db):
        # First-display/decision admission is bounded by the original offer
        # validity. Never collect an active playback or a promised held fact.
        rows = db.execute("""SELECT r.id FROM recommendations r JOIN feedback_v2_offers o ON o.offer_id=r.id
            WHERE o.expires_at<? AND r.state IN ('pending','skipped','ended','stopped','failed')
            AND NOT EXISTS (SELECT 1 FROM feedback_v2_spool s WHERE s.recommendation_id=r.id)
            AND NOT EXISTS (SELECT 1 FROM recommendation_events e WHERE e.recommendation_id=r.id)
            LIMIT 256""", (time.time(),)).fetchall()
        for row in rows:
            db.execute("DELETE FROM feedback_v2_offers WHERE offer_id=?", (row[0],))
            db.execute("DELETE FROM recommendations WHERE id=?", (row[0],))

    def _prepare_batch_v2(self, owner, limit):
        if not owner.get("user_id") or not owner.get("community"):
            return None
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FeedbackConflict("invalid_feedback_batch_limit")
        with self._transaction() as db:
            self._v2_retire_pending(db)
            self._v2_gc_offers(db)
            active = db.execute("SELECT * FROM feedback_sync_batches WHERE owner=?", (owner["key"],)).fetchone()
            if active:
                return None if active["blocked"] else json.loads(active["body"])
            rows = db.execute("SELECT * FROM feedback_v2_spool WHERE owner=? AND state='ready' ORDER BY seq LIMIT ?", (owner["key"], limit)).fetchall()
            if not rows:
                return None
            first_context = json.loads(rows[0]["event"])["context"]
            binding = {key: first_context[key] for key in self._BINDING_FIELDS}
            batch = {"schema_version": self.V2_SCHEMA, "batch_id": str(uuid.uuid4()), "source_id": self._source(db, owner),
                     "binding": binding, "items": []}
            included = []
            for row in rows:
                context = json.loads(row["event"])["context"]
                if any(context.get(key) != value for key, value in binding.items()):
                    continue
                batch["items"].append(self._v2_wire_item(db, row))
                if len(_canonical(batch).encode()) > self.MAX_BATCH_BYTES:
                    batch["items"].pop()
                    break
                included.append(row["seq"])
            if not included:
                raise FeedbackConflict("feedback_upload_payload_too_large")
            for seq in included:
                db.execute("UPDATE feedback_v2_spool SET state='frozen' WHERE seq=?", (seq,))
            db.execute("INSERT INTO feedback_sync_batches(owner,batch_id,body,created_at) VALUES (?,?,?,?)",
                       (owner["key"], batch["batch_id"], _canonical(batch), time.time()))
            return batch

    def _acknowledge_batch_v2(self, owner, receipt):
        from services.proactive_feedback.protocol import valid_receipt_v2
        if not isinstance(receipt, dict):
            raise FeedbackConflict("invalid_feedback_receipt")
        with self._transaction() as db:
            self._source(db, owner)
            active = db.execute("SELECT * FROM feedback_sync_batches WHERE owner=?", (owner["key"],)).fetchone()
            if not active or receipt.get("batch_id") != active["batch_id"]:
                # Old transport receipts never acquire authority over a new
                # binding or another frozen batch. No unbounded receipt log.
                return False
            batch = json.loads(active["body"])
            if not valid_receipt_v2(batch, receipt):
                raise FeedbackConflict("invalid_feedback_receipt")
            for result in receipt["results"]:
                if result["status"] in {"time_deferred", "dependency_deferred"}:
                    db.execute("UPDATE feedback_v2_spool SET state='held',reason=?,retry_after=? WHERE owner=? AND event_id=?",
                               (result["status"], time.time() + 60, owner["key"], result["id"]))
                else:
                    db.execute("DELETE FROM feedback_v2_spool WHERE owner=? AND event_id=?", (owner["key"], result["id"]))
                    db.execute("""UPDATE feedback_v2_baseline SET state=?,event_id=NULL
                        WHERE owner=? AND event_id=?""", ("complete" if result["status"] in {"stored", "duplicate"} else "pending",
                                                              owner["key"], result["id"]))
            db.execute("DELETE FROM feedback_sync_batches WHERE owner=? AND batch_id=?", (owner["key"], batch["batch_id"]))
            terminal = sum(result["status"] not in {"time_deferred", "dependency_deferred"} for result in receipt["results"])
            minimal_result = {key: receipt[key] for key in ("schema_version", "batch_id", "source_id", "batch_hash", "binding")}
            minimal_result.update(terminal_count=terminal, deferred_count=len(receipt["results"]) - terminal)
            db.execute("""INSERT INTO feedback_v2_status(owner,acknowledged,latest_receipt,acknowledged_at)
                VALUES (?,?,?,?) ON CONFLICT(owner) DO UPDATE SET
                acknowledged=acknowledged+excluded.acknowledged, latest_receipt=excluded.latest_receipt,
                acknowledged_at=excluded.acknowledged_at""", (owner["key"], terminal, _canonical(minimal_result), time.time()))
            return True

    def current_settings_baseline_needed(self, owner):
        with self._transaction() as db:
            control = db.execute("SELECT snapshot FROM feedback_v2_controls WHERE owner=?", (owner["key"],)).fetchone()
            if control is None:
                return False
            snapshot = json.loads(control[0])
            key = _canonical([snapshot.get(name) for name in ("account_incarnation", "registration_id", "registration_version")])
            row = db.execute("SELECT * FROM feedback_v2_baseline WHERE owner=?", (owner["key"],)).fetchone()
            return row is None or row["binding_key"] != key or row["state"] == "pending"

    def retry_deferred(self, owner, *, limit=100, now=None):
        """Bounded retry of server-deferred originals, after fresh facts drain.

        A new anchor cannot replace an old capture's anchor ID. Retry keeps the
        original proof/capture and lets the server re-evaluate its interval;
        uncertainty that remains unprovable stays deferred, never fabricated.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FeedbackConflict("invalid_feedback_batch_limit")
        now = time.time() if now is None else now
        with self._transaction() as db:
            if (db.execute("SELECT 1 FROM feedback_sync_batches WHERE owner=?", (owner["key"],)).fetchone()
                    or db.execute("SELECT 1 FROM feedback_v2_spool WHERE owner=? AND state='ready'", (owner["key"],)).fetchone()):
                return 0
            saved = db.execute("SELECT snapshot FROM feedback_v2_controls WHERE owner=?", (owner["key"],)).fetchone()
            if saved is None:
                return 0
            control = json.loads(saved[0])
            rows = db.execute("""SELECT * FROM feedback_v2_spool WHERE owner=? AND state='held'
                AND reason IN ('time_deferred','dependency_deferred') AND retry_after<=?
                ORDER BY retry_after,seq LIMIT ?""", (owner["key"], now, limit)).fetchall()
            queued = 0
            for row in rows:
                state, reason = self._v2_classify(owner, json.loads(row["event"]), control, json.loads(row["proof"]))
                if state == "discarded":
                    db.execute("DELETE FROM feedback_v2_spool WHERE seq=?", (row["seq"],))
                else:
                    db.execute("UPDATE feedback_v2_spool SET state=?,reason=?,retry_after=? WHERE seq=?",
                               (state, reason, now + 60, row["seq"]))
                    queued += state == "ready"
            return queued

    def ensure_current_settings_baseline(self, owner, payload, context):
        """One bounded snapshot slot per account; ordinary clear keeps completion.

        The preference revision is reserved before this transaction. A crash in
        that gap may leave a revision gap, never a promised-but-lost intent.
        """
        with self._transaction() as db:
            saved = db.execute("SELECT snapshot FROM feedback_v2_controls WHERE owner=?", (owner["key"],)).fetchone()
            control = json.loads(saved[0]) if saved else None
            if not control or any((context or {}).get(key) != control.get(key) for key in self._BINDING_FIELDS):
                raise FeedbackConflict("feedback_baseline_binding_changed")
            binding_key = _canonical([control[key] for key in ("account_incarnation", "registration_id", "registration_version")])
            old = db.execute("SELECT * FROM feedback_v2_baseline WHERE owner=?", (owner["key"],)).fetchone()
            if old and old["binding_key"] == binding_key and old["state"] != "pending":
                return {"status": old["state"], "created": False}
            event_id = str(uuid.uuid4())
            intent = {"event_id": event_id, "kind": "settings", "context": context, "settings_payload": payload}
            original, proofs, digest = self.event_identity(intent)
            result = self._v2_custody(db, owner, original, proofs, digest, "settings", control, "")
            queued = result["status"] == "durable"
            db.execute("INSERT OR REPLACE INTO feedback_v2_baseline VALUES (?,?,?,?)",
                       (owner["key"], binding_key, event_id if queued else None, "queued" if queued else "pending"))
            return {**result, "created": queued}

    def update_time_proof(self, owner, event_id, payload_hash, proof, *, control=None):
        """Attach evidence to the same fact. Never edit a frozen transport body."""
        if not isinstance(proof, dict):
            raise FeedbackConflict("invalid_feedback_proof")
        with self._transaction() as db:
            persisted = db.execute("SELECT snapshot FROM feedback_v2_controls WHERE owner=?", (owner["key"],)).fetchone()
            if persisted:
                control = json.loads(persisted[0])
            row = db.execute("SELECT * FROM feedback_v2_spool WHERE owner=? AND event_id=?", (owner["key"], event_id)).fetchone()
            if row is None or row["payload_hash"] != payload_hash:
                return False
            if row["state"] == "frozen":
                return False
            original = json.loads(row["event"])
            proofs = json.loads(row["proof"])
            proofs["event"] = proof
            used = db.execute("SELECT bytes_used FROM feedback_v2_usage WHERE singleton=1").fetchone()[0]
            if used - len(row["proof"].encode()) + len(_canonical(proofs).encode()) > self.MAX_V2_PENDING_BYTES:
                return False
            state, reason = self._v2_classify(owner, original, control, proofs)
            if state == "discarded":
                db.execute("DELETE FROM feedback_v2_spool WHERE seq=?", (row["seq"],))
            else:
                db.execute("UPDATE feedback_v2_spool SET proof=?,state=?,reason=? WHERE seq=?", (_canonical(proofs), state, reason, row["seq"]))
            return True

    def _sync_status_v2(self, owner):
        with self._transaction() as db:
            counts = dict(db.execute("SELECT state,count(*) FROM feedback_v2_spool WHERE owner=? GROUP BY state", (owner["key"],)).fetchall())
            active = db.execute("SELECT * FROM feedback_sync_batches WHERE owner=?", (owner["key"],)).fetchone()
            used = db.execute("SELECT rows_used,bytes_used FROM feedback_v2_usage WHERE singleton=1").fetchone()
            control = db.execute("SELECT snapshot FROM feedback_v2_controls WHERE owner=?", (owner["key"],)).fetchone()
            age = db.execute("SELECT min(created_at) FROM feedback_v2_spool WHERE owner=?", (owner["key"],)).fetchone()[0]
            latest = db.execute("SELECT * FROM feedback_v2_status WHERE owner=?", (owner["key"],)).fetchone()
        return {"protocol_version": 2, "pending": sum(counts.values()), "ready": counts.get("ready", 0),
                "held": counts.get("held", 0), "frozen": counts.get("frozen", 0), "acked": latest["acknowledged"] if latest else 0,
                "batch_id": active["batch_id"] if active else None,
                "batch_hash": _digest(json.loads(active["body"])) if active else None,
                "blocked": bool(active["blocked"]) if active else False,
                "last_error": active["last_error"] if active else "", "attempts": active["attempts"] if active else 0,
                "oldest_pending_at": age, "control": json.loads(control[0]) if control else None,
                "statistics_incomplete": bool(latest["incomplete"]) if latest else False,
                "last_acknowledged_batch": {**json.loads(latest["latest_receipt"]), "acknowledged_at": latest["acknowledged_at"]} if latest and latest["latest_receipt"] else None,
                "capacity": {"scope": "all_local_accounts", "pending_rows": used[0], "pending_bytes": used[1],
                             "limits": {"pending_uploads": self.MAX_PENDING_UPLOADS, "pending_bytes": self.MAX_V2_PENDING_BYTES}}}
