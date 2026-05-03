from __future__ import annotations

from typing import Any


class ValueHelpersMixin:
    def _first_present(self, *values: Any, default: Any = None) -> Any:
        for value in values:
            if value is not None:
                return value
        return default
