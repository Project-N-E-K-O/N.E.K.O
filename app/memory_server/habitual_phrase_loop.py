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
sessions (``memory/habitual_phrases.py``), has the model judge the candidates
it has no fresh verdict for, and stores the list that the next session start
reads. A round runs only while memory is idle, a character is recomputed at
most once per ``HABITUAL_PHRASE_REFRESH_INTERVAL_SECONDS``, and nothing on the
startup or session-start path waits for any of it.

The review runs only while both "powerful memory" (the switch every newer
memory model call follows) and "natural expression" (the avoid list's own
switch) are on; otherwise, and when the call fails, unjudged candidates are
decided by the rules alone. The memory browser can also ask for a recompute
of one character right away, through the internal route below.
"""

import asyncio
import time

from fastapi import HTTPException

from config import (
    HABITUAL_PHRASE_REFRESH_INTERVAL_SECONDS,
    HABITUAL_PHRASE_SOURCE_REPLY_LIMIT,
)
from memory.habitual_phrases import (
    TimedReply,
    areview_candidates,
    build_payload,
    card_text_from_character,
    compute_entries,
    fresh_reviews,
    get_habitual_phrase_store,
)
from memory.stop_names import acollect_stop_names
from utils.cloudsave_runtime import MaintenanceModeError
from utils.language_utils import (
    get_global_language_full,
    is_supported_language_code,
    normalize_language_code,
)
from utils.natural_expression_candidates import CandidateMinerError, normalize_language

from . import gates, runtime
from ._shared import logger, validate_lanlan_name
from .gates import _INITIAL_DELAY_HABITUAL_PHRASES
from .locale_state import get_character_prompt_locale
from .runtime import app

HABITUAL_PHRASE_POLL_INTERVAL = 600  # Seconds between idle checks; a due character is rare.

# Characters being recomputed right now. The background round and a click in
# the memory browser must not both rewrite one file from two readings.
_refreshing: set[str] = set()

# The latest session's request (UI) language per character, noted by
# /new_dialog when the session has no explicit language. Such a session speaks
# this language, and the session start only injects a list mined in the same
# one, so the miner follows it rather than the server's global language.
# Memory only: it is a fallback, never a preference, and a restart forgets it.
_session_render_languages: dict[str, str] = {}


def note_session_render_language(name: str, language: str | None) -> None:
    """Remember the language a session without an explicit locale speaks."""
    if is_supported_language_code(language):
        _session_render_languages[name] = normalize_language_code(str(language), format="full")


async def _aresolve_mining_language(name: str) -> str | None:
    """The miner language tag: the durable session locale, else the latest
    session's UI language, else the global language."""
    selected = await asyncio.to_thread(get_character_prompt_locale, name)
    try:
        return normalize_language(
            selected or _session_render_languages.get(name) or get_global_language_full()
        )
    except CandidateMinerError:
        return None


async def _areview_enabled() -> bool:
    """Whether the model may judge: powerful memory and natural expression both on."""
    from utils.slop_filter import is_slop_filter_enabled

    if not await gates._ais_powerful_memory_enabled():
        return False
    return bool(await asyncio.to_thread(is_slop_filter_enabled))


async def _arefresh_habitual_phrases_one(
    raw_name: str, *, now: float, force: bool = False,
) -> bool:
    """Recompute one character's list when it is due (or ``force``). Returns whether it was written."""
    try:
        name = validate_lanlan_name(raw_name)
    except Exception:
        return False
    if name != raw_name:
        # The file and time index are keyed by the raw characters.json key
        # everywhere else; an alias that normalizes differently is skipped.
        return False
    if name in _refreshing:
        return False
    # Claimed before the first await, so two callers cannot both get past.
    _refreshing.add(name)
    try:
        return await _arefresh_claimed(name, now=now, force=force)
    finally:
        _refreshing.discard(name)


async def _arefresh_claimed(name: str, *, now: float, force: bool) -> bool:
    """Body of ``_arefresh_habitual_phrases_one``; the caller holds ``name`` in ``_refreshing``."""
    store = get_habitual_phrase_store()
    stored = await store.aread(name)
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
        if (
            not force
            and stored is not None
            # A list mined in another language than the next session will
            # speak is never injected, so a language change makes it due.
            and stored["language"] == language
            and 0 <= now - stored["computed_at"] < HABITUAL_PHRASE_REFRESH_INTERVAL_SECONDS
        ):
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
        overrides = await store.aread_overrides(name)
        replies = [
            TimedReply(text, at)
            for text, at in zip(history.messages, history.timestamps)
            if at is not None
        ]
        # An allowed phrase is a protected term from here on: it, and any
        # phrase holding it, never even becomes a candidate again.
        entries, source = await asyncio.to_thread(
            compute_entries,
            replies,
            language=language,
            card_text=card_text,
            protected_terms=[*names, *card_terms, *overrides.allowed_phrases()],
        )
        reviews = fresh_reviews(stored, now=now)
        forced = overrides.suppressed_keys()
        pending = [
            entry for entry in entries
            if entry["normalized_phrase"] not in reviews
            and entry["normalized_phrase"] not in forced
        ]
        review_status, reviewed = "not_needed", 0
        if pending:
            if not await _areview_enabled():
                review_status = "disabled"
            else:
                try:
                    verdicts, asked = await areview_candidates(
                        pending,
                        ai_name=name,
                        card_text=card_text,
                        language=language,
                        config_manager=runtime._config_manager,
                        now=now,
                    )
                except Exception as e:  # noqa: BLE001 — the rules alone decide this time
                    review_status = "failed"
                    logger.warning(
                        f"[HabitualPhrases] {name}: 模型判定失败，本次未判定的候选按规则压制: "
                        f"{type(e).__name__}"
                    )
                else:
                    reviewed = len(verdicts)
                    reviews.update(verdicts)
                    review_status = "ok" if verdicts or not asked else "failed"
                    if verdicts:
                        # Her own words: console only, never the log file.
                        print(
                            f"[HabitualPhrases] {name} 判定: "
                            + " | ".join(
                                f"{entry['phrase']}={verdicts[entry['normalized_phrase']]['verdict']}"
                                for entry in pending
                                if entry["normalized_phrase"] in verdicts
                            )
                        )
        payload = build_payload(
            entries, source,
            language=language, now=now,
            reviews=reviews, review_status=review_status, reviewed=reviewed,
            overrides=overrides,
        )
        written = await store.awrite(name, payload)
    except MaintenanceModeError:
        return False
    finally:
        runtime._end_character_request(name, lease)
    if written:
        suppressed = [entry for entry in payload["entries"] if entry["suppressed"]]
        logger.info(
            "[HabitualPhrases] %s: 重算完成，候选 %d 条，压制 %d 条，新判定 %d 条（%s）"
            "（分析 %d 条回复，%d 个会话）",
            name, len(entries), len(suppressed), reviewed, review_status,
            source["analyzed_reply_count"], source["session_count"],
        )
        if suppressed:
            # Her own words: console only, never the log file.
            print(f"[HabitualPhrases] {name}: " + " | ".join(e["phrase"] for e in suppressed))
    return written


@app.post("/internal/memory/{lanlan_name}/habitual_phrases/recompute")
async def recompute_habitual_phrases(lanlan_name: str):
    """Recompute one character's avoid list now, for the memory browser.

    Unlike the background round this skips the daily cadence and the idle
    gate: the user asked for it. It still takes the character lease and never
    runs twice at once for one character.
    """
    try:
        name = validate_lanlan_name(lanlan_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid lanlan_name") from exc
    if name in _refreshing:
        raise HTTPException(status_code=409, detail="already recomputing")
    written = await _arefresh_habitual_phrases_one(name, now=time.time(), force=True)
    return {"success": True, "written": bool(written)}


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
