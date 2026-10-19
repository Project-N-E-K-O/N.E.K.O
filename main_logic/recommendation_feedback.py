"""Provider-neutral, local recommendation evidence. No dialogue or credentials.

Call this synchronous store on a worker thread. Decisions and their evidence
commit in one SQLite transaction; the insertion cursor also orders late events.
"""
from __future__ import annotations

import json
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


class RecommendationStore:
    MAX_RECOMMENDATIONS = 10_000
    MAX_EVENTS = 100_000
    TTL_SECONDS = 1800
    MAX_PENDING_UPLOADS = 100_000
    MAX_BATCH_BYTES = 1_048_576
    HISTORY_WARNING_BYTES = 512 * 1024 * 1024

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
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

    def create(self, owner: dict, character: str, turn_id: str, track: dict, settings: dict, *, category: str = "music"):
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

    def record(self, owner: dict, recommendation_id: str, event: dict, *, disabled_categories: frozenset[str] = frozenset()):
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
            item["track"] = json.loads(item["track"])
            item["settings"] = json.loads(item["settings"])
            item["category"] = item["track"].get("category", "music")
            result.append(item)
        return {"events": result, "next_cursor": result[-1]["seq"] if result else after}

    def enqueue_settings(self, owner: dict, entry_id: str, payload: dict):
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
                or not isinstance(entry_id, str) or not entry_id
                or not isinstance(payload, dict) or set(payload) != allowed
                or not isinstance(payload["origin"], str)
                or payload["origin"] not in {"user", "system", "baseline"}
                or type(payload["revision"]) is not int or payload["revision"] < 1
                or type(payload["occurred_at"]) not in (int, float)):
            raise FeedbackConflict("invalid_settings_evidence")
        try:
            finite_timestamp = math.isfinite(payload["occurred_at"])
        except OverflowError:
            # A JSON integer may exceed the finite timestamp representation.
            finite_timestamp = False
        if not finite_timestamp:
            raise FeedbackConflict("invalid_settings_evidence")
        for name in ("previous", "settings"):
            if not isinstance(payload[name], dict) or _settings_snapshot(payload[name]) != payload[name]:
                raise FeedbackConflict("invalid_settings_evidence")
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
            db.execute("""UPDATE feedback_sync_batches SET attempts=attempts+1,last_error=?,
                blocked=CASE WHEN ? THEN 1 ELSE blocked END WHERE owner=? AND batch_id=?""",
                       (safe_code, bool(permanent), owner["key"], batch_id))

    def resume_blocked_batch(self, owner: dict, batch_id: str, batch_hash: str):
        """Explicit retry of exactly the inspected immutable batch, never a replacement.

        The caller fences the logged-in account. BEGIN IMMEDIATE serializes this
        CAS with late acknowledgments, account writes and other recovery requests.
        """
        with self._transaction() as db:
            self._source(db, owner)
            active = db.execute("SELECT * FROM feedback_sync_batches WHERE owner=? AND batch_id=?",
                                (owner["key"], batch_id)).fetchone()
            if (active is None or not active["blocked"]
                    or _digest(json.loads(active["body"])) != batch_hash):
                raise FeedbackConflict("feedback_resume_conflict")
            changed = db.execute("""UPDATE feedback_sync_batches SET blocked=0,last_error=''
                WHERE owner=? AND batch_id=? AND body=? AND blocked=1""",
                (owner["key"], batch_id, active["body"])).rowcount
            if changed != 1:
                raise FeedbackConflict("feedback_resume_conflict")
        return True

    def sync_status(self, owner: dict):
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
