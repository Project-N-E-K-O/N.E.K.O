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

"""Bailian (DashScope) workspace API Host parsing and decision URL building.

The native ``decision-model-preview`` endpoint is only served from a
workspace-dedicated host ``{WorkspaceId}.{region}.maas.aliyuncs.com`` (the
"API Host" column of the Bailian console's workspace page). Users may enter
that host, a URL on it, or only the workspace ID. The stored value is
canonical: the full host, or the bare ID, whose region then follows the chat
endpoint the API key resolved to.

Both the workspace ID and the region end up inside a hostname, so each is
validated as a single DNS label (ASCII letters, digits and ``-``; 1-63
characters; no leading or trailing ``-``) on the save path and the read path,
and the host must end with ``.maas.aliyuncs.com``. Anything else is rejected
so a crafted value can never point requests at another host.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping

WORKSPACE_HOST_SUFFIX = ".maas.aliyuncs.com"
WORKSPACE_HOST_PLACEHOLDER = "{workspace_host}"
# Longest accepted input; a pasted URL may carry a path that is dropped.
WORKSPACE_HOST_INPUT_MAX_LENGTH = 512

# Regions where the Bailian decision model API reference lists an endpoint.
DECISION_REGIONS = frozenset({"cn-beijing", "ap-southeast-1"})

# Shared DashScope chat hosts and their region, used for a bare workspace ID.
_SHARED_CHAT_HOST_REGIONS: Mapping[str, str] = {
    "dashscope.aliyuncs.com": "cn-beijing",
    "dashscope-intl.aliyuncs.com": "ap-southeast-1",
    "dashscope-us.aliyuncs.com": "us-east-1",
    "cn-hongkong.dashscope.aliyuncs.com": "cn-hongkong",
}

_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")

# Reasons a workspace decision endpoint is unavailable (shown on the settings page).
WORKSPACE_MISSING = "workspace_missing"
REGION_UNSUPPORTED = "region_unsupported"


class InvalidWorkspaceHostError(ValueError):
    """Raised when the input is neither a workspace ID nor a Bailian workspace API Host."""


@dataclass(frozen=True)
class WorkspaceHost:
    """A parsed workspace reference; ``region`` is ``""`` when only the ID was given."""

    workspace_id: str
    region: str = ""

    @property
    def canonical(self) -> str:
        """Stored form: the full API Host, or the bare ID when no region was given."""
        if not self.region:
            return self.workspace_id
        return f"{self.workspace_id}.{self.region}{WORKSPACE_HOST_SUFFIX}"

    def effective_region(self, chat_base_url: Any = "") -> str:
        """The host's own region, else the region of the chat endpoint, else ``""``."""
        return self.region or region_from_chat_url(chat_base_url)


def _is_dns_label(value: str) -> bool:
    return bool(_DNS_LABEL_RE.fullmatch(value))


def parse_workspace_host(value: Any) -> WorkspaceHost | None:
    """Parse a workspace ID, API Host or URL on it; ``None`` when unset.

    Raises :class:`InvalidWorkspaceHostError` for anything else. Non-ASCII
    input is rejected before case folding so look-alike characters cannot fold
    into ASCII.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidWorkspaceHostError("workspace host must be a string")
    cleaned = value.strip()
    if not cleaned:
        return None
    if not cleaned.isascii() or len(cleaned) > WORKSPACE_HOST_INPUT_MAX_LENGTH:
        raise InvalidWorkspaceHostError("workspace host has an invalid format")
    cleaned = cleaned.lower()
    for scheme in ("https://", "http://"):
        if cleaned.startswith(scheme):
            cleaned = cleaned[len(scheme):]
            break
    # A pasted URL keeps only its host; userinfo or a port fail the label checks.
    cleaned = re.split(r"[/?#]", cleaned, maxsplit=1)[0]
    if "." not in cleaned:
        if not _is_dns_label(cleaned):
            raise InvalidWorkspaceHostError("workspace host has an invalid format")
        return WorkspaceHost(cleaned)
    if not cleaned.endswith(WORKSPACE_HOST_SUFFIX):
        raise InvalidWorkspaceHostError("workspace host has an invalid format")
    labels = cleaned[: -len(WORKSPACE_HOST_SUFFIX)].split(".")
    if len(labels) != 2 or not all(_is_dns_label(label) for label in labels):
        raise InvalidWorkspaceHostError("workspace host has an invalid format")
    return WorkspaceHost(labels[0], labels[1])


def normalize_workspace_host(value: Any) -> str:
    """Return the canonical stored form, or ``""`` when unset; raises on invalid input."""
    parsed = parse_workspace_host(value)
    return parsed.canonical if parsed else ""


def safe_workspace_host(value: Any) -> str:
    """Read-side variant of :func:`normalize_workspace_host` that maps invalid input to ``""``."""
    try:
        return normalize_workspace_host(value)
    except InvalidWorkspaceHostError:
        return ""


def workspace_id_of(value: Any) -> str:
    """The workspace ID part of a host or bare ID, or ``""`` when unset or invalid."""
    try:
        parsed = parse_workspace_host(value)
    except InvalidWorkspaceHostError:
        return ""
    return parsed.workspace_id if parsed else ""


def stored_workspace_host(core_cfg: Mapping[str, Any], host_field: str, legacy_id_field: str) -> str:
    """Read the stored workspace host, falling back to the legacy ID-only field.

    The legacy field is read only while the host field is empty, so a saved
    host always wins over a leftover ID. Invalid values read as unset.
    """
    raw = core_cfg.get(host_field)
    if isinstance(raw, str) and raw.strip():
        return safe_workspace_host(raw)
    return safe_workspace_host(core_cfg.get(legacy_id_field))


def region_from_chat_url(url: Any) -> str:
    """Region of a DashScope chat endpoint (shared or workspace host), or ``""``."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        host = (urllib.parse.urlsplit(raw).hostname or "").lower()
    except ValueError:
        return ""
    if host in _SHARED_CHAT_HOST_REGIONS:
        return _SHARED_CHAT_HOST_REGIONS[host]
    if host.endswith(WORKSPACE_HOST_SUFFIX):
        try:
            parsed = parse_workspace_host(host)
        except InvalidWorkspaceHostError:
            return ""
        return parsed.region if parsed else ""
    return ""


def resolve_workspace_decision_url(template: Any, stored: Any, chat_base_url: Any = "") -> tuple[str, str]:
    """Build the workspace decision URL; returns ``(url, "")`` or ``("", reason)``.

    *template* carries the ``{workspace_host}`` placeholder. *stored* is the
    saved workspace value (host or bare ID). *chat_base_url* is the chat
    endpoint the API key resolved to; it supplies the region for a bare ID.
    The reason is :data:`REGION_UNSUPPORTED` when the region has no decision
    endpoint and :data:`WORKSPACE_MISSING` otherwise.
    """
    try:
        parsed = parse_workspace_host(stored)
    except InvalidWorkspaceHostError:
        parsed = None
    if parsed is None:
        # Say "this region has none" before "fill in a workspace": an ID would not help there.
        chat_region = region_from_chat_url(chat_base_url)
        if chat_region and chat_region not in DECISION_REGIONS:
            return "", REGION_UNSUPPORTED
        return "", WORKSPACE_MISSING
    region = parsed.effective_region(chat_base_url)
    if not region or region not in DECISION_REGIONS:
        return "", REGION_UNSUPPORTED
    host = f"{parsed.workspace_id}.{region}{WORKSPACE_HOST_SUFFIX}"
    tpl = str(template or "").strip()
    if WORKSPACE_HOST_PLACEHOLDER not in tpl:
        return "", WORKSPACE_MISSING
    url = tpl.replace(WORKSPACE_HOST_PLACEHOLDER, host)
    try:
        split = urllib.parse.urlsplit(url)
    except ValueError:
        return "", WORKSPACE_MISSING
    if split.scheme != "https" or (split.hostname or "").lower() != host or split.username or split.password or split.port:
        return "", WORKSPACE_MISSING
    return url, ""
