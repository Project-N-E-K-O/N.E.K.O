from __future__ import annotations

import pytest

from plugin.plugins.study_companion.json_utils import json_copy

pytestmark = pytest.mark.unit


def test_json_copy_recursively_normalizes_json_container_shape() -> None:
    source = {
        1: ["alpha", ("beta", {None: "none-key"})],
        "nested": {"tuple": (1, 2)},
    }

    copied = json_copy(source)

    assert copied == {
        "1": ["alpha", ["beta", {"None": "none-key"}]],
        "nested": {"tuple": [1, 2]},
    }
    assert copied is not source
    assert copied["1"] is not source[1]


def test_json_copy_keeps_unsupported_scalars_unchanged() -> None:
    marker = object()

    assert json_copy(marker) is marker
    assert json_copy(None) is None
    assert json_copy(3.14) == 3.14
