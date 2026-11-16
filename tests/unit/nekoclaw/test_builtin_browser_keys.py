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

"""Key names of the browser tools: what a model writes and what the protocol is sent."""

from __future__ import annotations

import pytest

from nekoclaw.builtin.browser import keys
from nekoclaw.builtin.browser.keys import (
    MODIFIER_BITS,
    KeyDefinition,
    UnknownKey,
    key_definition,
    modifier_mask,
    parse_key,
    typed_text,
)

CONTROL = key_definition("Control")
SHIFT = key_definition("Shift")
ALT = key_definition("Alt")
META = key_definition("Meta")


# ---------------------------------------------------------------------------
# One key
# ---------------------------------------------------------------------------


def test_a_named_key_has_the_fields_the_protocol_takes() -> None:
    assert key_definition("Enter") == KeyDefinition("Enter", "Enter", 13, "\r")
    assert key_definition("Tab") == KeyDefinition("Tab", "Tab", 9)
    assert key_definition("ArrowDown") == KeyDefinition("ArrowDown", "ArrowDown", 40)
    assert key_definition("Control") == KeyDefinition("Control", "ControlLeft", 17)


def test_space_is_known_by_its_name_and_by_the_character() -> None:
    by_name = key_definition("Space")
    assert by_name == KeyDefinition(" ", "Space", 32, " ")
    assert key_definition(" ") == by_name
    assert key_definition("spacebar") == by_name


@pytest.mark.parametrize(
    ("written", "key"),
    [
        ("ctrl", "Control"),
        ("CTRL", "Control"),
        ("control", "Control"),
        ("cmd", "Meta"),
        ("Command", "Meta"),
        ("win", "Meta"),
        ("super", "Meta"),
        ("option", "Alt"),
        ("esc", "Escape"),
        ("ESC", "Escape"),
        ("return", "Enter"),
        ("enter", "Enter"),
        ("del", "Delete"),
        ("ins", "Insert"),
        ("PgUp", "PageUp"),
        ("pgdn", "PageDown"),
        ("pagedown", "PageDown"),
        ("up", "ArrowUp"),
        ("Down", "ArrowDown"),
        ("left", "ArrowLeft"),
        ("arrowright", "ArrowRight"),
        ("capslock", "CapsLock"),
        ("backspace", "Backspace"),
        ("tab", "Tab"),
        ("home", "Home"),
        ("END", "End"),
    ],
)
def test_other_spellings_of_a_key_name_are_understood(written: str, key: str) -> None:
    assert key_definition(written).key == key


def test_every_alias_names_a_key_that_exists() -> None:
    assert set(keys._ALIASES.values()) <= set(keys._NAMED)


def test_a_letter_is_its_key_whatever_its_case() -> None:
    expected = KeyDefinition("a", "KeyA", 65, "a", "A")
    assert key_definition("a") == expected
    assert key_definition("A") == expected
    assert key_definition("z").code == "KeyZ" and key_definition("z").key_code == 90


def test_a_digit_knows_the_character_shift_turns_it_into() -> None:
    assert key_definition("7") == KeyDefinition("7", "Digit7", 55, "7", "&")
    assert key_definition("0") == KeyDefinition("0", "Digit0", 48, "0", ")")


def test_punctuation_is_the_key_it_is_printed_on() -> None:
    assert key_definition("-") == KeyDefinition("-", "Minus", 189, "-", "_")
    assert key_definition("/") == KeyDefinition("/", "Slash", 191, "/", "?")
    assert key_definition("\\") == KeyDefinition("\\", "Backslash", 220, "\\", "|")


@pytest.mark.parametrize(
    ("character", "code"),
    [("+", "Equal"), ("?", "Slash"), ("!", "Digit1"), ("_", "Minus"), ("~", "Backquote"), (")", "Digit0"), ('"', "Quote")],
)
def test_a_character_that_needs_shift_is_the_key_under_it(character: str, code: str) -> None:
    definition = key_definition(character)
    assert definition.code == code
    assert definition.shift_text == character


def test_function_keys_run_from_f1_to_f12() -> None:
    assert key_definition("F1") == KeyDefinition("F1", "F1", 112)
    assert key_definition("F12") == KeyDefinition("F12", "F12", 123)
    assert key_definition("f5") == key_definition("F5")
    for name in ("F0", "F13", "F99"):
        with pytest.raises(UnknownKey, match="is not a key name"):
            key_definition(name)


@pytest.mark.parametrize("name", ["Entre", "PageDwon", "Fn", "Ctrl2", ""])
def test_an_unknown_key_name_is_refused_with_the_names_that_exist(name: str) -> None:
    with pytest.raises(UnknownKey) as caught:
        key_definition(name)
    assert "is not a key name" in str(caught.value)
    assert "Enter" in str(caught.value) and "F1 to F12" in str(caught.value)


@pytest.mark.parametrize("character", ["é", "中", "€", "\n"])
def test_a_character_no_key_types_is_refused_and_points_to_the_typing_tool(character: str) -> None:
    with pytest.raises(UnknownKey, match="browser_type_text"):
        key_definition(character)


def test_an_unknown_key_is_a_value_error() -> None:
    # The session turns ``UnknownKey`` into a refusal; nothing else may be raised for a bad name.
    assert issubclass(UnknownKey, ValueError)


# ---------------------------------------------------------------------------
# Combinations
# ---------------------------------------------------------------------------


def test_a_single_key_has_no_modifiers() -> None:
    assert parse_key("Enter") == (key_definition("Enter"), [])
    assert parse_key("a") == (key_definition("a"), [])


def test_the_last_part_is_the_key_and_the_ones_before_it_are_modifiers_in_order() -> None:
    key, modifiers = parse_key("Control+A")
    assert key == key_definition("a")
    assert modifiers == [CONTROL]
    key, modifiers = parse_key("Control+Shift+R")
    assert key == key_definition("r")
    assert modifiers == [CONTROL, SHIFT]
    key, modifiers = parse_key("Shift+Alt+Meta+Control+Tab")
    assert key == key_definition("Tab")
    assert modifiers == [SHIFT, ALT, META, CONTROL]


def test_modifiers_may_be_written_as_aliases_and_with_spaces() -> None:
    assert parse_key("ctrl+c") == (key_definition("c"), [CONTROL])
    assert parse_key("cmd+c") == (key_definition("c"), [META])
    assert parse_key("Control + Shift + r") == (key_definition("r"), [CONTROL, SHIFT])


def test_a_plus_that_is_the_key_comes_last_or_stands_alone() -> None:
    key, modifiers = parse_key("Control++")
    assert key.code == "Equal"
    # "+" is typed with Shift, which is added when it was not named.
    assert modifiers == [CONTROL, SHIFT]
    key, modifiers = parse_key("+")
    assert key.code == "Equal"
    assert modifiers == [SHIFT]


def test_shift_is_added_for_a_character_that_needs_it_but_never_twice() -> None:
    assert parse_key("?") == (key_definition("/"), [SHIFT])
    assert parse_key("Shift+?") == (key_definition("/"), [SHIFT])
    assert parse_key("Control+!") == (key_definition("1"), [CONTROL, SHIFT])
    # A letter or a digit is pressed as it is.
    assert parse_key("A") == (key_definition("a"), [])
    assert parse_key("5") == (key_definition("5"), [])


def test_a_modifier_named_twice_is_pressed_once() -> None:
    assert parse_key("Control+ctrl+a") == (key_definition("a"), [CONTROL])


def test_a_key_that_is_not_a_modifier_cannot_stand_before_the_last_part() -> None:
    with pytest.raises(UnknownKey, match="is not a modifier"):
        parse_key("a+b")
    with pytest.raises(UnknownKey, match="is not a modifier"):
        parse_key("Enter+Control")


def test_a_trailing_plus_joins_nothing() -> None:
    with pytest.raises(UnknownKey, match="ends with a \\+ that joins nothing"):
        parse_key("Control+")
    with pytest.raises(UnknownKey, match="ends with a \\+ that joins nothing"):
        parse_key("Control+Shift+")


def test_an_empty_part_is_refused() -> None:
    with pytest.raises(UnknownKey, match="empty part"):
        parse_key("+a")
    with pytest.raises(UnknownKey, match="empty part"):
        parse_key("Control++a")


@pytest.mark.parametrize("nothing", ["", None, 13, ["Enter"]])
def test_no_key_at_all_is_refused(nothing: object) -> None:
    with pytest.raises(UnknownKey, match="a key is required"):
        parse_key(nothing)  # type: ignore[arg-type]


def test_an_unknown_name_inside_a_combination_is_refused() -> None:
    with pytest.raises(UnknownKey, match="is not a key name"):
        parse_key("Control+Entre")
    with pytest.raises(UnknownKey, match="is not a key name"):
        parse_key("Contrl+a")


# ---------------------------------------------------------------------------
# Modifier bits and typed text
# ---------------------------------------------------------------------------


def test_the_modifier_bits_are_those_of_the_protocol() -> None:
    assert MODIFIER_BITS == {"Alt": 1, "Control": 2, "Meta": 4, "Shift": 8}
    assert modifier_mask([]) == 0
    assert modifier_mask([ALT]) == 1
    assert modifier_mask([CONTROL]) == 2
    assert modifier_mask([META]) == 4
    assert modifier_mask([SHIFT]) == 8
    assert modifier_mask([CONTROL, SHIFT]) == 10
    assert modifier_mask([ALT, CONTROL, META, SHIFT]) == 15


def test_a_key_types_its_text_and_shift_types_the_shifted_one() -> None:
    letter = key_definition("a")
    assert typed_text(letter, 0) == "a"
    assert typed_text(letter, MODIFIER_BITS["Shift"]) == "A"
    assert typed_text(key_definition("1"), MODIFIER_BITS["Shift"]) == "!"
    assert typed_text(key_definition("/"), MODIFIER_BITS["Shift"]) == "?"
    # Enter types a carriage return with or without Shift.
    assert typed_text(key_definition("Enter"), 0) == "\r"
    assert typed_text(key_definition("Enter"), MODIFIER_BITS["Shift"]) == "\r"


@pytest.mark.parametrize("modifier", ["Control", "Alt", "Meta"])
def test_nothing_is_typed_while_a_command_modifier_is_held(modifier: str) -> None:
    letter = key_definition("a")
    assert typed_text(letter, MODIFIER_BITS[modifier]) is None
    assert typed_text(letter, MODIFIER_BITS[modifier] | MODIFIER_BITS["Shift"]) is None


def test_a_key_that_types_nothing_types_nothing() -> None:
    for name in ("Tab", "Escape", "ArrowLeft", "F5", "Shift", "Backspace", "Delete"):
        assert typed_text(key_definition(name), 0) is None
        assert typed_text(key_definition(name), MODIFIER_BITS["Shift"]) is None
