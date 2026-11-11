"""Run the one-time cleanup of old duplicate facts (see ``memory.legacy_dedup``).

The organizer of a character runs it once, during idle maintenance:

1. copy the character directory aside and nominate pairs, from live facts and
   from absorbed facts that age has already moved to the archive (everyday
   dedup reaches neither of the latter);
2. ask the model about each pair with the dedup prompt, 20 at a time, saving
   every answer so an interrupted run resumes where it stopped;
3. archive what a merge or replace folds away, with ``arbitration_archived_at``
   and ``superseded_by``; point reflections and persona entries at the facts
   that stand for them; mark a survivor absorbed when what it replaced was;
4. record the run here and in the snapshot's ``maintenance.legacyDedup``.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path

from memory import legacy_dedup, organizer
from utils.file_utils import atomic_write_json

from . import runtime
from ._shared import logger

BATCH = 20
# Consecutive failed model rounds before a batch is given up as "different facts".
MAX_FAILURES = 3
# Same input bound as the retired-assertion review, which uses the same prompt.
PROMPT_TOKEN_BUDGET = 8000


def _directory(name):
    return Path(runtime._config_manager.memory_dir) / name


def _decode(row):
    try:
        from memory.embeddings import decode_valid_cached_embedding
    except ImportError:
        return None
    try:
        vector = decode_valid_cached_embedding(row, row.get('text', ''), row.get('embedding_model_id'))
    except Exception:  # noqa: BLE001 - a broken cache only loses the vector nomination
        return None
    return None if vector is None else [float(value) for value in vector]


def _read_archive(name):
    try:
        value = json.loads(Path(runtime.fact_store._facts_archive_path(name)).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return []
    if not isinstance(value, list):
        raise ValueError('facts_archive is not a list')
    return value


def _save_marker(directory, marker):
    from utils.cloudsave_runtime import cloudsave_writable_transaction
    target = directory / legacy_dedup.MARKER
    with cloudsave_writable_transaction(runtime._config_manager, operation='save', target=target):
        atomic_write_json(target, marker, ensure_ascii=False, indent=2)


async def _publish_marker(name):
    """Carry this device's completed run into the snapshot, so no other device repeats it."""
    directory = _directory(name)
    state_path = directory / 'long_term_portable.json'
    if legacy_dedup.local_version(directory) <= legacy_dedup.published_version(directory) or not state_path.exists():
        return False

    def save():
        from utils.cloudsave_runtime import cloudsave_writable_transaction
        with cloudsave_writable_transaction(runtime._config_manager, operation='save', target=state_path):
            state = json.loads(state_path.read_text(encoding='utf-8'))
            if state == []:
                state = {}
            if not isinstance(state, dict):
                raise ValueError('invalid_long_term_memory')
            maintenance = dict(state.get('maintenance') or {})
            maintenance[legacy_dedup.MAINTENANCE_KEY] = max(
                legacy_dedup.local_version(directory), int(maintenance.get(legacy_dedup.MAINTENANCE_KEY) or 0))
            state['maintenance'] = maintenance
            atomic_write_json(state_path, state, ensure_ascii=False, indent=2)

    from memory.recent import _await_recent_mutation_to_completion
    # Same lock as facts and extraction progress, which share this file.
    async with runtime.fact_store._get_persist_alock(name):
        await _await_recent_mutation_to_completion(save)
    from .long_term_sync import queue_capture_after_extraction
    queue_capture_after_extraction(name)
    return True


async def _arbitrate(name, batch, rows):
    """The model's action for each pair of the batch, or None when the round failed."""
    from config import EVIDENCE_EXTRACT_FACTS_MODEL_TIER
    from config.prompts.prompts_memory import get_fact_dedup_prompt
    from memory.fact_dedup import _detect_fact_dedup_prompt_language
    from utils.language_utils import get_global_language_full
    lines = '\n'.join(f"[{index}] candidate: {rows[pair['candidate']]['text']} | existing: {rows[pair['existing']]['text']}"
                      f" | {pair['evidence']}" for index, pair in enumerate(batch))
    language = _detect_fact_dedup_prompt_language(lines, ui_language=get_global_language_full())
    prompt = get_fact_dedup_prompt(language).replace('{PAIRS}', lines).replace('{COUNT}', str(len(batch)))
    from utils.tokenize import acount_tokens
    if await acount_tokens(prompt) > PROMPT_TOKEN_BUDGET:
        if len(batch) == 1:
            # Too long to show the model whole; left as two facts.
            return {legacy_dedup.pair_key(batch[0]): 'keep_both'}
        middle = len(batch) // 2
        first = await _arbitrate(name, batch[:middle], rows)
        second = await _arbitrate(name, batch[middle:], rows) if first is not None else None
        return None if second is None else {**first, **second}
    decisions = await runtime.fact_store._allm_call_with_retries(
        prompt, name, tier=EVIDENCE_EXTRACT_FACTS_MODEL_TIER, call_type='memory_fact_dedup', parse_max_retries=1)
    if not isinstance(decisions, list) or len(decisions) != len(batch):
        return None
    actions = {}
    for decision in decisions:
        if (not isinstance(decision, dict) or type(decision.get('index')) is not int
                or not 0 <= decision['index'] < len(batch) or decision['index'] in actions
                or decision.get('action') not in ('merge', 'replace', 'keep_both')):
            return None
        actions[decision['index']] = decision['action']
    return {legacy_dedup.pair_key(pair): actions[index] for index, pair in enumerate(batch)}


async def _archive_folded(name, folded):
    """Archive every folded fact, live or already archived by age. Repeating it changes nothing."""
    store = runtime.fact_store
    live = {row.get('id') for row in await store.aload_facts(name) if isinstance(row, dict)}
    live_specs = {loser: {'reason': 'legacy_dedup', 'superseded_by': survivor}
                  for loser, survivor in folded.items() if loser in live}
    if live_specs:
        await store.aarchive_arbitrated_facts(name, live_specs)
    remaining = {loser: survivor for loser, survivor in folded.items() if loser not in live}
    if not remaining:
        return

    def mark():
        from utils.cloudsave_runtime import cloudsave_writable_transaction
        archive_path = store._facts_archive_path(name)
        with store._get_lock(name):
            with cloudsave_writable_transaction(runtime._config_manager, operation='archive', target=archive_path):
                archive = _read_archive(name)
                now = datetime.now().isoformat()
                changed = False
                for row in archive:
                    if isinstance(row, dict) and row.get('id') in remaining and not row.get('arbitration_archived_at'):
                        # Archived by age, never arbitrated: it now also says what replaced it.
                        row.update(arbitration_archived_at=now, arbitration_reason='legacy_dedup',
                                   superseded_by=remaining[row['id']])
                        changed = True
                if changed:
                    atomic_write_json(archive_path, archive, indent=2, ensure_ascii=False)

    from memory.recent import _await_recent_mutation_to_completion
    async with store._get_persist_alock(name):
        await _await_recent_mutation_to_completion(mark)


async def _repoint(name, mapping):
    """Point reflection and persona evidence at the surviving facts."""
    reflections_changed = persona_changed = 0
    engine = runtime.reflection_engine
    async with engine._get_alock(name):
        reflections = await engine.aload_reflections(name)
        for row in reflections:
            if isinstance(row, dict) and legacy_dedup.repoint(row, mapping):
                reflections_changed += 1
        if reflections_changed:
            await engine.asave_reflections(name, reflections)
    manager = runtime.persona_manager
    async with manager._get_alock(name):
        persona = await manager._aensure_persona_locked(name)
        for section in persona.values():
            for entry in (section.get('facts') or []) if isinstance(section, dict) else []:
                if isinstance(entry, dict) and legacy_dedup.repoint(entry, mapping):
                    persona_changed += 1
        if persona_changed:
            await manager.asave_persona(name, persona)
    return reflections_changed, persona_changed


async def run(name, *, still_idle=lambda: True):
    """Advance the cleanup for one character.

    Returns the finished marker, or None while there is nothing to do or the
    run waits for a later idle round.
    """
    directory = _directory(name)
    if max(legacy_dedup.local_version(directory), legacy_dedup.published_version(directory)) >= legacy_dedup.VERSION:
        await _publish_marker(name)
        return None
    if not organizer.allowed(name):
        return None
    marker = legacy_dedup.read_marker(directory) or {}
    if marker.get('version') != legacy_dedup.VERSION or marker.get('stage') not in ('deciding', 'decided'):
        rows = legacy_dedup.candidates(await runtime.fact_store.aload_facts(name), await asyncio.to_thread(_read_archive, name))
        pairs = await asyncio.to_thread(legacy_dedup.nominate, rows, decode=_decode)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        copy = await asyncio.to_thread(legacy_dedup.backup, directory, stamp)
        marker = {'version': legacy_dedup.VERSION, 'stage': 'deciding', 'startedAt': datetime.now().isoformat(),
                  'backup': copy.relative_to(directory).as_posix(), 'pairs': pairs, 'decisions': {}, 'failures': 0}
        await asyncio.to_thread(_save_marker, directory, marker)
        logger.info(f"[LegacyDedup] {name}: 已备份，提名 {len(pairs)} 对疑似重复")
    if marker['stage'] == 'deciding':
        rows = legacy_dedup.candidates(await runtime.fact_store.aload_facts(name), await asyncio.to_thread(_read_archive, name))
        decisions = marker['decisions']
        for pair in marker['pairs']:
            if legacy_dedup.pair_key(pair) not in decisions and (pair['candidate'] not in rows or pair['existing'] not in rows):
                # Gone or folded away by everyday dedup since nomination.
                decisions[legacy_dedup.pair_key(pair)] = 'gone'
        waiting = [pair for pair in marker['pairs'] if legacy_dedup.pair_key(pair) not in decisions]
        for start in range(0, len(waiting), BATCH):
            if not still_idle():
                await asyncio.to_thread(_save_marker, directory, marker)
                return None
            batch = waiting[start:start + BATCH]
            answer = await _arbitrate(name, batch, rows)
            if answer is None:
                marker['failures'] = marker.get('failures', 0) + 1
                if marker['failures'] < MAX_FAILURES:
                    await asyncio.to_thread(_save_marker, directory, marker)
                    return None
                # Unanswerable pairs stay as two facts.
                answer = {legacy_dedup.pair_key(pair): 'keep_both' for pair in batch}
            marker['failures'] = 0
            decisions.update(answer)
            await asyncio.to_thread(_save_marker, directory, marker)
        marker['stage'] = 'decided'
        await asyncio.to_thread(_save_marker, directory, marker)
    folded = legacy_dedup.losers(marker['pairs'], marker['decisions'])
    mapping = legacy_dedup.survivors(folded)
    await _archive_folded(name, folded)
    reflections_changed, persona_changed = await _repoint(name, mapping) if mapping else (0, 0)
    archive = {row.get('id'): row for row in await asyncio.to_thread(_read_archive, name) if isinstance(row, dict)}
    live = {row.get('id'): row for row in await runtime.fact_store.aload_facts(name) if isinstance(row, dict)}
    # A survivor stands for what it replaced: absorbed then, absorbed now.
    absorbed = sorted({survivor for loser, survivor in mapping.items()
                       if archive.get(loser, {}).get('absorbed') and survivor in live and not live[survivor].get('absorbed')})
    if absorbed:
        await runtime.fact_store.amark_absorbed(name, absorbed)
    marker = {**marker, 'stage': 'done', 'finishedAt': datetime.now().isoformat(), 'archived': mapping,
              'reflectionsRepointed': reflections_changed, 'personaRepointed': persona_changed,
              'survivorsAbsorbed': absorbed}
    await asyncio.to_thread(_save_marker, directory, marker)
    logger.info(f"[LegacyDedup] {name}: 完成，{len(marker['pairs'])} 对中降级 {len(mapping)} 条，"
                f"改指反思 {reflections_changed} 条、画像 {persona_changed} 条")
    await _publish_marker(name)
    return marker


async def restore(name):
    """Bring back the facts the cleanup folded away, each under a new id.

    The archived rows stay as they are: reusing their ids would let a peer's
    snapshot, which still holds them archived, fold them away again at the next
    merge. Returns how many facts came back.
    """
    import hashlib
    directory = _directory(name)
    marker = legacy_dedup.read_marker(directory)
    if not marker or marker.get('stage') != 'done' or not marker.get('archived'):
        return 0
    store = runtime.fact_store
    async with store._get_persist_alock(name):
        archive = await asyncio.to_thread(_read_archive, name)
        facts = await store.aload_facts(name)
        restored_from = {row.get('restored_from') for row in facts if isinstance(row, dict)}
        now = datetime.now()
        added = []
        for row in archive:
            if (not isinstance(row, dict) or row.get('id') not in marker['archived']
                    or row['id'] in restored_from or not row.get('arbitration_archived_at')):
                continue
            copy = {key: value for key, value in row.items()
                    if key not in ('arbitration_archived_at', 'arbitration_reason', 'superseded_by', 'merged_from_ids')}
            digest = hashlib.sha256(f"{row['id']}|{now.isoformat()}".encode()).hexdigest()[:8]
            copy.update(id=f"fact_{now.strftime('%Y%m%d%H%M%S')}_{digest}", restored_from=row['id'],
                        created_at=now.isoformat())
            added.append(copy)
        if added:
            facts.extend(added)
            await store.asave_facts(name)
    return len(added)
