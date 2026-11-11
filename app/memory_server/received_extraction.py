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
_INITIAL_DELAY_SECONDS = 150
_failures = {}


def _now_ms():
    return time.time_ns() // 1_000_000


def _snapshot_checked_at(uid):
    """When the learned-memory lane last finished a successful check, or None while one is owed."""
    from services.chat_sync.ledger import ChatDomain
    from .long_term_sync import _authority, _binding, _capture_scheduler

    writer = _authority()
    binding = _binding(uid) if writer is not None else None
    if binding is None:
        return None
    if DesktopLongTermAdapter(runtime._config_manager).incoming_path(uid).is_file():
        # A downloaded snapshot not applied yet may mark these messages as read.
        return None
    return _capture_scheduler(writer.directory).succeeded_at(ChatDomain(binding['owner_id'], uid).key(), 'long_term')


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
    if checked is None or checked < max(batch['receivedAt'] for batch in due):
        # Progress published since these arrived has not been read yet.
        return 0
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
            key = (name, paths[-1].name)
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
