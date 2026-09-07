"""Pure HTML content field validation shared by SDK and display routing."""
from __future__ import annotations

import json
from typing import Any


def card_fields(fields: dict[str, Any]) -> dict[str, Any]:
    for key in ("html", "css", "summary", "title"):
        if key in fields and not isinstance(fields[key], str):
            raise TypeError(f"{key} must be a string")
    if "actions" in fields:
        actions = fields["actions"]
        if not isinstance(actions, dict):
            raise TypeError("actions must be a dict")
        for name, action in actions.items():
            if not isinstance(name, str) or not name or not isinstance(action, dict):
                raise TypeError("actions must map non-empty button IDs to action objects")
            if not isinstance(action.get("entry"), str) or not action["entry"]:
                raise ValueError("each card action requires an entry ID")
            if not isinstance(action.get("args", {}), dict):
                raise TypeError("card action args must be a dict")
    # Snapshot mutable arguments before the asynchronous submission.
    return json.loads(json.dumps(fields, allow_nan=False))

