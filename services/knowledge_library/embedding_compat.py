"""Derive compatible prefix views without modifying persisted vectors or specifications."""

from __future__ import annotations

import math

from .package_validate import compatible_dimension


def project_vector(vector: list, dimension: int) -> list[float] | None:
    """Return a finite nonzero normalized prefix; never pad missing components."""
    if not isinstance(vector, (list, tuple)) or len(vector) < dimension:
        return None
    values = vector[:dimension]
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        return None
    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm <= 0:
        return None
    return [value / norm for value in values]


def semantic_cosine(left: list, left_spec: dict | None, right: list,
                    right_spec: dict | None) -> float | None:
    """Score only usable views of two vectors with compatible full identities."""
    dimension = compatible_dimension(left_spec, right_spec)
    if dimension is None or not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)):
        return None
    if len(left) != left_spec["dimension"] or len(right) != right_spec["dimension"]:
        return None
    a, b = project_vector(left, dimension), project_vector(right, dimension)
    if a is None or b is None:
        return None
    return max(-1.0, min(1.0, math.fsum(x * y for x, y in zip(a, b))))
