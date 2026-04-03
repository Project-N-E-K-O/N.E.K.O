import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from utils.character_name import validate_character_name


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [
        "CON",
        "con",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        "COM1",
        "lpt9",
        "CON.txt",
        "com1.backup",
    ],
)
def test_validate_character_name_rejects_windows_reserved_device_names(name):
    result = validate_character_name(name, allow_dots=True)
    assert result.code == "reserved_device_name"


@pytest.mark.unit
@pytest.mark.parametrize("name", ["COM10", "LPT10", "CONSOLE", "AUX_PORT"])
def test_validate_character_name_allows_non_reserved_similar_names(name):
    result = validate_character_name(name, allow_dots=True)
    assert result.ok
