from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from plugin.server.infrastructure.config_fingerprint import (
    canonicalize_config,
    fingerprint_config,
)


@pytest.mark.plugin_unit
def test_fingerprint_is_independent_of_mapping_insertion_order() -> None:
    first = {"z": {"b": 2, "a": 1}, "a": ["one", "two"]}
    second = {"a": ["one", "two"], "z": {"a": 1, "b": 2}}

    assert fingerprint_config(first) == fingerprint_config(second)
    assert canonicalize_config(first) == {
        "a": ["one", "two"],
        "z": {"a": 1, "b": 2},
    }


@pytest.mark.plugin_unit
def test_fingerprint_preserves_array_order_and_scalar_types() -> None:
    assert fingerprint_config({"items": [1, 2]}) != fingerprint_config({"items": [2, 1]})
    assert fingerprint_config({"value": 1}) != fingerprint_config({"value": 1.0})
    assert fingerprint_config({"value": True}) != fingerprint_config({"value": 1})


@pytest.mark.plugin_unit
def test_toml_special_scalars_are_tagged_and_stable() -> None:
    values = {
        "date": date(2026, 10, 1),
        "datetime": datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc),
        "time": time(12, 30, 5),
    }
    normalized = canonicalize_config(values)

    assert normalized == {
        "date": {"__neko_config_value_type__": "date", "value": "2026-10-01"},
        "datetime": {
            "__neko_config_value_type__": "datetime",
            "value": "2026-10-01T12:30:00+00:00",
        },
        "time": {"__neko_config_value_type__": "time", "value": "12:30:05"},
    }
    assert fingerprint_config(values) == fingerprint_config(dict(reversed(list(values.items()))))


@pytest.mark.plugin_unit
def test_fingerprint_separates_tagged_scalars_from_user_mappings() -> None:
    ordinary_mapping = {
        "value": {
            "__neko_config_value_type__": "date",
            "value": "2026-10-01",
        }
    }
    actual_date = {"value": date(2026, 10, 1)}

    assert fingerprint_config(ordinary_mapping) != fingerprint_config(actual_date)


@pytest.mark.plugin_unit
def test_unsupported_values_fail_without_rendering_the_value() -> None:
    class SecretValue:
        def __repr__(self) -> str:
            return "super-secret-value"

    with pytest.raises(TypeError, match="SecretValue") as exc_info:
        fingerprint_config({"secret": SecretValue()})

    assert "super-secret-value" not in str(exc_info.value)
