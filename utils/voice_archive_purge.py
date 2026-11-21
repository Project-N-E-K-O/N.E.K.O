"""Durable, identity-scoped fences for voice archive deletion.

This module is intentionally below services and main_logic in the dependency
graph so history reads, archive writers and HTTP routes share one purge fence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


def _purge_intent_path(app_docs_dir, character: str) -> Path:
    key = hashlib.sha256(str(character).encode("utf-8")).hexdigest()
    return Path(app_docs_dir) / "voice_archive" / "purge-intents" / f"{key}.json"


def has_pending_character_voice_archive_purge(app_docs_dir, character: str) -> bool:
    """Fail closed while a deleted identity's archive purge is pending."""
    path = _purge_intent_path(app_docs_dir, character)
    return path.exists() or path.is_symlink()


def _read_purge_intent(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(payload.get("character"), str)
        or not payload["character"]
        or payload.get("state") not in {"prepared", "committed"}
    ):
        raise ValueError("invalid voice archive purge intent")
    return payload


def write_character_voice_archive_purge_intent(
    app_docs_dir,
    character: str,
    *,
    character_uid: str | None = None,
    character_id: str | None = None,
) -> Path:
    """Atomically fence a character name before its config deletion commits."""
    if not isinstance(character, str) or not character:
        raise ValueError("voice archive purge requires a character name")
    path = _purge_intent_path(app_docs_dir, character)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "version": 1,
        "character": character,
        "character_uid": str(character_uid or "").strip() or None,
        "character_id": str(character_id or "").strip() or None,
        "state": "prepared",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if path.exists() or path.is_symlink():
        existing = _read_purge_intent(path)
        if existing["character"] != character:
            raise RuntimeError("voice archive purge intent identity mismatch")
        for field in ("character_uid", "character_id"):
            old_value, new_value = existing.get(field), payload.get(field)
            if old_value and new_value and old_value != new_value:
                raise RuntimeError("another identity's voice archive purge is still pending")
            if old_value:
                payload[field] = old_value
        if existing.get("state") == "committed":
            payload["state"] = "committed"
            payload["created_at"] = existing.get("created_at") or payload["created_at"]
    _write_purge_intent_payload(path, payload)
    return path


def _write_purge_intent_payload(path: Path, payload: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".purge-", suffix=".tmp", dir=path.parent)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Atomic replace is the required durability boundary; directory
            # fsync is best-effort on platforms that do not support it.
            pass
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def commit_character_voice_archive_purge_intent(
    app_docs_dir,
    character: str,
    *,
    character_uid: str | None = None,
    character_id: str | None = None,
) -> Path:
    """Mark a prepared intent committed after characters.json is durable."""
    path = write_character_voice_archive_purge_intent(
        app_docs_dir, character, character_uid=character_uid, character_id=character_id,
    )
    payload = _read_purge_intent(path)
    payload["state"] = "committed"
    _write_purge_intent_payload(path, payload)
    return path


def discard_character_voice_archive_purge_intent(
    app_docs_dir,
    character: str,
    *,
    character_uid: str | None = None,
    character_id: str | None = None,
) -> bool:
    """Remove only an uncommitted intent for the same character identity."""
    path = _purge_intent_path(app_docs_dir, character)
    if not path.exists() and not path.is_symlink():
        return True
    payload = _read_purge_intent(path)
    if payload.get("state") == "committed":
        return False
    for field, expected in (("character_uid", character_uid), ("character_id", character_id)):
        actual = payload.get(field)
        if actual and expected and actual != expected:
            return False
    path.unlink(missing_ok=True)
    return True
