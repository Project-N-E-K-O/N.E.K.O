"""Read complete conversation records shared by Memory Browser and history sync.

The chronological archive is independent of recent-memory review/compression.
Legacy row identities include the character and session, never matching by text.
"""

from contextlib import closing
from datetime import datetime
import json
import logging
from pathlib import Path
import re
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from memory.message_sources import is_theater_memory_message


_ID = re.compile(r"[0-9a-f]{32}")
_STAMP = re.compile(r"^\[\d{8} [^\]\s]+ \d{2}:\d{2}\] ?")


def read_chat_history(path: Path, uid: str) -> list[dict]:
    """Read original text only, without creating a database or reading recent.json."""
    path = Path(path)
    if not path.exists():
        return []
    records = {}
    conflicts = set()
    invalid = 0
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='time_indexed_original'").fetchone():
            return []
        columns = {row[1] for row in conn.execute("PRAGMA table_info(time_indexed_original)")}
        stamp_column = "timestamp" if "timestamp" in columns else "NULL"
        rows = conn.execute(f"SELECT id,session_id,message,{stamp_column} FROM time_indexed_original ORDER BY id")
        for row_id, session_id, payload, saved_at in rows:
            try:
                item = json.loads(payload)
            except (ValueError, TypeError):
                invalid += 1
                continue
            if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
                invalid += 1
                continue
            data = item["data"]
            metadata = data.get("metadata", {})
            if metadata is None:
                metadata = {}
            if not isinstance(metadata, dict):
                invalid += 1
                continue
            proof = metadata.get("chat_original_source", {})
            if proof is None:
                proof = {}
            if not isinstance(proof, dict):
                invalid += 1
                continue
            kind = item.get("type")
            role = {"human": "user", "ai": "assistant"}.get(kind) if isinstance(kind, str) else None
            if role is None or is_theater_memory_message(item):
                continue
            sources = metadata.get("sources") or []
            sources = [sources] if isinstance(sources, str) else sources
            # Drop-generated model scaffolds are not chat. Voice/legacy tags and tool text remain valid.
            if role == "user" and (metadata.get("source") == "avatar-drop"
                    or isinstance(sources, list) and "avatar-drop" in sources):
                continue
            origin = proof.get("origin", "legacy") if role == "assistant" else "legacy"
            body = data.get("content", "")
            if isinstance(body, list):
                body = "".join(part["text"] for part in body if isinstance(part, dict)
                               and part.get("type") == "text" and isinstance(part.get("text"), str))
            if not isinstance(body, str) or not body:
                continue
            if role == "assistant":
                body = _STAMP.sub("", body, count=1)
            identity = proof.get("recordId")
            if not isinstance(identity, str) or not _ID.fullmatch(identity):
                identity = uuid5(NAMESPACE_URL, f"neko-chat-history:{uid}:{session_id}:{row_id}").hex
            original_time = proof.get("originalTime")
            if not isinstance(original_time, str) or not original_time:
                original_time = None
            if original_time is None and saved_at:
                try:
                    original_time = str(int(datetime.fromisoformat(saved_at).timestamp() * 1000))
                except (ValueError, TypeError, OverflowError, OSError):
                    original_time = None
            record = {"messageId": identity, "characterSyncId": uid, "role": role, "text": body,
                      "originalTime": original_time, "timezone": "UTC" if original_time else None,
                      "conversationId": None, "replyToMessageId": None, "origin": origin}
            if identity in conflicts:
                continue
            if identity in records and records[identity] != record:
                conflicts.add(identity)
                del records[identity]
                continue
            records[identity] = record
    if invalid or conflicts:
        logging.getLogger(__name__).debug("Chat archive ignored invalid rows=%d, conflicting identities=%d", invalid, len(conflicts))
    return list(records.values())
