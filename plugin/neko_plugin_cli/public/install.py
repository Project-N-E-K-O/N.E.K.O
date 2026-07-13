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
    validate_plugin_manifest_types,
    verify_payload_hash,
)
from ..core.models import InstalledPlugin, InstallResult
from ..core.install import PackageInstaller, install_package

__all__ = [
    "PackageInstaller",
    "InstallResult",
    "InstalledPlugin",
    "collect_plugin_folders",
    "compute_archive_payload_hash",
    "read_manifest",
    "read_metadata",
    "safe_archive_path",
    "install_package",
    "validate_package_type",
    "validate_plugin_layout",
    "validate_plugin_manifest_types",
    "verify_payload_hash",
]
