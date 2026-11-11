"""Extract facts from what another device said, once that device has had its turn.

The device a message was said on normally extracts from it first and
publishes which messages it read. This device waits, then reads only what no
snapshot marks as read, so a phone that was closed right after a
conversation still gets its facts extracted.
"""

import asyncio
import time

from memory import received_messages
from memory.extraction_progress import unprocessed
from memory.facts import FactExtractionFailed
from memory.long_term_sync import DesktopLongTermAdapter, FactEvidenceUnavailable
from utils.llm_client import convert_to_messages

from . import runtime
from ._shared import logger

# How long the device that said a message keeps the first turn, by this device's clock.
RECEIVED_EXTRACTION_DELAY_MS = 15 * 60 * 1000
CHECK_INTERVAL_SECONDS = 60
MAX_FAILURES = 5
# One model call reads at most this many arrivals; a backlog is worked off over several rounds.
MAX_ENTRIES_PER_RUN = 40
_INITIAL_DELAY_SECONDS = 150
_failures = {}


def _now_ms():
    return time.time_ns() // 1_000_000


def _snapshot_checked_at(uid):
    """When this device last knew it held the newest published snapshot, or None while it may not."""
    from .long_term_sync import _authority, _binding

    writer = _authority()
    if writer is None or _binding(uid) is None:
        return None
    adapter = DesktopLongTermAdapter(runtime._config_manager)
    if adapter.incoming_path(uid).is_file():
        # A downloaded snapshot not applied yet may mark these messages as read.
        return None
    return adapter.checked_at(uid)


async def extract_received(name, uid):
    """Run the user pass over arrivals that are due. Returns how many messages were sent to the model."""
    adapter = DesktopLongTermAdapter(runtime._config_manager)
    directory = adapter.received_directory(uid)
    pending = await asyncio.to_thread(received_messages.batches, directory)
    if not pending:
        return 0
    now = _now_ms()
    # A clock moved backwards makes an arrival look like the future; it then simply waits.
    due = [batch for batch in pending if 0 <= now - batch['receivedAt'] and now - batch['receivedAt'] >= RECEIVED_EXTRACTION_DELAY_MS]
    if not due:
        return 0
    checked = await asyncio.to_thread(_snapshot_checked_at, uid)
    # The check has to come after the wait is over: one made while the other device
    # still had its turn says nothing about progress it published later.
    due = [batch for batch in due if checked is not None and checked >= batch['receivedAt'] + RECEIVED_EXTRACTION_DELAY_MS]
    if not due:
        return 0
    # Oldest first and bounded, so a long offline backlog never becomes one input too
    # large for the model, and a failure costs only the batches of this round.
    taken, count = [], 0
    for batch in due:
        if taken and count + len(batch['entries']) > MAX_ENTRIES_PER_RUN:
            break
        taken.append(batch)
        count += len(batch['entries'])
    due = taken
    entries = list({row['id']: row for batch in due for row in batch['entries']}.values())
    messages = convert_to_messages(received_messages.message_dicts(entries))
    done = set((await runtime.fact_store.aextraction_progress(name))['a'])
    unread = [message for message in unprocessed(messages, done) if message.type == 'human']
    paths = [batch['path'] for batch in due]
    if unread:
        try:
            # Stage-1 only: judging belongs to the organizer, which finds these facts unjudged.
            await runtime.fact_store.extract_facts(unread, name, fail_closed=True)
        except (FactExtractionFailed, FactEvidenceUnavailable) as error:
            # The oldest batch stays the same until it is given up; a newer arrival
            # joining the round must not restart the count.
            key = (name, paths[0].name)
            _failures[key] = _failures.get(key, 0) + 1
            if _failures[key] < MAX_FAILURES:
                logger.warning(f"[ReceivedExtraction] {name}: 提取失败，保留待下轮重试: {error}")
                return 0
            # A window that keeps failing must not block later arrivals forever.
            logger.warning(f"[ReceivedExtraction] {name}: 连续 {MAX_FAILURES} 次失败，放弃这批收到的消息")
            _failures.pop(key, None)
        else:
            from .long_term_sync import queue_capture_after_extraction
            queue_capture_after_extraction(name)
    await asyncio.to_thread(received_messages.discard, paths)
    return len(unread)


async def _periodic_received_extraction_loop():
    from utils.config_manager.reserved_schema import get_character_uid
    from .signal_extraction import _run_signal_check_with_character_locale

    await asyncio.sleep(_INITIAL_DELAY_SECONDS)
    while True:
        try:
            if getattr(runtime._config_manager, 'app_docs_dir', None):
                characters = (await runtime._config_manager.aload_characters()).get('猫娘', {})
                for name, profile in characters.items():
                    uid = get_character_uid(profile) if isinstance(profile, dict) else None
                    if not uid:
                        continue
                    try:
                        async def run(_name, _uid=uid):
                            return await extract_received(_name, _uid)
                        await _run_signal_check_with_character_locale(name, run)
                    except Exception as error:
                        logger.debug(f"[ReceivedExtraction] {name}: 本轮跳过: {error}")
        except Exception as error:
            logger.debug(f"[ReceivedExtraction] 加载角色列表失败: {error}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
