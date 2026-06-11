from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


# After PR #1188 + #1191 the runtime pip-install path was removed for rapidocr
# and dxcam (they're bundled main-program deps now). Textractor still has a
# real runtime install flow (it's a desktop binary, not a Python wheel), so
# `is_installable_missing_dependency` continues to filter on `can_install`.
# Inspectors for rapidocr/dxcam now return `can_install=False` whenever the
# package is unimportable, so missing-cohort dev envs no longer surface a
# user-actionable warning here — the bundled_hint banner from #1191 covers
# the source-install case directly in the OCR backend cards.
MISSING_DEPENDENCY_DETAILS = frozenset({"missing"})
INSPECTION_FAILED_DEPENDENCY_DETAILS = frozenset({"inspection_failed"})


def is_installable_missing_dependency(status: object) -> bool:
    if not isinstance(status, Mapping):
        return False
    if status.get("installed") is not False:
        return False
    if status.get("install_supported", True) is False:
        return False
    if status.get("can_install", True) is False:
        return False
    detail = str(status.get("detail") or "").strip()
    return detail in MISSING_DEPENDENCY_DETAILS


def infer_missing_dependencies(
    dependencies: Iterable[tuple[str, object]],
) -> list[str]:
    return [
        name
        for name, status in dependencies
        if is_installable_missing_dependency(status)
    ]


def is_dependency_inspection_failed(status: object) -> bool:
    if not isinstance(status, Mapping):
        return False
    detail = str(status.get("detail") or "").strip()
    return detail in INSPECTION_FAILED_DEPENDENCY_DETAILS


def infer_inspection_failed_dependencies(
    dependencies: Iterable[tuple[str, object]],
) -> list[str]:
    return [
        name
        for name, status in dependencies
        if is_dependency_inspection_failed(status)
    ]
