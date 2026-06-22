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

"""
Persistent Steam64 cache.

The live ``Steamworks Users.GetSteamID()`` only returns a value while the Steam
client is open and logged in **this session**. To classify "is this a Steam
user" reliably across sessions (e.g. for the version survey gate), we persist the
Steam64 to disk the first time we observe it, then read that cache later even when
the client isn't running.

Best-effort throughout: every function swallows its own errors and degrades to
"no cached id" — a cache miss must never break the caller (instrumentation /
gating must not throw).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "steam_id_cache.json"


def _resolve_config_dir(config_dir=None) -> Path | None:
    if config_dir is not None:
        try:
            return Path(config_dir)
        except Exception:
            return None
    try:
        from utils.config_manager import get_config_manager
        return Path(get_config_manager().config_dir)
    except Exception:
        return None


def _is_valid_steam64(sid: str) -> bool:
    """Pure-digit, positive, <= 20 chars (u64 has 20 decimal digits) — same shape as the telemetry server's normalizer."""
    return bool(sid) and sid.isascii() and sid.isdigit() and len(sid) <= 20 and int(sid) > 0


def get_cached_steam_id(config_dir=None) -> str:
    """Return the cached Steam64 (decimal string), or '' when absent / invalid."""
    d = _resolve_config_dir(config_dir)
    if d is None:
        return ""
    try:
        with open(d / _CACHE_FILENAME, "r", encoding="utf-8") as f:
            data = json.load(f)
        sid = data.get("steam_id") if isinstance(data, dict) else None
        if isinstance(sid, str) and _is_valid_steam64(sid):
            return sid
    except Exception:
        pass
    return ""


def write_cached_steam_id(steam_id, config_dir=None) -> None:
    """Persist an observed Steam64 (atomic, best-effort). No-op on invalid id or unchanged value."""
    try:
        sid = str(steam_id or "").strip()
        if not _is_valid_steam64(sid):
            return
        if get_cached_steam_id(config_dir) == sid:
            return  # 已是同值，省一次写 + 避免无谓 churn
        d = _resolve_config_dir(config_dir)
        if d is None:
            return
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (_CACHE_FILENAME + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"steam_id": sid}, f)
        os.replace(tmp, d / _CACHE_FILENAME)
    except Exception as e:
        logger.debug("write_cached_steam_id failed (non-critical): %s", e)
