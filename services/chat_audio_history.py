"""Local recordings and durable delivery receipts for chat audio messages."""

from __future__ import annotations

import asyncio
import io
import logging
import sqlite3
import time
import wave
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from utils.asyncio_retirement import await_retirement
from utils.config_manager.reserved_schema import get_character_uid, is_valid_character_uid
from utils.file_utils import atomic_write_json, read_json_tolerating_replace


logger = logging.getLogger(__name__)
AUDIO_SAMPLE_RATE = 16_000
_PROCESS_ID = uuid4().hex
_FIELDS = "request_id, sequence, created_at, duration_ms, state, process_id, transcript"


class ChatAudioHistory:
    def __init__(self, root: Path, character_uid: str):
        self.path = Path(root) / "chat_audio" / "history.sqlite3"
        self.character_uid = character_uid

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=2)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS recordings (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_uid TEXT NOT NULL, request_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL, expires_at REAL NOT NULL,
                    digest BLOB NOT NULL, duration_ms INTEGER NOT NULL, wav BLOB,
                    state TEXT NOT NULL, process_id TEXT NOT NULL,
                    transcript TEXT NOT NULL DEFAULT '',
                    UNIQUE (character_uid, request_id)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS recordings_history
                ON recordings (character_uid, sequence DESC) WHERE wav IS NOT NULL
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS retired_audio (
                    character_uid TEXT PRIMARY KEY, retired_at REAL NOT NULL
                )
            """)
            yield connection

    @staticmethod
    def _metadata(row, *, for_history=False):
        record = dict(row)
        state = record["state"]
        if record.pop("process_id") != _PROCESS_ID:
            if state == "sending":
                state = "unconfirmed"
            elif state == "accepted":
                state = "accepted_unconfirmed"
        if for_history and state in ("accepted", "accepted_unconfirmed", "completed"):
            state = "sent"
        record["state"] = state
        return record

    def receipt(self, request_id: str):
        if not self.path.is_file():
            return None
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {_FIELDS}, digest FROM recordings WHERE character_uid = ? AND request_id = ?",
                (self.character_uid, request_id),
            ).fetchone()
            return self._metadata(row) if row else None

    def reserve(
        self, request_id: str, pcm: bytes, digest: bytes, expires_at: float, *,
        save_local: bool = True, submitted_at: float | None = None,
    ) -> bool:
        if submitted_at is None:
            submitted_at = time.time()
        wav = None
        if save_local:
            with io.BytesIO() as buffer:
                with wave.open(buffer, "wb") as audio:
                    audio.setparams((1, 2, AUDIO_SAMPLE_RATE, 0, "NONE", "not compressed"))
                    audio.writeframes(pcm)
                wav = buffer.getvalue()
        with self._connection() as connection:
            connection.execute("DELETE FROM recordings WHERE wav IS NULL AND expires_at < ?", (time.time(),))
            inserted = connection.execute("""
                INSERT INTO recordings (character_uid, request_id, created_at, expires_at, digest,
                                        duration_ms, wav, state, process_id)
                SELECT ?, ?, ?, ?, ?, ?, ?, 'sending', ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM retired_audio WHERE character_uid = ? AND retired_at >= ?
                )
                ON CONFLICT (character_uid, request_id) DO NOTHING
            """, (self.character_uid, request_id, int(time.time() * 1000), expires_at, digest,
                  len(pcm) * 1000 // (AUDIO_SAMPLE_RATE * 2), wav, _PROCESS_ID,
                  self.character_uid, submitted_at))
            return inserted.rowcount == 1

    def update(self, request_id: str, *, state: str | None = None, transcript: str | None = None) -> bool:
        with self._connection() as connection:
            updated = connection.execute("""
                UPDATE recordings
                   SET state = CASE WHEN state IN ('sending', 'accepted') THEN COALESCE(?, state)
                                    ELSE state END,
                       transcript = CASE WHEN wav IS NULL THEN '' ELSE COALESCE(?, transcript) END
                 WHERE character_uid = ? AND request_id = ? AND process_id = ? AND state != 'deleted'
            """, (state, transcript, self.character_uid, request_id, _PROCESS_ID))
            return updated.rowcount == 1

    def page(self, before: int | None, limit: int):
        if not self.path.is_file():
            return {"messages": [], "next_before": None}
        with self._connection() as connection:
            rows = connection.execute(f"""
                SELECT {_FIELDS} FROM recordings
                 WHERE character_uid = ? AND wav IS NOT NULL AND sequence < ?
                 ORDER BY sequence DESC LIMIT ?
            """, (self.character_uid, before or 2**63 - 1, limit + 1)).fetchall()
        return {
            "messages": [self._metadata(row, for_history=True) for row in rows[:limit]],
            "next_before": rows[limit - 1]["sequence"] if len(rows) > limit else None,
        }

    def read_audio(self, request_id: str) -> bytes | None:
        if not self.path.is_file():
            return None
        with self._connection() as connection:
            row = connection.execute(
                "SELECT wav FROM recordings WHERE character_uid = ? AND request_id = ?",
                (self.character_uid, request_id),
            ).fetchone()
            return row["wav"] if row else None

    def delete(self, request_id: str | None = None) -> None:
        if not self.path.is_file():
            return
        with self._connection() as connection:
            # Keep the short-lived receipt so a retry cannot recreate the message.
            connection.execute("""
                UPDATE recordings SET wav = NULL, transcript = '', state = 'deleted'
                 WHERE character_uid = ? AND (? IS NULL OR request_id = ?) AND wav IS NOT NULL
            """, (self.character_uid, request_id, request_id))


    def purge(self) -> None:
        """Erase a deleted character's audio and fence submissions already in flight."""
        with self._connection() as connection:
            connection.execute("DELETE FROM recordings WHERE character_uid = ?", (self.character_uid,))
            connection.execute("""
                INSERT INTO retired_audio (character_uid, retired_at) VALUES (?, ?)
                ON CONFLICT (character_uid) DO UPDATE SET retired_at = excluded.retired_at
            """, (self.character_uid, time.time()))


def _retirement_marker(root: Path, uid: str) -> Path:
    if not is_valid_character_uid(uid):
        raise ValueError("Invalid character UID for audio retirement")
    return Path(root) / "chat_audio" / "pending_retirements" / f"{uid}.json"


async def prepare_character_audio_retirement(root: Path, uid: str | None) -> None:
    if uid is not None:
        marker = _retirement_marker(root, uid)
        try:
            await await_retirement(asyncio.to_thread(atomic_write_json, marker, {"character_uid": uid}))
        finally:
            audio_retirement_recovery.wake()


class _RetirementMarkerAccessError(OSError):
    pass


def _settle_retirement(root: Path, uid: str, *, deleted: bool) -> None:
    marker = _retirement_marker(root, uid)
    try:
        payload = read_json_tolerating_replace(marker)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _RetirementMarkerAccessError("Cannot read audio retirement marker") from exc
    if payload != {"character_uid": uid}:
        raise ValueError("Invalid audio retirement marker")
    if deleted:
        ChatAudioHistory(root, uid).purge()
    try:
        marker.unlink(missing_ok=True)
    except OSError as exc:
        raise _RetirementMarkerAccessError("Cannot remove audio retirement marker") from exc


async def retire_character_audio(root: Path, uid: str | None) -> bool:
    if uid is None:
        return True
    try:
        await await_retirement(asyncio.to_thread(_settle_retirement, root, uid, deleted=True))
    except (OSError, ValueError, sqlite3.Error) as exc:
        logger.warning("Deleted character audio cleanup failed: %s", type(exc).__name__)
        return False
    return True


def _recover_retirements(root: Path, characters_path: Path) -> None:
    directory = Path(root) / "chat_audio" / "pending_retirements"
    try:
        pending = [path.stem for path in directory.iterdir() if path.suffix == ".json"]
    except FileNotFoundError:
        return
    if not pending:
        return

    # Use the committed file. The regular loader can return defaults after a read failure.
    registry = read_json_tolerating_replace(characters_path)
    characters = registry.get("猫娘") if isinstance(registry, dict) else None
    if not isinstance(characters, dict) or any(not isinstance(data, dict) for data in characters.values()):
        raise ValueError("Invalid character registry for audio retirement")
    live = {get_character_uid(data) for data in characters.values()}
    if None in live:
        raise ValueError("Incomplete character registry for audio retirement")
    marker_error = None
    for uid in pending:
        try:
            _settle_retirement(root, uid, deleted=uid not in live)
        except ValueError:
            logger.warning("Invalid character audio retirement marker retained")
        except _RetirementMarkerAccessError as exc:
            marker_error = exc
    if marker_error is not None:
        raise marker_error


class _AudioRetirementRecovery:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None

    def start(self, config_manager, mutation_lock: asyncio.Lock) -> None:
        if self._task is not None and not self._task.done():
            return
        wake = self._wake = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(config_manager, mutation_lock, wake), name="chat-audio-retirement",
        )

    def wake(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def stop(self) -> None:
        task = self._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self._task is task:
                self._task = self._wake = None

    async def _run(self, config_manager, mutation_lock: asyncio.Lock, wake: asyncio.Event) -> None:
        while True:
            wake.clear()
            try:
                async with mutation_lock:
                    await await_retirement(asyncio.to_thread(
                        _recover_retirements,
                        config_manager.app_docs_dir, config_manager.get_runtime_config_path("characters.json"),
                    ))
            except (OSError, ValueError, sqlite3.Error) as exc:
                logger.warning("Character audio retirement will retry: %s", type(exc).__name__)
                try:
                    await asyncio.wait_for(wake.wait(), timeout=10)
                except TimeoutError:
                    pass
            else:
                await wake.wait()


audio_retirement_recovery = _AudioRetirementRecovery()


async def history_for_character(config_manager, name: str) -> ChatAudioHistory | None:
    characters = await config_manager.aload_characters()
    uid = get_character_uid(characters.get("猫娘", {}).get(name, {}))
    if uid:
        return ChatAudioHistory(config_manager.app_docs_dir, uid)
    return None
