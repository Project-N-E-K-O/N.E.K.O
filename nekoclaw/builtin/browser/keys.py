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

"""Key names and what the debugging protocol needs to press them.

``parse_key`` reads what a model writes ("Enter", "Control+A", "Control++",
"Control+Shift+R") the way Chrome DevTools MCP does (``src/utils/keyboard.ts``):
the parts are joined with ``+``, the last part is the key and the ones before
it are modifiers. Each key becomes a ``KeyDefinition`` with the fields
``Input.dispatchKeyEvent`` takes; the values are those of a US keyboard.
"""

from __future__ import annotations

from dataclasses import dataclass

MODIFIER_BITS = {"Alt": 1, "Control": 2, "Meta": 4, "Shift": 8}


class UnknownKey(ValueError):
    """A key name that is not known; the text is for the model."""


@dataclass(frozen=True, slots=True)
class KeyDefinition:
    key: str
    code: str
    key_code: int
    # What the key types, when it types something.
    text: str | None = None
    # What it types with Shift held.
    shift_text: str | None = None


_NAMED: dict[str, KeyDefinition] = {
    "Enter": KeyDefinition("Enter", "Enter", 13, "\r"),
    "Tab": KeyDefinition("Tab", "Tab", 9),
    "Escape": KeyDefinition("Escape", "Escape", 27),
    "Backspace": KeyDefinition("Backspace", "Backspace", 8),
    "Delete": KeyDefinition("Delete", "Delete", 46),
    "Insert": KeyDefinition("Insert", "Insert", 45),
    "Home": KeyDefinition("Home", "Home", 36),
    "End": KeyDefinition("End", "End", 35),
    "PageUp": KeyDefinition("PageUp", "PageUp", 33),
    "PageDown": KeyDefinition("PageDown", "PageDown", 34),
    "ArrowLeft": KeyDefinition("ArrowLeft", "ArrowLeft", 37),
    "ArrowUp": KeyDefinition("ArrowUp", "ArrowUp", 38),
    "ArrowRight": KeyDefinition("ArrowRight", "ArrowRight", 39),
    "ArrowDown": KeyDefinition("ArrowDown", "ArrowDown", 40),
    "Space": KeyDefinition(" ", "Space", 32, " "),
    "Control": KeyDefinition("Control", "ControlLeft", 17),
    "Shift": KeyDefinition("Shift", "ShiftLeft", 16),
    "Alt": KeyDefinition("Alt", "AltLeft", 18),
    "Meta": KeyDefinition("Meta", "MetaLeft", 91),
    "CapsLock": KeyDefinition("CapsLock", "CapsLock", 20),
    "ContextMenu": KeyDefinition("ContextMenu", "ContextMenu", 93),
}
for _number in range(1, 13):
    _NAMED[f"F{_number}"] = KeyDefinition(f"F{_number}", f"F{_number}", 111 + _number)

_ALIASES = {
    "ctrl": "Control",
    "control": "Control",
    "cmd": "Meta",
    "command": "Meta",
    "win": "Meta",
    "windows": "Meta",
    "super": "Meta",
    "meta": "Meta",
    "option": "Alt",
    "alt": "Alt",
    "shift": "Shift",
    "esc": "Escape",
    "escape": "Escape",
    "return": "Enter",
    "enter": "Enter",
    "del": "Delete",
    "delete": "Delete",
    "ins": "Insert",
    "insert": "Insert",
    "backspace": "Backspace",
    "tab": "Tab",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pgup": "PageUp",
    "pagedown": "PageDown",
    "pgdn": "PageDown",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "arrowup": "ArrowUp",
    "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight",
    "space": "Space",
    "spacebar": "Space",
    "capslock": "CapsLock",
    "contextmenu": "ContextMenu",
}

# character -> (code, key code, the character Shift turns it into)
_PUNCTUATION = {
    "-": ("Minus", 189, "_"),
    "=": ("Equal", 187, "+"),
    "[": ("BracketLeft", 219, "{"),
    "]": ("BracketRight", 221, "}"),
    "\\": ("Backslash", 220, "|"),
    ";": ("Semicolon", 186, ":"),
    "'": ("Quote", 222, '"'),
    ",": ("Comma", 188, "<"),
    ".": ("Period", 190, ">"),
    "/": ("Slash", 191, "?"),
    "`": ("Backquote", 192, "~"),
}
_SHIFTED_DIGITS = ")!@#$%^&*("
_SHIFTED = {shifted: char for char, (_code, _key_code, shifted) in _PUNCTUATION.items()}
_SHIFTED.update({shifted: str(digit) for digit, shifted in enumerate(_SHIFTED_DIGITS)})


def key_definition(name: str) -> KeyDefinition:
    """Return the definition of one key by its name or its character."""
    if len(name) == 1:
        if name == " ":
            return _NAMED["Space"]
        if name.isalpha() and name.isascii():
            lower = name.lower()
            return KeyDefinition(lower, f"Key{lower.upper()}", ord(lower.upper()), lower, lower.upper())
        if name.isdigit() and name.isascii():
            return KeyDefinition(name, f"Digit{name}", ord(name), name, _SHIFTED_DIGITS[int(name)])
        if name in _PUNCTUATION:
            code, key_code, shifted = _PUNCTUATION[name]
            return KeyDefinition(name, code, key_code, name, shifted)
        if name in _SHIFTED:
            # A character that needs Shift ("+", "?", "!"): the key under it.
            return key_definition(_SHIFTED[name])
        raise UnknownKey(f"{name!r} is not a key of the keyboard; type such text with browser_type_text")
    canonical = name if name in _NAMED else _ALIASES.get(name.lower())
    if canonical is None and name[:1] in "fF" and name[1:].isdigit():
        canonical = f"F{int(name[1:])}"
    if canonical is None or canonical not in _NAMED:
        raise UnknownKey(
            f"{name!r} is not a key name. Use names such as Enter, Tab, Escape, Backspace, Delete, ArrowUp, "
            "ArrowDown, ArrowLeft, ArrowRight, Home, End, PageUp, PageDown, Space, F1 to F12, or a single character."
        )
    return _NAMED[canonical]


def parse_key(text: str) -> tuple[KeyDefinition, list[KeyDefinition]]:
    """Return the key of a combination and its modifiers, in the order they are named."""
    if not isinstance(text, str) or not text:
        raise UnknownKey("a key is required, for example Enter or Control+A")
    parts: list[str] = []
    rest = text
    while rest:
        if rest == "+":
            parts.append("+")
            break
        head, separator, tail = rest.partition("+")
        if not head:
            raise UnknownKey(f"key {text!r} has an empty part")
        parts.append(head.strip())
        if separator and not tail:
            raise UnknownKey(f"key {text!r} ends with a + that joins nothing")
        rest = tail
    needs_shift = len(parts[-1]) == 1 and parts[-1] in _SHIFTED
    key = key_definition(parts[-1])
    modifiers: list[KeyDefinition] = []
    for part in parts[:-1]:
        modifier = key_definition(part)
        if modifier.key not in MODIFIER_BITS:
            raise UnknownKey(f"{part!r} is not a modifier; the modifiers are Control, Shift, Alt and Meta")
        if modifier not in modifiers:
            modifiers.append(modifier)
    if needs_shift and _NAMED["Shift"] not in modifiers:
        modifiers.append(_NAMED["Shift"])
    return key, modifiers


def modifier_mask(modifiers: list[KeyDefinition]) -> int:
    mask = 0
    for modifier in modifiers:
        mask |= MODIFIER_BITS[modifier.key]
    return mask


def typed_text(key: KeyDefinition, mask: int) -> str | None:
    """Return what the key types while the modifiers of ``mask`` are held, or None."""
    if mask & (MODIFIER_BITS["Control"] | MODIFIER_BITS["Alt"] | MODIFIER_BITS["Meta"]):
        return None
    if mask & MODIFIER_BITS["Shift"]:
        return key.shift_text or key.text
    return key.text
