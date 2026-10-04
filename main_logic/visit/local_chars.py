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

import asyncio
import json

from utils.config_manager import get_config_manager
from utils.config_manager.reserved_schema import get_character_uid


class CharactersUnreadable(OSError):
    """``characters.json`` exists but cannot be read as a character map."""


def _check_characters_file(path: str) -> None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        # 没有配置文件就是全新安装：默认角色就是这台机器真实的角色
        return
    except (OSError, ValueError, RecursionError) as exc:
        raise CharactersUnreadable(f"characters.json unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("猫娘"), dict):
        raise CharactersUnreadable("characters.json has no character map")
    # 逐个核对：枚举（load_local_characters）会静默跳过的条目——名字 / 记录坏了、
    # 缺 id 或 id 坏了（启动时会补发，此刻缺就是异常）、id 重复——都不能当作「没有这个角色」，
    # 否则清除全部会漏掉它还照样报成功
    seen: set[str] = set()
    for name, entry in data["猫娘"].items():
        uid = get_character_uid(entry) if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name or uid is None or uid in seen:
            raise CharactersUnreadable(f"character entry {name!r} cannot be enumerated")
        seen.add(uid)


async def ensure_characters_readable() -> None:
    """Raise :class:`CharactersUnreadable` when ``characters.json`` exists but is unreadable.

    The regular loader silently falls back to the default characters; an
    erase that enumerates "every local character" must not take those
    defaults for the real scope.
    """
    path = str(get_config_manager().get_config_path("characters.json"))
    await asyncio.to_thread(_check_characters_file, path)


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
