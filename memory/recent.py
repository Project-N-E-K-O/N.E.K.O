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

from utils.config_manager import get_config_manager
from utils.token_tracker import set_call_type
from memory.thinking_llm import (
    aclose_quietly,
    describe_output,
    output_cap_rejected,
    response_hit_output_limit,
)
from memory.message_sources import (
    is_theater_episode_summary,
    is_theater_memory_message,
    theater_memory_episode_key,
)
from utils.llm_client import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    create_chat_llm,
    message_metadata,
    messages_from_dict,
    messages_to_dict,
    openai_retry_error_types,
)
import re
import json
import os
import asyncio
import hashlib
import logging
import locale
import secrets
import sys
import time
from contextlib import suppress

from config.prompts.prompts_memory import (
    get_recent_history_manager_prompt, get_detailed_recent_history_manager_prompt,
    get_further_summarize_prompt, get_history_review_prompt,
    get_summary_stale_hint,
)
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.language_utils import (
    detect_prompt_language_with_ascii_fallback,
    get_global_language_full,
)
from utils.tokenize import acount_tokens
from utils.screen_comment_guard import strip_screen_labels
from config import (
    LLM_OUTPUT_GUARD_MAX_TOKENS,
    MEMORY_LLM_HARD_TIMEOUT_SECONDS,
    MEMORY_THINKING_OUTPUT_MAX_TOKENS,
    RECENT_HISTORY_MAX_ITEMS,
    RECENT_COMPRESS_THRESHOLD_ITEMS,
    RECENT_SUMMARY_MAX_TOKENS,
    RECENT_PER_MESSAGE_MAX_TOKENS,
    RECENT_SUMMARY_STALE_HOURS,
    RECENT_COMPRESS_INPUT_BUDGET_TOKENS,
    RECENT_HARD_CAP_TOKENS,
)
from datetime import datetime


def _safe_print(*args, **kwargs):
    """Print without crashing on GBK consoles (emoji cannot be encoded).

    On Windows Chinese (GBK) consoles, printing emoji like ✅/⚠️ raises
    ``UnicodeEncodeError`` and aborts the memory pipeline mid-flight. Keep the
    plain ``print`` (privacy rule: raw dialog only via print, never logger) but
    degrade only the un-encodable emoji to ``?`` (keep CJK text readable by
    encoding with the target stream's encoding).
    """
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        output = kwargs.get("file") or sys.stdout
        encoding = getattr(output, "encoding", None) or locale.getpreferredencoding(False)
        sep = kwargs.get("sep", " ")
        joined = str(sep).join(str(a) for a in args)
        safe = joined.encode(encoding, "replace").decode(encoding)
        kwargs.pop("file", None)
        kwargs.pop("flush", None)
        print(safe, **kwargs)


def _detect_recent_prompt_language(text: str) -> str:
    return detect_prompt_language_with_ascii_fallback(
        text,
        ui_language=get_global_language_full(),
    )

# Backward-compat alias (Stage-1 → Stage-2 trigger threshold).
# Two-stage flow: Stage 1 (`compress_history`) summarises raw messages with no
# explicit length cap; Stage 2 (`further_compress`) is invoked only when Stage-1
# output exceeds this threshold. Stage-2's own prompt hard-caps output at
# 500 chars/words per language.
MAX_SUMMARY_TOKENS = RECENT_SUMMARY_MAX_TOKENS

# ── Phase C review snapshot/capacity algorithms ─────────────────────────────
# Fingerprint = list of (type, content[:50]) tuples for last K messages. K=3 balances
# collision resistance (three consecutive mixed user+AI messages rarely false-match) with location precision.
REVIEW_FINGERPRINT_K = 3
REVIEW_FINGERPRINT_CONTENT_PREFIX = 50
_PROMPT_TEXT_PART_TYPES = frozenset((None, 'text', 'input_text', 'output_text'))


def _review_message_content(message) -> str:
    if isinstance(message, dict):
        content = message.get('content', '')
        if 'content' not in message and isinstance(message.get('data'), dict):
            content = message['data'].get('content', '')
    elif hasattr(message, 'content'):
        content = message.content
    else:
        return str(message)
    content = content or ''
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get('text', '') or item))
            else:
                parts.append(str(item))
        return '\n'.join(parts)
    if isinstance(content, str):
        return content
    return str(content)


def _message_locale_text(message) -> str:
    if isinstance(message, dict):
        content = message.get('content', '')
        if 'content' not in message and isinstance(message.get('data'), dict):
            content = message['data'].get('content', '')
    elif hasattr(message, 'content'):
        content = message.content
    else:
        return ''
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    parts = []
    for item in content:
        if not isinstance(item, dict):
            if isinstance(item, str):
                parts.append(item)
            continue
        item_type = item.get('type')
        text = item.get('text')
        if item_type in _PROMPT_TEXT_PART_TYPES and isinstance(text, str):
            parts.append(text)
    return '\n'.join(parts)


def _message_prompt_role(message) -> str:
    if isinstance(message, dict):
        role = message.get('type') or message.get('role')
        if not role and isinstance(message.get('data'), dict):
            role = message['data'].get('type') or message['data'].get('role')
    else:
        role = getattr(message, 'type', None) or getattr(message, 'role', None)
    return str(role or '').strip().lower()


def _review_prompt_locale_text(messages: list) -> str:
    """Prefer user turns as locale evidence for the review prompt."""
    user_messages = [
        message
        for message in messages
        if _message_prompt_role(message) in {'human', 'user'}
    ]
    locale_messages = user_messages or messages
    return '\n\n'.join(
        _message_locale_text(message) for message in locale_messages
    )


async def _await_recent_mutation_to_completion(func, *args):
    """Wait for a submitted recent-file mutation before propagating cancellation."""
    operation = asyncio.create_task(asyncio.to_thread(func, *args))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        while not operation.done():
            with suppress(asyncio.CancelledError):
                await asyncio.wait({operation})
        with suppress(BaseException):
            operation.result()
        raise


async def review_context_token_count(messages: list) -> int:
    """Return a stable token-size metric without blocking the event loop."""
    rows = []
    for message in messages:
        role = getattr(message, 'type', '') or ''
        content = _review_message_content(message)
        if isinstance(message, dict):
            role = message.get('type', message.get('role', role)) or ''
        rows.append(f"{role}: {content}")
    return await acount_tokens('\n\n'.join(rows))


def _msg_identity(m) -> tuple[str, str]:
    """Normalize a message into its full type/content identity.

    Supports message objects (HumanMessage/AIMessage/...) and dicts (persisted
    fingerprints). When content is a list (multimodal), it is joined into a
    string.
    """
    if isinstance(m, dict):
        t = m.get('type', '') or ''
        c = m.get('content', '') if 'content' in m else (m.get('data', {}).get('content', '') if isinstance(m.get('data'), dict) else '')
    else:
        t = getattr(m, 'type', '') or ''
        c = getattr(m, 'content', '') or ''
    if isinstance(c, list):
        parts = []
        for p in c:
            if isinstance(p, dict):
                parts.append(p.get('text', '') or str(p))
            else:
                parts.append(str(p))
        c = ' '.join(parts)
    elif not isinstance(c, str):
        c = str(c)
    return (str(t), c)


def _msg_fingerprint(m) -> tuple[str, str]:
    """Return the legacy type/content-prefix fingerprint tuple."""
    t, c = _msg_identity(m)
    return (t, c[:REVIEW_FINGERPRINT_CONTENT_PREFIX])


def _msg_content_sha256(m) -> str:
    """Return a stable digest of the normalized full message content."""
    return hashlib.sha256(_msg_identity(m)[1].encode("utf-8")).hexdigest()


def build_review_fingerprint(snapshot, k: int = REVIEW_FINGERPRINT_K) -> list[dict]:
    """Take the last K messages of the snapshot as the fingerprint, serialized into JSON-persistable dicts."""
    if not snapshot:
        return []
    tail = snapshot[-k:] if len(snapshot) >= k else list(snapshot)
    out = []
    for m in tail:
        t, c = _msg_fingerprint(m)
        out.append({
            'type': t,
            'content': c,
            'content_sha256': _msg_content_sha256(m),
        })
    return out


def _find_fingerprint_position(current: list, fingerprint: list[dict]) -> int | None:
    """Find the unique run of K consecutive messages matching the fingerprint.

    Returns the index in current of the fingerprint's last element (i.e. the
    cutoff); None when not found or ambiguous. New fingerprints include a full
    content digest; legacy persisted fingerprints fall back to their prefix.
    """
    if not current or not fingerprint:
        return None
    k = len(fingerprint)
    if len(current) < k:
        return None
    def _matches(message, fp: dict) -> bool:
        msg_type, content_prefix = _msg_fingerprint(message)
        if msg_type != fp.get('type', '') or content_prefix != fp.get('content', ''):
            return False
        digest = fp.get('content_sha256')
        return digest is None or _msg_content_sha256(message) == digest

    match = None
    for i in range(len(current) - k + 1):
        if all(_matches(current[i + j], fingerprint[j]) for j in range(k)):
            if match is not None:
                # With multiple candidates, prefer wasted work over cutting review/compression results into the wrong range.
                return None
            match = i + k - 1
    return match


def _compute_review_slots(snapshot: list, current: list) -> list[int]:
    """Locate ordinary-message slots in current history still safely replaceable by snapshot."""  # noqa: DOCSTRING_CJK

    if not snapshot or not current:
        return []
    # General-review snapshot excludes theater capsules; apply same projection to current history at commit,
    # while retaining original indices so capsules do not truncate reverse matching of surrounding ordinary dialogue.
    if (
        not any(is_theater_memory_message(message) for message in snapshot)
        and any(is_theater_memory_message(message) for message in current)
    ):
        current_indexes = [
            index
            for index, message in enumerate(current)
            if not is_theater_memory_message(message)
        ]
        projected_current = [current[index] for index in current_indexes]
    else:
        current_indexes = list(range(len(current)))
        projected_current = current
    if (
        len(projected_current) >= len(snapshot)
        and all(
            _msg_identity(projected_current[index]) == _msg_identity(snapshot[index])
            for index in range(len(snapshot))
        )
    ):
        return current_indexes[:len(snapshot)]
    anchor = build_review_fingerprint(snapshot, REVIEW_FINGERPRINT_K)
    projected_cutoff = _find_fingerprint_position(projected_current, anchor)
    if projected_cutoff is None:
        return []
    slots: list[int] = []
    snapshot_index = len(snapshot) - 1
    current_index = projected_cutoff
    while (
        snapshot_index >= 0
        and current_index >= 0
        and _msg_identity(projected_current[current_index])
        == _msg_identity(snapshot[snapshot_index])
    ):
        slots.append(current_indexes[current_index])
        snapshot_index -= 1
        current_index -= 1
    slots.reverse()
    return slots


def _compute_review_capacity(snapshot: list, current: list) -> tuple[int, int | None]:
    """Given the snapshot taken at review start and the current history, compute (capacity, cutoff_idx).

    1. Use the snapshot's last K entries as an anchor to locate cutoff_idx in current.
    2. Walk backwards from cutoff_idx, comparing snapshot[-1], snapshot[-2], ...
       against current[cutoff_idx], current[cutoff_idx-1], ...; the consecutive
       match length is the capacity (stopping when an "alien" entry such as a
       compression SystemMessage appears in between).

    Returns ``(0, None)`` for a wasted (no-op) review.
    """
    slots = _compute_review_slots(snapshot, current)
    return (len(slots), slots[-1] if slots else None)


# Setup logger
from utils.file_utils import (
    atomic_write_json_async,
    read_json_async,
    robust_json_loads,
)
from utils import recent_file
from utils.logger_config import setup_logging
logger, log_config = setup_logging(service_name="Memory", log_level=logging.INFO)

# ── Three recent.json read states ──────────────────────────────────────────────
# Crucial distinction: unreadable does not mean empty on disk. Previously both became [], so one
# transient read failure let next persistence overwrite all history with new messages.
RECENT_READ_OK = 'ok'
RECENT_READ_MISSING = 'missing'
RECENT_READ_UNREADABLE = 'unreadable'

# Maximum pending-message count while disk stays unwritable; drop oldest above cap, degrading to retain
# latest N in memory, strictly better than previously dropping the batch immediately.
RECENT_PENDING_MAX_ITEMS = 64
# Only latest three playthroughs per script enter everyday working memory; older complete narrative remains in public cold archives.
THEATER_RECENT_EPISODES_PER_STORY = 3
# All scripts together retain at most thirty recent capsules, protecting ordinary dialogue as script count grows.
THEATER_RECENT_EPISODES_TOTAL = 30


def _copy_message_metadata(message, metadata):
    """Copy internal messages with replaced metadata without mutating caller objects."""  # noqa: DOCSTRING_CJK

    message_type = getattr(message, "type", "system")
    message_class = {
        "human": HumanMessage,
        "ai": AIMessage,
        "system": SystemMessage,
    }.get(message_type, SystemMessage)
    return message_class(
        content=getattr(message, "content", ""),
        metadata=dict(metadata),
    )


def _positive_metadata_int(value) -> int:
    """Read a theater counter from capsule metadata; anything but a positive int is 0.

    Shared with the memory server's prompt rendering so the stored and rendered
    run_index / story_run_count can never disagree.
    """
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


def is_retracted_theater_episode(
    message, story_id: str, session_id: str, archive_through_revision: int,
) -> bool:
    """Match the capsule written by one theater archive range (story, session, through revision)."""

    if not is_theater_memory_message(message):
        return False
    metadata = message_metadata(message)
    through = metadata.get("archive_through_revision")
    return (
        str(metadata.get("story_id") or "") == story_id
        and str(metadata.get("session_id") or "") == session_id
        and isinstance(through, int)
        and not isinstance(through, bool)
        and through == archive_through_revision
    )


# A declined (retracted) theater archive leaves a tombstone next to recent.json so
# a late /cache write of that same archive attempt cannot resurrect the capsule.
# Late writes arrive within seconds to minutes; a week of history is ample.
THEATER_RETRACTIONS_FILENAME = "theater_retractions.json"
THEATER_RETRACTION_TTL_SECONDS = 7 * 24 * 3600
THEATER_RETRACTIONS_MAX = 128
THEATER_RUNS_FILENAME = "theater_runs.json"
THEATER_PREVIOUS_EPISODE = "_theater_previous_episode"


def restored_theater_history(history, story_id, session_id, through):
    """Replace a declined capsule with its previous version, without nesting backups."""
    restored = []
    for message in history:
        if not is_retracted_theater_episode(message, story_id, session_id, through):
            restored.append(message)
            continue
        backup = message_metadata(message).get(THEATER_PREVIOUS_EPISODE)
        if not isinstance(backup, dict):
            continue
        previous = messages_from_dict([backup])[0]
        metadata = message_metadata(previous)
        if (
            theater_memory_episode_key(previous) == (story_id, session_id)
            and _positive_metadata_int(metadata.get("archive_through_revision")) < through
        ):
            restored.append(previous)
    return restored


def _load_theater_runs_unlocked(recent_path):
    """Read durable numbers, quarantining malformed data but propagating I/O errors."""
    path = recent_file.recent_sidecar_path(recent_path, THEATER_RUNS_FILENAME)
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict) or any(
        not isinstance(story, dict)
        or not isinstance(story.get("sessions"), dict)
        or not isinstance(story.get("total"), int)
        or not _positive_metadata_int(story.get("total"))
        or any(not isinstance(number, int) or not _positive_metadata_int(number)
               for number in story["sessions"].values())
        or story["total"] < max(story["sessions"].values(), default=0)
        for story in payload.values()
    ):
        # Preserve the damaged file, then let the caller seed only verifiable
        # numbers from recent. I/O errors still propagate without resetting it.
        os.replace(path, f"{path}.{secrets.token_hex(8)}.corrupt")
        logger.warning("[RecentHistory] quarantined invalid theater run numbers: %s", path)
        return {}
    return payload


class TheaterEpisodeRetracted(RuntimeError):
    """A theater archive write matched a tombstone left by the player's decline."""


def _load_theater_retraction_state_unlocked(recent_path) -> tuple[list[dict], list[dict]]:
    """Read (attempt tombstones, story forget tombstones); the caller holds the file lock.

    A missing or structurally invalid file reads as empty (writes are atomic, so
    invalid content only comes from outside tampering); an I/O failure raises so
    a pending tombstone is never silently ignored.
    """

    path = recent_file.recent_sidecar_path(recent_path, THEATER_RETRACTIONS_FILENAME)
    try:
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return [], []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"[RecentHistory] ignoring invalid theater retraction file: {path}")
        return [], []
    if not isinstance(payload, dict):
        return [], []
    lists = []
    for key in ("entries", "forgotten_stories"):
        values = payload.get(key)
        lists.append(
            [entry for entry in values if isinstance(entry, dict)]
            if isinstance(values, list)
            else []
        )
    return lists[0], lists[1]


def _write_theater_retraction_state_unlocked(
    recent_path, entries: list[dict], forgotten: list[dict],
) -> None:
    """Atomically persist both tombstone lists; the caller holds the file lock."""

    recent_file.write_recent_sidecar_unlocked(
        recent_path,
        THEATER_RETRACTIONS_FILENAME,
        {
            "schema": "neko.theater.retractions.v1",
            "entries": entries[-THEATER_RETRACTIONS_MAX:],
            "forgotten_stories": forgotten[-THEATER_RETRACTIONS_MAX:],
        },
    )


def _is_fresh_tombstone(stamp, now: float) -> bool:
    return (
        isinstance(stamp, (int, float))
        and not isinstance(stamp, bool)
        and now - stamp < THEATER_RETRACTION_TTL_SECONDS
    )


def _theater_story_write_is_forgotten(
    forgotten: list[dict], story_id: str, forget_marker: str | None, now: float,
) -> bool:
    """True when a write for this story was issued before the story was forgotten.

    Every forget of a story stores a fresh random ``forget_marker`` and returns
    it to the theater, which persists it and attaches it to each archive request
    it issues afterwards (under the story's memory lock, which forget also
    holds). A write issued before the forget carries an older marker or none, so
    it cannot match and is dropped; an archive the player starts after the forget
    carries the current marker and lands. No clock is compared, so a system clock
    stepping backwards cannot reject a legitimate archive. A tombstone without a
    marker (written by an intermediate build) drops every write of its story.
    Tombstones stop applying after the retention window, which bounds the damage
    if the theater ever loses its marker.
    """

    if not story_id:
        return False
    for entry in forgotten:
        if str(entry.get("story_id") or "") != story_id:
            continue
        forgotten_at = entry.get("forgotten_at")
        if (
            isinstance(forgotten_at, (int, float))
            and not isinstance(forgotten_at, bool)
            and not _is_fresh_tombstone(forgotten_at, now)
        ):
            continue
        current_marker = entry.get("forget_marker")
        if not isinstance(current_marker, str) or not current_marker:
            return True
        if forget_marker != current_marker:
            return True
    return False


def _theater_attempt_is_retracted(
    entries: list[dict], archive_request_id: str, archive_attempt: int | None,
) -> bool:
    """True when this archive attempt was issued before a matching retraction.

    Attempts are numbered per archive request; a retraction covers every attempt
    up to the one the theater had issued when the player declined, while a later
    explicit archive of the same request (a new attempt) stays allowed.
    """

    if not archive_request_id:
        return False
    for entry in entries:
        if str(entry.get("archive_request_id") or "") != archive_request_id:
            continue
        through_attempt = entry.get("through_attempt")
        if not isinstance(through_attempt, int) or isinstance(through_attempt, bool):
            return True
        if archive_attempt is None or archive_attempt <= through_attempt:
            return True
    return False


def _theater_episode_capsule(messages: list):
    """Collapse legacy multiple narrative entries from one Session into a single episode-summary capsule."""  # noqa: DOCSTRING_CJK

    latest = messages[-1]
    metadata = dict(message_metadata(latest))
    summary = str(
        metadata.get("episode_summary")
        or metadata.get("ending_summary")
        or ""
    ).strip()
    if not summary:
        for message in reversed(messages):
            content = getattr(message, "content", "")
            if isinstance(content, list):
                content = "\n".join(
                    str(part.get("text") or "").strip()
                    for part in content
                    if isinstance(part, dict) and str(part.get("text") or "").strip()
                )
            summary = " ".join(str(content or "").split())
            if summary:
                break
    if len(summary) > 360:
        summary = summary[:360].rstrip() + "……"
    metadata.update({
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "episode_summary": summary,
    })
    return SystemMessage(content=summary, metadata=metadata)


def _collapse_theater_episode_messages(history: list) -> list:
    """Merge legacy theater messages by story_id + session_id, retaining ordinary-dialogue order."""  # noqa: DOCSTRING_CJK

    collapsed: list = []
    groups: dict[tuple[str, str], list] = {}
    slots: dict[tuple[str, str], int] = {}
    for message in history:
        if not is_theater_memory_message(message):
            collapsed.append(message)
            continue
        key = theater_memory_episode_key(message)
        if not all(key):
            collapsed.append(message)
            continue
        groups.setdefault(key, []).append(message)
        if key not in slots:
            slots[key] = len(collapsed)
            collapsed.append(None)
    for key, slot in slots.items():
        group = groups[key]
        if len(group) == 1 and is_theater_episode_summary(group[0]):
            # An already-folded capsule stays byte-identical: rebuilding it would
            # turn list content into a string and truncate long ending summaries,
            # which resets the story's time-index timestamp and breaks in-flight
            # compression/review snapshots whenever another story is archived.
            collapsed[slot] = group[0]
        else:
            collapsed[slot] = _theater_episode_capsule(group)
    return [message for message in collapsed if message is not None]


def _merge_theater_episode_summary(history: list, incoming):
    """Idempotently update playthrough summary and bound recent memories for same script."""  # noqa: DOCSTRING_CJK

    merged = _collapse_theater_episode_messages(list(history))
    incoming_metadata = dict(message_metadata(incoming))
    incoming_key = theater_memory_episode_key(incoming)
    story_id = incoming_key[0]
    previous_index = next(
        (
            index
            for index, message in enumerate(merged)
            if is_theater_episode_summary(message)
            and theater_memory_episode_key(message) == incoming_key
        ),
        None,
    )
    previous = merged[previous_index] if previous_index is not None else None
    merged = [
        message
        for message in merged
        if not (
            is_theater_episode_summary(message)
            and theater_memory_episode_key(message) == incoming_key
        )
    ]

    story_episodes = [
        (position, message)
        for position, message in enumerate(merged)
        if is_theater_episode_summary(message)
        and message_metadata(message).get("story_id") == story_id
    ]
    known_total = 0
    ending_titles: list[str] = []
    for index, (position, message) in enumerate(story_episodes, start=1):
        metadata = dict(message_metadata(message))
        run_index = _positive_metadata_int(metadata.get("run_index"))
        if not run_index:
            run_index = index
            metadata["run_index"] = run_index
            replacement = _copy_message_metadata(message, metadata)
            merged[position] = replacement
        known_total = max(
            known_total,
            run_index,
            _positive_metadata_int(metadata.get("story_run_count")),
        )
        for title in metadata.get("ending_titles_seen") or []:
            normalized = str(title or "").strip()
            if normalized and normalized not in ending_titles:
                ending_titles.append(normalized)
        current_ending = str(metadata.get("ending_title") or "").strip()
        if current_ending and current_ending not in ending_titles:
            ending_titles.append(current_ending)

    previous_metadata = message_metadata(previous) if previous is not None else {}
    if previous is not None:
        if previous_metadata.get("archive_through_revision") == incoming_metadata.get("archive_through_revision"):
            backup = previous_metadata.get(THEATER_PREVIOUS_EPISODE)
        else:
            backup = messages_to_dict([previous])[0]
            backup["data"]["metadata"].pop(THEATER_PREVIOUS_EPISODE, None)
        if backup:
            incoming_metadata[THEATER_PREVIOUS_EPISODE] = backup
    previous_run_index = _positive_metadata_int(previous_metadata.get("run_index"))
    if previous_run_index:
        run_index = previous_run_index
        known_total = max(
            known_total,
            _positive_metadata_int(previous_metadata.get("story_run_count")),
        )
    else:
        run_index = _positive_metadata_int(incoming_metadata.get("run_index")) or known_total + 1
    known_total = max(known_total, _positive_metadata_int(incoming_metadata.get("story_run_count")))
    for title in previous_metadata.get("ending_titles_seen") or []:
        normalized = str(title or "").strip()
        if normalized and normalized not in ending_titles:
            ending_titles.append(normalized)
    previous_ending = str(previous_metadata.get("ending_title") or "").strip()
    if previous_ending and previous_ending not in ending_titles:
        ending_titles.append(previous_ending)
    current_ending = str(incoming_metadata.get("ending_title") or "").strip()
    if current_ending and current_ending not in ending_titles:
        ending_titles.append(current_ending)
    incoming_metadata.update({
        "run_index": run_index,
        "story_run_count": max(known_total, run_index),
        "ending_titles_seen": ending_titles,
    })
    stored_incoming = _copy_message_metadata(incoming, incoming_metadata)

    def retry_payload(message):
        metadata = dict(message_metadata(message))
        # These describe the whole story, not a change to this episode.
        metadata.pop("story_run_count", None)
        metadata.pop("ending_titles_seen", None)
        return messages_to_dict([_copy_message_metadata(message, metadata)])

    if (
        previous is not None
        and retry_payload(stored_incoming) == retry_payload(previous)
    ):
        # A retried archive that changes nothing keeps its slot, so the retry
        # cannot reorder history under an in-flight compression snapshot or
        # reset the story's time-index timestamp. A changed capsule (resumed,
        # paused again, or completed) is the newest event and moves to the end,
        # matching the fresh timestamp reconcile gives an updated story.
        merged.insert(previous_index, stored_incoming)
    else:
        merged.append(stored_incoming)

    story_indexes = [
        index
        for index, message in enumerate(merged)
        if is_theater_episode_summary(message)
        and message_metadata(message).get("story_id") == story_id
    ]
    # The durable run number determines the recent three episodes. An evicted
    # old Session arriving late must not displace a newer run just by arriving.
    ranked_story_indexes = sorted(story_indexes, key=lambda index: (
        _positive_metadata_int(message_metadata(merged[index]).get("run_index")), index,
    ))
    drop_indexes = set(ranked_story_indexes[:-THEATER_RECENT_EPISODES_PER_STORY])
    incoming_evicted_by_run_limit = any(merged[index] is stored_incoming for index in drop_indexes)
    if drop_indexes:
        merged = [
            message
            for index, message in enumerate(merged)
            if index not in drop_indexes
        ]
    theater_indexes = [
        index
        for index, message in enumerate(merged)
        if is_theater_episode_summary(message)
    ]
    global_drop_indexes = set(
        theater_indexes[:-THEATER_RECENT_EPISODES_TOTAL]
    )
    if global_drop_indexes:
        merged = [
            message
            for index, message in enumerate(merged)
            if index not in global_drop_indexes
        ]
    from memory.theater_budget import bound_theater_history
    bounded = bound_theater_history(merged)
    # A known older run intentionally outside the per-story window may be
    # acknowledged without re-entering hot memory; token-budget rejection is
    # still an error for an incoming run that should have been retained.
    if not incoming_evicted_by_run_limit and not any(message is stored_incoming for message in bounded):
        # Reject before persistence: the caller must retain its archive receipt
        # rather than acknowledge a capsule that never entered hot memory.
        raise ValueError("theater_episode_budget_exceeded")
    return bounded, stored_incoming


class CompressedRecentHistoryManager:
    def __init__(
        self,
        max_history_length: int = RECENT_HISTORY_MAX_ITEMS,
        compress_threshold: int = RECENT_COMPRESS_THRESHOLD_ITEMS,
    ):
        self._config_manager = get_config_manager()
        # Fetch relevant variables through get_character_data.
        _, _, _, _, name_mapping, _, _, _, recent_log = self._config_manager.get_character_data()
        self.max_history_length = max_history_length      # Entries retained after compression.
        self.compress_threshold = compress_threshold      # Compress only above this value.
        self.log_file_path = recent_log
        self.name_mapping = name_mapping
        # Do **not** preload here. Both construction sites (memory_server.runtime startup + reload) are
        # async functions on event-loop thread, unsuitable for waiting on other writers' file critical sections
        # or doing multi-MB blocking reads for N characters.
        # This also distinguishes unknown views (name absent from dict) from known-empty views ([]),
        # required by persistence-failure merging.
        self.user_histories = {}
        # user_histories is caller-visible view, potentially already including process-wide pending.
        # Track pending-tail length separately for precise replacement on read failure rather than content deduplication,
        # since consecutive identical user messages are valid.
        self._cached_pending_counts = {}
        # A successful write from another producer invalidates the cached disk
        # baseline even when the next physical read is transiently unavailable.
        self._cached_disk_versions = {}

    # ── Unpersisted-batch ledger ────────────────────────────────────────────────
    def _pending_batches(self, lanlan_name: str) -> list:
        """Return messages that could not be persisted to this character's file."""
        return recent_file.get_recent_pending(self._ensure_path_for_character(lanlan_name))

    def _set_pending_batches(self, lanlan_name: str, messages: list, file_path=None) -> None:
        """Record (or clear) unpersisted messages while the file lock is held."""
        cap = max(2 * self.compress_threshold, RECENT_PENDING_MAX_ITEMS)
        if len(messages) > cap:
            logger.warning(
                f"[RecentHistory] {lanlan_name} 未落盘消息已达上界 {cap} 条，"
                f"丢弃最旧 {len(messages) - cap} 条"
            )
            messages = messages[-cap:]
        recent_file.set_recent_pending_unlocked(
            file_path or self._ensure_path_for_character(lanlan_name), messages,
        )

    def _get_default_path(self, lanlan_name: str) -> str:
        """Single place for the default path, avoiding duplicated code."""
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, lanlan_name), 'recent.json')

    def _ensure_path_for_character(self, lanlan_name: str) -> str:
        """Ensure the character has a valid file path; returns the path."""
        if lanlan_name not in self.log_file_path:
            self.log_file_path[lanlan_name] = self._get_default_path(lanlan_name)
            logger.info(f"[RecentHistory] 角色 '{lanlan_name}' 不在配置中，使用默认路径")
        return self.log_file_path[lanlan_name]

    def _capture_recent_operation_admission(self, lanlan_name: str):
        """Freeze the path identity before an operation reaches its first await."""
        file_path = (self.log_file_path or {}).get(lanlan_name)
        if not file_path:
            file_path = os.path.join(
                self._config_manager.memory_dir, lanlan_name, "recent.json",
            )
        return file_path, recent_file.capture_recent_generation(file_path)

    def _reset_history_file_unlocked(self, file_path, lanlan_name, reason):
        """Reset a corrupt/empty recent file to ``[]``.

        The caller MUST already hold ``recent_file.recent_file_lock(file_path)``:
        this is reached from inside the locked read path and ``threading.Lock``
        is not reentrant. Naming follows ``event_log._write_line_unlocked``.
        """
        try:
            assert_cloudsave_writable(
                self._config_manager,
                operation="reset",
                target=f"memory/{lanlan_name}/recent.json",
            )
            recent_file.write_recent_payload_unlocked(file_path, [])
            logger.warning(f"[RecentHistory] {lanlan_name} 的历史记录文件无效（{reason}），已重置为空列表: {file_path}")
        except MaintenanceModeError:
            raise
        except Exception as reset_error:
            logger.error(f"[RecentHistory] 重置 {lanlan_name} 的历史记录文件失败: {reset_error}", exc_info=True)

    def _load_history_unlocked(self, file_path, lanlan_name) -> tuple[str, list]:
        """Read and parse the recent file. The caller MUST already hold the file lock.

        Returns ``(status, history)``:
          ``(RECENT_READ_OK, [...])``       parsed successfully;
          ``(RECENT_READ_MISSING, [])``     the file is absent, or it was empty /
              corrupt and has been reset — empty is the truth here;
          ``(RECENT_READ_UNREADABLE, [])``  the bytes could not be read or
              decoded. This is NOT an empty history; callers must not persist
              anything on top of it.
        """
        if not os.path.exists(file_path):
            return (RECENT_READ_MISSING, [])
        try:
            raw_content = recent_file.read_recent_text_unlocked(file_path)

            if not raw_content.strip():
                self._reset_history_file_unlocked(file_path, lanlan_name, "文件为空")
                return (RECENT_READ_MISSING, [])

            file_content = json.loads(raw_content)
            if not isinstance(file_content, list):
                self._reset_history_file_unlocked(file_path, lanlan_name, "JSON 根节点不是列表")
                return (RECENT_READ_MISSING, [])

            return (RECENT_READ_OK, messages_from_dict(file_content))
        except json.JSONDecodeError as e:
            # Same as before refactor: MaintenanceModeError raised by reset in this handler
            # propagates directly (except Exception below is a sibling branch and cannot catch it).
            self._reset_history_file_unlocked(file_path, lanlan_name, f"JSON 解析失败: {e}")
            return (RECENT_READ_MISSING, [])
        except Exception as e:
            logger.warning(f"读取 {lanlan_name} 的历史记录文件失败: {e}，本轮视为读不到（不覆盖）")
            return (RECENT_READ_UNREADABLE, [])

    def _cache_history_view(
        self, file_path, lanlan_name, disk_history, pending=(),
    ) -> list:
        """Cache and return a visible disk-plus-pending history view."""
        visible = list(disk_history) + list(pending)
        self.user_histories[lanlan_name] = visible
        pending_counts = getattr(self, "_cached_pending_counts", None)
        if pending_counts is None:
            pending_counts = self._cached_pending_counts = {}
        pending_counts[lanlan_name] = len(pending)
        disk_versions = getattr(self, "_cached_disk_versions", None)
        if disk_versions is None:
            disk_versions = self._cached_disk_versions = {}
        disk_versions[lanlan_name] = (
            recent_file.get_recent_content_version_unlocked(file_path)
        )
        return visible

    def _cached_disk_history(self, lanlan_name) -> list:
        """Return the cached disk base without any pending tail already shown."""
        cached = list(self.user_histories.get(lanlan_name, ()))
        pending_count = getattr(self, "_cached_pending_counts", {}).get(lanlan_name, 0)
        if pending_count:
            return cached[:-pending_count]
        return cached

    def _current_cached_disk_history(self, file_path, lanlan_name) -> list:
        """Return the cached disk base only when no successful write superseded it."""
        current_version = recent_file.get_recent_content_version_unlocked(file_path)
        cached_version = getattr(
            self, "_cached_disk_versions", {},
        ).get(lanlan_name)
        if cached_version != current_version:
            return []
        return self._cached_disk_history(lanlan_name)

    def _read_history_locked(
        self, file_path, lanlan_name, expected_generation=None,
    ) -> list:
        """Read one character's history under the file lock. Run me in a worker thread."""
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            status, history = self._load_history_unlocked(file_path, lanlan_name)
            pending = recent_file.get_recent_pending_unlocked(file_path)
            if status == RECENT_READ_UNREADABLE:
                # Read failure must never mean empty history; once stored in user_histories, next
                # persistence overwrites all unreadable history with new messages. Retain cached disk baseline,
                # while process-wide pending still represents accepted messages prompts must see.
                return self._cache_history_view(
                    file_path,
                    lanlan_name,
                    self._current_cached_disk_history(file_path, lanlan_name),
                    pending,
                )
            # Process-wide pending from failed writes is also part of current visible history. Ordinary reads
            # must not erase it from memory whether recent.json is absent or still an old version.
            return self._cache_history_view(
                file_path, lanlan_name, history, pending,
            )

    def _commit_hard_cap_locked(
        self, file_path, lanlan_name, expected_history, new_history,
        expected_generation=None,
    ) -> str:
        """Persist a precomputed trim only if the locked disk snapshot is unchanged."""
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            status, current = self._load_history_unlocked(file_path, lanlan_name)
            if status == RECENT_READ_UNREADABLE:
                return 'failed'
            pending = recent_file.get_recent_pending_unlocked(file_path)
            current_visible = list(current) + list(pending)
            if messages_to_dict(current_visible) != messages_to_dict(expected_history):
                return 'stale'
            try:
                recent_file.write_recent_payload_unlocked(
                    file_path, messages_to_dict(new_history),
                )
            except Exception as e:
                logger.error(
                    f"[RecentHistory] {lanlan_name} 硬上限裁剪落盘失败: {e}",
                    exc_info=True,
                )
                return 'failed'
            recent_file.set_recent_pending_unlocked(file_path, [])
            self._cache_history_view(file_path, lanlan_name, new_history)
            return 'committed'

    def _merge_backup_memo_locked(
        self, file_path, lanlan_name, snapshot, memo, expected_generation=None,
        preserved_messages=(),
    ) -> tuple[str, int, int]:
        """Re-read, locate, merge and persist a backup memo in one critical section."""
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            read_status, current = self._load_history_unlocked(file_path, lanlan_name)
            if read_status == RECENT_READ_UNREADABLE:
                return ('failed', 0, 0)
            if not current or not snapshot:
                return ('moot', len(current), len(current))
            capacity, cutoff_idx = _compute_review_capacity(snapshot, current)
            if (
                cutoff_idx is None
                or capacity != len(snapshot)
                or (cutoff_idx - capacity + 1) != 0
            ):
                return ('moot', len(current), len(current))
            new_history = [memo] + list(preserved_messages) + current[cutoff_idx + 1:]
            try:
                recent_file.write_recent_payload_unlocked(
                    file_path, messages_to_dict(new_history),
                )
            except Exception as e:
                logger.error(
                    f"[RecentHistory] {lanlan_name} 后台压缩合并落盘失败: {e}",
                    exc_info=True,
                )
                return ('failed', len(current), len(current))
            pending = recent_file.get_recent_pending_unlocked(file_path)
            self._cache_history_view(
                file_path, lanlan_name, new_history, pending,
            )
            return ('merged', len(current), len(new_history))


    def _get_llm(self):
        """Fetch the LLM instance dynamically to support config hot-reload.

        timeout=30 pairs with business-level max_retries=3 + exponential backoff
        (worst case ~127s), covering the "give up" ceiling after the /process
        upstream's 30s timeout.
        max_retries=0 disables the OpenAI SDK's default 2 automatic retries,
        avoiding a 3× blow-up stacked on the business-level retry.
        """
        api_config = self._config_manager.get_model_api_config('summary')
        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        return create_chat_llm(
            api_config['model'], api_config['base_url'],
            api_config['api_key'] or None,
            timeout=30, max_retries=0,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length summary/JSON isn't truncated
            provider_type=api_config.get('provider_type'),
        )

    def _get_review_llm(self, max_completion_tokens: int = MEMORY_THINKING_OUTPUT_MAX_TOKENS):
        """Fetch the review LLM instance dynamically to support config hot-reload.

        timeout uses MEMORY_LLM_HARD_TIMEOUT_SECONDS (the upstream forwards with
        a 120s hard cap; must be ≤110). Review is a pure background task (after
        the Phase C redesign it holds no locks, never blocks the user path, and
        concurrent runs are harmless), so thinking can remain enabled for the
        judgment-dense work of rewriting history.

        ``extra_body`` overrides the provider-aware factory default (which
        would disable thinking): ``None`` leaves thinking models on their
        native behavior, and endpoints whose native thinking is unbounded get
        the lower-effort form from ``memory_thinking_extra_body``.
        max_retries=0 as above: SDK auto-retry off; the business-layer retry is
        the safety net.
        """
        from config.providers import memory_thinking_extra_body
        api_config = self._config_manager.get_model_api_config('correction')
        return create_chat_llm(
            api_config['model'], api_config['base_url'],
            api_config['api_key'] or None,
            timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS, max_retries=0,
            max_completion_tokens=max_completion_tokens,  # thinking shares this budget with the corrected-dialogue JSON
            extra_body=memory_thinking_extra_body(api_config['base_url'], max_completion_tokens),
            provider_type=api_config.get('provider_type'),
        )

    def _append_and_persist_locked(
        self, file_path, lanlan_name, new_messages, expected_generation=None,
    ) -> tuple[list, bool]:
        """Read → merge unpersisted batches → append → persist, as one critical section.

        Returns ``(history, persisted)``. ``persisted`` is False when the batch
        only made it into memory; those messages are recorded in ``_pending`` so
        that the next call re-attaches them instead of letting the on-disk copy
        silently win.
        """
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            status, history = self._load_history_unlocked(file_path, lanlan_name)
            pending = recent_file.get_recent_pending_unlocked(file_path)
            if status == RECENT_READ_UNREADABLE:
                # Unreadable disk content is not empty. Writing here would replace
                # all unreadable history with this new batch (exactly how pre-refactor `except Exception: return []`
                # lost it), so skip disk writing entirely this pass.
                updated_pending = list(pending) + list(new_messages)
                self._set_pending_batches(lanlan_name, updated_pending, file_path)
                logger.warning(
                    f"[RecentHistory] {lanlan_name} 历史文件读取失败，本轮不落盘，"
                    f"{len(new_messages)} 条新消息暂存内存等下次补写"
                )
                return (
                    self._cache_history_view(
                        file_path,
                        lanlan_name,
                        self._current_cached_disk_history(file_path, lanlan_name),
                        updated_pending,
                    ),
                    False,
                )

            merged = list(history) + list(pending) + list(new_messages)
            try:
                recent_file.write_recent_payload_unlocked(
                    file_path, messages_to_dict(merged),
                )
            except Exception as e:
                # Persistence failure means target not replaced (_replace_with_busy_retry is atomic_write_text's
                # final statement), so reattaching the full batch to
                # pending cannot duplicate persistence and needs no deduplication heuristics.
                self._set_pending_batches(
                    lanlan_name, list(pending) + list(new_messages), file_path,
                )
                self._cache_history_view(
                    file_path,
                    lanlan_name,
                    history,
                    list(pending) + list(new_messages),
                )
                logger.error(f"[RecentHistory] 保存历史记录失败: {e}", exc_info=True)
                return (merged, False)

            self._set_pending_batches(lanlan_name, [], file_path)
            self._cache_history_view(file_path, lanlan_name, merged)
            return (merged, True)

    def _upsert_theater_episode_locked(
        self, file_path, lanlan_name, incoming, expected_generation=None,
        archive_request_id="", archive_attempt=None, forget_marker=None,
    ):
        """Replace same-Session capsule and persist within one file critical section."""  # noqa: DOCSTRING_CJK

        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            retracted_attempts, forgotten_stories = (
                _load_theater_retraction_state_unlocked(file_path)
            )
            if archive_request_id and _theater_attempt_is_retracted(
                retracted_attempts,
                archive_request_id,
                archive_attempt,
            ):
                raise TheaterEpisodeRetracted(archive_request_id)
            if _theater_story_write_is_forgotten(
                forgotten_stories,
                str(message_metadata(incoming).get("story_id") or ""),
                forget_marker,
                time.time(),
            ):
                raise TheaterEpisodeRetracted(archive_request_id)
            status, history = self._load_history_unlocked(file_path, lanlan_name)
            pending = recent_file.get_recent_pending_unlocked(file_path)
            if status == RECENT_READ_UNREADABLE:
                logger.warning(
                    f"[RecentHistory] {lanlan_name} 历史文件读取失败，"
                    "剧场单集摘要由归档回执重试"
                )
                # Ordinary pending is flushed without theater merge/tombstone
                # checks or index reconciliation. Never enqueue an archive here;
                # its durable receipt retries through the complete /cache path.
                raise RuntimeError("theater_episode_persist_failed")

            runs = _load_theater_runs_unlocked(file_path)
            current = list(history) + list(pending)
            for message in current:
                metadata = message_metadata(message)
                story, session = theater_memory_episode_key(message)
                if story and session and _positive_metadata_int(metadata.get("run_index")):
                    record = runs.setdefault(story, {"sessions": {}, "total": 0})
                    record["sessions"].setdefault(session, int(metadata["run_index"]))
                    record["total"] = max(record["total"], int(metadata["run_index"]),
                                          _positive_metadata_int(metadata.get("story_run_count")))
            # Allocate old unnumbered capsules from the same durable registry
            # before allocating incoming, rather than giving both position 1.
            for position, message in enumerate(current):
                if not is_theater_episode_summary(message):
                    continue
                metadata = dict(message_metadata(message))
                if _positive_metadata_int(metadata.get("run_index")):
                    continue
                story, session = theater_memory_episode_key(message)
                record = runs.setdefault(story, {"sessions": {}, "total": 0})
                number = record["sessions"].get(session) or record["total"] + 1
                record["sessions"][session] = number
                record["total"] = max(record["total"], number)
                metadata.update(run_index=number, story_run_count=record["total"])
                current[position] = _copy_message_metadata(message, metadata)
            metadata = dict(message_metadata(incoming))
            story, session = theater_memory_episode_key(incoming)
            record = runs.setdefault(story, {"sessions": {}, "total": 0})
            sessions = record["sessions"]
            number = sessions.get(session) or record["total"] + 1
            sessions[session] = number
            record["total"] = max(record["total"], number)
            metadata.update(run_index=number, story_run_count=record["total"])
            incoming = _copy_message_metadata(incoming, metadata)
            merged, stored_incoming = _merge_theater_episode_summary(
                current,
                incoming,
            )
            try:
                # Write ahead: retry reuses the same number even if recent fails.
                recent_file.write_recent_sidecar_unlocked(file_path, THEATER_RUNS_FILENAME, runs)
                recent_file.write_recent_payload_unlocked(
                    file_path, messages_to_dict(merged),
                )
            except Exception as exc:
                logger.error(
                    f"[RecentHistory] 保存 {lanlan_name} 剧场单集摘要失败: {exc}",
                    exc_info=True,
                )
                # Keep existing ordinary pending intact. Failed archive input
                # must remain solely in the theater's durable retry protocol.
                raise RuntimeError("theater_episode_persist_failed") from exc

            self._set_pending_batches(lanlan_name, [], file_path)
            self._cache_history_view(file_path, lanlan_name, merged)
            return stored_incoming

    async def upsert_theater_episode(
        self, message, lanlan_name, *, archive_request_id="", archive_attempt=None,
        forget_marker=None,
    ):
        """Converge paused/completed states of one Session into one recent memory.

        If archive_request_id hits a retraction tombstone, or script has forgetting tombstone without write's
        current forget_marker (issued before forgetting), raise TheaterEpisodeRetracted without persistence.
        """  # noqa: DOCSTRING_CJK

        if not is_theater_episode_summary(message):
            raise ValueError("theater_episode_summary_required")
        story_id, session_id = theater_memory_episode_key(message)
        if not story_id or not session_id:
            raise ValueError("theater_episode_identity_required")
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        try:
            _, _, _, _, _, _, _, _, recent_log = await self._config_manager.aget_character_data()
            self.log_file_path = recent_log
        except Exception as exc:
            logger.error(f"获取角色配置失败: {exc}")
        await asyncio.to_thread(
            assert_cloudsave_writable,
            self._config_manager,
            operation="save",
            target=f"memory/{lanlan_name}/recent.json",
        )
        return await _await_recent_mutation_to_completion(
            self._upsert_theater_episode_locked,
            file_path,
            lanlan_name,
            message,
            admission_generation,
            str(archive_request_id or ""),
            archive_attempt,
            str(forget_marker or "") or None,
        )

    def _restore_theater_cache_snapshot_locked(
        self, file_path, lanlan_name, previous, expected, expected_generation=None,
    ):
        """Restore a failed theater cache write only if no later write replaced it."""

        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            status, history = self._load_history_unlocked(file_path, lanlan_name)
            if status == RECENT_READ_UNREADABLE:
                raise RuntimeError("theater_recent_history_unreadable")
            pending = recent_file.get_recent_pending_unlocked(file_path)
            if messages_to_dict(list(history) + list(pending)) != messages_to_dict(expected):
                raise RuntimeError("theater_recent_history_changed")
            recent_file.write_recent_payload_unlocked(file_path, messages_to_dict(previous))
            recent_file.set_recent_pending_unlocked(file_path, [])
            self._set_pending_batches(lanlan_name, [], file_path)
            self._cache_history_view(file_path, lanlan_name, previous)

    async def restore_theater_cache_snapshot(self, lanlan_name, previous, expected):
        """Roll back an episode upsert when the time-index transaction fails."""

        file_path, admission_generation = self._capture_recent_operation_admission(lanlan_name)
        await asyncio.to_thread(
            assert_cloudsave_writable,
            self._config_manager,
            operation="save",
            target=f"memory/{lanlan_name}/recent.json",
        )
        await _await_recent_mutation_to_completion(
            self._restore_theater_cache_snapshot_locked,
            file_path,
            lanlan_name,
            previous,
            expected,
            admission_generation,
        )

    def _forget_theater_story_locked(
        self, file_path, lanlan_name, story_id, expected_generation=None,
    ):
        """Delete specified script capsules within recent.json critical section."""  # noqa: DOCSTRING_CJK

        return self._drop_theater_messages_locked(
            file_path,
            lanlan_name,
            lambda message: (
                is_theater_memory_message(message)
                and str(message_metadata(message).get("story_id") or "") == story_id
            ),
            expected_generation,
        )

    def _retract_theater_episode_locked(
        self, file_path, lanlan_name, story_id, session_id, archive_through_revision,
        expected_generation=None,
    ):
        """Drop only the episode capsule written for one archive range."""

        return self._drop_theater_messages_locked(
            file_path,
            lanlan_name,
            lambda message: is_retracted_theater_episode(
                message, story_id, session_id, archive_through_revision,
            ),
            expected_generation,
            restore_episode=(story_id, session_id, archive_through_revision),
        )

    def _drop_theater_messages_locked(
        self, file_path, lanlan_name, should_drop, expected_generation=None,
        restore_episode=None,
    ):
        """Remove matching messages from recent.json inside its file critical section."""

        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            status, history = self._load_history_unlocked(file_path, lanlan_name)
            if status == RECENT_READ_UNREADABLE:
                raise RuntimeError("theater_recent_history_unreadable")
            pending = recent_file.get_recent_pending_unlocked(file_path)
            current = list(history) + list(pending)
            retained = (
                restored_theater_history(current, *restore_episode)
                if restore_episode else [message for message in current if not should_drop(message)]
            )
            removed = sum(bool(should_drop(message)) for message in current)
            if not removed:
                self._cache_history_view(file_path, lanlan_name, history, pending)
                return 0
            recent_file.write_recent_payload_unlocked(
                file_path, messages_to_dict(retained),
            )
            recent_file.set_recent_pending_unlocked(file_path, [])
            self._set_pending_batches(lanlan_name, [], file_path)
            self._cache_history_view(file_path, lanlan_name, retained)
            return removed

    def _record_theater_retraction_locked(
        self, file_path, lanlan_name, entry, expected_generation=None,
    ):
        """Persist one retraction tombstone inside the recent.json critical section."""

        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            now = float(entry["retracted_at"])
            kept = []
            through_attempt = int(entry["through_attempt"])
            entries, forgotten = _load_theater_retraction_state_unlocked(file_path)
            for existing in entries:
                if existing.get("archive_request_id") == entry["archive_request_id"]:
                    previous = existing.get("through_attempt")
                    if isinstance(previous, int) and not isinstance(previous, bool):
                        through_attempt = max(through_attempt, previous)
                    continue
                if _is_fresh_tombstone(existing.get("retracted_at"), now):
                    kept.append(existing)
            kept.append({**entry, "through_attempt": through_attempt})
            _write_theater_retraction_state_unlocked(file_path, kept, forgotten)

    def _record_theater_story_forget_locked(
        self, file_path, lanlan_name, story_id, forgotten_at, forget_marker,
        expected_generation=None,
    ):
        """Persist a story-level forget tombstone inside the recent.json critical section.

        A repeated forget of the same story replaces its tombstone: the new
        marker supersedes the old one, and ``forgotten_at`` (only used for
        retention) restarts from this forget.
        """

        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            entries, forgotten = _load_theater_retraction_state_unlocked(file_path)
            kept = [
                existing for existing in forgotten
                if existing.get("story_id") != story_id
                and _is_fresh_tombstone(existing.get("forgotten_at"), forgotten_at)
            ]
            kept.append({
                "story_id": story_id,
                "forgotten_at": forgotten_at,
                "forget_marker": forget_marker,
            })
            runs = _load_theater_runs_unlocked(file_path)
            runs.pop(story_id, None)
            recent_file.write_recent_sidecar_unlocked(file_path, THEATER_RUNS_FILENAME, runs)
            _write_theater_retraction_state_unlocked(file_path, entries, kept)

    async def record_theater_story_forget(self, lanlan_name, story_id) -> str:
        """Durably drop every later-arriving write of this story issued before now.

        Story forget removes the story's capsules, but an archive request the
        theater timed out on may still be in flight and land afterwards. The
        tombstone is keyed by story rather than by archive attempt, so it also
        covers writes whose receipts the forget has already deleted. Returns the
        fresh forget marker: only writes that carry it are admitted for this
        story from now on, and the theater attaches it only to archive requests
        it issues after this forget completed.
        """

        normalized_story_id = str(story_id or "").strip()
        if not normalized_story_id:
            raise ValueError("theater_story_id_required")
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        await asyncio.to_thread(
            assert_cloudsave_writable,
            self._config_manager,
            operation="save",
            target=f"memory/{lanlan_name}/{THEATER_RETRACTIONS_FILENAME}",
        )
        forget_marker = secrets.token_hex(16)
        await _await_recent_mutation_to_completion(
            self._record_theater_story_forget_locked,
            file_path,
            lanlan_name,
            normalized_story_id,
            time.time(),
            forget_marker,
            admission_generation,
        )
        return forget_marker

    async def record_theater_retraction(
        self,
        lanlan_name,
        *,
        story_id,
        session_id,
        archive_through_revision,
        archive_request_id,
        archive_attempt,
    ):
        """Durably block late writes of the archive attempts a declined archive issued."""

        normalized_request_id = str(archive_request_id or "").strip()
        if not normalized_request_id:
            raise ValueError("theater_archive_request_id_required")
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        await asyncio.to_thread(
            assert_cloudsave_writable,
            self._config_manager,
            operation="save",
            target=f"memory/{lanlan_name}/{THEATER_RETRACTIONS_FILENAME}",
        )
        entry = {
            "archive_request_id": normalized_request_id,
            "through_attempt": max(0, int(archive_attempt or 0)),
            "story_id": str(story_id or ""),
            "session_id": str(session_id or ""),
            "archive_through_revision": int(archive_through_revision),
            "retracted_at": time.time(),
        }
        await _await_recent_mutation_to_completion(
            self._record_theater_retraction_locked,
            file_path,
            lanlan_name,
            entry,
            admission_generation,
        )

    async def retract_theater_episode(
        self, story_id, session_id, archive_through_revision, lanlan_name,
    ):
        """Idempotently drop the episode capsule a declined theater archive wrote."""

        normalized_story_id = str(story_id or "").strip()
        normalized_session_id = str(session_id or "").strip()
        if not normalized_story_id or not normalized_session_id:
            raise ValueError("theater_episode_identity_required")
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        await asyncio.to_thread(
            assert_cloudsave_writable,
            self._config_manager,
            operation="delete",
            target=f"memory/{lanlan_name}/recent.json",
        )
        return await _await_recent_mutation_to_completion(
            self._retract_theater_episode_locked,
            file_path,
            lanlan_name,
            normalized_story_id,
            normalized_session_id,
            int(archive_through_revision),
            admission_generation,
        )

    async def forget_theater_story(self, story_id, lanlan_name):
        """Idempotently remove one script's recent theater memory."""  # noqa: DOCSTRING_CJK

        normalized_story_id = str(story_id or "").strip()
        if not normalized_story_id:
            raise ValueError("theater_story_id_required")
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        await asyncio.to_thread(
            assert_cloudsave_writable,
            self._config_manager,
            operation="delete",
            target=f"memory/{lanlan_name}/recent.json",
        )
        return await _await_recent_mutation_to_completion(
            self._forget_theater_story_locked,
            file_path,
            lanlan_name,
            normalized_story_id,
            admission_generation,
        )

    def _splice_compressed_locked(
        self,
        file_path,
        lanlan_name,
        snapshot,
        memo,
        expected_generation=None,
        preserved_messages=(),
    ) -> str:
        """Replace the compressed head with ``memo`` and persist, as one critical section.

        Re-reads the file inside the lock. The compression LLM ran for seconds to
        minutes; before this refactor the splice was computed from the in-memory
        view taken *before* the LLM call, so every batch persisted during that
        window was overwritten wholesale.
        """
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            status, current = self._load_history_unlocked(file_path, lanlan_name)
            if status == RECENT_READ_UNREADABLE:
                logger.warning(f"[RecentHistory] {lanlan_name} 压缩结果合并时读盘失败，放弃本轮合并")
                return 'failed'
            if not current:
                # Cleared by /new_dialog / user UI: reinserting memo would resurrect
                # deleted content. Same moot criterion as merge_backup_memo.
                logger.info(f"[RecentHistory] {lanlan_name} 压缩期间历史已被清空，丢弃本轮摘要")
                return 'moot'
            capacity, cutoff_idx = _compute_review_capacity(snapshot, current)
            if (
                cutoff_idx is not None
                and capacity == len(snapshot)
                and (cutoff_idx - capacity + 1) == 0
            ):
                # Ordinary summaries must not absorb theater capsules, losing source isolation and playthrough counts.
                new_history = [memo] + list(preserved_messages) + current[cutoff_idx + 1:]
            else:
                # Fail closed unless snapshot is provably current's complete prefix.
                # Count-based fallback with short-fingerprint collisions could cut off tail excluded from compression.
                logger.info(f"[RecentHistory] {lanlan_name} 压缩锚点失配，丢弃本轮摘要")
                return 'moot'
            try:
                recent_file.write_recent_payload_unlocked(
                    file_path, messages_to_dict(new_history),
                )
            except Exception as e:
                logger.error(f"[RecentHistory] {lanlan_name} 压缩结果落盘失败: {e}", exc_info=True)
                return 'failed'
            pending = recent_file.get_recent_pending_unlocked(file_path)
            self._cache_history_view(
                file_path, lanlan_name, new_history, pending,
            )
            return 'merged'

    def _append_keyed_locked(self, file_path, lanlan_name, new_messages, key, expected_generation=None) -> str:
        """Strict keyed append (one critical section). Returns ``'present'``, ``'retired'`` or ``'written'``.

        Unlike :meth:`_append_and_persist_locked` nothing is parked in the
        pending batches: an unreadable file or a failed write raises, so the
        keyed ``/cache`` caller leaves its key ``pending`` and answers 503
        instead of reporting a write that never reached the disk.
        """
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            presence, history, pending = self._keyed_presence_unlocked(file_path, lanlan_name, key)
            if presence != 'absent':
                return presence
            merged = list(history) + list(pending) + list(new_messages)
            recent_file.write_recent_payload_unlocked(file_path, messages_to_dict(merged))
            # Previously pending batches persisted together with this write.
            self._set_pending_batches(lanlan_name, [], file_path)
            self._cache_history_view(file_path, lanlan_name, merged)
            return 'written'

    def _keyed_presence_unlocked(self, file_path, lanlan_name, key) -> tuple[str, list, list]:
        """``('present'|'retired'|'absent', history, pending)`` of ``key``. The caller holds the file lock.

        The single place both :meth:`keyed_state` and :meth:`append_keyed`
        decide from, so the time-index step and the recent step never judge
        "already written" differently. Raises when the file or the retired-key
        sidecar cannot be read (never guessed as absent).
        """
        status, history = self._load_history_unlocked(file_path, lanlan_name)
        if status == RECENT_READ_UNREADABLE:
            raise RuntimeError(f"recent history of {lanlan_name!r} unreadable")
        pending = recent_file.get_recent_pending_unlocked(file_path)
        if key in recent_file.keyed_entry_keys(messages_to_dict(list(history) + list(pending))):
            return 'present', history, pending
        # Unreadable sidecar means no guessing: raise for keyed-write 503, retain pending for retry.
        if key in recent_file.read_retired_keys_unlocked(file_path):
            return 'retired', history, pending
        return 'absent', history, pending

    def _keyed_state_locked(self, file_path, lanlan_name, key, expected_generation=None) -> str:
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            return self._keyed_presence_unlocked(file_path, lanlan_name, key)[0]

    async def keyed_state(self, lanlan_name, key: str) -> str:
        """``'present'`` / ``'retired'`` / ``'absent'`` for ``key`` in this character's recent file; never writes.

        Raises when the file or its retired-key sidecar cannot be read.
        """
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        return await asyncio.to_thread(
            self._keyed_state_locked, file_path, lanlan_name, key, admission_generation,
        )

    async def append_keyed(self, new_messages, lanlan_name, key: str) -> str:
        """Append a keyed batch unless ``key`` already is (or was) in this character's recent file.

        Returns ``'present'`` (an entry carries ``key``), ``'retired'`` (an
        entry carrying it left the file, see
        :func:`utils.recent_file.retire_removed_keyed_entries`) or
        ``'written'``. Never compresses. Raises when the file cannot be read
        or written: the caller must not take that as written.
        """
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        await asyncio.to_thread(
            assert_cloudsave_writable,
            self._config_manager,
            operation="save",
            target=f"memory/{lanlan_name}/recent.json",
        )
        return await _await_recent_mutation_to_completion(
            self._append_keyed_locked,
            file_path,
            lanlan_name,
            list(new_messages),
            key,
            admission_generation,
        )

    async def update_history(self, new_messages, lanlan_name, detailed=False, compress=True, on_compress_done=None):
        # Ordinary writes carry no visit-diary idempotency key (only append_keyed stamps it server-side); strip caller-supplied keys.
        recent_file.strip_idempotency_metadata(new_messages)
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        try:
            _, _, _, _, _, _, _, _, recent_log = await self._config_manager.aget_character_data()
            self.log_file_path = recent_log
        except Exception as e:
            logger.error(f"获取角色配置失败: {e}")

        # Cloud-save fence remains outside try: MaintenanceModeError still propagates directly to caller.
        await asyncio.to_thread(
            assert_cloudsave_writable,
            self._config_manager,
            operation="save",
            target=f"memory/{lanlan_name}/recent.json",
        )

        try:
            # CS-1: read disk + merge pending batches + append + persist in one critical section.
            # Persist uncompressed state before expensive compress_history: LLM takes seconds to
            # tens of seconds; process crash/task cancellation during it would lose new_messages.
            history, persisted = await _await_recent_mutation_to_completion(
                self._append_and_persist_locked,
                file_path,
                lanlan_name,
                list(new_messages),
                admission_generation,
            )
            logger.debug(
                f"[RecentHistory] {lanlan_name} 添加了 {len(new_messages)} 条新消息，"
                f"当前共 {len(history)} 条（已落盘={persisted}）"
            )
            if not persisted:
                # Like pre-refactor, first persistence failure skips compression (previously exception skipped
                # the whole section). Batch is in _pending; next update_history writes it together
                # with disk contents.
                return

            # Preserve theater capsules unchanged through compression; exclude from trigger count and retained-tail count,
            # or many capsules keep compressed history above threshold, re-compressing and summarizing memo every settle.
            # Without theater messages, exactly equivalent to history[:-max_history_length+1].
            ordinary_indices = [
                index
                for index, message in enumerate(history)
                if not is_theater_memory_message(message)
            ]
            if compress and len(ordinary_indices) > self.compress_threshold:
                compressed_ordinary = len(
                    ordinary_indices[:-self.max_history_length+1]
                )
                cutoff = (
                    ordinary_indices[compressed_ordinary]
                    if compressed_ordinary < len(ordinary_indices)
                    else len(history)
                )
                to_compress = history[:cutoff]
                snapshot = list(to_compress)
                preserved_theater = [
                    message
                    for message in snapshot
                    if is_theater_memory_message(message)
                ]
                compression_input = [
                    message
                    for message in snapshot
                    if not is_theater_memory_message(message)
                ]
                if not compression_input or (
                    len(compression_input) == 1
                    and isinstance(compression_input[0], SystemMessage)
                ):
                    # Compressible prefix contains only theater capsules (or existing memo); another summary call only
                    # rewrites memo repeatedly. Use capsule-preserving hard-cap trimming instead.
                    # Unreachable with the shipped constants (threshold 20, keep 10:
                    # the head holds >= threshold - keep + 2 = 12 ordinary messages),
                    # but reachable whenever max_history_length > compress_threshold
                    # (the head shrinks to at most one message, e.g. the memo) or
                    # max_history_length == 1 (the
                    # [:-max_history_length+1] slice is [:0], so the head is empty).
                    await self.enforce_hard_cap(
                        lanlan_name,
                        admission_generation,
                    )
                    return
                # Compression LLM runs **outside lock**: seconds to tens of seconds inside critical section would
                # also block /cache.
                compressed_result = await self.compress_history(
                    compression_input,
                    lanlan_name,
                    detailed,
                )
                if compressed_result is None:
                    logger.warning(
                        f"[RecentHistory] {lanlan_name} 摘要失败，跳过本轮压缩以保留原始历史"
                    )
                    # Best effort: notify upper layer to launch protected background compression after primary failure.
                    # Do **not** hard-trim here: otherwise any transient failure above cap
                    # immediately discards oldest original, while backup uses pre-trim snapshot → mismatched merge
                    # moot → dialogue permanently lost without summary. Trim only after background best effort fails too
                    # (memory_server._run_backup_compress / dead-letter branch),
                    # allowing temporary failures to recover into retained summaries.
                    #
                    # Warning: this callback **must** remain outside every critical section; dead-letter synchronously
                    # calls enforce_hard_cap, which needs same file lock, and threading.Lock
                    # is not reentrant. Moving inside either section deadlocks the worker indefinitely.
                    # Background commit locates using full snapshot, sending only ordinary messages to summary model.
                    await self._notify_compress_done(
                        on_compress_done,
                        lanlan_name,
                        snapshot,
                        False,
                        detailed,
                        admission_generation,
                    )
                else:
                    # CS-2: read disk + locate + splice + persist in one critical section.
                    splice_status = await _await_recent_mutation_to_completion(
                        self._splice_compressed_locked,
                        file_path, lanlan_name, snapshot, compressed_result[0],
                        admission_generation, preserved_theater,
                    )
                    # merged / moot both need no repeat background summary; only read/write failures trigger backup.
                    await self._notify_compress_done(
                        on_compress_done,
                        lanlan_name,
                        snapshot,
                        splice_status != 'failed',
                        detailed,
                        admission_generation,
                    )
        except (MaintenanceModeError, recent_file.RecentFileDeletedError):
            raise
        except Exception as e:
            logger.error(f"[RecentHistory] 更新历史记录时出错: {e}", exc_info=True)


    # ── Past-block update metadata (prevent days-old events repeatedly resurfacing in summary;
    #    see config.RECENT_SUMMARY_STALE_HOURS comments).
    # Anchor is not last summary time: summary runs every compression, which would keep
    # stale hints perpetually one hour after latest compression, never establishing a stable every-N-hours
    # past-block update. Record when hint actually injected instead,
    # meaning when LLM actually updated past block; only those turns advance anchor.
    def _summary_meta_path(self, lanlan_name: str) -> str:
        """Side meta file per character, co-located with recent.json
        ({"last_past_block_update_at": ISO}).

        Prefers the directory of ``self.log_file_path[lanlan_name]`` — the
        actual path source of every recent.json read/write in this class (item
        9 of the character_data tuple). If the user's config moved a
        character's recent.json outside memory_dir, the meta file still sits in
        the same directory instead of wandering off (CodeRabbit review catch on
        PR #1316). Before update_history has run, log_file_path may be empty —
        in that case fall back to the memory_dir-based derivation.
        """
        recent_path = (self.log_file_path or {}).get(lanlan_name)
        if recent_path:
            return os.path.join(os.path.dirname(recent_path), 'recent_meta.json')
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, lanlan_name),
            'recent_meta.json',
        )

    async def _aread_last_past_block_update_at(self, lanlan_name: str) -> datetime | None:
        path = self._summary_meta_path(lanlan_name)
        if not await asyncio.to_thread(os.path.exists, path):
            return None
        try:
            def _read():
                with open(path, encoding='utf-8') as f:
                    return f.read()
            raw = await asyncio.to_thread(_read)
            data = robust_json_loads(raw)
            if not isinstance(data, dict):
                return None
            # Support legacy last_summary_at key from early in-progress PR #1316 versions.
            # OR fallback: merged version never writes last_summary_at; compatibility serves only
            # developers who ran intermediate commits locally. Next metadata write uses new key
            # and replaces old file.
            ts = data.get('last_past_block_update_at') or data.get('last_summary_at')
            if not ts:
                return None
            return datetime.fromisoformat(ts)
        except Exception:
            return None

    async def _awrite_last_past_block_update_at(self, lanlan_name: str) -> None:
        path = self._summary_meta_path(lanlan_name)
        try:
            await asyncio.to_thread(os.makedirs, os.path.dirname(path), exist_ok=True)
            await atomic_write_json_async(
                path,
                {'last_past_block_update_at': datetime.now().isoformat()},
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            logger.debug(f"[RecentHistory] {lanlan_name}: 写 recent_meta 失败: {e}")

    def _render_message_content(self, msg):
        from utils.tokenize import truncate_head_tail_tokens

        content = getattr(msg, 'content', '')
        half_cap = self._summary_message_half_cap()
        if isinstance(content, str):
            return truncate_head_tail_tokens(content, half_cap, half_cap)
        parts = []
        try:
            for item in content:
                if isinstance(item, dict):
                    parts.append(item.get('text', f"|{item.get('type', '')}|"))
                else:
                    parts.append(str(item))
        except Exception:
            parts = [str(content)]
        return truncate_head_tail_tokens(
            "\n".join(parts),
            half_cap,
            half_cap,
        )

    @staticmethod
    def _summary_message_half_cap():
        return RECENT_PER_MESSAGE_MAX_TOKENS // 2

    def _summary_prompt_locale_text(self, messages):
        from utils.tokenize import truncate_head_tail_tokens

        half_cap = self._summary_message_half_cap()
        user_messages = [
            msg for msg in messages
            if _message_prompt_role(msg) in {'human', 'user'}
        ]
        locale_messages = user_messages or messages
        return "\n".join(
            truncate_head_tail_tokens(
                _message_locale_text(msg),
                half_cap,
                half_cap,
            )
            for msg in locale_messages
        )

    def _render_messages_to_text(self, messages, lanlan_name):
        """Render messages for summary LLM with head/tail-preserving truncation and role prefixes.  # noqa: DOCSTRING_CJK

        Messages exceeding RECENT_PER_MESSAGE_MAX_TOKENS retain both ends
        (head=tail=half token budget). Long user pastes / occasional AI essays trigger it; each end
        preserves greeting/question and concluding summary/request, dropping middle.
        """
        name_mapping = self.name_mapping.copy()
        name_mapping['ai'] = lanlan_name
        lines = []
        for msg in messages:
            role = name_mapping.get(getattr(msg, 'type', ''), getattr(msg, 'type', ''))
            lines.append(f"{role} | {self._render_message_content(msg)}")
        return "\n".join(lines)

    def _build_summary_prompt(self, messages_text, detailed, *, locale_text=None):
        """Build Stage-1 summary prompt without stale-hint prefix, shared by single compression and segmented map.

        ``{MASTER_NAME}`` is a literal placeholder for master's real name in negative-feedback retention
        section, coexisting with existing ``%s``. Warning: replace master_name **last**:
        user-controlled ``%`` would break earlier ``%`` formatting; embedded
        ``%s`` would be replaced again by earlier ``.replace(\"%s\", ...)`` (codex P2).
        """
        lang = _detect_recent_prompt_language(
            locale_text if locale_text is not None else messages_text,
        )
        master_name = self.name_mapping['human']
        if not detailed:
            return (
                get_recent_history_manager_prompt(lang)
                .replace("%s", messages_text)
                .replace("{MASTER_NAME}", master_name)
            )
        return (
            (get_detailed_recent_history_manager_prompt(lang) % messages_text)
            .replace("{MASTER_NAME}", master_name)
        )

    async def _invoke_summary_llm(self, prompt):
        """Call summary LLM (3 retries + exponential network/429 backoff); return summary string on success,
        None on failure. Excludes further_compress / memo wrapping / stale anchors, handled by
        compress_history after receiving summary. Single Stage-1 compression and segmented map
        share this method.
        """
        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # Try parsing response content as JSON.
                set_call_type("memory_compression")
                llm = self._get_llm()
                try:
                    response_content = (await llm.ainvoke(prompt)).content  # noqa: LLM_INPUT_BUDGET  # compression prompt built from RECENT_PER_MESSAGE_MAX_TOKENS-capped history.
                finally:
                    await llm.aclose()
                response_content = str(response_content).strip()
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()
                summary_json = robust_json_loads(response_content)
                # Extract dialogue summary from JSON dict using key agreed in prompt template.
                if 'summary' in summary_json:
                    raw_summary = summary_json['summary']
                    # Qwen sometimes returns list/dict instead of string; coerce before use,
                    # or acount_tokens raises TypeError and crashes compression.
                    summary = (
                        raw_summary if isinstance(raw_summary, str)
                        else json.dumps(raw_summary, ensure_ascii=False)
                    )
                    _safe_print(f"💗摘要结果：{summary}")
                    return summary
                else:
                    _safe_print('💥 摘要failed: ', response_content)
                    retries += 1
            except openai_retry_error_types() as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                retries += 1
                if retries >= max_retries:
                    _safe_print(f'❌ 摘要模型失败，已达到最大重试次数: {e}')
                    break
                # Exponential backoff: 1, 2, 4 seconds.
                wait_time = 2 ** (retries - 1)
                _safe_print(f'⚠️ 遇到网络或429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
            except Exception as e:
                _safe_print(f'❌ 摘要模型失败：{e}')
                # Retry parsing failures.
                retries += 1
        return None

    def _split_messages_by_budget(self, messages, lanlan_name):
        """Split messages by rendered cumulative tokens, each ≤ RECENT_COMPRESS_INPUT_BUDGET_TOKENS.
        Never split individual messages, already truncated per message to ≤500 tokens."""
        from utils.tokenize import count_tokens
        budget = RECENT_COMPRESS_INPUT_BUDGET_TOKENS
        chunks, cur, cur_tok = [], [], 0
        for msg in messages:
            t = count_tokens(self._render_messages_to_text([msg], lanlan_name))
            if cur and cur_tok + t > budget:
                chunks.append(cur)
                cur, cur_tok = [], 0
            cur.append(msg)
            cur_tok += t
        if cur:
            chunks.append(cur)
        return chunks

    def _split_texts_by_budget(self, texts):
        """Batch strings by cumulative tokens so each concatenation stays within budget (reduce stage)."""
        from utils.tokenize import count_tokens
        budget = RECENT_COMPRESS_INPUT_BUDGET_TOKENS
        batches, cur, cur_tok = [], [], 0
        for txt in texts:
            tok = count_tokens(txt)
            if cur and cur_tok + tok > budget:
                batches.append(cur)
                cur, cur_tok = [], 0
            cur.append(txt)
            cur_tok += tok
        if cur:
            batches.append(cur)
        return batches

    async def _segmented_compress(self, messages, lanlan_name, detailed):
        """Segmented map-reduce for oversized input: split messages, summarize each, repeatedly
        reduce until concatenation fits budget; return for compress_history's final summary.
        Any segment LLM failure returns None, prompting caller to skip compression."""
        chunks = self._split_messages_by_budget(messages, lanlan_name)
        partials = []
        for chunk in chunks:
            s = await self._invoke_summary_llm(
                self._build_summary_prompt(
                    self._render_messages_to_text(chunk, lanlan_name),
                    detailed,
                    locale_text=self._summary_prompt_locale_text(chunk),
                )
            )
            if s is None:
                return None
            partials.append(s)
        logger.info(
            f"[RecentHistory] {lanlan_name} 分段压缩：{len(messages)} 条原始消息 → {len(chunks)} 段中间摘要"
        )
        # Combined intermediate summaries still over budget → recursively batch/reduce with bounded depth for extremes.
        depth = 0
        while (
            len(partials) > 1
            and await acount_tokens("\n\n".join(partials)) > RECENT_COMPRESS_INPUT_BUDGET_TOKENS
            and depth < 3
        ):
            batches = self._split_texts_by_budget(partials)
            if len(batches) >= len(partials):
                break  # Cannot reduce further (single segment over budget); leave fallback to main path.
            new_partials = []
            for batch in batches:
                s = await self._invoke_summary_llm(
                    self._build_summary_prompt("\n\n".join(batch), detailed)
                )
                if s is None:
                    return None
                new_partials.append(s)
            partials = new_partials
            depth += 1
        merged = "\n\n".join(partials)
        # If reduction cannot shrink / depth exhausted, merged may remain oversized; truncate to budget,
        # bounding final-summary input (best effort, dropping tail).
        if await acount_tokens(merged) > RECENT_COMPRESS_INPUT_BUDGET_TOKENS:
            from utils.tokenize import atruncate_to_tokens
            merged = await atruncate_to_tokens(merged, RECENT_COMPRESS_INPUT_BUDGET_TOKENS)
        return merged

    # detailed: retain as much detail as possible.
    async def compress_history(self, messages, lanlan_name, detailed=False):
        # The summary replaces these messages and is stored as a system memo
        # the screen-history guard does not rewrite: it is made from the
        # replies without source labels, but with every comment kept, so no
        # content the user saw is lost to the summary.
        messages = strip_screen_labels(list(messages))
        messages_text = self._render_messages_to_text(messages, lanlan_name)
        locale_text = self._summary_prompt_locale_text(messages)
        # Oversized input (growing uncompressible backlog) → segmented map-reduce first to reduce
        # per-call input and avoid timeout. Normal input bypasses this path.
        if await acount_tokens(messages_text) > RECENT_COMPRESS_INPUT_BUDGET_TOKENS:
            reduced = await self._segmented_compress(messages, lanlan_name, detailed)
            if reduced is None:
                logger.warning(f"[RecentHistory] {lanlan_name} 分段压缩失败，跳过本轮压缩")
                return None
            messages_text = reduced

        lang = _detect_recent_prompt_language(locale_text)
        prompt = self._build_summary_prompt(
            messages_text,
            detailed,
            locale_text=locale_text,
        )

        # Past-block time decay: if time since last actual past-block update exceeds
        # RECENT_SUMMARY_STALE_HOURS, prepend hint asking LLM to move clearly
        # stale content into summary's older-events tail. Advance anchor only when hint actually
        # injected, establishing every-N-hours cadence instead of every compression.
        # Affects current summary text only, never persists to reflection/persona.
        stale_hint_injected = False
        first_time_baseline = False
        try:
            last_past_update = await self._aread_last_past_block_update_at(lanlan_name)
            if last_past_update is None:
                # First compression for character: establish baseline anchor without injecting hint this turn.
                first_time_baseline = True
            else:
                gap_hours = (datetime.now() - last_past_update).total_seconds() / 3600.0
                if gap_hours >= RECENT_SUMMARY_STALE_HOURS:
                    hint = get_summary_stale_hint(lang, gap_hours)
                    prompt = hint + "\n\n" + prompt
                    stale_hint_injected = True
        except Exception as e:
            # Time-decay hint is best effort; failure cannot block summary flow.
            logger.debug(f"[RecentHistory] {lanlan_name}: stale hint 注入失败: {e}")

        # Joint Stage-1 + Stage-2 retries: previously further_compress failure retried entire
        # stage-1 (random LLM output might produce summary ≤MAX next time,
        # avoiding stage-2). Use bounded counter loop to reproduce behavior while avoiding original `continue`
        # potentially looping forever without count increments.
        summary = None
        for _ in range(3):
            s = await self._invoke_summary_llm(prompt)
            if s is None:
                # Repeated stage-1 failure: never generate empty memo, protecting existing memo/uncompressed originals.
                logger.warning(f"[RecentHistory] {lanlan_name} 摘要连续失败，跳过本轮压缩")
                return None
            if await acount_tokens(s) <= MAX_SUMMARY_TOKENS:
                summary = s
                break
            reduced = await self.further_compress(s)
            if reduced is not None:
                summary = reduced if isinstance(reduced, str) else json.dumps(reduced, ensure_ascii=False)
                break
            # Stage-2 failure → retry stage-1 (up to 3 rounds).
        if summary is None:
            logger.warning(f"[RecentHistory] {lanlan_name} 二次压缩连续失败，跳过本轮压缩")
            return None

        # Advance past-block update anchor best effort: first compression establishes baseline;
        # injected stale hint means LLM actually updated past block this turn. Ordinary compression leaves anchor,
        # maintaining stable every-N-hours hint cadence.
        if first_time_baseline or stale_hint_injected:
            await self._awrite_last_past_block_update_at(lanlan_name)

        # Second return value for upper cache must match summary used by memo_text;
        # raw summary previously made visible memo stage-2 but cached text stage-1.
        from config.prompts.prompts_sys import _loc, MEMORY_MEMO_WITH_SUMMARY
        memo_text = _loc(
            MEMORY_MEMO_WITH_SUMMARY,
            lang,
        ).format(summary=summary)
        return SystemMessage(content=memo_text), summary

    async def _notify_compress_done(
        self, callback, lanlan_name, snapshot, ok, detailed, admission_generation,
    ):
        """Invoke the best-effort compression callback without blocking the main flow.

        memory_server injects the callback: ok=False starts background
        compression, while ok=True cancels any running background task.
        """
        if callback is None:
            return
        try:
            await callback(
                lanlan_name,
                snapshot,
                ok,
                detailed,
                admission_generation,
            )
        except Exception as e:
            logger.debug(f"[RecentHistory] {lanlan_name} on_compress_done({ok}) 回调异常: {e}")

    async def enforce_hard_cap(self, lanlan_name, expected_generation=None):
        """Keep recent-history prompt size bounded as a final fallback.

        When history still exceeds RECENT_HARD_CAP_TOKENS after best-effort
        background compression, discard the oldest uncompressed dialogue text
        while preserving the first memo, when present, and at least the latest
        max_history_length entries.
        """
        if expected_generation is None:
            file_path, admission_generation = self._capture_recent_operation_admission(
                lanlan_name,
            )
        else:
            file_path = expected_generation[0]
            admission_generation = expected_generation
        from utils.tokenize import count_tokens

        def _raw_tokens(msgs):
            # Hard cap counts **actual prompt-injected** tokens, not _render_messages_to_text,
            # which truncates each compression-input message to ≤RECENT_PER_MESSAGE_MAX_TOKENS,
            # severely undercounting long messages and defeating cap on lengthy originals. Count raw content tokens.
            total = 0
            for m in msgs:
                c = getattr(m, 'content', '')
                if isinstance(c, list):
                    c = ' '.join(
                        p.get('text', '') if isinstance(p, dict) else str(p) for p in c
                    )
                elif not isinstance(c, str):
                    c = str(c)
                total += count_tokens(c)
            return total

        def _trim(history):
            # Theater eviction belongs to /cache's locked recent/index commit.
            # Background chat trimming cannot remove capsules without updating
            # their time index. Prompt rendering independently bounds old data.
            ordinary = [message for message in history if not is_theater_memory_message(message)]
            if _raw_tokens(ordinary) <= RECENT_HARD_CAP_TOKENS:
                return None
            # Theater capsules have independent source/playthrough semantics; ordinary-chat hard trimming cannot absorb them.
            # Same predicate as compression and review: every theater memory
            # message is outside the ordinary body and can never be the memo head.
            theater_indices = {
                index
                for index, message in enumerate(history)
                if is_theater_memory_message(message)
            }
            ordinary_indices = [
                index
                for index in range(len(history))
                if index not in theater_indices
            ]
            # Retain first ordinary-history entry if memo; drop only oldest ordinary original text.
            head_indices = (
                {ordinary_indices[0]}
                if ordinary_indices
                and isinstance(history[ordinary_indices[0]], SystemMessage)
                else set()
            )
            body_indices = ordinary_indices[len(head_indices):]
            kept_indices = set(head_indices) | theater_indices
            # Theater has its own allowance and never consumes the chat budget.
            kept_tok = _raw_tokens([history[index] for index in head_indices])
            kept_body_count = 0
            for index in reversed(body_indices):
                msg = history[index]
                mtok = _raw_tokens([msg])
                if (
                    kept_body_count
                    and kept_tok + mtok > RECENT_HARD_CAP_TOKENS
                    and kept_body_count >= self.max_history_length
                ):
                    break
                kept_indices.add(index)
                kept_body_count += 1
                kept_tok += mtok
            # Restore by original indices so theater capsules do not move to history end and change chronology.
            return [
                message
                for index, message in enumerate(history)
                if index in kept_indices
            ]

        # Tokenize always outside lock; commit uses full disk snapshot CAS. If new batches arrive, reread
        # and recalculate at most once, never overwriting concurrent append with stale results.
        for _ in range(2):
            try:
                history = await asyncio.to_thread(
                    self._read_history_locked,
                    file_path,
                    lanlan_name,
                    admission_generation,
                )
            except recent_file.RecentFileDeletedError:
                return
            if not history:
                return
            new_history = await asyncio.to_thread(_trim, list(history))
            if new_history is None or len(new_history) >= len(history):
                return
            await asyncio.to_thread(
                assert_cloudsave_writable,
                self._config_manager, operation="save",
                target=f"memory/{lanlan_name}/recent.json",
            )
            try:
                commit_status = await _await_recent_mutation_to_completion(
                    self._commit_hard_cap_locked,
                    file_path,
                    lanlan_name,
                    history,
                    new_history,
                    admission_generation,
                )
            except recent_file.RecentFileDeletedError:
                return
            if commit_status == 'committed':
                dropped = len(history) - len(new_history)
                logger.warning(
                    f"[RecentHistory] {lanlan_name} 历史超硬上限 {RECENT_HARD_CAP_TOKENS} token，"
                    f"丢弃最旧 {dropped} 条未压缩原文以保证有界"
                )
                return
            if commit_status == 'failed':
                return
        logger.info(f"[RecentHistory] {lanlan_name} 硬上限裁剪期间历史持续变化，跳过本轮")

    async def merge_backup_memo(
        self, lanlan_name, snapshot, memo, expected_generation=None,
        preserved_messages=(),
    ):
        """Merge a background compression memo when its disk snapshot still matches.

        The disk re-read, snapshot match, splice, and write form one file critical
        section. A changed or cleared head makes the result moot instead of
        resurrecting stale content.
        """
        if expected_generation is None:
            file_path, admission_generation = self._capture_recent_operation_admission(
                lanlan_name,
            )
        else:
            file_path = expected_generation[0]
            admission_generation = expected_generation
        try:
            current = await asyncio.to_thread(
                self._read_history_locked,
                file_path,
                lanlan_name,
                admission_generation,
            )
        except recent_file.RecentFileDeletedError:
            return 'moot'
        if not current or not snapshot:
            return 'moot'
        capacity, cutoff_idx = _compute_review_capacity(snapshot, current)
        if cutoff_idx is None or capacity != len(snapshot) or (cutoff_idx - capacity + 1) != 0:
            return 'moot'
        try:
            await asyncio.to_thread(
                assert_cloudsave_writable,
                self._config_manager, operation="save",
                target=f"memory/{lanlan_name}/recent.json",
            )
            status, before_count, after_count = await _await_recent_mutation_to_completion(
                self._merge_backup_memo_locked,
                file_path,
                lanlan_name,
                snapshot,
                memo,
                admission_generation,
                preserved_messages,
            )
        except recent_file.RecentFileDeletedError:
            return 'moot'
        except MaintenanceModeError:
            raise
        except Exception as e:
            logger.error(f"[RecentHistory] {lanlan_name} 后台压缩合并落盘失败: {e}", exc_info=True)
            return 'failed'
        if status != 'merged':
            return status
        logger.info(
            f"[RecentHistory] {lanlan_name} 后台压缩合并完成：history {before_count}→{after_count}"
        )
        return 'merged'

    async def further_compress(self, initial_summary):
        # Stage-2 output cap: RECENT_SUMMARY_MAX_TOKENS + 100 allowance = 1100 tokens.
        # Prompt asks 700 characters/words: 700 CJK characters ≈1050 tokens (×1.5),
        # 700 English words ≈933 tokens, both below 1100 cap.
        # Still prevent essays; if cap reached, sentence-ending backtracking below preserves semantic boundaries.
        from utils.tokenize import truncate_to_last_sentence_end
        stage2_cap = RECENT_SUMMARY_MAX_TOKENS + 100
        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # Try parsing response content as JSON.
                set_call_type("memory_compression")
                llm = self._get_llm()
                try:
                    response_content = (await llm.ainvoke(
                        # Codex P2: interpolate % before .replace, or % in master_name crashes.
                        (
                            get_further_summarize_prompt(
                                _detect_recent_prompt_language(initial_summary)
                            )
                            % initial_summary
                        )
                        .replace("{MASTER_NAME}", self.name_mapping['human']),
                        max_completion_tokens=stage2_cap,
                    )).content
                finally:
                    await llm.aclose()
                response_content = str(response_content).strip()
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()
                summary_json = robust_json_loads(response_content)
                # Extract dialogue summary from JSON dict using key agreed in prompt template.
                if 'summary' in summary_json:
                    raw_summary = summary_json['summary']
                    # Normalize Stage-2 consistently with Stage-1 ([memory/recent.py:382](memory/recent.py:382)):
                    # use json.dumps(ensure_ascii=False) for nonstrings rather than
                    # str(), avoiding Python single-quoted repr for lists/dicts and drift in persisted
                    # text/token measurement.
                    summary_text = (
                        raw_summary.strip() if isinstance(raw_summary, str)
                        else json.dumps(raw_summary, ensure_ascii=False)
                    )
                    # stage2_cap reached → LLM may stop mid-sentence (e.g. comma / phrase).
                    # Backtrack to last sentence-ending punctuation (. ! ? 。！？… \n), preserving complete
                    # semantic boundaries of persisted summary. Without any ending punctuation (extremely short text),
                    # truncate_to_last_sentence_end returns ""; fall back to original text
                    # rather than losing summary entirely.
                    sane = truncate_to_last_sentence_end(summary_text)
                    if not sane:
                        sane = summary_text
                    _safe_print(f"💗第二轮摘要结果：{sane}")
                    return sane
                else:
                    _safe_print('💥 第二轮摘要failed: ', response_content)
                    retries += 1
            except openai_retry_error_types() as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                retries += 1
                if retries >= max_retries:
                    _safe_print(f'❌ 第二轮摘要模型失败，已达到最大重试次数: {e}')
                    return None
                # Exponential backoff: 1, 2, 4 seconds.
                wait_time = 2 ** (retries - 1)
                _safe_print(f'⚠️ 遇到网络或429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
            except Exception as e:
                _safe_print(f'❌ 第二轮摘要模型失败：{e}')
                retries += 1
        return None

    def get_recent_history(self, lanlan_name):
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        try:
            _, _, _, _, _, _, _, _, recent_log = self._config_manager.get_character_data()
            self.log_file_path = recent_log
        except Exception as e:
            logger.error(f"获取角色配置失败: {e}")

        # Readers must lock too: on Windows, bare open() suffices to make concurrent os.replace raise
        # PermissionError; locking only writers misses over half the failures.
        try:
            return self._read_history_locked(
                file_path, lanlan_name, admission_generation,
            )
        except recent_file.RecentFileDeletedError:
            return []

    async def aget_recent_history(self, lanlan_name, *, include_admission=False):
        file_path, admission_generation = self._capture_recent_operation_admission(
            lanlan_name,
        )
        try:
            _, _, _, _, _, _, _, _, recent_log = await self._config_manager.aget_character_data()
            self.log_file_path = recent_log
        except Exception as e:
            logger.error(f"获取角色配置失败: {e}")

        try:
            history = await asyncio.to_thread(
                self._read_history_locked,
                file_path,
                lanlan_name,
                admission_generation,
            )
        except recent_file.RecentFileDeletedError:
            history = []
        if include_admission:
            return history, admission_generation
        return history

    def _commit_review_locked(
        self, file_path, lanlan_name, snapshot, corrected_messages,
        expected_generation=None,
    ) -> tuple[str, list[dict] | None, str]:
        """Locate the review window, splice the corrected messages in and persist.

        One critical section, run in a worker thread: the current history is
        re-read inside the lock so that batches persisted while the review LLM
        was running are not overwritten.

        Returns ``(status, new_fingerprint, detail)`` where status is
        ``'patched'`` / ``'white'`` / ``'failed'`` and detail is a log fragment.
        Persist failures are NOT swallowed — they propagate so that the caller's
        ``except Exception`` maps them to ``('failed', None)`` exactly as before.
        """
        with recent_file.recent_file_access(
            file_path, expected_generation=expected_generation,
        ) as file_path:
            read_status, current = self._load_history_unlocked(file_path, lanlan_name)
            if read_status == RECENT_READ_UNREADABLE:
                # Unreadable disk means cutoff cannot be located. Never report 'white': _mutate_review_white
                # clears last_reviewed_cutoff_tail and review_fail_attempts, deliberately leaving
                # last_review_ts unchanged → all gates pass → every /process reruns entire review
                # LLM forever without breaker. Read-only disk would burn tokens all night.
                return ('failed', None, '提交时读盘失败，无法定位 cutoff')

            review_slots = _compute_review_slots(snapshot, current)
            capacity = len(review_slots)
            cutoff_idx = review_slots[-1] if review_slots else None
            if cutoff_idx is None:
                # Wasted review: cutoff mismatches current history (compressed/cleared).
                return ('white', None, 'review 完成但 cutoff 失配')

            take_count = min(capacity, len(corrected_messages))
            if take_count == 0:
                # Empty corrected (rare empty corrected_dialogue) means deleting the whole
                # review range. Treat as wasted review so next turn rebuilds anchor; neither
                # persist nor update fingerprint, preventing anchor drift outside reviewed range.
                return ('white', None, 'review 输出为空')

            # Replace only matched ordinary-message slots; retain interleaved theater capsules/new concurrent messages in place.
            # Shorter corrected keeps legacy behavior: delete earlier slots and write model output into latest slots.
            review_slot_set = set(review_slots)
            replacement_slots = review_slots[-take_count:]
            replacements = dict(zip(
                replacement_slots,
                corrected_messages[-take_count:],
                strict=True,
            ))
            new_history = []
            patched_end = -1
            for index, message in enumerate(current):
                if index not in review_slot_set:
                    new_history.append(message)
                    continue
                replacement = replacements.get(index)
                if replacement is not None:
                    new_history.append(replacement)
                    patched_end = len(new_history) - 1

            # Fence follows wasted-review checks, matching pre-refactor placement after both early returns;
            # wasted review during maintenance still does not become failed.
            assert_cloudsave_writable(
                self._config_manager,
                operation="save",
                target=f"memory/{lanlan_name}/recent.json",
            )
            recent_file.write_recent_payload_unlocked(
                file_path, messages_to_dict(new_history),
            )
            self._cache_history_view(file_path, lanlan_name, new_history)

            # ── Issue #3 fix: compute new fingerprint from patched new_history ──
            # Patched range in new_history is [patched_start, patched_end]:
            #   patched_start = cutoff_idx - capacity + 1
            #   patched_end   = patched_start + take_count - 1
            # New fingerprint = K messages ending at patched_end. If patched_end
            # has fewer than K-1 predecessors, take all available from index 0.
            reviewed_prefix = [
                message
                for message in new_history[:patched_end + 1]
                if not is_theater_memory_message(message)
            ]
            fp_messages = reviewed_prefix[-REVIEW_FINGERPRINT_K:]
            new_fingerprint = build_review_fingerprint(fp_messages, k=REVIEW_FINGERPRINT_K)
            detail = (
                f"cutoff_idx={cutoff_idx}, capacity={capacity}, "
                f"corrected={len(corrected_messages)}, take={take_count}, "
                f"history {len(current)}→{len(new_history)}"
            )
            return ('patched', new_fingerprint, detail)

    async def review_history(
        self,
        lanlan_name,
        snapshot=None,
        cancel_event=None,
        expected_generation=None,
    ):
        """
        Review the history, finding and fixing contradictions, redundancy,
        logical confusion, or repetition.

        Phase C redesign (snapshot + capacity-based replacement):
        - ``snapshot``: a copy of the history taken at spawn time (list of
          message objects). The LLM input uses the snapshot, not the current
          history — so while the review LLM runs, the user path can keep
          appending messages / triggering compression without interference.
        - On completion, fingerprint-match the snapshot's last K=3 entries to
          locate the cutoff position in the current history; walk back to get
          the capacity (consecutive match length); replace the consecutive
          ``capacity`` slots before the cutoff in the current history with the
          last ``min(capacity, len(corrected))`` entries of corrected; messages
          added after the cutoff stay untouched.
        - Cutoff not found (swallowed by compression / cleared by /new_dialog)
          → drop the whole batch = wasted review → the caller should set the
          fingerprint to None so the next review can start immediately.

        Returns:
            (str, list[dict] | None) tuple:
              ('patched', new_fingerprint) — patched and persisted;
                  new_fingerprint is the K-entry fingerprint of the review
                  region at the tail of the patched new_history, for the caller
                  to write into maint_state (it **must** use this new
                  fingerprint rather than ``build_review_fingerprint(snapshot)``
                  — the review may have rewritten any of the last K entries,
                  and the old fingerprint would never locate again in the new
                  history)
              ('white', None) — cutoff failed to match in the current history; batch dropped
              ('failed', None) — LLM failure / cancelled / empty history / malformed response
        """
        if expected_generation is None:
            file_path, admission_generation = self._capture_recent_operation_admission(
                lanlan_name,
            )
        else:
            file_path = expected_generation[0]
            admission_generation = expected_generation

        # Check cancellation.
        if cancel_event and cancel_event.is_set():
            _safe_print(f"⚠️ {lanlan_name} 的记忆整理被取消（启动前）")
            return ('failed', None)

        # Caller supplies snapshot captured at spawn; fall back to disk for legacy calls.
        if snapshot is None:
            try:
                snapshot = await asyncio.to_thread(
                    self._read_history_locked,
                    file_path,
                    lanlan_name,
                    admission_generation,
                )
            except recent_file.RecentFileDeletedError:
                return ('failed', None)

        # Apply the same fiction boundary to caller snapshots and legacy reads.
        # This filtered snapshot also defines the ordinary-message commit slots.
        snapshot = [message for message in snapshot if not is_theater_memory_message(message)]
        if not snapshot:
            _safe_print(f"{lanlan_name} 的历史记录为空，无需审阅")
            return ('failed', None)

        # Render snapshot into readable text for LLM.
        name_mapping = self.name_mapping.copy()
        name_mapping['ai'] = lanlan_name

        history_text = ""
        for msg in snapshot:
            if hasattr(msg, 'type') and msg.type in name_mapping:
                role = name_mapping[msg.type]
            else:
                role = "unknown"

            content = _review_message_content(msg)

            history_text += f"{role}: {content}\n\n"

        # Check cancellation.
        if cancel_event and cancel_event.is_set():
            _safe_print(f"⚠️ {lanlan_name} 的记忆整理被取消（准备调用LLM前）")
            return ('failed', None)

        retries = 0
        max_retries = 3
        while retries < max_retries:
            # Character reuse / cloud import may replace identity after spawn before first LLM call or
            # during retry backoff. Commit gate protects disk only, not correction_tasks occupancy
            # and wasted LLM calls; check original admission token before each call
            # fail closed。
            if recent_file.capture_recent_generation(file_path) != admission_generation:
                return ('failed', None)
            try:
                # Review history with LLM.
                set_call_type("memory_review")
                prompt = (
                    # Codex P2: % formatting before .replace, or % in master_name
                    # becomes a format specifier and crashes five-argument `% (...)`.
                    (
                        get_history_review_prompt(
                            _detect_recent_prompt_language(
                                _review_prompt_locale_text(snapshot)
                            )
                        )
                        % (self.name_mapping['human'], name_mapping['ai'], history_text, self.name_mapping['human'], name_mapping['ai'])
                    )
                    .replace("{MASTER_NAME}", self.name_mapping['human'])
                )
                # Review budget exceeds shared guard; custom correction models with lower output limits may return
                # 400 before generation. Retry once at shared cap, preserving previously working configurations.
                review_cap = MEMORY_THINKING_OUTPUT_MAX_TOKENS
                review_llm = self._get_review_llm()
                try:
                    try:
                        response = await review_llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # review prompt built from RECENT_PER_MESSAGE_MAX_TOKENS-capped history.
                    except Exception as cap_error:
                        if not output_cap_rejected(cap_error):
                            raise
                        logger.info(
                            f"[RecentHistory] {lanlan_name} 纠错模型拒绝 {review_cap} 输出额度，"
                            f"改用 {LLM_OUTPUT_GUARD_MAX_TOKENS} 重试"
                        )
                        await aclose_quietly(review_llm, f"{lanlan_name} memory_review")
                        review_cap = LLM_OUTPUT_GUARD_MAX_TOKENS
                        review_llm = self._get_review_llm(review_cap)
                        response = await review_llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # same capped prompt, lower output cap.
                finally:
                    await aclose_quietly(review_llm, f"{lanlan_name} memory_review")

                # Check cancellation after LLM call.
                if cancel_event and cancel_event.is_set():
                    _safe_print(f"⚠️ {lanlan_name} 的记忆整理被取消（LLM调用后，保存前）")
                    return ('failed', None)

                if response_hit_output_limit(response, review_cap):
                    _safe_print(
                        f"⚠️ {lanlan_name} 的历史审阅输出达到 token 上限，本轮暂停解析"
                        f"（{describe_output(response)}）"
                    )
                    return ('output_exhausted', None)

                # Ensure response_content is a string.
                response_content = str(response.content).strip()

                # Clean response content with safe regex extraction.
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()

                # Parse JSON response.
                review_result = robust_json_loads(response_content)

                if not (
                    isinstance(review_result, dict)
                    and 'explanation' in review_result
                    and isinstance(review_result.get('corrected_dialogue'), list)
                ):
                    _safe_print(f"❌ 审阅响应格式错误：{response_content}")
                    return ('failed', None)

                _safe_print(f"记忆整理结果：{review_result['explanation']}")

                # Convert corrected dialogue back to message format. Compression creates SystemMessage
                # summary memos; review should not create them, so discard to protect compression boundary.
                #
                # Normalize content at trust boundary: thinking models sometimes output
                # JSON content as list/dict instead of string. Existing
                # compress_history（[memory/recent.py:329-340](memory/recent.py:329)）
                # paths already handle it; review output is also generated and
                # untrusted, requiring normalization before recent-history write, or downstream recall /
                # prompt building / fingerprint content[:50] slicing receives nonstring
                # data and crashes.
                corrected_messages = []
                for msg_data in review_result['corrected_dialogue']:
                    if not isinstance(msg_data, dict):
                        continue
                    role = msg_data.get('role', 'user')
                    content = msg_data.get('content', '')

                    # Normalize content to str.
                    if not isinstance(content, str):
                        if isinstance(content, list):
                            parts = []
                            for item in content:
                                if isinstance(item, dict):
                                    parts.append(item.get('text', '') or str(item))
                                else:
                                    parts.append(str(item))
                            content = '\n'.join(parts)
                        else:
                            content = str(content)

                    if role in ['system', 'system_message', name_mapping['system']]:
                        # Prompt <要点3> requires retaining/editing memo; filtering would
                        # waste that work, and capacity traversing head SystemMessage leaves this slot
                        # unfilled, making memo disappear on persistence (scenario D).
                        # Accept model system output only when snapshot already begins with SystemMessage,
                        # otherwise history is not compressed and should have no
                        # SystemMessage. Discard hallucinated system messages to avoid injecting false
                        # memos into uncompressed dialogue and polluting downstream.
                        if snapshot and isinstance(snapshot[0], SystemMessage):
                            corrected_messages.append(SystemMessage(content=content))
                        # else: silently drop, restoring legacy behavior.
                    elif role in ['user', 'human', name_mapping['human']]:
                        corrected_messages.append(HumanMessage(content=content))
                    elif role in ['ai', 'assistant', name_mapping['ai']]:
                        corrected_messages.append(AIMessage(content=content))
                    else:
                        # Default to user message.
                        corrected_messages.append(HumanMessage(content=content))

                # Normalize SystemMessage position: if snapshot head is memo,
                # corrected_messages must begin with exactly one SystemMessage.
                # Handle three malformed model outputs:
                # (a) Missing entirely → fall back to snapshot[0].
                # (b) In middle → move to head.
                # (c) Multiple → keep first only.
                # Otherwise head memo boundary breaks, downstream prompt gets misplaced
                # system rows, and middle SystemMessage may even conflict with downstream compression's alien-stop
                # invariant.
                # Note: require nonempty corrected_messages; model empty list means
                # delete the entire range, handled as wasted review below by take_count == 0.
                # Adding snapshot[0] here bypasses that gate, wiping all dialogue
                # except memo.
                if (
                    corrected_messages
                    and snapshot
                    and isinstance(snapshot[0], SystemMessage)
                ):
                    sys_msgs = [m for m in corrected_messages if isinstance(m, SystemMessage)]
                    others = [m for m in corrected_messages if not isinstance(m, SystemMessage)]
                    if not others:
                        # Model returns only system without dialogue ≡ delete all dialogue,
                        # equivalent to empty list, requiring wasted review. Reset empty for take_count==0
                        # gate below; otherwise normalization inserts one SystemMessage,
                        # making length 1 bypass gate and erasing all dialogue except memo.
                        corrected_messages = []
                    else:
                        head = sys_msgs[0] if sys_msgs else snapshot[0]
                        corrected_messages = [head] + others

                # ── Phase C: snapshot-based capacity for tail-aligned replacement ──
                # Read current → locate → splice → persist must share **one** critical section; previously
                # two awaits separated them, letting /cache batches arriving between review LLM completion
                # and persistence be overwritten entirely.
                try:
                    commit_status, new_fingerprint, detail = (
                        await _await_recent_mutation_to_completion(
                            self._commit_review_locked,
                            file_path,
                            lanlan_name,
                            snapshot,
                            corrected_messages,
                            admission_generation,
                        )
                    )
                except recent_file.RecentFileDeletedError:
                    return ('failed', None)
                if commit_status == 'white':
                    _safe_print(f"⚠️ {lanlan_name} {detail}（白 review，丢弃）")
                    return ('white', None)
                if commit_status != 'patched':
                    _safe_print(f"❌ {lanlan_name} review 提交失败：{detail}")
                    return ('failed', None)

                _safe_print(f"✅ {lanlan_name} 的记忆已修正：{detail}")
                return ('patched', new_fingerprint)

            except openai_retry_error_types() as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                retries += 1
                if retries >= max_retries:
                    _safe_print(f'❌ 记忆整理失败，已达到最大重试次数: {e}')
                    return ('failed', None)
                # Exponential backoff: 1, 2, 4 seconds.
                wait_time = 2 ** (retries - 1)
                _safe_print(f'⚠️ 遇到网络或429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
                # Check cancellation.
                if cancel_event and cancel_event.is_set():
                    _safe_print(f"⚠️ {lanlan_name} 的记忆整理在重试等待期间被取消")
                    return ('failed', None)
            except Exception as e:
                logger.error(f"❌ 历史记录审阅失败：{e}")
                return ('failed', None)

        # If every retry fails.
        _safe_print(f"❌ {lanlan_name} 的记忆整理失败，已达到最大重试次数")
        return ('failed', None)
