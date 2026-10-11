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

from main_logic.visit import char_lifecycle
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
    """Add the ``pending_retire`` item of a character about to be deleted."""
    config_dir = _config_dir(config_manager)
    if config_dir is None:
        return None
    return await char_lifecycle.begin_retire(config_dir, name, character_uid)


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
    except Exception as exc:  # noqa: BLE001 - 调用方在删除事务收尾处调：标记留给启动对账，不盖掉删除结果
        logger.warning("visit retire: pending_retire item not settled: %r", exc)
        return False


async def name_blocked(config_manager: Any, name: str) -> bool:
    """Whether ``name`` may not be taken yet: a deleted character of that name is still being retired.

    The caller holds the character-config lock, so the pending retirements
    are retried once first (a transient failure clears without a restart).
    """
    config_dir = _config_dir(config_manager)
    if config_dir is None or not await char_lifecycle.is_name_retiring(config_dir, name):
        return False
    await char_lifecycle.replay_retires(config_dir, names_loader(config_manager), retire_persona=retire_persona)
    return await char_lifecycle.is_name_retiring(config_dir, name)
