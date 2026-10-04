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

"""Local characters as the visit code sees them: current name <-> stable ``character_uid``.

Visit files store the character's stable id (``own_char_uid``) next to a
name that may be stale after a rename; every write that names the character
(roster entries, memory_server endpoints) resolves the current name here.
"""

from __future__ import annotations

from utils.config_manager import get_config_manager
from utils.config_manager.reserved_schema import get_character_uid


async def load_local_characters() -> dict[str, str]:
    """Return ``{current name: character_uid}`` of every local character that has a valid id."""
    characters = await get_config_manager().aload_characters()
    catgirls = characters.get("猫娘") if isinstance(characters, dict) else None
    out: dict[str, str] = {}
    for name, data in (catgirls or {}).items():
        if isinstance(name, str) and name and isinstance(data, dict):
            uid = get_character_uid(data)
            if uid:
                out[name] = uid
    return out


async def resolve_char_name(character_uid: str) -> str | None:
    """Return the current name of the character with ``character_uid``, ``None`` once it is deleted."""
    for name, uid in (await load_local_characters()).items():
        if uid == character_uid:
            return name
    return None


async def resolve_char_uid(name: str) -> str | None:
    """Return the ``character_uid`` of the local character currently named ``name``."""
    return (await load_local_characters()).get(name)
