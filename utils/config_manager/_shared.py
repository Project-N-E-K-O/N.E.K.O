# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared primitives for the config_manager package.

Home of the package logger, the local-state directory error type and the
small boolean coercion helper used across submodules.
"""
from pathlib import Path

from utils.logger_config import get_module_logger


# Keep the historical logger name of the former monolithic module so any
# log routing/filtering keyed on "utils.config_manager" stays intact.
logger = get_module_logger("utils.config_manager")


class LocalStateDirectoryError(OSError):
    """Raised when the local non-cloud state directory cannot be prepared."""

    local_state_directory_error = True

    def __init__(
        self,
        message,
        *,
        anchor_root=None,
        local_state_dir=None,
        failed_path=None,
        reason="",
    ):
        self.anchor_root = str(Path(anchor_root).expanduser().resolve(strict=False)) if anchor_root else ""
        self.local_state_dir = (
            str(Path(local_state_dir).expanduser().resolve(strict=False)) if local_state_dir else ""
        )
        self.failed_path = str(Path(failed_path).expanduser().resolve(strict=False)) if failed_path else ""
        self.reason = str(reason or "")
        parts = [str(message or "Local state directory is unavailable")]
        if self.anchor_root:
            parts.append(f"anchor_root={self.anchor_root}")
        if self.local_state_dir:
            parts.append(f"local_state_dir={self.local_state_dir}")
        if self.failed_path:
            parts.append(f"failed_path={self.failed_path}")
        if self.reason:
            parts.append(f"reason={self.reason}")
        super().__init__("\n".join(parts))


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ('true', '1', 'yes', 'on'):
            return True
        if lowered in ('false', '0', 'no', 'off', ''):
            return False
    if value is None:
        return default
    return bool(value)
