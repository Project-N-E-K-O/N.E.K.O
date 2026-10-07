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

"""Local facts the visit endpoints need: family names, character cards, display labels.

Everything is read through the config manager off the event loop
(``aget_character_data``); nothing here writes. Tests replace
:func:`load_character_context` instead of touching the real runtime root.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from config.prompts.prompts_visit import get_family_neutral_term, get_visit_speaker_header
from utils.language_utils import get_global_language_full

# 昵称一栏常见的分隔写法：中英文逗号、顿号、斜杠、分号、空白（与 memory.stop_names 的拆法一致，
# 另认斜杠）。「Alice Ally」是两个称呼，单独出现的「Alice」也要替换
_NICKNAME_SPLIT_RE = re.compile(r"[,，、/;；\s]+")
# 多词档案名（「Alice Smith」「约翰·史密斯」）：整名之外，每个词也单独算亲人名，只写名或姓也要替换
_PROFILE_NAME_SPLIT_RE = re.compile(r"[\s·・]+")
_NAME_PART_MIN_CHARS = 2


@dataclass(frozen=True)
class CharacterContext:
    """Snapshot of the local characters as the visit code sees them.

    ``cards`` maps every character name to its raw prompt card
    (``lanlan_prompt_map``); ``family_names`` are the names the family member
    goes by (profile name plus nicknames), replaced by the neutral family term
    in everything that leaves this machine.
    """

    family_names: tuple[str, ...] = ()
    cards: Mapping[str, str] = field(default_factory=dict)

    @property
    def char_names(self) -> tuple[str, ...]:
        return tuple(self.cards)

    def card(self, name: str) -> str | None:
        value = self.cards.get(name)
        return value if isinstance(value, str) else None


def family_names_of(master: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Profile name and nicknames of the family member, deduplicated, in profile order."""
    if not isinstance(master, Mapping):
        return ()
    out: list[str] = []
    for key in ("档案名", "昵称"):
        raw = master.get(key)
        if not isinstance(raw, str):
            continue
        if key == "档案名":
            parts = [raw, *(p for p in _PROFILE_NAME_SPLIT_RE.split(raw.strip())
                            if len(p.strip()) >= _NAME_PART_MIN_CHARS)]
        else:
            parts = _NICKNAME_SPLIT_RE.split(raw)
        for part in parts:
            name = part.strip()
            if name and name not in out:
                out.append(name)
    return tuple(out)


async def load_character_context() -> CharacterContext:
    """Read the family names and every character card (off the event loop)."""
    from utils.config_manager import get_config_manager

    data = await get_config_manager().aget_character_data()
    master = data[2] if len(data) > 2 else None
    prompt_map = data[5] if len(data) > 5 else None
    cards = {
        str(name): str(card or "")
        for name, card in (prompt_map.items() if isinstance(prompt_map, Mapping) else ())
    }
    return CharacterContext(family_names=family_names_of(master), cards=cards)


def prompt_lang() -> str:
    """Language of visit-generated text and labels (the UI language, ``zh-TW`` kept)."""
    try:
        return get_global_language_full()
    except Exception:  # noqa: BLE001 - 语言只影响措辞，读不到按英文
        return "en"


def speaker_label(speaker: str, lang: str | None) -> str:
    """The bare speaker label (``own_cat`` / ``peer_cat`` / ...) without its brackets."""
    return get_visit_speaker_header(speaker, lang).strip("[] ")


def protected_display_names(
    lang: str | None, family_names: Iterable[str], char_names: Iterable[str],
) -> tuple[str, ...]:
    """Names a peer-reported display name must never take over (OD-23).

    Every local character, the family names, the neutral family term and the
    four visit speaker labels -- the same set the memory commit uses.
    """
    labels = tuple(speaker_label(s, lang) for s in ("own_cat", "own_human", "peer_cat", "peer_human"))
    return (*char_names, *labels, get_family_neutral_term(lang), *family_names)
