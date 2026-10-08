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

"""Which ``visit_uid`` the signed-in community account has (a local, read-only map).

``visit_uid`` is derived by Servers (``HMAC(server_secret, community_uuid)``)
and only ever arrives in a ``POST /api/visit/credentials`` reply. The runtime
records ``{community local_user_id: visit_uid}`` here every time credentials
arrive (:func:`record_account_visit_uid`); everything that must act as "the
account that took part in this visit" -- transcript and report uploads, the
memory browser's account partition, local forgets -- looks the signed-in
account up here instead of asking Servers. An account that never fetched
credentials on this machine has no entry: it took part in no local visit.

File: ``config_dir/visit_accounts.json`` = ``{"accounts": {local_user_id:
visit_uid}}`` (atomic write, ``0o600``). Servers keeps ``visit_uid`` stable
per account (§4.7), so an entry normally never changes; if a reply ever
disagrees, the newest reply wins and a warning is logged.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from pathlib import Path

from config.visit_settings import VISIT_ACCOUNTS_FILENAME
from main_logic.visit.subjects import path_lock
from utils.file_utils import atomic_write_json, move_aside
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

VISIT_UID_RE = re.compile(r"^[0-9a-f]{24}$")
_ACCOUNT_MAX_CHARS = 128


def _default_config_dir() -> Path:
    from utils.config_manager import get_config_manager

    return Path(get_config_manager().config_dir)


config_dir_provider: Callable[[], Path] = _default_config_dir
"""Where the map lives (tests point it at ``tmp_path``)."""


def _path() -> Path:
    return Path(config_dir_provider()) / VISIT_ACCOUNTS_FILENAME


def _valid_account(account: object) -> bool:
    return isinstance(account, str) and 0 < len(account) <= _ACCOUNT_MAX_CHARS and account.isprintable()


class _MapUnreadable(Exception):
    """The map exists but cannot be read right now (sharing violation, permissions)."""


class _MapCorrupt(Exception):
    """The map exists but its content is not a map."""


def _read_sync(path: Path, *, strict: bool = False) -> dict[str, str]:
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning("visit accounts map unreadable: %s", type(exc).__name__)
        if strict:
            raise _MapUnreadable from exc
        return {}
    except (ValueError, RecursionError) as exc:
        logger.warning("visit accounts map corrupt: %s", type(exc).__name__)
        if strict:
            raise _MapCorrupt from exc
        return {}
    accounts = doc.get("accounts") if isinstance(doc, dict) else None
    if not isinstance(accounts, dict):
        if strict:
            raise _MapCorrupt
        return {}
    return {
        k: v for k, v in accounts.items()
        if _valid_account(k) and isinstance(v, str) and VISIT_UID_RE.fullmatch(v)
    }


class AccountMapDeferred(OSError):
    """The map cannot be written right now (unreadable, or a corrupt file not moved aside); retry later."""


def _record_sync(path: Path, account: str, visit_uid: str) -> bool:
    with path_lock(path):
        try:
            accounts = _read_sync(path, strict=True)
        except _MapUnreadable as exc:
            # 暂时读不了：不拿只有这一个账号的表覆盖它（其余账号的映射会丢）；报给调用方，由它稍后补写。
            # 带上底层原因：共享冲突这类会自己好，PermissionError / 目录只读会一直失败，排查时要分得清
            # Windows 上文件被占用（WinError 32）、替换瞬间的冲突与真正的权限拒绝（都可能是 WinError 5）全是
            # PermissionError：errno / winerror 一并写上帮助排查；是不是一直失败还得看它是否反复出现
            c = exc.__cause__
            cause = (f"{type(c).__name__}(errno={getattr(c, 'errno', None)}, winerror={getattr(c, 'winerror', None)})"
                     if c is not None else "unknown")
            raise AccountMapDeferred(f"visit accounts map unreadable: {cause}") from exc
        except _MapCorrupt:
            # 内容坏了、读不出任何映射：原文件改名留底，再从这个账号重新记起；改名都做不到就报给调用方稍后补写
            if move_aside(path, "corrupt") is None:
                raise AccountMapDeferred("corrupt visit accounts map not moved aside") from None
            accounts = {}
        if accounts.get(account) == visit_uid:
            return False
        if account in accounts:
            # Servers 承诺 visit_uid 对同一账号稳定：变了多半是本机文件被改过，以 Servers 刚给的为准并记一笔
            logger.warning("visit accounts map: visit_uid of an account changed, replaced")
        accounts[account] = visit_uid
        atomic_write_json(path, {"accounts": accounts})
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            logger.debug("visit accounts map: chmod 0600 failed: %s", exc)
        return True


async def record_account_visit_uid(account: str, visit_uid: str) -> bool:
    """Remember that community ``account`` is ``visit_uid``; True when the file changed.

    Called by the runtime with ``VisitCredentials.account`` / ``.visit_uid``
    after every successful credentials fetch. Bad values are ignored.
    Raises :class:`AccountMapDeferred` when the map cannot be written right
    now (False only means it already held this mapping); the runtime keeps
    retrying in the background.
    """
    if not _valid_account(account) or not isinstance(visit_uid, str) or not VISIT_UID_RE.fullmatch(visit_uid):
        return False
    return await asyncio.to_thread(_record_sync, _path(), account, visit_uid)


async def lookup_visit_uid(account: str | None) -> str | None:
    """The ``visit_uid`` recorded for community ``account``, or None."""
    if not _valid_account(account):
        return None
    return (await asyncio.to_thread(_read_sync, _path())).get(account)


def _local_account_sync() -> str | None:
    from main_routers import card_drop_router

    snapshot = card_drop_router._desktop_session_snapshot()
    if not isinstance(snapshot, dict) or not snapshot.get("access_token"):
        return None
    account = snapshot.get("local_user_id")
    return account if _valid_account(account) else None


async def local_account() -> str | None:
    """Community ``local_user_id`` of the saved desktop session (local file only, no network)."""
    try:
        return await asyncio.to_thread(_local_account_sync)
    except Exception as exc:  # noqa: BLE001 - 会话文件读不出就当没登录
        logger.warning("visit accounts: session snapshot unreadable: %s", type(exc).__name__)
        return None


async def own_visit_uid() -> str | None:
    """``visit_uid`` of the signed-in account, from the local map (no network); None when unknown.

    The ``own_visit_uid`` hook of the memory endpoints (wired in PR-09b) and
    the account check of queued reports.
    """
    return await lookup_visit_uid(await local_account())
