from __future__ import annotations

from dataclasses import dataclass
import unicodedata


PROFILE_NAME_MAX_UNITS = 20

# 与 Windows 文件名规则保持兼容，避免角色名写入 memory_dir/{name}/ 时踩坑。
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
    if not allow_dots and "." in normalized:
        return CharacterNameValidationResult(normalized=normalized, code="contains_dot")
    if ".." in normalized:
        return CharacterNameValidationResult(normalized=normalized, code="path_traversal")
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
