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

"""Visit hooks of the character rename / delete transactions (design OD-13, section 3.2.6, PR-09b).

Called by ``characters_router/crud.py`` (rename, delete), the character-card
and workshop paths that create characters, and the workshop unsubscribe
delete, always under ``character_config_mutation_lock``. The logic lives in
:mod:`main_logic.visit.char_lifecycle`; this module binds the config
manager's directory and character list and the persona retirement.

A config object without a real ``config_dir`` (``str`` / ``Path``; the
application's ``ConfigManager`` always has one) has no visit data: every
hook is then a no-op, so it never resolves a relative or foreign directory.
"""

from __future__ import annotations

from pathlib import Path, PurePath
from typing import Any

from main_logic.visit import char_lifecycle, local_chars
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")


async def retire_persona(character_uid: str) -> bool:
    """``RetirePersona`` of the retirement: the persona store's locked delete."""
    from main_routers.visit_router.persona import retire_persona as retire

    return await retire(character_uid)


def _config_dir(config_manager: Any) -> Path | None:
    value = getattr(config_manager, "config_dir", None)
    return Path(value) if isinstance(value, (str, PurePath)) and str(value) else None


def names_loader(config_manager: Any) -> char_lifecycle.LoadNames:
    """``LoadNames`` reading the character config through ``config_manager``."""

    async def load() -> tuple[set[str], dict[str, str] | None]:
        config_path = getattr(config_manager, "get_runtime_config_path", None)
        if callable(config_path):
            # Ordinary reads silently fall back to cache/default characters for missing or corrupt files. Reconciliation would
            # misidentify surviving characters as deleted, clear markers without migration. Raise on unreadability so callers
            # treat reconciliation as incomplete. Markers prove prior characters; missing runtime data is not a fresh install.
            await local_chars.ensure_characters_file_readable(config_path("characters.json"), missing_ok=False)
        return char_lifecycle.names_of(await config_manager.aload_characters())

    return load


async def begin_rename(config_manager: Any, characters: Any, old: str, new: str,
                       character_uid: str | None) -> dict | None:
    """Write ``pending_rename`` (see :func:`char_lifecycle.begin_rename`); ``characters`` is the pre-rename config."""
    config_dir = _config_dir(config_manager)
    if config_dir is None:
        return None
    names, uid_of = char_lifecycle.names_of(characters)
    return await char_lifecycle.begin_rename(config_dir, old, new, character_uid, names=names, uid_of=uid_of)


async def settle_rename(config_manager: Any, marker: dict) -> bool:
    """Migrate or undo the visit data once the rename transaction ended; True once settled."""
    config_dir = _config_dir(config_manager)
    if config_dir is None:
        return False
    return await char_lifecycle.settle_rename(config_dir, marker, names_loader(config_manager))


async def begin_retire(config_manager: Any, name: str, character_uid: str | None) -> dict | None:
    """Add the ``pending_retire`` item of a character about to be deleted.

    A pending rename of this character is reconciled first (see
    :func:`char_lifecycle.begin_retire`); the caller holds the config lock,
    so the config read for it is the one the delete starts from.
    """
    config_dir = _config_dir(config_manager)
    if config_dir is None:
        return None
    return await char_lifecycle.begin_retire(
        config_dir, name, character_uid, load_names=names_loader(config_manager),
    )


async def settle_retire(config_manager: Any, item: dict) -> bool:
    """Retire the visit data once the delete committed (drop the item if it rolled back); True once settled."""
    config_dir = _config_dir(config_manager)
    if config_dir is None:
        return False
    try:
        names, uid_of = await names_loader(config_manager)()
        return await char_lifecycle.settle_retire(
            config_dir, item, names=names, uid_of=uid_of, retire_persona=retire_persona,
        )
    except Exception as exc:  # noqa: BLE001 - Called at deletion-transaction cleanup; leave marker for startup reconciliation without masking deletion outcome.
        logger.warning("visit retire: pending_retire item not settled: %r", exc)
        return False


NAME_BUSY = "busy"
"""``name_block_reason``: a pending retirement / rename holds the name; retrying later can clear it."""

NAME_AMBIGUOUS = "ambiguous"
"""``name_block_reason``: the name's rename can never be reconciled (its other name was taken by another
character); only deleting one of the two characters clears it."""


async def name_blocked(config_manager: Any, name: str) -> bool:
    """Whether ``name`` may not be taken yet by a new character (see :func:`name_block_reason`)."""
    return await name_block_reason(config_manager, name) is not None


async def name_block_reason(config_manager: Any, name: str) -> tuple[str, frozenset[str]] | None:
    """Why ``name`` may not be taken yet, ``None`` when it may.

    ``(NAME_BUSY, frozenset())`` or ``(NAME_AMBIGUOUS, {old, new})`` with the
    two names of the rename that can never be reconciled.

    Taken while a deleted character of that name is still being retired, or
    while it is the old or new name of an unreconciled rename (a new
    character would inherit the entries left under it, and the rename could
    never be told apart from it again). The caller holds the character-config
    lock, so the pending marker is retried once first (a transient failure
    clears without a restart). An unreadable roster blocks nothing. A rename
    that is ambiguous for good keeps blocking (an overwrite would replace the
    character's identity, and recovery could then never retire its data) and
    answers ``NAME_AMBIGUOUS``, so the user can be told which two characters
    to choose between instead of retrying.
    """
    config_dir = _config_dir(config_manager)
    if config_dir is None:
        return None
    state = await char_lifecycle.name_marker_state(config_dir, name)
    if state == char_lifecycle.NAME_RETIRING:
        try:
            await char_lifecycle.replay_retires(config_dir, names_loader(config_manager), retire_persona=retire_persona)
        except Exception as exc:  # noqa: BLE001 - Retry failure means still retiring: 409 / skip this card, never 500.
            logger.warning("visit retire: retry before reusing name %r failed: %r", name, exc)
            return NAME_BUSY, frozenset()
        state = await char_lifecycle.name_marker_state(config_dir, name)
        if state == char_lifecycle.NAME_RETIRING and await char_lifecycle.is_name_renaming(config_dir, name):
            # Retirement incomplete and name also belongs to a pending rename: still block, but reconcile the rename first.
            # Permanent ambiguity must say "delete one first"; completed roster retirement cannot resolve it, so "retry later" never helps.
            state = char_lifecycle.NAME_RENAMING
        retried = True
    else:
        retried = False
    if state == char_lifecycle.NAME_RENAMING:
        try:
            # Rename migration waits for target-name retirement: retry once now to avoid waiting until startup (unless already retried above).
            if not retried:
                await char_lifecycle.replay_retires(
                    config_dir, names_loader(config_manager), retire_persona=retire_persona)
        except Exception as exc:  # noqa: BLE001 - Reconcile even when retirement remains incomplete; rename keeps waiting.
            logger.warning("visit retire: retry before reconciling the rename holding %r failed: %r", name, exc)
        try:
            names, uid_of = await names_loader(config_manager)()
            groups: list[frozenset[str]] = []
            unsettled = await char_lifecycle.reconcile_renames(config_dir, names, uid_of, pending_groups=groups)
        except Exception as exc:  # noqa: BLE001 - Incomplete reconciliation blocks this name; leave marker for startup reconciliation.
            logger.warning("visit rename: pending_rename not reconciled before reusing %r: %r", name, exc)
        else:
            # Explicit check: ALL_NAMES (unreadable) is a different sentinel, not "delete a character". For a name only in the queue,
            # the preceding ambiguous rename is the blocker, not the name itself; answer "retry later".
            # Keep markers when rename waits for another name's retirement: its turn has not come, so do not ask users to delete characters.
            # Inspect only the transaction involving this name: other independent pending renames and retirements are unrelated.
            held = next((group for group in groups if name in group), None) if isinstance(unsettled, frozenset) else None
            if held is not None and not await char_lifecycle.names_retiring(config_dir, held):
                # Permanent ambiguity (another character occupies the other name): retries cannot resolve it; one character must be deleted.
                logger.warning("visit rename: ambiguous pending_rename holds %r", name)
                return NAME_AMBIGUOUS, held
        state = await char_lifecycle.name_marker_state(config_dir, name)
    if state in (char_lifecycle.NAME_RETIRING, char_lifecycle.NAME_RENAMING):
        return NAME_BUSY, frozenset()
    return None
