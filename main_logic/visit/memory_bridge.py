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

"""Visit memory bridge: scoped memory reads and writes of one local character.

Design: ``docs/design/visit-infrastructure.md`` sections 3.7.2 / 3.7.3 and
PR-08 ``memory_bridge.py``. Every call goes through
:class:`memory.scoped_client.ScopedMemoryClient`; nothing here reads
``/new_dialog`` or any private memory (OD-10).

``name`` is always the local character's current name: memory_server keys
every scoped store by it, so the same subject under two local characters is
two separate memories.

Reads:

* :func:`fetch_visit_context` renders ``scoped_context`` for the visit
  subjects (``include_legacy_private=False``), cut to a token budget.
* :func:`build_visit_memory_block` assembles the session memory block: the
  last-visit summary of this person with this local character first (from the
  local roster, works without memory_server), then ``scoped_context`` with
  the remaining budget. It first hands over from the previous visit of the
  same pair (waits for its summary, at most ``VISIT_LAST_SUMMARY_HANDOFF_S``)
  and loads nothing while a forget of this pair is in progress. It only
  returns a string and never writes.

Writes (:func:`post_visit_digest`, :func:`post_visit_segments`,
:func:`post_visit_forget`) carry the idempotency key / forget generation the
caller hands in; ``shutdown=True`` makes one call with no back-off, bounded by
``VISIT_SHUTDOWN_BUDGET_S``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from config import MEMORY_SERVER_PORT, SCOPED_HISTORY_BATCH_MAX_MESSAGES
from config.prompts.prompts_visit import (
    build_visit_last_summary_block,
    get_visit_speaker_header,
)
from config.visit_settings import (
    VISIT_CONTEXT_MAX_TOKENS,
    VISIT_LAST_SUMMARY_HANDOFF_S,
    VISIT_LAST_SUMMARY_MAX_TOKENS,
    VISIT_MEMORY_PLATFORM,
    VISIT_SHUTDOWN_BUDGET_S,
)
from main_logic.visit.forget import (
    ClearingSentinels,
    ForgetEpochs,
    RevocationLog,
    RevocationLogUnreadable,
    sentinel_covers,
    subject_key,
)
from main_logic.visit.sanitize import neutralize_display_name
from main_logic.visit.subjects import (
    PeerRoster,
    derive_person_id,
    derive_short_code,
    group_participant_subject,
    participant_subject,
)
from memory.scoped_client import ScopedMemoryClient, ScopedMemoryError
from utils.logger_config import get_module_logger
from utils.tokenize import acount_tokens, atruncate_to_tokens

logger = get_module_logger(__name__, "Main")

_SPEAKER_TIER = "none"
_CONTEXT_JOINER = "\n\n"


def diag(event: str, **fields: Any) -> None:
    """Record one local diagnostic event (a warning log line ``visit diag <event>``)."""
    logger.warning("visit diag %s %s", event, fields)


def default_client() -> ScopedMemoryClient:
    """Return a client for the local memory_server (internal HTTP client, 502 back-off)."""
    return ScopedMemoryClient(base_url=f"http://127.0.0.1:{MEMORY_SERVER_PORT}")


def _shutdown_client(client: ScopedMemoryClient) -> ScopedMemoryClient:
    # 关机：同一个 memory_server，但不做 502 退避（单次、受 VISIT_SHUTDOWN_BUDGET_S 约束）
    return client.with_retry_delays(())


async def _bounded(coro: Awaitable[Any], *, shutdown: bool, failed: Any) -> Any:
    if not shutdown:
        return await coro
    try:
        return await asyncio.wait_for(coro, VISIT_SHUTDOWN_BUDGET_S)
    except asyncio.TimeoutError:
        logger.warning("visit memory write exceeded the shutdown budget; left for recovery")
        return failed


# ── 读 ────────────────────────────────────────────────────────────────


async def fetch_visit_context(
    name: str,
    subjects: list[dict],
    lang: str | None,
    *,
    max_tokens: int = VISIT_CONTEXT_MAX_TOKENS,
    client: ScopedMemoryClient | None = None,
) -> str:
    """Render the scoped visit context of ``subjects`` within ``max_tokens``.

    ``subjects`` are in budget priority order (group, peer cat, peer person).
    Never serves the legacy private corpus. An unavailable memory_server, no
    subjects or no budget all yield an empty string.
    """
    if not subjects or max_tokens <= 0:
        return ""
    client = client or default_client()
    try:
        text = await client.fetch_bootstrap(
            name, subjects=list(subjects), lang=lang,
            include_legacy_private=False, max_tokens=max_tokens,
        )
    except ScopedMemoryError as exc:
        logger.warning("visit scoped_context unavailable for %s: %s", name, exc)
        return ""
    return await atruncate_to_tokens(text, max_tokens)


def _local_date(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _default_peer_label(lang: str | None) -> str:
    return get_visit_speaker_header("peer_human", lang).strip("[] ")


async def forget_in_progress(config_dir: str | Path, own_char_uid: str, peer_uid: str) -> bool:
    """Whether a local forget covering ``(own_char_uid, peer_uid)`` is unfinished.

    Counts open revocation logs (any account) and clearing sentinels whose
    scope covers the pair; unreadable ones count as in progress (fail closed).
    """
    config_dir = Path(config_dir)
    try:
        logs = await RevocationLog.list_all_open(config_dir)
        sentinels = await ClearingSentinels(config_dir).list_open()
    except RevocationLogUnreadable:
        # 读不出来的清除记录不能当作「没有在清除」
        return True
    if any(log["own_char_uid"] == own_char_uid and log["peer_uid"] == peer_uid for log in logs):
        return True
    return any(sentinel_covers(doc, own_char_uid, peer_uid) for doc in sentinels)


async def build_visit_memory_block(
    name: str,
    *,
    own_uid: str,
    own_char: str,
    peer_uid: str,
    peer_display: str | None,
    subjects: list[dict],
    lang: str | None,
    own_char_uid: str,
    config_dir: str | Path,
    handoff: Callable[[], Awaitable[Any]] | None = None,
    protected_names: Iterable[str] = (),
    generic_label: str | None = None,
    max_tokens: int = VISIT_CONTEXT_MAX_TOKENS,
    client: ScopedMemoryClient | None = None,
) -> str:
    """Assemble the memory block of a new visit session (read only, zero writes).

    ``own_uid`` is this side's verified ``visit_uid`` (the account the visit
    runs under); ``own_char`` the local character's current name and
    ``own_char_uid`` its stable id; ``peer_uid`` the verified peer.

    Order: an empty ``subjects`` (not derivable) returns ``""`` without
    reading anything. Otherwise ``handoff`` (the previous visit of this pair
    finishing its last-visit summary, see
    :func:`main_logic.visit.memory_commit.last_summary_handoff`) is awaited
    for at most ``VISIT_LAST_SUMMARY_HANDOFF_S``; on timeout the visit opens
    with whatever the roster holds and one diagnostic is logged. While a
    forget of this ``(own_char_uid, peer_uid)`` is unfinished (revocation log
    or clearing sentinel, including ones waiting for replay) nothing is
    loaded: neither the summary nor ``scoped_context``.

    The summary of exactly ``accounts[own_uid].peers[peer_uid].by_char[own_char]``
    goes first, wrapped in ``VISIT_LAST_SUMMARY_BLOCK`` with the local date of
    its ``ended_at`` and the neutralized peer name, its text cut again to
    ``VISIT_LAST_SUMMARY_MAX_TOKENS`` and delimiter-escaped. ``scoped_context``
    gets the remaining budget, so the whole block stays within ``max_tokens``.
    The summary works without memory_server.
    """
    if not subjects:
        return ""
    config_dir = Path(config_dir)
    if handoff is not None:
        try:
            await asyncio.wait_for(handoff(), VISIT_LAST_SUMMARY_HANDOFF_S)
        except asyncio.TimeoutError:
            diag("last_summary_handoff_timeout", own_char_uid=own_char_uid,
                 short_id=derive_short_code(peer_uid))
        except Exception as exc:  # noqa: BLE001 - 交接失败不挡开场，按现有那份开场
            diag("last_summary_handoff_failed", error=repr(exc))
    if await forget_in_progress(config_dir, own_char_uid, peer_uid):
        diag("memory_block_skipped_forget_in_progress", own_char_uid=own_char_uid,
             short_id=derive_short_code(peer_uid))
        return ""

    parts: list[str] = []
    summary = await PeerRoster(config_dir, own_uid=own_uid).get_last_summary(peer_uid, own_char)
    if summary and isinstance(summary.get("text"), str):
        text = await atruncate_to_tokens(summary["text"], VISIT_LAST_SUMMARY_MAX_TOKENS)
        label = neutralize_display_name(
            peer_display,
            protected_names=protected_names,
            generic_label=generic_label or _default_peer_label(lang),
            short_code=derive_short_code(peer_uid),
        )
        block = build_visit_last_summary_block(
            summary=text, date=_local_date(summary.get("ended_at")),
            peer_display=label, lang=lang,
        )
        if block:
            parts.append(block)
    used = await acount_tokens(_CONTEXT_JOINER.join(parts) + _CONTEXT_JOINER) if parts else 0
    remaining = max_tokens - used
    if remaining > 0:
        context = await fetch_visit_context(name, subjects, lang, max_tokens=remaining, client=client)
        if context:
            parts.append(context)
    result = _CONTEXT_JOINER.join(parts)
    # 拼接处的换行可能与两侧合并成别的 token：最后整体再量一次，超了就截
    if await acount_tokens(result) > max_tokens:
        result = await atruncate_to_tokens(result, max_tokens)
    return result


async def list_visit_subjects(name: str, *, client: ScopedMemoryClient | None = None) -> list[dict]:
    """List the stored visit subjects of one local character (read-only ``scoped_subjects``).

    Raises :class:`memory.scoped_client.ScopedMemoryError` when memory_server
    cannot answer.
    """
    client = client or default_client()
    return await client.list_scoped_subjects(name, platform=VISIT_MEMORY_PLATFORM)


# ── 写 ────────────────────────────────────────────────────────────────


async def post_visit_digest(
    name: str,
    pair_id: str,
    lines: Sequence[Mapping[str, Any]],
    *,
    subject: dict,
    lang: str | None,
    idempotency_key: str,
    client_requested_at: float,
    subject_epochs: Mapping[str, int] | None = None,
    shutdown: bool = False,
    client: ScopedMemoryClient | None = None,
) -> bool:
    """Send one batch (1..200 lines) of the group digest to ``/scoped_history``.

    ``lines`` are spool lines in ``(lp, side_rank)`` order; own-cat lines go as
    the character's own turns, every other line as a user turn prefixed with
    its speaker tag. ``subject`` must be the visit's ``group_chat`` subject of
    ``pair_id``. More than ``SCOPED_HISTORY_BATCH_MAX_MESSAGES`` lines raise
    ``ValueError`` (batching belongs to ``commit_visit_region``). Returns
    whether memory_server confirmed the batch (a duplicate of a completed key
    counts as confirmed).
    """
    if not lines:
        raise ValueError("a digest batch needs at least one line")
    if len(lines) > SCOPED_HISTORY_BATCH_MAX_MESSAGES:
        raise ValueError(
            f"a digest batch holds at most {SCOPED_HISTORY_BATCH_MAX_MESSAGES} lines"
        )
    if subject.get("subject_kind") != "group_chat" or pair_id not in str(subject.get("subject_id")):
        raise ValueError("the digest subject must be the group_chat subject of pair_id")
    messages = [_group_message(line, lang) for line in lines]
    client = client or default_client()
    if shutdown:
        client = _shutdown_client(client)
    return await _bounded(
        client.post_history(
            name, subject=subject, messages=messages,
            idempotency_key=idempotency_key, client_requested_at=client_requested_at,
            subject_epochs=dict(subject_epochs) if subject_epochs is not None else None,
        ),
        shutdown=shutdown, failed=False,
    )


def _group_message(line: Mapping[str, Any], lang: str | None) -> dict:
    speaker = line["from"]
    text = str(line.get("text") or "")
    if speaker == "own_cat":
        return {"role": "assistant", "content": text}
    return {"role": "user", "content": f"{get_visit_speaker_header(speaker, lang)} {text}"}


async def post_visit_segments(
    name: str,
    *,
    pair_id: str,
    own_uid: str,
    peer_uid: str,
    peer_char_id: str,
    peer_cat_display: str,
    peer_human_display: str,
    lines: Sequence[Mapping[str, Any]],
    idempotency_key: str,
    client_requested_at: float,
    subject_epochs: Mapping[str, int] | None = None,
    shutdown: bool = False,
    client: ScopedMemoryClient | None = None,
) -> bool:
    """Send one segments batch of the two peer speakers to ``/scoped_history``.

    ``lines`` are the batch's peer lines (``peer_cat`` / ``peer_human``) in
    ``(lp, side_rank)`` order, at most ``SCOPED_HISTORY_BATCH_MAX_MESSAGES``
    together (``ValueError`` otherwise; own lines raise too). The peer cat goes
    to ``group_participant(pair_id, peer_char_id)`` with speaker id
    ``neko_visit:<peer_char_id>``, the peer person to the person-level
    ``participant(person_id)`` with ``neko_visit:<person_id>``; both with
    ``speaker_tier='none'``. ``peer_uid`` / ``peer_char_id`` come from the
    spool header (``pair_id`` is a one-way hash). A speaker without lines in
    this batch gets no segment. Returns whether both segments were confirmed.
    """
    if not lines:
        raise ValueError("a segments batch needs at least one line")
    if len(lines) > SCOPED_HISTORY_BATCH_MAX_MESSAGES:
        raise ValueError(
            f"a segments batch holds at most {SCOPED_HISTORY_BATCH_MAX_MESSAGES} lines in total"
        )
    if any(line["from"] not in ("peer_cat", "peer_human") for line in lines):
        raise ValueError("segments carry only peer lines")
    person_id = derive_person_id(own_uid, peer_uid)
    speakers = (
        ("peer_cat", group_participant_subject(pair_id, peer_char_id),
         f"{VISIT_MEMORY_PLATFORM}:{peer_char_id}", peer_cat_display),
        ("peer_human", participant_subject(person_id),
         f"{VISIT_MEMORY_PLATFORM}:{person_id}", peer_human_display),
    )
    segments = []
    for speaker, subject, speaker_id, display in speakers:
        messages = [
            {"role": "user", "content": str(line.get("text") or "")}
            for line in lines if line["from"] == speaker
        ]
        if not messages:
            continue
        segments.append({
            "messages": messages,
            "subject": subject,
            "speaker_label": display,
            "speaker_tier": _SPEAKER_TIER,
            "speaker_id": speaker_id,
            "display_name": display,
        })
    client = client or default_client()
    if shutdown:
        client = _shutdown_client(client)
    result = await _bounded(
        client.post_history_batch(
            name, segments=segments,
            idempotency_key=idempotency_key, client_requested_at=client_requested_at,
            subject_epochs=dict(subject_epochs) if subject_epochs is not None else None,
        ),
        shutdown=shutdown, failed=False,
    )
    # 同一个幂等键下整批成败：服务端按键记录整批，部分成功也只能同键整批重试
    return bool(result)


async def post_visit_forget(
    name: str,
    subjects: list[dict],
    *,
    config_dir: str | Path,
    client: ScopedMemoryClient | None = None,
) -> bool:
    """Erase every subject in ``subjects`` with one ``/scoped_forget`` each.

    ``subjects`` is the already expanded list (from the revocation log, i.e.
    ``PeerRoster.expand_subjects``): every pair's ``group_chat``, every pair
    times every peer cat ever seen as ``group_participant``, and the person's
    ``participant``. Nothing is re-expanded here (a ``pair_id`` cannot be
    turned back into peer cat ids). Each call carries the subject's current
    forget generation from ``visit_forget_epochs.json`` (bumped by the
    revocation executor before this call). Stops at the first failure and
    returns False; True once every erase is confirmed.
    """
    client = client or default_client()
    epochs = await ForgetEpochs(config_dir).get(subjects)
    for subject in subjects:
        epoch = epochs[subject_key(subject)]
        if await client.post_forget(name, subject=dict(subject), forget_epoch=epoch) is not True:
            return False
    return True

