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

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


PROFILE_NAME_MAX_UNITS = 60

# Follow Windows filename rules to avoid problems storing character names under memory_dir/{name}/.
WINDOWS_FORBIDDEN_NAME_CHARS = frozenset('<>:"/\\|?*')
SAFE_CHARACTER_NAME_EXTRA_CHARS = frozenset({
    " ",
    "_",
    "-",
    "(",
    ")",
    "（",
    "）",
    "·",
    "・",
    "•",
    "'",
    "’",
})
WINDOWS_RESERVED_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CLOCK$"})
WINDOWS_RESERVED_DEVICE_NAME_PATTERN = re.compile(r"^(COM[1-9]|LPT[1-9])$", re.IGNORECASE)

# Reserved top-level route names: conflicting character names prevent /{lanlan_name} from matching correctly.
# Includes pages_router page routes, static-resource mount paths, and other top-level paths.
RESERVED_ROUTE_NAMES = frozenset({
    # pages_router page routes
    "l2d",
    "model_manager",
    "live2d_parameter_editor",
    "live2d_emotion_manager",
    "vrm_emotion_manager",
    "mmd_emotion_manager",
    "voice_clone",
    "voice_identity",
    "api_key",
    "character_card_manager",
    "cloudsave_manager",
    "memory_browser",
    "cookies_login",
    "chat",
    "subtitle",
    "agenthud",
    "toast",
    "card_maker",
    "soccer_demo",
    "badminton_demo",
    "air_basketball",
    "drawing_guess_demo",
    "watch_together",
    "jukebox",
    # Static resources / mount paths
    "static",
    "user_live2d",
    "user_live2d_local",
    "user_vrm",
    "user_mmd",
    "user_mods",
    "workshop",
    # API / WebSocket / system
    "api",
    "ws",
    "health",
})


@dataclass(frozen=True)
class CharacterNameValidationResult:
    normalized: str
    code: str | None = None
    invalid_char: str | None = None

    @property
    def ok(self) -> bool:
        return self.code is None


def count_character_name_units(name: str) -> int:
    return sum(1 if ord(ch) <= 0x7F else 2 for ch in name)


def trim_character_name_to_max_units(name: str, max_units: int) -> str:
    units = 0
    out = []
    for ch in str(name or ""):
        inc = 1 if ord(ch) <= 0x7F else 2
        if units + inc > max_units:
            break
        out.append(ch)
        units += inc
    return "".join(out)


def _is_space_separator(ch: str) -> bool:
    return unicodedata.category(ch) == "Zs"


def is_character_name_char_allowed(ch: str, *, allow_dots: bool = False) -> bool:
    if not ch:
        return False
    if ch in WINDOWS_FORBIDDEN_NAME_CHARS:
        return False
    if ch == ".":
        return allow_dots
    if unicodedata.category(ch).startswith("C"):
        return False
    if ch.isalnum():
        return True
    if ch in SAFE_CHARACTER_NAME_EXTRA_CHARS:
        return True
    if _is_space_separator(ch):
        return True
    return False


def find_invalid_character_name_char(name: str, *, allow_dots: bool = False) -> str | None:
    for ch in name:
        if not is_character_name_char_allowed(ch, allow_dots=allow_dots):
            return ch
    return None


def is_reserved_device_name(name: str) -> bool:
    base_name = str(name or "").split(".", 1)[0]
    if not base_name:
        return False
    upper_base_name = base_name.upper()
    return (
        upper_base_name in WINDOWS_RESERVED_DEVICE_NAMES
        or WINDOWS_RESERVED_DEVICE_NAME_PATTERN.fullmatch(upper_base_name) is not None
    )


def validate_character_name(
    value: object,
    *,
    allow_dots: bool = False,
    max_length: int | None = None,
    max_units: int | None = None,
) -> CharacterNameValidationResult:
    normalized = "" if value is None else str(value).strip()
    if not normalized:
        return CharacterNameValidationResult(normalized=normalized, code="empty")
    if "/" in normalized or "\\" in normalized:
        return CharacterNameValidationResult(normalized=normalized, code="contains_path_separator")
    if ".." in normalized:
        return CharacterNameValidationResult(normalized=normalized, code="path_traversal")
    # Directory name "." means the current directory and breaks character-data isolation;
    # Windows silently strips trailing periods, creating ambiguous paths (such as "foo." → "foo").
    if normalized == "." or normalized.endswith("."):
        return CharacterNameValidationResult(normalized=normalized, code="unsafe_dot")
    # NO leading-dot rule here, deliberately, and it was tried.
    #
    # The migration reserves dot-prefixed names inside memory_dir for its
    # workspaces, so it wants no character to hold one -- and CREATION
    # already sees to that: every path that makes a character validates with
    # ``allow_dots=False``, which refuses any dot at all. Adding the rule
    # here as well changed nothing there and broke the other direction: this
    # function is also the REQUEST and SNAPSHOT check, applied with
    # ``allow_dots=True`` to names that already exist, so an install
    # upgrading with a legacy ".Carol" would have started failing every
    # memory call with a 400 and reporting its own cloud snapshot invalid.
    #
    # Forbidding a name and breaking the data already carrying it are not the
    # same change. The first is done; the second is not wanted.
    if not allow_dots and "." in normalized:
        return CharacterNameValidationResult(normalized=normalized, code="contains_dot")
    if is_reserved_device_name(normalized):
        return CharacterNameValidationResult(normalized=normalized, code="reserved_device_name")
    if normalized in RESERVED_ROUTE_NAMES:
        return CharacterNameValidationResult(normalized=normalized, code="reserved_route_name")
    invalid_char = find_invalid_character_name_char(normalized, allow_dots=allow_dots)
    if invalid_char is not None:
        return CharacterNameValidationResult(
            normalized=normalized,
            code="invalid_character",
            invalid_char=invalid_char,
        )
    if max_units is not None and count_character_name_units(normalized) > max_units:
        return CharacterNameValidationResult(normalized=normalized, code="too_long_units")
    if max_length is not None and len(normalized) > max_length:
        return CharacterNameValidationResult(normalized=normalized, code="too_long_length")
    return CharacterNameValidationResult(normalized=normalized)
