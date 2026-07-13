from __future__ import annotations


def bus_sort_key(value: object, cast: str | None = None) -> tuple[int, object]:
    """Return the canonical comparison key for Core and SDK bus lists."""
    normalized_cast = str(cast or "").strip().lower()
    if normalized_cast in {"int", "i"}:
        try:
            value = int(str(value).strip())
        except Exception:
            value = 0
    elif normalized_cast in {"float", "f"}:
        try:
            value = float(str(value).strip())
        except Exception:
            value = 0.0
    elif normalized_cast in {"str", "s"}:
        try:
            value = "" if value is None else str(value)
        except Exception:
            value = ""

    if value is None:
        return (2, "")
    if isinstance(value, (int, float)):
        return (0, value)
    return (1, str(value))
