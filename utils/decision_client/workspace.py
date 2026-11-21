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

"""Bailian (DashScope) workspace ID validation and dedicated-domain URL building.

The native ``decision-model-preview`` endpoint is only served from a
workspace-dedicated host such as
``https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com``. The workspace ID is
user input that ends up inside a hostname, so it is validated as a single DNS
label (ASCII letters, digits and ``-``; 1-63 characters; no leading or
trailing ``-``) on both the save path and the read path. Anything else is
rejected so a crafted value can never point requests at another host.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

WORKSPACE_ID_MAX_LENGTH = 63
WORKSPACE_ID_PLACEHOLDER = "{workspace_id}"

_WORKSPACE_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class InvalidWorkspaceIdError(ValueError):
    """Raised when a workspace ID is not a single safe DNS label."""


def normalize_workspace_id(value: Any) -> str:
    """Return the canonical (lower-case) workspace ID, or ``""`` when unset.

    Raises :class:`InvalidWorkspaceIdError` for any value that is not a single
    DNS label. Non-ASCII input is rejected before case folding so look-alike
    characters cannot fold into ASCII.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InvalidWorkspaceIdError("workspace ID must be a string")
    cleaned = value.strip()
    if not cleaned:
        return ""
    if not cleaned.isascii() or len(cleaned) > WORKSPACE_ID_MAX_LENGTH:
        raise InvalidWorkspaceIdError("workspace ID has an invalid format")
    cleaned = cleaned.lower()
    if not _WORKSPACE_ID_RE.fullmatch(cleaned):
        raise InvalidWorkspaceIdError("workspace ID has an invalid format")
    return cleaned


def safe_workspace_id(value: Any) -> str:
    """Read-side variant of :func:`normalize_workspace_id` that maps invalid input to ``""``."""
    try:
        return normalize_workspace_id(value)
    except InvalidWorkspaceIdError:
        return ""


def build_workspace_decision_url(template: Any, workspace_id: Any) -> str:
    """Fill *template*'s ``{workspace_id}`` placeholder, or return ``""``.

    Returns ``""`` when the workspace ID is unset or invalid, when the template
    has no placeholder, or when the result is not an ``https`` URL whose host
    starts with the workspace ID label.
    """
    tpl = str(template or "").strip()
    ws = safe_workspace_id(workspace_id)
    if not ws or WORKSPACE_ID_PLACEHOLDER not in tpl:
        return ""
    url = tpl.replace(WORKSPACE_ID_PLACEHOLDER, ws)
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.startswith(f"{ws}.") or parsed.username or parsed.password:
        return ""
    return url
