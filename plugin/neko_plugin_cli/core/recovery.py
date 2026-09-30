"""Shared names for dependency-sync recovery artifacts."""

from __future__ import annotations


RECOVERY_DIR_PREFIXES = (
    ".vendor.staging-",
    ".vendor.backup-",
    ".vendor.restore-",
)
RECOVERY_RUFF_EXCLUDE_PATTERNS = ("vendor", *RECOVERY_DIR_PREFIXES)
