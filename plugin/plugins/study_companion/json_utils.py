from __future__ import annotations

from typing import Any


def json_copy(value: Any) -> Any:
    """Return a JSON-shaped recursive copy for store/config payloads.

    Supported containers are dict, list, and tuple. Dict keys are normalized
    with str(key) because the plugin persists these objects through JSON, whose
    object keys are strings. Tuples are copied as lists for the same JSON
    compatibility reason. Scalars, None, sets, datetimes, and custom objects are
    returned unchanged; callers that pass non-JSON-serializable values still need
    to rely on the final json.dumps/default handling or reject them upstream.
    This preserves the historical shallow behavior for unsupported types while
    making container keys and tuple/list shape deterministic.
    """
    if isinstance(value, dict):
        return {str(key): json_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_copy(item) for item in value]
    if isinstance(value, tuple):
        return [json_copy(item) for item in value]
    return value
