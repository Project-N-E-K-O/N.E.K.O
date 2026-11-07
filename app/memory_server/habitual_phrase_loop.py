# -*- coding: utf-8 -*-
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

"""Background recompute of the habitual-phrase avoid list.

For every character, mines the latest replies for wording she reuses across
sessions (``memory/habitual_phrases.py``) and stores the list that the next
session start reads. No model call and no network. A round runs only while
memory is idle, a character is recomputed at most once per
``HABITUAL_PHRASE_REFRESH_INTERVAL_SECONDS``, and nothing on the startup or
session-start path waits for any of it.
"""

import asyncio
import time

from config import (
    HABITUAL_PHRASE_REFRESH_INTERVAL_SECONDS,
    HABITUAL_PHRASE_SOURCE_REPLY_LIMIT,
)
from memory.habitual_phrases import (
    TimedReply,
    build_payload,
    card_text_from_character,
    compute_entries,
    get_habitual_phrase_store,
)
from memory.stop_names import acollect_stop_names
from utils.cloudsave_runtime import MaintenanceModeError
from utils.language_utils import get_global_language_full
from utils.natural_expression_candidates import CandidateMinerError, normalize_language

from . import gates, runtime
from ._shared import logger, validate_lanlan_name
from .gates import _INITIAL_DELAY_HABITUAL_PHRASES
from .locale_state import get_character_prompt_locale

HABITUAL_PHRASE_POLL_INTERVAL = 600  # Seconds between idle checks; a due character is rare.


async def _aresolve_mining_language(name: str) -> str | None:
    """The miner language tag for this character's latest session locale."""
    selected = await asyncio.to_thread(get_character_prompt_locale, name)
    try:
        return normalize_language(selected or get_global_language_full())
    except CandidateMinerError:
        return None


async def _arefresh_habitual_phrases_one(raw_name: str, *, now: float) -> bool:
    """Recompute one character's list when it is due. Returns whether it was written."""
    try:
        name = validate_lanlan_name(raw_name)
    except Exception:
        return False
    if name != raw_name:
        # The file and time index are keyed by the raw characters.json key
        # everywhere else; an alias that normalizes differently is skipped.
        return False
    store = get_habitual_phrase_store()
    stored = await store.aread(name)
    if stored is not None and 0 <= now - stored["computed_at"] < HABITUAL_PHRASE_REFRESH_INTERVAL_SECONDS:
        return False
    time_manager = runtime.time_manager
    if time_manager is None:
        return False
    lease = runtime._begin_character_request(name)
    if lease is None:
        return False
    try:
        language = await _aresolve_mining_language(name)
        if language is None:
            return False
        history = await time_manager.aretrieve_latest_assistant_texts(
            name, HABITUAL_PHRASE_SOURCE_REPLY_LIMIT,
        )
        if not history.source_available:
            return False
        _, _, _, catgirl_data, _, prompt_map, *_ = (
            await runtime._config_manager.aget_character_data()
        )
        card_text, card_terms = card_text_from_character(
            (catgirl_data or {}).get(name), (prompt_map or {}).get(name, ""),
        )
        names = await acollect_stop_names(runtime._config_manager, name)
        replies = [
            TimedReply(text, at)
            for text, at in zip(history.messages, history.timestamps)
            if at is not None
        ]
        entries, source = await asyncio.to_thread(
            compute_entries,
            replies,
            language=language,
            card_text=card_text,
            protected_terms=[*names, *card_terms],
        )
        written = await store.awrite(
            name, build_payload(entries, source, language=language, now=now),
        )
    except MaintenanceModeError:
        return False
    finally:
        runtime._end_character_request(name, lease)
    if written:
        logger.info(
            "[HabitualPhrases] %s: 重算完成，%d 条口癖（分析 %d 条回复，%d 个会话）",
            name, len(entries), source["analyzed_reply_count"], source["session_count"],
        )
        if entries:
            # Her own words: console only, never the log file.
            print(f"[HabitualPhrases] {name}: " + " | ".join(e["phrase"] for e in entries))
    return written


async def _arefresh_habitual_phrases_once() -> None:
    """One round: every due character in turn, stopping as soon as memory is busy."""
    if runtime._memory_storage_blocked_after_init or not gates._is_idle():
        return
    from utils import cloudsave_runtime
    try:
        fenced = await asyncio.to_thread(
            cloudsave_runtime.is_write_fence_active, runtime._config_manager,
        )
    except Exception as e:
        # Unknown fence state counts as "fence up": skip the round.
        logger.debug(f"[HabitualPhrases] 读取写入围栏失败，本轮跳过: {e}")
        return
    if fenced:
        return
    try:
        character_data = await runtime._config_manager.aload_characters()
        raw_names = list(character_data.get('猫娘', {}).keys())
    except Exception as e:
        logger.debug(f"[HabitualPhrases] 加载角色列表失败: {e}")
        return
    for raw_name in raw_names:
        if not gates._is_idle():
            break
        try:
            await _arefresh_habitual_phrases_one(raw_name, now=time.time())
        except Exception as e:
            logger.warning(f"[HabitualPhrases] {raw_name}: 重算失败，跳过: {type(e).__name__}")


async def _periodic_habitual_phrase_loop():
    """Fire-and-forget: first check _INITIAL_DELAY_HABITUAL_PHRASES seconds after startup.

    Then every HABITUAL_PHRASE_POLL_INTERVAL seconds. A round body that raises
    is logged and the loop sleeps on to the next one.
    """
    await asyncio.sleep(_INITIAL_DELAY_HABITUAL_PHRASES)
    while True:
        try:
            await _arefresh_habitual_phrases_once()
        except Exception as e:
            logger.warning(f"[HabitualPhrases] 本轮失败，下一轮再试: {type(e).__name__}: {e}")
        await asyncio.sleep(HABITUAL_PHRASE_POLL_INTERVAL)
