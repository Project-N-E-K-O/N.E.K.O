"""Stable public package inspection exports."""

from __future__ import annotations

from ..core.archive_utils import (
    collect_plugin_folders,
    collect_profile_names,
    compute_archive_payload_hash,
    read_manifest,
    read_metadata,
    validate_package_type,
    validate_plugin_layout,
    verify_payload_hash,
)
from ..core.inspect import PackageInspector, inspect_package
from ..core.models import InspectedPackagePlugin, PackageInspectResult

__all__ = [
    "InspectedPackagePlugin",
    "PackageInspectResult",
    "PackageInspector",
    "collect_plugin_folders",
    "collect_profile_names",
    "compute_archive_payload_hash",
    "inspect_package",
    "read_manifest",
    "read_metadata",
    "validate_package_type",
    "validate_plugin_layout",
    "verify_payload_hash",
]
