"""Shared primitives for voice registration provider adapters."""


class VoiceCloneError(Exception):
    """Compatibility base error for voice registration failures."""


def first_nested_value(payload: object, names: set[str]) -> object:
    """Find the first non-empty value whose key is in ``names``."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in names and value not in (None, ""):
                return value
        for value in payload.values():
            found = first_nested_value(value, names)
            if found not in (None, ""):
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = first_nested_value(item, names)
            if found not in (None, ""):
                return found
    return None
