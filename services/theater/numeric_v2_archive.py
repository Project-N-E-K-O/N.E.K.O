"""Numeric v2 ending receipts and public-memory archival."""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import asyncio
from contextlib import nullcontext
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from weakref import WeakValueDictionary

from memory.message_sources import THEATER_MEMORY_SOURCE

from .numeric_v2_performance import content_blocks, mixed_performance_blocks
from .numeric_v2_storage_transaction import discard_temporary_file, run_storage_mutation


# The caller strongly holds the lock inside with; idle receipt locks need not remain resident, so historical Session counts do not determine process memory.
_RECEIPT_LOCKS: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
_RECEIPT_LOCKS_GUARD = threading.Lock()
PUBLIC_ARCHIVES_PER_STORY_CHARACTER = 5
_RECEIPT_ID_RE = re.compile(r"^theater_end_[0-9a-f]{40}$")
# Public cold archives that fail schema checks are moved here at startup. It is
# never trimmed; explicit character deletes, story forgets and package deletes
# erase the quarantined files that belong to their scope.
PUBLIC_ARCHIVE_QUARANTINE_DIRNAME = "quarantine_public_archives"
# Quarantine keeps the original ``sha256(session_id).json`` basename as the suffix.
_QUARANTINED_ARCHIVE_KEY_RE = re.compile(r"(?:^|-)([0-9a-f]{64})\.json$")
# Startup audit moves invalid/duplicate session files (full ledger and transcript)
# here as ``quarantined_file_name(reason, "<session_id>.json")``. It is never
# trimmed; explicit deletes/forgets erase the ones in their scope.
SESSION_QUARANTINE_DIRNAME = "quarantine"
# Files quarantined before the nonce was shortened carry a full 32-hex uuid.
_QUARANTINED_SESSION_NAME_RE = re.compile(
    r"^[a-z]+-\d+-[0-9a-f]{8}(?:[0-9a-f]{24})?-([A-Za-z0-9._-]+)\.json$"
)
_QUARANTINED_SESSION_FILE_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*\.json")
# Memory writes of end receipts deleted without a successor receipt (restart
# replacement, startup cleanup) wait here, one file per archive request, until
# the next archive/skip/forget of that character retracts them.
RETRACT_INTENT_DIRNAME = "retract_intents"
RETRACT_INTENT_SCHEMA = "neko.theater.retract-intent.v1"
# The memory service's latest story-forget marker per (story, character); the
# theater attaches it to archive requests issued after that forget completed.
FORGET_MARKER_DIRNAME = "forget_markers"
FORGET_MARKER_SCHEMA = "neko.theater.forget-marker.v1"

logger = logging.getLogger(__name__)


def quarantined_file_name(reason: str, original_name: str) -> str:
    """Name a quarantined file ``{reason}-{ms}-{nonce}-{original_name}``.

    The original basename stays as the suffix because scoped deletes attribute
    quarantined files by it. The nonce is kept short: without Windows long-path
    support a move past 260 characters fails, and a public archive basename
    alone is already 69 characters.
    """
    return f"{reason}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}-{original_name}"


def _retry_windows_permission_error(operation):
    """Retry transient Windows file-sharing failures without hiding persistent errors."""

    for delay in (0.005, 0.01, 0.02, 0.04, 0.08):
        try:
            return operation()
        except PermissionError:
            if os.name != "nt":
                raise
            time.sleep(delay)
    return operation()


def _receipt_lock(path: Path) -> threading.Lock:
    """Serialize receipt creation by Session pointer within one process to prevent concurrent refreshes creating forks."""  # noqa: DOCSTRING_CJK

    key = str(path.resolve())
    with _RECEIPT_LOCKS_GUARD:
        lock = _RECEIPT_LOCKS.get(key)
        if lock is None:
            # Retain a strong local reference until return so the weak-reference table cannot immediately collect a new lock before with takes ownership.
            lock = threading.Lock()
            _RECEIPT_LOCKS[key] = lock
        return lock


class NumericV2ArchiveError(ValueError):
    """Invalid ending receipt or archive request."""  # noqa: DOCSTRING_CJK


class NumericV2ArchiveStore:
    """Persist archive receipts separately from story Sessions to avoid rewriting ended Ledgers."""  # noqa: DOCSTRING_CJK

    def __init__(self, theater_root: Path, *, write_transaction=nullcontext):
        self.write_transaction = write_transaction
        self.root = Path(theater_root) / "numeric_v2" / "end_receipts"
        self.public_archive_root = Path(theater_root) / "numeric_v2" / "public_archives"
        self.public_archive_quarantine_root = (
            Path(theater_root) / "numeric_v2" / PUBLIC_ARCHIVE_QUARANTINE_DIRNAME
        )
        self.session_quarantine_root = (
            Path(theater_root) / "numeric_v2" / SESSION_QUARANTINE_DIRNAME
        )
        self.retract_intent_root = Path(theater_root) / "numeric_v2" / RETRACT_INTENT_DIRNAME
        self.forget_marker_root = Path(theater_root) / "numeric_v2" / FORGET_MARKER_DIRNAME

    @staticmethod
    def _session_key(session_id: str) -> str:
        return hashlib.sha256(session_id.encode("utf-8")).hexdigest()

    def _session_path(self, session_id: str) -> Path:
        return self.root / f"session-{self._session_key(session_id)}.json"

    def _forget_path(self, story_id: str, character_id: str) -> Path:
        key = json.dumps([story_id, character_id], ensure_ascii=False)
        return self.root.parent / "forget_transactions" / f"{self._session_key(key)}.json"

    def pending_forget(self, story_id: str, character_id: str) -> dict[str, Any] | None:
        """Return the durable, scoped intent left by an interrupted forget request."""
        pending = self._read(self._forget_path(story_id, character_id))
        if pending is not None and (
            pending.get("schema") != "neko.theater.forget.v1"
            or pending.get("story_id") != story_id
            or pending.get("character_id") != character_id
        ):
            raise NumericV2ArchiveError("numeric_forget_transaction_invalid")
        return pending

    def pending_forget_story_ids(self, character_id: str) -> list[str]:
        """Keep interrupted forget operations discoverable without their packages."""
        result = []
        for path in sorted((self.root.parent / "forget_transactions").glob("*.json")):
            pending = self._read(path)
            if pending and pending.get("character_id") == character_id:
                story_id = str(pending.get("story_id") or "")
                if self.pending_forget(story_id, character_id) != pending:
                    raise NumericV2ArchiveError("numeric_forget_transaction_invalid")
                result.append(story_id)
        return result

    def prepare_forget(self, *, story_id: str, character_id: str,
                       legacy_catgirl_name: str, session: Any = None) -> dict[str, Any]:
        """Freeze targets and the revision boundary before either service deletes data."""
        pending = self.pending_forget(story_id, character_id)
        if pending is not None:
            return pending
        scope = dict(story_id=story_id, character_id=character_id,
                     legacy_catgirl_name=legacy_catgirl_name)
        archives = self.list_public_archives(**scope, raise_on_io_error=True)
        receipts = self.receipt_paths_for_scope(**scope, raise_on_io_error=True)
        # An explicit forget also erases quarantined copies of this story's archives
        # and every quarantined archive whose owner can no longer be determined.
        quarantined = self.quarantined_public_archive_paths(
            **scope,
            session_ids=[str(session.session_id)] if session is not None else (),
            include_unattributable=True,
        )
        # Quarantined session files hold the full ledger; same policy as archives.
        quarantined_sessions = self.quarantined_session_paths(
            **scope,
            session_ids=[str(session.session_id)] if session is not None else (),
            include_unattributable=True,
        )
        pending = {
            "schema": "neko.theater.forget.v1", "story_id": story_id,
            "character_id": character_id,
            "session_id": session.session_id if session is not None else "",
            "through_revision": session.revision if session is not None else -1,
            "archive_files": [Path(archive["path"]).name for archive in archives],
            "receipt_files": [path.name for path in receipts],
            "quarantined_archive_files": [path.name for path in quarantined],
            "quarantined_session_files": [path.name for path in quarantined_sessions],
        }
        self._write(self._forget_path(story_id, character_id), pending)
        return pending

    def forget_paths_for_character(self, character_id: str) -> list[Path]:
        """Expose validated intent files for character deletion and rollback."""
        if not character_id:
            return []
        return [self._forget_path(story_id, character_id)
                for story_id in self.pending_forget_story_ids(character_id)]

    def delete_forget_files(self, pending: Mapping[str, Any]) -> None:
        """Retry the original deletion list, including pointers orphaned by an interruption."""
        targets = []
        for key, root in (
            ("archive_files", self.public_archive_root),
            ("receipt_files", self.root),
            ("quarantined_archive_files", self.public_archive_quarantine_root),
        ):
            # Intents written before quarantine purging existed carry no quarantine list.
            names = pending.get(key, []) if key == "quarantined_archive_files" else pending[key]
            if not isinstance(names, list):
                raise NumericV2ArchiveError("numeric_forget_transaction_invalid")
            for name in names:
                if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_-]+\.json", name):
                    raise NumericV2ArchiveError("numeric_forget_transaction_invalid")
                targets.append(root / name)
        # Intents written before session-quarantine purging carry no such list.
        session_names = pending.get("quarantined_session_files", [])
        if not isinstance(session_names, list):
            raise NumericV2ArchiveError("numeric_forget_transaction_invalid")
        for name in session_names:
            if not isinstance(name, str) or not _QUARANTINED_SESSION_FILE_RE.fullmatch(name):
                raise NumericV2ArchiveError("numeric_forget_transaction_invalid")
            targets.append(self.session_quarantine_root / name)
        for path in targets:
            path.unlink(missing_ok=True)

    def complete_forget(self, story_id: str, character_id: str) -> None:
        self._forget_path(story_id, character_id).unlink(missing_ok=True)

    def _forget_marker_path(self, story_id: str, character_id: str) -> Path:
        key = json.dumps([story_id, character_id], ensure_ascii=False)
        return self.forget_marker_root / f"{self._session_key(key)}.json"

    def forget_marker(self, story_id: str, character_id: str) -> str:
        """Return the memory service's marker from this story's latest completed forget.

        Empty when the story was never forgotten for this character. Archive
        requests attach it so the memory service can tell them apart from late
        writes issued before the forget, which cannot know the marker.
        """
        payload = self._read(self._forget_marker_path(story_id, character_id))
        if payload is None:
            return ""
        marker = payload.get("forget_marker")
        if (
            payload.get("schema") != FORGET_MARKER_SCHEMA
            or payload.get("story_id") != story_id
            or payload.get("character_id") != character_id
            or not isinstance(marker, str)
            or not marker
        ):
            raise NumericV2ArchiveError("numeric_forget_marker_invalid")
        return marker

    def record_forget_marker(self, story_id: str, character_id: str, marker: str) -> None:
        """Durably adopt the marker a story forget just returned (before completing it)."""
        if not isinstance(marker, str) or not marker.strip():
            raise NumericV2ArchiveError("numeric_forget_marker_invalid")
        self._write(self._forget_marker_path(story_id, character_id), {
            "schema": FORGET_MARKER_SCHEMA,
            "story_id": story_id,
            "character_id": character_id,
            "forget_marker": marker,
        })

    def forget_marker_paths_for_character(self, character_id: str) -> list[Path]:
        """Markers die with their character, whose memory (and tombstones) go too."""
        if not character_id or not self.forget_marker_root.is_dir():
            return []
        paths = []
        for path in sorted(self.forget_marker_root.glob("*.json")):
            payload = self._read(path)
            if payload is not None and payload.get("character_id") == character_id:
                paths.append(path)
        return paths

    def _receipt_path(self, receipt_id: str) -> Path:
        # Receipt IDs must use the fixed server-generated format; forbid path separators and parent segments escaping the receipt root.
        if not _RECEIPT_ID_RE.fullmatch(str(receipt_id or "")):
            raise NumericV2ArchiveError("numeric_end_receipt_invalid")
        return self.root / f"{receipt_id}.json"

    def _public_archive_path(self, session_id: str) -> Path:
        return self.public_archive_root / f"{self._session_key(session_id)}.json"

    def _staged_archive_path(self, receipt_id: str) -> Path:
        # Pending archives share the receipt root so script-deletion transactions can use the same backup manifest.
        return self.root / f"staged-{self._receipt_path(receipt_id).stem}.json"

    @staticmethod
    def _matches_character(
        payload: Mapping[str, Any],
        character_id: str,
        legacy_catgirl_name: str = "",
    ) -> bool:
        stored_character_id = str(payload.get("character_id") or "").strip()
        if character_id:
            return stored_character_id == character_id or bool(
                not stored_character_id
                and legacy_catgirl_name
                and str(payload.get("catgirl_name") or "").strip()
                == legacy_catgirl_name
            )
        return bool(
            not legacy_catgirl_name
            or str(payload.get("catgirl_name") or "").strip()
            == legacy_catgirl_name
        )

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(_retry_windows_permission_error(lambda: path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            error = NumericV2ArchiveError("numeric_end_receipt_read_failed")
            # On strict-enumeration failure, callers must identify the specific file so users can repair or move it.
            error.path = str(path)
            raise error from exc
        return value if isinstance(value, dict) else None

    @staticmethod
    def _valid_public_archive_payload(payload: Mapping[str, Any] | None) -> bool:
        """Return whether a public archive payload names its story, session and owner."""
        return bool(
            payload is not None
            and payload.get("schema") == "neko.theater.numeric.v2.public-archive"
            and isinstance(payload.get("story_id"), str)
            and payload["story_id"].strip()
            and isinstance(payload.get("session_id"), str)
            and payload["session_id"].strip()
            and "character_id" in payload
            and isinstance(payload["character_id"], str)
        )

    @staticmethod
    def _write(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=f".{path.stem}-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True).encode("utf-8"))
                temporary.flush()
                os.fsync(temporary.fileno())
            _retry_windows_permission_error(lambda: os.replace(temporary_path, path))
        except OSError as exc:
            raise NumericV2ArchiveError("numeric_end_receipt_write_failed") from exc
        finally:
            discard_temporary_file(temporary_path)

    @staticmethod
    def _reconciled_written_pointer(
        pointer: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Return the Session watermark a written receipt should backfill; never roll pointers back after a newer receipt takes ownership."""  # noqa: DOCSTRING_CJK

        if receipt.get("status") != "written":
            return None
        receipt_id = str(receipt.get("receipt_id") or "")
        if not receipt_id or str(pointer.get("receipt_id") or "") != receipt_id:
            return None
        completed_revision = receipt.get(
            "archive_through_revision",
            receipt.get("revision"),
        )
        if not isinstance(completed_revision, int) or isinstance(completed_revision, bool):
            return None
        current_revision = pointer.get("archived_through_revision", -1)
        if not isinstance(current_revision, int) or isinstance(current_revision, bool):
            current_revision = -1
        if current_revision >= completed_revision:
            return None
        return {
            "receipt_id": receipt_id,
            "archived_through_revision": completed_revision,
        }

    def reconcile_written_receipt(self, receipt: Mapping[str, Any]) -> bool:
        """Repair interruption between receipt writing and Session watermark commit."""  # noqa: DOCSTRING_CJK

        session_id = str(receipt.get("session_id") or "")
        if not session_id:
            return False
        session_path = self._session_path(session_id)
        with _receipt_lock(session_path):
            pointer = self._read(session_path) or {}
            reconciled = self._reconciled_written_pointer(pointer, receipt)
            if reconciled is None:
                return False
            self._write(session_path, reconciled)
            return True

    @staticmethod
    def pending_retractions(receipt: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        """Return the well-formed retraction obligations a receipt inherited."""

        result: list[dict[str, Any]] = []
        raw = (receipt or {}).get("pending_retractions")
        for entry in raw if isinstance(raw, list) else ():
            if not isinstance(entry, Mapping):
                continue
            request_id = str(entry.get("archive_request_id") or "").strip()
            attempt = entry.get("archive_attempt")
            through = entry.get("archive_through_revision")
            if (
                request_id
                and isinstance(attempt, int) and not isinstance(attempt, bool)
                and isinstance(through, int) and not isinstance(through, bool)
            ):
                result.append({
                    "archive_request_id": request_id,
                    "archive_attempt": attempt,
                    "archive_through_revision": through,
                })
        return result

    def _carried_retractions(self, previous: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        """Collect the memory writes a replaced receipt may have left behind.

        A receipt that is neither written nor skipped may have issued archive
        attempts whose memory write timed out but still landed. Replacing it
        deletes its staged copy (the only local evidence), so the obligation to
        retract those attempts moves onto the new receipt instead.
        """

        if previous is None or previous.get("status") in {"written", "skipped"}:
            return []
        carried = {
            entry["archive_request_id"]: entry
            for entry in self.pending_retractions(previous)
        }
        request_id = str(previous.get("archive_request_id") or "").strip()
        raw_attempt = previous.get("archive_attempt")
        attempt = (
            raw_attempt
            if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool) and raw_attempt > 0
            else 0
        )
        staged = self._staged_archive_path(str(previous.get("receipt_id") or "")).is_file()
        through = previous.get("archive_through_revision", previous.get("revision"))
        if (
            request_id
            and (attempt > 0 or staged)
            and isinstance(through, int)
            and not isinstance(through, bool)
        ):
            prior = carried.get(request_id)
            carried[request_id] = {
                "archive_request_id": request_id,
                "archive_attempt": max(attempt, int((prior or {}).get("archive_attempt") or 0)),
                "archive_through_revision": through,
            }
        return list(carried.values())

    def forget_retraction_obligations(self, pending: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Return the unresolved archive attempts of the receipts a forget deletes.

        Covers each receipt's own issued attempts and the ones it inherited from
        replaced receipts (``pending_retractions``). Unreadable receipts are
        skipped: this list is a best-effort second fence behind the memory
        server's story tombstone.
        """

        names = pending.get("receipt_files")
        obligations: dict[str, dict[str, Any]] = {}
        for name in names if isinstance(names, list) else ():
            if not isinstance(name, str) or not re.fullmatch(r"theater_end_[a-z0-9_-]+\.json", name):
                continue
            try:
                receipt = self._read(self.root / name)
            except NumericV2ArchiveError:
                continue
            if receipt is None:
                continue
            for entry in self._carried_retractions(receipt):
                obligations[entry["archive_request_id"]] = {
                    "story_id": str(receipt.get("story_id") or ""),
                    "session_id": str(receipt.get("session_id") or ""),
                    **entry,
                }
        return list(obligations.values())

    def _retract_intent_path(self, archive_request_id: str) -> Path:
        return self.retract_intent_root / f"{self._session_key(archive_request_id)}.json"

    def queue_retractions(
        self,
        receipt: Mapping[str, Any] | None,
        *,
        skip_request_ids: Iterable[str] = (),
    ) -> int:
        """Durably queue the unresolved memory writes of a receipt about to be deleted.

        Used where a receipt disappears without a successor that inherits its
        ``pending_retractions`` (restart replacement, startup cleanup). Written
        before the receipt and its staged copy are removed, so a crash in between
        keeps the obligation. Written and skipped receipts queue nothing: the
        player chose to keep that memory, or the skip already retracted it.
        """

        skipped = set(skip_request_ids)
        queued = 0
        for entry in self._carried_retractions(receipt):
            if entry["archive_request_id"] in skipped:
                continue
            path = self._retract_intent_path(entry["archive_request_id"])
            with _receipt_lock(path):
                try:
                    previous = self._read(path) or {}
                except NumericV2ArchiveError as exc:
                    if isinstance(exc.__cause__, OSError):
                        raise
                    previous = {}
                previous_attempt = previous.get("archive_attempt")
                if (
                    previous.get("archive_request_id") != entry["archive_request_id"]
                    or not isinstance(previous_attempt, int)
                    or isinstance(previous_attempt, bool)
                ):
                    previous_attempt = 0
                self._write(path, {
                    "schema": RETRACT_INTENT_SCHEMA,
                    "story_id": str((receipt or {}).get("story_id") or ""),
                    "session_id": str((receipt or {}).get("session_id") or ""),
                    "character_id": str((receipt or {}).get("character_id") or ""),
                    "catgirl_name": str((receipt or {}).get("catgirl_name") or ""),
                    **entry,
                    "archive_attempt": max(entry["archive_attempt"], previous_attempt),
                })
            queued += 1
        return queued

    def _retract_intents(self) -> list[tuple[Path, dict[str, Any]]]:
        if not self.retract_intent_root.is_dir():
            return []
        result = []
        for path in sorted(self.retract_intent_root.glob("*.json")):
            try:
                payload = self._read(path)
            except NumericV2ArchiveError:
                # Unreadable now; the next drain tries again. Never guess its owner.
                continue
            if (
                payload is None
                or payload.get("schema") != RETRACT_INTENT_SCHEMA
                or not str(payload.get("story_id") or "").strip()
                or not str(payload.get("session_id") or "").strip()
                or not self.pending_retractions({"pending_retractions": [payload]})
            ):
                continue
            result.append((path, payload))
        return result

    def pending_retract_intents(
        self,
        *,
        character_id: str,
        legacy_catgirl_name: str = "",
    ) -> list[dict[str, Any]]:
        """Return the queued retractions owed to one character's memory."""

        return [
            payload
            for _path, payload in self._retract_intents()
            if self._matches_character(payload, character_id, legacy_catgirl_name)
        ]

    def retract_intent_paths_for_character(
        self,
        character_id: str,
        legacy_catgirl_name: str = "",
    ) -> list[Path]:
        """Queued retractions die with their character, whose memory is deleted too."""

        if not character_id and not legacy_catgirl_name:
            return []
        return [
            path
            for path, payload in self._retract_intents()
            if self._matches_character(payload, character_id, legacy_catgirl_name)
        ]

    def complete_retract_intent(self, intent: Mapping[str, Any]) -> bool:
        """Drop a queued retraction once the memory service confirmed it.

        A concurrent queue call may have raised the attempt number meanwhile;
        that newer obligation stays queued.
        """

        path = self._retract_intent_path(str(intent.get("archive_request_id") or ""))
        with _receipt_lock(path):
            current = self._read(path)
            if current is None:
                return True
            attempt = current.get("archive_attempt")
            if (
                isinstance(attempt, int)
                and not isinstance(attempt, bool)
                and attempt > int(intent.get("archive_attempt") or 0)
            ):
                return False
            path.unlink(missing_ok=True)
            return True

    def create_or_get(self, session: Any) -> dict[str, Any]:
        session_id = str(session.session_id)
        session_path = self._session_path(session_id)
        with _receipt_lock(session_path):
            pointer = self._read(session_path)
            previous_receipt_id = str((pointer or {}).get("receipt_id") or "")
            if pointer and pointer.get("receipt_id"):
                existing = self.load(str(pointer["receipt_id"]))
                reconciled = self._reconciled_written_pointer(pointer, existing or {})
                if reconciled is not None:
                    # A process can stop between atomic writes of a written receipt and watermark pointer; reconcile before creating the next receipt.
                    self._write(session_path, reconciled)
                    pointer = reconciled
                # A Session can exit and resume repeatedly; only matching revisions identify the same exit receipt.
                if existing is not None and existing.get("revision") == int(session.revision):
                    return existing
            archived_through_revision = -1
            if pointer is not None:
                raw_archived_revision = pointer.get("archived_through_revision")
                if isinstance(raw_archived_revision, int) and not isinstance(raw_archived_revision, bool):
                    archived_through_revision = raw_archived_revision
                elif pointer.get("receipt_id"):
                    # Legacy pointers lack archive watermarks; treat successful old receipts as archived through their revision,
                    # avoiding duplicate catgirl-memory writes of the same public performance after an upgrade.
                    previous = self.load(str(pointer["receipt_id"]))
                    if previous is not None and previous.get("status") == "written":
                        previous_revision = previous.get("revision")
                        if isinstance(previous_revision, int) and not isinstance(previous_revision, bool):
                            archived_through_revision = previous_revision
            # Explicit forgetting means these revisions must never enter memory again; new receipts must not reset that watermark.
            forgotten_through_revision = getattr(session, "forgotten_through_revision", -1)
            if isinstance(forgotten_through_revision, int) and not isinstance(forgotten_through_revision, bool):
                archived_through_revision = max(
                    archived_through_revision,
                    forgotten_through_revision,
                )
            # Immutable ending facts determine both receipt/archive-request IDs, ensuring convergence after crashes or concurrent creation.
            seed = "\x1f".join(
                (
                    str(session.story_package_id),
                    session_id,
                    str(int(session.revision)),
                    str(session.catgirl_binding.get("character_id") or ""),
                )
            )
            digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
            receipt = {
                "schema": "neko.theater.numeric.v2.end-receipt",
                "receipt_id": f"theater_end_{digest[:40]}",
                "story_id": str(session.story_package_id),
                "session_id": session_id,
                "revision": int(session.revision),
                "character_id": str(session.catgirl_binding.get("character_id") or ""),
                "catgirl_name": str(session.catgirl_binding.get("catgirl_name") or ""),
                "status": "skipped" if archived_through_revision >= int(session.revision) else "pending",
                "archive_request_id": f"theater_archive_{digest}",
                "archive_from_revision": max(1, archived_through_revision + 1),
                "archive_through_revision": int(session.revision),
                "include_opening": archived_through_revision < 0,
            }
            if previous_receipt_id and previous_receipt_id != receipt["receipt_id"]:
                # Written before the old receipt and its staged copy are deleted, so
                # a crash in between never loses a pending retraction.
                carried = self._carried_retractions(self.load(previous_receipt_id))
                if carried:
                    receipt["pending_retractions"] = carried
            self._write(self._receipt_path(receipt["receipt_id"]), receipt)
            self._write(session_path, {
                "receipt_id": receipt["receipt_id"],
                "archived_through_revision": archived_through_revision,
            })
            if previous_receipt_id and previous_receipt_id != receipt["receipt_id"]:
                # A new revision owns the unique pointer; the old receipt and pending archive are no longer reachable.
                previous_receipt = self.load(previous_receipt_id)
                previous_public_exists = self._public_archive_path(session_id).is_file()
                # Pre-upgrade data may have reached memory without a cold archive; retain these old written
                # receipts until reopening backfills the archive, or the only compatibility evidence is lost.
                if (
                    previous_receipt is None
                    or previous_receipt.get("status") != "written"
                    or previous_public_exists
                ):
                    for stale_path in (
                        self._receipt_path(previous_receipt_id),
                        self._staged_archive_path(previous_receipt_id),
                    ):
                        try:
                            stale_path.unlink()
                        except FileNotFoundError:
                            pass
            return receipt

    def load(self, receipt_id: str) -> dict[str, Any] | None:
        return self._read(self._receipt_path(receipt_id))

    def load_for_session(self, session_id: str) -> dict[str, Any] | None:
        pointer = self._read(self._session_path(str(session_id)))
        if not pointer or not pointer.get("receipt_id"):
            return None
        receipt = self.load(str(pointer["receipt_id"]))
        if receipt is not None:
            self.reconcile_written_receipt(receipt)
        return receipt

    def has_written_receipt_for_session(self, session_id: str) -> bool:
        """Check pre-upgrade written receipts for cold-archive backfill before reopening."""  # noqa: DOCSTRING_CJK

        normalized_session_id = str(session_id or "").strip()
        receipt_paths = self.root.glob("theater_end_*.json") if self.root.is_dir() else ()
        for path in receipt_paths:
            try:
                receipt = self._read(path)
            except NumericV2ArchiveError:
                continue
            if (
                receipt is not None
                and str(receipt.get("session_id") or "") == normalized_session_id
                and receipt.get("status") == "written"
            ):
                return True
        return False

    def update(
        self,
        receipt: Mapping[str, Any],
        *,
        status: str,
        archive_request_id: str = "",
        archive_attempt: int | None = None,
    ) -> dict[str, Any]:
        if status not in {"pending", "writing", "written", "skipped"}:
            raise NumericV2ArchiveError("numeric_archive_status_invalid")
        updated = dict(receipt)
        updated["status"] = status
        if archive_request_id:
            updated["archive_request_id"] = archive_request_id
        if archive_attempt is not None:
            # Numbered before each memory request so a later skip can fence
            # exactly the attempts that may still be in flight.
            updated["archive_attempt"] = int(archive_attempt)
        self._write(self._receipt_path(str(updated.get("receipt_id") or "")), updated)
        session_id = str(updated.get("session_id") or "")
        if session_id:
            session_path = self._session_path(session_id)
            with _receipt_lock(session_path):
                pointer = self._read(session_path) or {}
                archived_through_revision = pointer.get("archived_through_revision", -1)
                if not isinstance(archived_through_revision, int) or isinstance(archived_through_revision, bool):
                    archived_through_revision = -1
                if status == "written":
                    completed_revision = updated.get("archive_through_revision", updated.get("revision"))
                    if isinstance(completed_revision, int) and not isinstance(completed_revision, bool):
                        archived_through_revision = max(archived_through_revision, completed_revision)
                self._write(session_path, {
                    "receipt_id": str(updated.get("receipt_id") or ""),
                    "archived_through_revision": archived_through_revision,
                })
        return updated

    async def mutate(self, operation, *args, **kwargs):
        return await run_storage_mutation(self.write_transaction, operation, *args, **kwargs)

    async def acreate_or_get(self, session: Any) -> dict[str, Any]:
        """Persist online requests through threads to avoid blocking the FastAPI event loop."""  # noqa: DOCSTRING_CJK

        return await self.mutate(self.create_or_get, session)

    async def aload(self, receipt_id: str) -> dict[str, Any] | None:
        """Read ending receipts asynchronously."""  # noqa: DOCSTRING_CJK

        return await asyncio.to_thread(self.load, receipt_id)

    async def aload_for_session(self, session_id: str) -> dict[str, Any] | None:
        return await self.mutate(self.load_for_session, session_id)

    async def areconcile_written_receipt(self, receipt: Mapping[str, Any]) -> bool:
        return await self.mutate(self.reconcile_written_receipt, receipt)

    async def aupdate(
        self,
        receipt: Mapping[str, Any],
        *,
        status: str,
        archive_request_id: str = "",
        archive_attempt: int | None = None,
    ) -> dict[str, Any]:
        """Atomically update archival state asynchronously."""  # noqa: DOCSTRING_CJK

        return await self.mutate(
            self.update,
            receipt,
            status=status,
            archive_request_id=archive_request_id,
            archive_attempt=archive_attempt,
        )

    def write_public_archive(
        self,
        *,
        title: str,
        session: Any,
        ending: Mapping[str, Any] | None,
    ) -> int:
        """Atomically save complete public performance; hidden Runtime state never enters cold archives."""  # noqa: DOCSTRING_CJK

        archive_path = self._public_archive_path(str(session.session_id))
        previous = self._read(archive_path) or {}
        now = datetime.now(timezone.utc).isoformat()
        archive = build_numeric_v2_public_archive(
            title=title,
            session=session,
            ending=ending,
        )
        archive.update({
            "created_at": str(previous.get("created_at") or now),
            "updated_at": now,
            # Rewriting after pausing the same Session must preserve user favorites.
            "pinned": previous.get("pinned") is True,
        })
        self._write(archive_path, archive)
        return self.prune_public_archives(
            story_id=str(session.story_package_id),
            character_id=str(session.catgirl_binding.get("character_id") or ""),
            legacy_catgirl_name=str(session.catgirl_binding.get("catgirl_name") or ""),
        )

    def stage_public_archive(
        self,
        *,
        receipt: Mapping[str, Any],
        title: str,
        session: Any,
        ending: Mapping[str, Any] | None,
    ) -> None:
        """Save a recoverable pending public archive before memory-service migration."""  # noqa: DOCSTRING_CJK

        receipt_id = str(receipt.get("receipt_id") or "")
        if str(receipt.get("session_id") or "") != str(session.session_id):
            raise NumericV2ArchiveError("numeric_end_receipt_mismatch")
        public_path = self._public_archive_path(str(session.session_id))
        previous = self._read(public_path) or {}
        now = datetime.now(timezone.utc).isoformat()
        archive = build_numeric_v2_public_archive(
            title=title,
            session=session,
            ending=ending,
        )
        archive.update({
            "created_at": str(previous.get("created_at") or now),
            "updated_at": now,
            "pinned": previous.get("pinned") is True,
        })
        self._write(self._staged_archive_path(receipt_id), archive)

    def commit_staged_public_archive(self, receipt: Mapping[str, Any]) -> int:
        """After memory-service success, atomically publish the pending archive and enforce retention."""  # noqa: DOCSTRING_CJK

        receipt_id = str(receipt.get("receipt_id") or "")
        staged_path = self._staged_archive_path(receipt_id)
        archive = self._read(staged_path)
        if archive is None:
            raise NumericV2ArchiveError("numeric_public_archive_stage_missing")
        session_id = str(receipt.get("session_id") or "")
        if str(archive.get("session_id") or "") != session_id:
            raise NumericV2ArchiveError("numeric_end_receipt_mismatch")
        self._write(self._public_archive_path(session_id), archive)
        try:
            staged_path.unlink()
        except FileNotFoundError:
            pass
        return self.prune_public_archives(
            story_id=str(archive.get("story_id") or ""),
            character_id=str(archive.get("character_id") or ""),
            legacy_catgirl_name=str(archive.get("catgirl_name") or ""),
        )

    def has_staged_public_archive(self, receipt_id: str) -> bool:
        """True once an archive attempt staged its copy, i.e. a memory write may exist.

        Staging happens after the receipt enters ``writing`` and before the memory
        request is sent; only a commit or an explicit skip removes the file.
        """

        return self._staged_archive_path(receipt_id).is_file()

    def discard_staged_public_archive(self, receipt_id: str) -> None:
        try:
            self._staged_archive_path(receipt_id).unlink()
        except FileNotFoundError:
            pass

    async def awrite_public_archive(
        self,
        *,
        title: str,
        session: Any,
        ending: Mapping[str, Any] | None,
    ) -> int:
        return await self.mutate(
            self.write_public_archive,
            title=title,
            session=session,
            ending=ending,
        )

    async def astage_public_archive(self, **kwargs) -> None:
        await self.mutate(self.stage_public_archive, **kwargs)

    async def acommit_staged_public_archive(self, receipt: Mapping[str, Any]) -> int:
        return await self.mutate(self.commit_staged_public_archive, receipt)

    async def adiscard_staged_public_archive(self, receipt_id: str) -> None:
        await self.mutate(self.discard_staged_public_archive, receipt_id)

    def list_public_archives(
        self,
        *,
        story_id: str = "",
        character_id: str = "",
        legacy_catgirl_name: str = "",
        raise_on_io_error: bool = False,
    ) -> list[dict[str, Any]]:
        """Return only cold-archive summaries; listing endpoints do not expose complete performance bodies."""  # noqa: DOCSTRING_CJK

        normalized_story_id = str(story_id or "").strip()
        normalized_character_id = str(character_id or "").strip()
        normalized_legacy_name = str(legacy_catgirl_name or "").strip()
        if not self.public_archive_root.is_dir():
            return []
        archives: list[dict[str, Any]] = []
        for path in self.public_archive_root.glob("*.json"):
            try:
                payload = self._read(path)
                modified_ns = path.stat().st_mtime_ns
            except NumericV2ArchiveError:
                # Ordinary listings may skip unreadable archives; destructive operations must abort, including for corrupt JSON.
                if raise_on_io_error:
                    raise
                continue
            except OSError:
                if raise_on_io_error:
                    raise
                continue
            if not self._valid_public_archive_payload(payload):
                # Strict enumeration cannot establish story/character ownership of unknown payloads; abort instead of claiming complete deletion.
                if raise_on_io_error:
                    error = NumericV2ArchiveError("numeric_public_archive_invalid")
                    error.path = str(path)
                    raise error
                continue
            if normalized_story_id and str(payload.get("story_id") or "") != normalized_story_id:
                continue
            if not self._matches_character(
                payload,
                normalized_character_id,
                normalized_legacy_name,
            ):
                continue
            ending = payload.get("ending") if isinstance(payload.get("ending"), dict) else {}
            raw_revision = payload.get("revision")
            revision = (
                raw_revision
                if isinstance(raw_revision, int) and not isinstance(raw_revision, bool)
                else 0
            )
            archives.append({
                "story_id": str(payload.get("story_id") or ""),
                "session_id": str(payload.get("session_id") or ""),
                "story_title": str(payload.get("story_title") or ""),
                "character_id": str(payload.get("character_id") or ""),
                "catgirl_name": str(payload.get("catgirl_name") or ""),
                "revision": revision,
                "episode_status": str(payload.get("episode_status") or "paused"),
                "ending_title": str(ending.get("title") or ""),
                "pinned": payload.get("pinned") is True,
                "created_at": str(payload.get("created_at") or ""),
                "updated_at": str(payload.get("updated_at") or ""),
                "path": str(path),
                "modified_ns": modified_ns,
            })
        archives.sort(
            key=lambda item: (
                str(item.get("updated_at") or ""),
                int(item.get("modified_ns") or 0),
                str(item.get("session_id") or ""),
            ),
            reverse=True,
        )
        return archives

    def quarantine_invalid_public_archives(
        self,
        quarantine_root: Path,
        *,
        skip_story_ids: frozenset[str] | set[str] = frozenset(),
    ) -> int:
        """Move unparseable public archives aside so strict scans stop failing on them.

        Only files that the normal listing already hides and the strict
        enumeration refuses are moved; nothing is deleted, and files that fail
        with an OS error are left in place because they may be valid. Archives
        that still name a story in ``skip_story_ids`` (a delete rollback that
        failed) are left for its recovery.
        """
        blocked_stories = {str(value).strip() for value in skip_story_ids if str(value).strip()}
        if not self.public_archive_root.is_dir():
            return 0
        moved = 0
        for path in sorted(self.public_archive_root.glob("*.json")):
            try:
                payload = self._read(path)
            except NumericV2ArchiveError as exc:
                if isinstance(exc.__cause__, OSError):
                    # Temporarily unreadable archives may still be valid; never move them.
                    continue
                payload = None
            if self._valid_public_archive_payload(payload) or not path.is_file():
                continue
            if (
                payload is not None
                and isinstance(payload.get("story_id"), str)
                and payload["story_id"].strip() in blocked_stories
            ):
                continue
            try:
                quarantine_root.mkdir(parents=True, exist_ok=True)
                # Move only, never delete; this directory is excluded from Session-quarantine count-based pruning.
                os.replace(path, quarantine_root / quarantined_file_name("invalid", path.name))
                moved += 1
            except OSError:
                # Left in place, the strict preflight scan still reports this file.
                logger.warning("Numeric v2 cannot quarantine public archive %s", path, exc_info=True)
                continue
        return moved

    @staticmethod
    def _payload_text(payload: Mapping[str, Any] | None, key: str) -> str:
        value = payload.get(key) if payload is not None else None
        return value.strip() if isinstance(value, str) else ""

    def _scope_session_ids(
        self,
        *,
        story_id: str,
        character_id: str,
        legacy_catgirl_name: str,
    ) -> set[str]:
        """Collect session ids known to belong to a scope from archives, receipts and intents.

        They only widen hash matching for quarantined archives, so unreadable
        sources are skipped here; the callers' own strict scans fail closed.
        """
        scope = dict(
            story_id=story_id,
            character_id=character_id,
            legacy_catgirl_name=legacy_catgirl_name,
        )
        session_ids = {
            str(archive.get("session_id") or "")
            for archive in self.list_public_archives(**scope)
        }
        for path in self.receipt_paths_for_scope(**scope):
            if not path.name.startswith("theater_end_"):
                continue
            try:
                receipt = self._read(path)
            except NumericV2ArchiveError:
                continue
            session_ids.add(self._payload_text(receipt, "session_id"))
        forget_root = self.root.parent / "forget_transactions"
        for path in sorted(forget_root.glob("*.json")) if forget_root.is_dir() else ():
            try:
                pending = self._read(path)
            except NumericV2ArchiveError:
                continue
            if pending is None or (
                story_id and self._payload_text(pending, "story_id") != story_id
            ) or (
                character_id and self._payload_text(pending, "character_id") != character_id
            ):
                continue
            session_ids.add(self._payload_text(pending, "session_id"))
        session_ids.discard("")
        return session_ids

    @classmethod
    def _character_attribution(
        cls,
        payload: Mapping[str, Any] | None,
        character_id: str,
        legacy_catgirl_name: str,
    ) -> bool | None:
        """Mirror ``_matches_character``, returning None when the owner is unknown."""
        stored_id = cls._payload_text(payload, "character_id")
        stored_name = cls._payload_text(payload, "catgirl_name")
        if character_id:
            if stored_id:
                return stored_id == character_id
            if stored_name:
                return bool(legacy_catgirl_name) and stored_name == legacy_catgirl_name
            return None
        if not legacy_catgirl_name:
            return True
        if stored_name:
            return stored_name == legacy_catgirl_name
        return None

    def quarantined_public_archive_paths(
        self,
        *,
        story_id: str = "",
        character_id: str = "",
        legacy_catgirl_name: str = "",
        session_ids: Iterable[str] = (),
        include_unattributable: bool = False,
    ) -> list[Path]:
        """List quarantined public archives an explicit delete of this scope must erase.

        A quarantined file is attributable to the scope when its original
        ``sha256(session_id)`` basename or its ``session_id`` names an in-scope
        session, or when every scope dimension (story, character) it still
        records matches. A file recording a different story or character is
        never returned. A file whose owner cannot be determined for this scope
        (unparseable, or missing the scoped fields) is returned only with
        ``include_unattributable``: the app never reads it, yet it may hold a
        private transcript, so an explicit character delete or story forget
        erases it. Transient read failures raise so the delete fails closed.
        """
        root = self.public_archive_quarantine_root
        if not root.is_dir():
            return []
        normalized_story_id = str(story_id or "").strip()
        normalized_character_id = str(character_id or "").strip()
        normalized_legacy_name = str(legacy_catgirl_name or "").strip()
        known_session_ids = {
            str(value or "").strip() for value in session_ids
        } | self._scope_session_ids(
            story_id=normalized_story_id,
            character_id=normalized_character_id,
            legacy_catgirl_name=normalized_legacy_name,
        )
        known_session_ids.discard("")
        known_keys = {self._session_key(value) for value in known_session_ids}
        result: list[Path] = []
        for path in sorted(root.glob("*.json")):
            if not path.is_file():
                continue
            try:
                payload = self._read(path)
            except NumericV2ArchiveError as exc:
                if isinstance(exc.__cause__, OSError):
                    # Temporary unreadability prevents confirming ownership; abort the entire destructive operation and identify the file.
                    raise
                payload = None
            key_match = _QUARANTINED_ARCHIVE_KEY_RE.search(path.name)
            if self._quarantined_in_scope(
                payload,
                name_matches_session=key_match is not None and key_match.group(1) in known_keys,
                known_session_ids=known_session_ids,
                story_id=normalized_story_id,
                character_id=normalized_character_id,
                legacy_catgirl_name=normalized_legacy_name,
                include_unattributable=include_unattributable,
            ):
                result.append(path)
        return result

    @classmethod
    def _quarantined_in_scope(
        cls,
        identity: Mapping[str, Any] | None,
        *,
        name_matches_session: bool,
        known_session_ids: set[str],
        story_id: str,
        character_id: str,
        legacy_catgirl_name: str,
        include_unattributable: bool,
    ) -> bool:
        """Decide whether one quarantined file belongs to an explicit delete's scope.

        ``identity`` carries ``session_id``, ``story_id``, ``character_id`` and
        ``catgirl_name`` when the file was parseable enough to record them.
        """
        if name_matches_session or (
            cls._payload_text(identity, "session_id") in known_session_ids
        ):
            return True
        attributions: list[bool | None] = []
        if story_id:
            stored_story_id = cls._payload_text(identity, "story_id")
            attributions.append(
                stored_story_id == story_id if stored_story_id else None
            )
        if character_id or legacy_catgirl_name:
            attributions.append(
                cls._character_attribution(identity, character_id, legacy_catgirl_name)
            )
        if not attributions:
            # An empty scope attributes nothing by its fields.
            attributions.append(None)
        if False in attributions:
            return False
        return None not in attributions or include_unattributable

    @classmethod
    def _quarantined_session_identity(cls, payload: Mapping[str, Any] | None) -> dict[str, str] | None:
        """Project a (possibly damaged) stored session onto the archive identity fields."""
        session = payload.get("session") if isinstance(payload, Mapping) else None
        if not isinstance(session, Mapping):
            return None
        binding = session.get("catgirl_binding")
        binding = binding if isinstance(binding, Mapping) else {}
        return {
            "session_id": cls._payload_text(session, "session_id"),
            "story_id": cls._payload_text(session, "story_package_id"),
            "character_id": cls._payload_text(binding, "character_id"),
            "catgirl_name": cls._payload_text(binding, "catgirl_name"),
        }

    def quarantined_session_paths(
        self,
        *,
        story_id: str = "",
        character_id: str = "",
        legacy_catgirl_name: str = "",
        session_ids: Iterable[str] = (),
        include_unattributable: bool = False,
    ) -> list[Path]:
        """List quarantined session files an explicit delete of this scope must erase.

        Startup audit moves invalid and duplicate session files, ledger and
        transcript included, into the session quarantine. They follow the same
        policy as quarantined public archives: a file is attributable when the
        ``session_id`` in its name or payload is an in-scope session, or when
        every scope dimension its payload still records matches; a file naming
        another story or character is never returned; a file whose owner
        cannot be determined is returned only with ``include_unattributable``
        (explicit character delete or story forget). Transient read failures
        raise so the delete fails closed. Nothing else removes these files.
        """
        root = self.session_quarantine_root
        if not root.is_dir():
            return []
        normalized_story_id = str(story_id or "").strip()
        normalized_character_id = str(character_id or "").strip()
        normalized_legacy_name = str(legacy_catgirl_name or "").strip()
        known_session_ids = {
            str(value or "").strip() for value in session_ids
        } | self._scope_session_ids(
            story_id=normalized_story_id,
            character_id=normalized_character_id,
            legacy_catgirl_name=normalized_legacy_name,
        )
        known_session_ids.discard("")
        result: list[Path] = []
        for path in sorted(root.glob("*.json")):
            if not path.is_file():
                continue
            try:
                payload = self._read(path)
            except NumericV2ArchiveError as exc:
                if isinstance(exc.__cause__, OSError):
                    # Temporary unreadability prevents confirming ownership; abort the entire destructive operation and identify the file.
                    raise
                payload = None
            name_match = _QUARANTINED_SESSION_NAME_RE.fullmatch(path.name)
            if self._quarantined_in_scope(
                self._quarantined_session_identity(payload),
                name_matches_session=(
                    name_match is not None and name_match.group(1) in known_session_ids
                ),
                known_session_ids=known_session_ids,
                story_id=normalized_story_id,
                character_id=normalized_character_id,
                legacy_catgirl_name=normalized_legacy_name,
                include_unattributable=include_unattributable,
            ):
                result.append(path)
        return result

    def load_public_archive(
        self,
        *,
        story_id: str,
        session_id: str,
        character_id: str,
        legacy_catgirl_name: str = "",
    ) -> dict[str, Any]:
        """Read a public-performance body by script, Session, and current character."""  # noqa: DOCSTRING_CJK

        normalized_story_id = str(story_id or "").strip()
        normalized_session_id = str(session_id or "").strip()
        normalized_character_id = str(character_id or "").strip()
        normalized_legacy_name = str(legacy_catgirl_name or "").strip()
        if not normalized_story_id or not normalized_session_id:
            raise NumericV2ArchiveError("numeric_public_archive_not_found")
        payload = self._read(self._public_archive_path(normalized_session_id))
        if (
            payload is None
            or payload.get("schema") != "neko.theater.numeric.v2.public-archive"
            or str(payload.get("story_id") or "") != normalized_story_id
            or str(payload.get("session_id") or "") != normalized_session_id
            or not self._matches_character(
                payload,
                normalized_character_id,
                normalized_legacy_name,
            )
        ):
            # Return not found for both identity mismatch and missing files, avoiding disclosure of other characters' saved playthroughs.
            raise NumericV2ArchiveError("numeric_public_archive_not_found")
        return dict(payload)

    def prune_public_archives(
        self,
        *,
        story_id: str,
        character_id: str,
        legacy_catgirl_name: str = "",
    ) -> int:
        """Retain the latest five unfavorited archives; favorites do not count toward automatic eviction."""  # noqa: DOCSTRING_CJK

        archives = self.list_public_archives(
            story_id=story_id,
            character_id=character_id,
            legacy_catgirl_name=legacy_catgirl_name,
        )
        stale = [
            archive
            for archive in archives
            if not archive["pinned"]
        ][PUBLIC_ARCHIVES_PER_STORY_CHARACTER:]
        removed = 0
        for archive in stale:
            try:
                Path(str(archive["path"])).unlink()
                removed += 1
            except FileNotFoundError:
                pass
        return removed

    def set_public_archive_pinned(
        self,
        *,
        story_id: str,
        session_id: str,
        character_id: str,
        legacy_catgirl_name: str,
        pinned: bool,
    ) -> dict[str, Any]:
        """Allow only the current character to favorite its own cold archives for the specified script."""  # noqa: DOCSTRING_CJK

        path = self._public_archive_path(str(session_id or "").strip())
        payload = self._read(path)
        if (
            payload is None
            or str(payload.get("story_id") or "") != str(story_id or "").strip()
            or not self._matches_character(payload, character_id, legacy_catgirl_name)
        ):
            raise NumericV2ArchiveError("numeric_public_archive_not_found")
        payload["pinned"] = pinned is True
        # Favoriting must not change performance time, or unfavoriting an old playthrough makes it appear newest,
        # causing retention to evict genuinely newer performances.
        self._write(path, payload)
        self.prune_public_archives(
            story_id=str(payload.get("story_id") or ""),
            character_id=str(payload.get("character_id") or ""),
            legacy_catgirl_name=str(payload.get("catgirl_name") or ""),
        )
        return {"session_id": str(session_id), "pinned": payload["pinned"]}

    def update_character_binding(
        self,
        *,
        character_id: str,
        legacy_catgirl_name: str,
        catgirl_name: str,
    ) -> dict[str, int]:
        """On character rename, refresh identity projections in cold archives, ending receipts, and pending archives together."""  # noqa: DOCSTRING_CJK

        normalized_character_id = str(character_id or "").strip()
        normalized_legacy_name = str(legacy_catgirl_name or "").strip()
        normalized_catgirl_name = str(catgirl_name or "").strip()
        if not normalized_character_id or not normalized_catgirl_name:
            raise NumericV2ArchiveError("numeric_archive_character_binding_invalid")

        updated_archives = 0
        for archive in self.list_public_archives(
            character_id=normalized_character_id,
            legacy_catgirl_name=normalized_legacy_name,
        ):
            path = Path(str(archive["path"]))
            payload = self._read(path)
            if payload is None or not self._matches_character(
                payload,
                normalized_character_id,
                normalized_legacy_name,
            ):
                continue
            payload["character_id"] = normalized_character_id
            payload["catgirl_name"] = normalized_catgirl_name
            self._write(path, payload)
            updated_archives += 1

        updated_receipts = 0
        updated_staged_archives = 0
        receipt_paths = (
            sorted(self.root.glob("theater_end_*.json"))
            if self.root.is_dir()
            else []
        )
        for path in receipt_paths:
            receipt = self._read(path)
            if receipt is None or not self._matches_character(
                receipt,
                normalized_character_id,
                normalized_legacy_name,
            ):
                continue
            receipt["character_id"] = normalized_character_id
            receipt["catgirl_name"] = normalized_catgirl_name
            self._write(path, receipt)
            updated_receipts += 1

            staged_path = self._staged_archive_path(
                str(receipt.get("receipt_id") or "")
            )
            staged = self._read(staged_path)
            if staged is None or not self._matches_character(
                staged,
                normalized_character_id,
                normalized_legacy_name,
            ):
                continue
            staged["character_id"] = normalized_character_id
            staged["catgirl_name"] = normalized_catgirl_name
            self._write(staged_path, staged)
            updated_staged_archives += 1

        return {
            "archives": updated_archives,
            "receipts": updated_receipts,
            "staged_archives": updated_staged_archives,
        }

    def delete_public_archives(
        self,
        *,
        story_id: str,
        character_id: str,
        legacy_catgirl_name: str = "",
    ) -> int:
        removed = 0
        for archive in self.list_public_archives(
            story_id=story_id,
            character_id=character_id,
            legacy_catgirl_name=legacy_catgirl_name,
            raise_on_io_error=True,
        ):
            try:
                Path(str(archive["path"])).unlink()
                removed += 1
            except FileNotFoundError:
                pass
        return removed

    def receipt_paths_for_scope(
        self,
        *,
        story_id: str = "",
        character_id: str = "",
        legacy_catgirl_name: str = "",
        raise_on_io_error: bool = False,
    ) -> list[Path]:
        """List receipts and Session pointers within scope for shared transactional backup/deletion."""  # noqa: DOCSTRING_CJK

        normalized_story_id = str(story_id or "").strip()
        normalized_character_id = str(character_id or "").strip()
        normalized_legacy_name = str(legacy_catgirl_name or "").strip()
        if not self.root.is_dir():
            return []
        paths: set[Path] = set()
        for path in self.root.glob("theater_end_*.json"):
            try:
                receipt = self._read(path)
            except NumericV2ArchiveError:
                # Ordinary queries may skip unreadable receipts; destructive operations must abort, including for corrupt JSON.
                if raise_on_io_error:
                    raise
                continue
            if receipt is None:
                continue
            if normalized_story_id and str(receipt.get("story_id") or "") != normalized_story_id:
                continue
            if not self._matches_character(
                receipt,
                normalized_character_id,
                normalized_legacy_name,
            ):
                continue
            paths.add(path)
            staged_path = self._staged_archive_path(str(receipt.get("receipt_id") or ""))
            if staged_path.is_file():
                paths.add(staged_path)
            session_id = str(receipt.get("session_id") or "").strip()
            if session_id:
                paths.add(self._session_path(session_id))
        return sorted(paths)

    def delete_receipts(
        self,
        *,
        story_id: str = "",
        character_id: str = "",
        legacy_catgirl_name: str = "",
    ) -> int:
        if not any(
            str(value or "").strip()
            for value in (story_id, character_id, legacy_catgirl_name)
        ):
            raise NumericV2ArchiveError("numeric_receipt_delete_scope_required")
        paths = self.receipt_paths_for_scope(
            story_id=story_id,
            character_id=character_id,
            legacy_catgirl_name=legacy_catgirl_name,
            raise_on_io_error=True,
        )
        removed = 0
        for path in paths:
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
        return removed

    def delete_session_receipts(self, session_id: str) -> int:
        """Delete invalidated receipts after restarting replaces the old Session."""  # noqa: DOCSTRING_CJK

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return 0
        removed = 0
        receipt_paths = self.root.glob("theater_end_*.json") if self.root.is_dir() else ()
        for path in receipt_paths:
            try:
                receipt = self._read(path)
            except NumericV2ArchiveError:
                continue
            if receipt is None or str(receipt.get("session_id") or "") != normalized_session_id:
                continue
            # The replacement session has no successor receipt to inherit these.
            self.queue_retractions(receipt)
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
            staged_path = self._staged_archive_path(str((receipt or {}).get("receipt_id") or ""))
            try:
                staged_path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
        try:
            self._session_path(normalized_session_id).unlink()
            removed += 1
        except FileNotFoundError:
            pass
        return removed

    def cleanup_receipts(
        self,
        active_session_ids: set[str],
        *,
        skip_story_ids: frozenset[str] | set[str] = frozenset(),
    ) -> dict[str, int]:
        """Clean receipts with no Session pointer or superseded pointers on cold startup.

        Preserve receipts, pointers, and pending archives for skip_story_ids (failed deletion rollback awaiting manual recovery)
        unchanged, without scheduling revocations: their missing Sessions may be inside transaction backups.
        """  # noqa: DOCSTRING_CJK

        if not self.root.is_dir():
            return {"receipts_removed": 0, "pointers_removed": 0}
        normalized_active = {str(value) for value in active_session_ids if str(value)}
        blocked_stories = {str(value).strip() for value in skip_story_ids if str(value).strip()}

        def blocked(payload: Mapping[str, Any] | None) -> bool:
            return bool(
                blocked_stories
                and payload is not None
                and str(payload.get("story_id") or "").strip() in blocked_stories
            )

        kept_receipt_ids: set[str] = set()
        # Obligations a live receipt already inherited need no queue entry.
        carried_request_ids: set[str] = set()
        receipts_removed = 0
        pointers_removed = 0
        for pointer_path in self.root.glob("session-*.json"):
            try:
                pointer = self._read(pointer_path)
            except NumericV2ArchiveError as exc:
                # Transient I/O failures must not mean garbage data, which would delete still-valid receipt pointers.
                if isinstance(exc.__cause__, OSError):
                    raise
                pointer = None
            receipt_id = str((pointer or {}).get("receipt_id") or "")
            try:
                receipt = self.load(receipt_id) if receipt_id else None
            except NumericV2ArchiveError as exc:
                if isinstance(exc.__cause__, OSError):
                    raise
                receipt = None
            session_id = str((receipt or {}).get("session_id") or "")
            if blocked(receipt):
                # Leave a recovery-blocked story exactly as it is.
                kept_receipt_ids.add(receipt_id)
                continue
            if receipt is not None and session_id in normalized_active:
                kept_receipt_ids.add(receipt_id)
                carried_request_ids.update(
                    entry["archive_request_id"] for entry in self.pending_retractions(receipt)
                )
                continue
            try:
                pointer_path.unlink()
                pointers_removed += 1
            except FileNotFoundError:
                pass
            if receipt is not None:
                # The session is gone; its possibly landed memory write is queued.
                self.queue_retractions(receipt)
                try:
                    self._receipt_path(receipt_id).unlink()
                    receipts_removed += 1
                except FileNotFoundError:
                    pass
        for receipt_path in self.root.glob("theater_end_*.json"):
            if receipt_path.stem in kept_receipt_ids:
                continue
            try:
                receipt = self._read(receipt_path)
            except NumericV2ArchiveError as exc:
                # Written compatibility receipts may be the only upgrade-backfill evidence; preserve them and abort cleanup on I/O failure.
                if isinstance(exc.__cause__, OSError):
                    raise
                receipt = None
            if blocked(receipt):
                kept_receipt_ids.add(receipt_path.stem)
                continue
            receipt_session_id = str((receipt or {}).get("session_id") or "")
            if (
                receipt is not None
                and receipt.get("status") == "written"
                and receipt_session_id in normalized_active
                and not self._public_archive_path(receipt_session_id).is_file()
            ):
                # Retain compatibility receipts whose upgrade backfill is incomplete.
                continue
            # Queued before the receipt (and, below, its staged copy) is removed.
            self.queue_retractions(receipt, skip_request_ids=carried_request_ids)
            try:
                receipt_path.unlink()
                receipts_removed += 1
            except FileNotFoundError:
                pass
        for staged_path in self.root.glob("staged-theater_end_*.json"):
            receipt_id = staged_path.stem.removeprefix("staged-")
            if receipt_id in kept_receipt_ids:
                continue
            if blocked_stories:
                try:
                    staged = self._read(staged_path)
                except NumericV2ArchiveError:
                    # Unreadable now: it may belong to a blocked story, keep it.
                    continue
                if blocked(staged):
                    continue
            try:
                staged_path.unlink()
            except FileNotFoundError:
                pass
        return {
            "receipts_removed": receipts_removed,
            "pointers_removed": pointers_removed,
        }


def _visible_action(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if (
        (normalized.startswith("（") and normalized.endswith("）"))
        or (normalized.startswith("(") and normalized.endswith(")"))
    ):
        return normalized
    return f"（{normalized}）"


def _performance_memory_parts(container: Mapping[str, Any], *, phase: str) -> tuple[list[dict[str, str]], str]:
    """Project one committed performance into a structured memory segment without text labels."""  # noqa: DOCSTRING_CJK

    parts: list[dict[str, str]] = []
    chunks: list[str] = []
    if "scene_narration" in container or "performance" in container:
        scene_narration = str(container.get("scene_narration") or "").strip()
        if scene_narration:
            parts.append({"kind": "scene_narration", "phase": phase, "text": scene_narration})
            chunks.append(scene_narration)
        for item in container.get("fixed_narrations", []):
            if item["position"] == "before":
                parts.append({"kind": "scene_narration", "phase": phase, "text": item["text"]})
                chunks.append(item["text"])
        performance = str(container.get("performance") or "").strip()
        if performance:
            for block in mixed_performance_blocks(performance):
                kind = "action" if block.get("type") == "action" else "dialogue"
                visible_text = _visible_action(block["text"]) if kind == "action" else block["text"]
                parts.append({"kind": kind, "phase": phase, "text": visible_text})
            chunks.append(performance)
        for item in container.get("fixed_narrations", []):
            if item["position"] == "after":
                parts.append({"kind": "scene_narration", "phase": phase, "text": item["text"]})
                chunks.append(item["text"])
        return parts, "\n\n".join(chunks)

    visible: list[str] = []
    for block in content_blocks(container):
        block_type = str(block.get("type") or "")
        if block_type == "dialogue":
            kind = "dialogue"
            text = str(block.get("text") or "").strip()
        elif block_type == "action":
            kind = "action"
            text = _visible_action(str(block.get("text") or "").strip())
        else:
            kind = "action" if phase in {"ordinary", "source_response"} else "scene_narration"
            raw_text = str(block.get("text") or "").strip()
            text = _visible_action(raw_text) if kind == "action" else raw_text
        if not text:
            continue
        parts.append({"kind": kind, "phase": phase, "text": text})
        visible.append(text)
    return parts, "\n".join(visible)


def _performance_memory_projection(
    performance: Mapping[str, Any],
    *,
    fallback_phase: str,
) -> tuple[list[dict[str, str]], str]:
    raw_segments = performance.get("segments")
    containers = raw_segments if isinstance(raw_segments, list) else [performance]
    all_parts: list[dict[str, str]] = []
    visible_chunks: list[str] = []
    for raw_container in containers:
        if not isinstance(raw_container, Mapping):
            continue
        phase = str(raw_container.get("phase") or fallback_phase).strip() or fallback_phase
        parts, text = _performance_memory_parts(raw_container, phase=phase)
        all_parts.extend(parts)
        if text:
            visible_chunks.append(text)
    return all_parts, "\n\n".join(visible_chunks)


def _session_reached_ending(session: Any) -> bool:
    # Natural completion has no lifecycle exit reason. This durable state still
    # identifies a completed run when an upgraded package cannot project its ending.
    return (
        getattr(session, "status", None) == "ended"
        and getattr(session, "ended_reason", None) in (None, "natural_ending")
    )


def _episode_metadata(
    *,
    title: str,
    session: Any,
    ending: Mapping[str, Any] | None,
    archive_from_revision: int,
    archive_through_revision: int,
    episode_summary: str,
) -> dict[str, Any]:
    ending_title = str((ending or {}).get("title") or "").strip()
    ending_summary = str((ending or {}).get("summary") or "").strip()
    return {
        "source": THEATER_MEMORY_SOURCE,
        "story_id": str(session.story_package_id),
        "session_id": str(session.session_id),
        "story_title": str(title),
        "episode_status": "completed" if ending_title or _session_reached_ending(session) else "paused",
        "ending_title": ending_title,
        "ending_summary": ending_summary,
        "archive_from_revision": int(archive_from_revision),
        "archive_through_revision": int(archive_through_revision),
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "episode_summary": episode_summary,
        "performed_at": _episode_performed_at(
            session, archive_from_revision, archive_through_revision,
        ),
    }


def _episode_performed_at(session: Any, start: int, through: int) -> str:
    """Use the archived turn's clock, not receipt creation or archive time."""
    rows = [row for row in session.performance_history if start <= row.get("revision", 0) <= through]
    if rows:
        return str(rows[-1].get("performed_at") or "")
    return str(getattr(session, "opening_performed_at", "") or "")


def _compact_episode_summary(
    session: Any,
    ending: Mapping[str, Any] | None,
    *,
    max_chars: int = 360,
    archive_from_revision: int = 1,
    archive_through_revision: int | None = None,
    include_opening: bool = True,
) -> str:
    """Generate episode summaries deterministically; Theater cold archives hold the complete public body."""  # noqa: DOCSTRING_CJK

    through = session.revision if archive_through_revision is None else archive_through_revision
    forgotten = getattr(session, "forgotten_through_revision", -1)
    if not isinstance(forgotten, int) or isinstance(forgotten, bool):
        forgotten = -1
    history = [row for row in session.performance_history
               if max(archive_from_revision, forgotten + 1) <= row.get("revision", 0) <= through]
    if history:
        source = history[-1]
        fallback_phase = "ordinary"
    elif include_opening and forgotten < 0:
        source = session.opening_performance
        fallback_phase = "opening"
    else:
        return ""
    ending_summary = str((ending or {}).get("summary") or "").strip()
    if ending_summary and history and history[-1].get("revision") == session.revision:
        return ending_summary
    _, text = _performance_memory_projection(source, fallback_phase=fallback_phase)
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    candidate = normalized[:max_chars]
    cut = max(candidate.rfind(mark) for mark in "。！？")
    if cut >= max_chars // 2:
        return candidate[:cut + 1]
    return candidate.rstrip() + "……"


def build_numeric_v2_public_archive(
    *,
    title: str,
    session: Any,
    ending: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project a complete public-performance cold archive without hidden state."""  # noqa: DOCSTRING_CJK

    forgotten_through_revision = getattr(session, "forgotten_through_revision", -1)
    if not isinstance(forgotten_through_revision, int) or isinstance(forgotten_through_revision, bool):
        forgotten_through_revision = -1
    if forgotten_through_revision < 0:
        opening_parts, opening_text = _performance_memory_projection(
            session.opening_performance,
            fallback_phase="opening",
        )
    else:
        # The opening is an initial fact predating revision 0; explicit forgetting must prevent revival in later cold archives.
        opening_parts, opening_text = [], ""
    turns: list[dict[str, Any]] = []
    for record in session.performance_history:
        revision = int(record.get("revision") or 0)
        if revision <= forgotten_through_revision:
            continue
        parts, performance_text = _performance_memory_projection(
            record,
            fallback_phase="ordinary",
        )
        turns.append({
            "revision": revision,
            "performed_at": str(record.get("performed_at") or ""),
            "player_input": str(record.get("input_text") or "").strip(),
            "performance": performance_text,
            "parts": parts,
        })
    return {
        "schema": "neko.theater.numeric.v2.public-archive",
        "story_id": str(session.story_package_id),
        "session_id": str(session.session_id),
        "story_title": str(title),
        "character_id": str(session.catgirl_binding.get("character_id") or ""),
        "catgirl_name": str(session.catgirl_binding.get("catgirl_name") or ""),
        "player_name": str(session.catgirl_binding.get("player_address") or "你"),
        "revision": int(session.revision),
        "episode_status": "completed" if ending or _session_reached_ending(session) else "paused",
        "ending": {
            "title": str((ending or {}).get("title") or "").strip(),
            "summary": str((ending or {}).get("summary") or "").strip(),
        },
        "opening": {
            "performed_at": str(getattr(session, "opening_performed_at", "") or ""),
            "performance": opening_text,
            "parts": opening_parts,
        },
        "turns": turns,
    }


def build_numeric_v2_memory_messages(
    *,
    title: str,
    session: Any,
    ending: Mapping[str, Any] | None,
    archive_from_revision: int = 1,
    archive_through_revision: int | None = None,
    include_opening: bool = True,
) -> list[dict[str, Any]]:
    """Build one theater memory capsule; complete performances remain only in Theater cold archives."""  # noqa: DOCSTRING_CJK

    through_revision = int(
        session.revision if archive_through_revision is None else archive_through_revision
    )
    from_revision = max(1, int(archive_from_revision))
    episode_summary = _compact_episode_summary(
        session, ending, archive_from_revision=from_revision,
        archive_through_revision=through_revision, include_opening=include_opening,
    )
    if not episode_summary:
        return []
    episode = _episode_metadata(
        title=title,
        session=session,
        ending=ending,
        archive_from_revision=from_revision,
        archive_through_revision=through_revision,
        episode_summary=episode_summary,
    )
    return [{
        "role": "system",
        "content": [{"type": "text", "text": episode_summary}],
        "metadata": episode,
    }]


__all__ = [
    "NumericV2ArchiveError",
    "NumericV2ArchiveStore",
    "PUBLIC_ARCHIVES_PER_STORY_CHARACTER",
    "PUBLIC_ARCHIVE_QUARANTINE_DIRNAME",
    "THEATER_MEMORY_SOURCE",
    "build_numeric_v2_memory_messages",
    "build_numeric_v2_public_archive",
    "quarantined_file_name",
]
