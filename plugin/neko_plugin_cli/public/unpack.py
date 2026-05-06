"""Stable public package extraction exports."""

from __future__ import annotations

from ..core.archive_utils import (
    collect_plugin_folders,
    compute_archive_payload_hash,
    read_manifest,
    read_metadata,
    safe_archive_path,
    validate_package_type,
    validate_plugin_layout,
    verify_payload_hash,
)
from ..core.models import UnpackedPlugin, UnpackResult
from ..core.unpack import PackageUnpacker, unpack_package

__all__ = [
    "PackageUnpacker",
    "UnpackResult",
    "UnpackedPlugin",
    "collect_plugin_folders",
    "compute_archive_payload_hash",
    "read_manifest",
    "read_metadata",
    "safe_archive_path",
    "unpack_package",
    "validate_package_type",
    "validate_plugin_layout",
    "verify_payload_hash",
]
