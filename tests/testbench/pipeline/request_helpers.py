"""Shared helpers for parsing user-supplied values out of HTTP bodies.

Background (P24 §13.3 + AGENT_NOTES §3A A10)
----------------------------------------------
FastAPI auto-coerces query/path parameters via Pydantic type hints —
``?flag=true`` becomes ``True``, ``?x=1.5`` becomes ``1.5``. But when a
router accepts a raw ``dict[str, Any]`` / ``body: dict`` (because the
incoming filter shape is too dynamic for a Pydantic model, or the
endpoint was written before Pydantic models were common here), user
values arrive as **raw strings**:

* ``body["passed"] = "false"``   → ``bool("false") == True``  (always truthy!)
* ``body["min_overall"] = "0.5"`` → ``float("0.5")`` works, but an empty
  string or missing key raises or silently returns the wrong default

These helpers normalize. Every router that reads filter/bool/float
fields from a raw dict body MUST use them — see
``.cursor/rules/single-append-message.mdc`` family (future Rule 7 will
enforce).

Originally :func:`_coerce_bool` / :func:`_coerce_float` lived as private
helpers inside ``routers/judge_router.py`` only. Sweep showed no other
router used them, but several router endpoints with ``body: dict``
(memory_router, config_router, persona_router) are candidates for the
same exposure. Moving to ``pipeline/`` and re-exporting keeps those
endpoints one import away from the correct parser.

Safe range notes
----------------
* **bool**: accepts ``{true, 1, yes, y}`` → ``True`` and the reverse
  for ``False``; everything else → ``None``. ``None`` propagates
  "no filter applied" semantics (do not default to ``False``).
* **float**: any ``float()``-parseable numeric; empty string / ``None``
  / junk → ``None``.
* **int** (new in P24): same shape, delegates to ``float()`` first to
  tolerate ``"1.0"``.

All three return ``Optional[T]`` precisely because "missing / empty /
malformed" should behave the same as "not supplied".
"""
from __future__ import annotations

from typing import Any


def coerce_bool(value: Any) -> bool | None:
    """Normalize user-supplied bool-ish values to ``True``/``False``/``None``.

    Returns ``None`` for missing / empty / unknown inputs so callers can
    distinguish "filter not applied" from "filter applied with value X".

    Examples
    --------
    >>> coerce_bool("true")
    True
    >>> coerce_bool("false")
    False
    >>> coerce_bool("maybe")
    >>> coerce_bool(None)
    >>> coerce_bool(0)
    False
    >>> coerce_bool(1)
    True
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return None


def coerce_float(value: Any) -> float | None:
    """Coerce a bodily numeric field; ``None`` on empty / invalid.

    Examples
    --------
    >>> coerce_float("0.5")
    0.5
    >>> coerce_float(None)
    >>> coerce_float("")
    >>> coerce_float("not a number")
    >>> coerce_float(42)
    42.0
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coerce_int(value: Any) -> int | None:
    """Coerce a bodily integer field; ``None`` on empty / invalid.

    Tolerates ``"1.0"``-style strings by delegating to float first, so
    ``body["max"] = "10.0"`` → ``10`` rather than ``None``. Truncates
    fractional input (``"1.5"`` → ``1``) silently, matching how Python's
    ``int(float_val)`` behaves — callers who want strict integer
    semantics should pre-validate.

    Examples
    --------
    >>> coerce_int("10")
    10
    >>> coerce_int("10.0")
    10
    >>> coerce_int("10.5")
    10
    >>> coerce_int(None)
    """
    fval = coerce_float(value)
    if fval is None:
        return None
    try:
        return int(fval)
    except (OverflowError, ValueError):
        # inf / -inf → OverflowError; nan → ValueError
        return None


__all__ = ["coerce_bool", "coerce_float", "coerce_int"]
