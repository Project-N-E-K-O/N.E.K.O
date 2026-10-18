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

"""The /unsubscribe endpoint and install-path / character-name
resolution helpers.

Split out of the former monolithic ``main_routers/workshop_router.py``.
"""

from ._shared import logger, router
from .meta import _write_deleted_character_tombstone

import os
import json
import asyncio
import threading
import time
from contextlib import suppress
from fastapi import Request
from fastapi.responses import JSONResponse
from ..shared_state import ensure_steamworks as get_steamworks, get_config_manager
from utils.character_memory import character_config_mutation_lock
from utils.config_manager import get_character_uid, get_reserved
from utils.asyncio_retirement import await_retirement
from utils.chat_avatar_store import ChatAvatarError, remove_record as remove_chat_avatar_record
from utils.config_manager.storage_roots import chat_avatar_directory


_RESUME_RETRY_INITIAL_DELAY_SECONDS = 1.0
_RESUME_RETRY_MAX_DELAY_SECONDS = 30.0
_released_derived_task_resume_tasks: dict[
    tuple[int, tuple[tuple[str, str], ...]], asyncio.Task[None]
] = {}


def _schedule_released_derived_task_resume_retry(
    config_mgr, released_claims: dict[str, str], item_id: int,
) -> None:
    """Keep retrying one failed admission recovery until it is acknowledged."""
    claims = dict(released_claims)
    key = (item_id, tuple(sorted(claims.items())))
    existing = _released_derived_task_resume_tasks.get(key)
    if existing is not None and not existing.done():
        return

    async def _retry() -> None:
        delay = _RESUME_RETRY_INITIAL_DELAY_SECONDS
        while True:
            await asyncio.sleep(delay)
            try:
                await _resume_released_derived_tasks(
                    config_mgr,
                    claims,
                    item_id,
                    schedule_retry=False,
                )
            except Exception as exc:
                logger.warning(
                    "取消订阅派生任务恢复重试失败: item_id=%s err=%s",
                    item_id,
                    exc,
                )
                delay = min(delay * 2, _RESUME_RETRY_MAX_DELAY_SECONDS)
                continue
            return

    task = asyncio.create_task(_retry())
    _released_derived_task_resume_tasks[key] = task

    def _discard(done: asyncio.Task[None]) -> None:
        if _released_derived_task_resume_tasks.get(key) is done:
            _released_derived_task_resume_tasks.pop(key, None)

    task.add_done_callback(_discard)


async def _await_thread_call_to_completion(func, *args, **kwargs):
    """Finish a thread-owned transaction before propagating cancellation."""
    operation = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(operation), False
    except asyncio.CancelledError:
        while not operation.done():
            with suppress(asyncio.CancelledError):
                await asyncio.wait({operation})
        try:
            result = operation.result()
        except BaseException as exc:
            logger.error(
                "取消到达后同步事务仍失败，保留取消语义: %s",
                exc,
                exc_info=True,
            )
            raise asyncio.CancelledError from exc
        return result, True


async def _finish_resume_preserving_cancellation(coro) -> None:
    """Finish admission recovery without replacing or swallowing cancellation."""
    current = asyncio.current_task()
    cancellation_pending = bool(current and current.cancelling())
    operation = asyncio.create_task(coro)
    try:
        await asyncio.shield(operation)
    except asyncio.CancelledError:
        cancellation_pending = True
        while not operation.done():
            with suppress(asyncio.CancelledError):
                await asyncio.wait({operation})
    except BaseException as exc:
        if cancellation_pending:
            raise asyncio.CancelledError from exc
        raise
    try:
        operation.result()
    except BaseException as exc:
        if cancellation_pending:
            raise asyncio.CancelledError from exc
        raise
    if cancellation_pending:
        raise asyncio.CancelledError


async def _resume_released_derived_tasks(
    config_mgr, released_claims: dict[str, str], item_id: int,
    *,
    schedule_retry: bool = True,
) -> bool:
    """Release this transaction's claims without reopening a replaced identity."""
    try:
        characters_after = await config_mgr.aload_characters()
        catgirls_after = (
            characters_after.get('猫娘')
            if isinstance(characters_after, dict)
            else None
        )
        active_names = set(catgirls_after) if isinstance(catgirls_after, dict) else set()
        claim_releases = {
            name: (token,)
            for name, token in released_claims.items()
        }
        if not claim_releases:
            return True
        from ..characters_router import notify_memory_server_reload

        resumed = await notify_memory_server_reload(
            reason=(
                f"取消订阅流程结束，释放派生任务声明: {item_id}; "
                f"仍存在={sorted(active_names & set(released_claims))}"
            ),
            release_derived_task_claims=claim_releases,
        )
        if not resumed:
            raise RuntimeError("notify_memory_server_reload returned False")
        return True
    except Exception as exc:
        if schedule_retry:
            _schedule_released_derived_task_resume_retry(
                config_mgr,
                released_claims,
                item_id,
            )
        logger.warning(
            "取消订阅流程结束后恢复角色派生任务失败: item_id=%s err=%s",
            item_id,
            exc,
        )
        raise


async def _release_workshop_character_handles(
    candidate_names: list[str],
    item_id: int,
    release_character,
    create_claim_token,
    claim_sink: dict[str, str],
    *,
    per_call_timeout: float = 2.5,
    overall_timeout: float = 3.0,
) -> list[tuple[str, bool, str | None]]:
    """Attempt releases while retaining every client-owned claim token."""
    claim_sink.update({
        name: create_claim_token()
        for name in candidate_names
    })

    async def _release_one(name: str) -> tuple[str, bool, str | None]:
        try:
            released = await asyncio.wait_for(
                release_character(
                    name,
                    reason=f"取消订阅前释放 SQLite 句柄: {name}（item_id={item_id}）",
                    derived_task_claim_token=claim_sink[name],
                ),
                timeout=per_call_timeout,
            )
            return name, bool(released), None
        except Exception as exc:
            return name, False, str(exc)

    try:
        return await asyncio.wait_for(
            asyncio.gather(
                *(_release_one(name) for name in candidate_names),
                return_exceptions=False,
            ),
            timeout=overall_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"取消订阅前置 release 总预算 {overall_timeout}s 超时"
            f"（item_id={item_id}），视为全部 non-success 继续清理"
        )
        return [(name, False, "overall_timeout") for name in candidate_names]


def _collect_character_names_by_workshop_item_id(config_mgr, item_id: int) -> list[str]:
    """
    Reverse-look up, via character_origin.source_id in characters.json, the names
    of characters originating from this Workshop item (a stable index that does
    not depend on the .chara.json files on disk).

    Args:
        config_mgr: ConfigManager instance
        item_id: Workshop item ID (integer)

    Returns:
        list[str]: matched character names (possibly empty; deduplicated, insertion order preserved)
    """
    try:
        characters = config_mgr.load_characters()
    except Exception as exc:
        logger.warning(
            f"_collect_character_names_by_workshop_item_id: 加载 characters.json 失败: {exc}"
        )
        return []

    # characters.json is user-writable; list/string roots or 猫娘 fields make
    # direct .get()/.items() fail with unsubscribe 500. Degrade in controlled fashion.
    if not isinstance(characters, dict):
        logger.warning(
            "_collect_character_names_by_workshop_item_id: "
            f"characters.json 根对象不是 dict（{type(characters).__name__}），跳过反查"
        )
        return []
    catgirl_map = characters.get('猫娘')
    if not isinstance(catgirl_map, dict):
        if catgirl_map is not None:
            logger.warning(
                "_collect_character_names_by_workshop_item_id: "
                f"characters.json 的 猫娘 字段不是 dict（{type(catgirl_map).__name__}），跳过反查"
            )
        return []

    target_id = str(item_id)
    names: list[str] = []
    seen: set[str] = set()
    for name, payload in catgirl_map.items():
        if not isinstance(payload, dict):
            continue
        source = str(
            get_reserved(payload, 'character_origin', 'source', default='') or ''
        ).strip()
        source_id = str(
            get_reserved(payload, 'character_origin', 'source_id', default='') or ''
        ).strip()
        if source == 'steam_workshop' and source_id == target_id and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def _scan_workshop_folder_character_names(item_path: str | None) -> list[str]:
    """
    Scan the .chara.json files in a Workshop item's disk directory and extract character names (complementing the reverse index).
    Returns an empty list if the directory does not exist or scanning fails.
    """
    if not item_path:
        return []
    try:
        normalized_path = os.path.abspath(os.path.normpath(item_path))
    except Exception:
        return []
    if not os.path.isdir(normalized_path):
        return []

    names: list[str] = []
    seen: set[str] = set()
    try:
        for root, _dirs, files in os.walk(normalized_path):
            for file_name in files:
                if not file_name.endswith('.chara.json'):
                    continue
                chara_file_path = os.path.join(root, file_name)
                try:
                    with open(chara_file_path, 'r', encoding='utf-8') as f:
                        chara_data = json.load(f)
                except Exception as exc:
                    logger.warning(
                        f"_scan_workshop_folder_character_names: 读取 {chara_file_path} 失败: {exc}"
                    )
                    continue
                # Workshop files are external; malformed roots/profile names (list/dict, etc.) must not
                # abort os.walk. Skip invalid cards, continue scanning.
                if not isinstance(chara_data, dict):
                    logger.warning(
                        f"_scan_workshop_folder_character_names: {chara_file_path} "
                        f"顶层不是 dict，跳过"
                    )
                    continue
                raw_name = chara_data.get('档案名') or chara_data.get('name')
                if not isinstance(raw_name, str):
                    if raw_name is not None:
                        logger.warning(
                            f"_scan_workshop_folder_character_names: {chara_file_path} "
                            f"档案名/name 不是字符串（{type(raw_name).__name__}），跳过"
                        )
                    continue
                chara_name = raw_name.strip()
                if chara_name and chara_name not in seen:
                    names.append(chara_name)
                    seen.add(chara_name)
    except Exception as exc:
        logger.warning(
            f"_scan_workshop_folder_character_names: 扫描 {normalized_path} 失败: {exc}"
        )
    return names


def _resolve_workshop_item_install_path(steamworks, item_id: int) -> str | None:
    """
    Best-effort resolution of a Workshop item's current install path on disk.
    Prefers GetItemInstallInfo, falls back to find_workshop_item_by_id; returns None on failure.
    """
    item_path: str | None = None
    try:
        if steamworks:
            install_info = steamworks.Workshop.GetItemInstallInfo(item_id)
            if isinstance(install_info, dict):
                folder_path = install_info.get('folder') or ''
                if folder_path:
                    item_path = str(folder_path)
            elif isinstance(install_info, tuple) and len(install_info) >= 2:
                folder = install_info[1]
                if folder:
                    item_path = str(folder)
    except Exception as exc:
        logger.debug(
            f"_resolve_workshop_item_install_path: GetItemInstallInfo({item_id}) 失败: {exc}"
        )

    if not item_path:
        try:
            from utils.frontend_utils import find_workshop_item_by_id
            candidate, _ = find_workshop_item_by_id(str(item_id))
            item_path = candidate or None
        except Exception as exc:
            logger.debug(
                f"_resolve_workshop_item_install_path: find_workshop_item_by_id({item_id}) 失败: {exc}"
            )
            return None

    if not item_path:
        return None
    try:
        return os.path.abspath(os.path.normpath(item_path))
    except Exception:
        return item_path


@router.post('/unsubscribe')
async def unsubscribe_workshop_item(request: Request):
    """Protect a started local unsubscribe transaction from request cancellation."""
    commit_started = asyncio.Event()
    operation = asyncio.create_task(
        _unsubscribe_workshop_item(request, commit_started),
    )
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        if not commit_started.is_set():
            operation.cancel()
        while not operation.done():
            with suppress(asyncio.CancelledError):
                await asyncio.wait({operation})
        if commit_started.is_set():
            try:
                operation.result()
            except Exception as exc:
                logger.error(
                    "取消订阅请求取消后，已开始的事务最终失败: %s",
                    exc,
                    exc_info=True,
                )
        else:
            with suppress(asyncio.CancelledError):
                operation.result()
        raise


async def _unsubscribe_workshop_item(request: Request, commit_started: asyncio.Event):
    """
    Unsubscribe from a Steam Workshop item.
    Accepts a POST request containing the item ID.
    """
    steamworks = get_steamworks()
    released_derived_task_claims: dict[str, str] = {}
    mutation_lock_acquired = False
    reserved_names: list[str] = []  # Character names reserved during transaction, blocking route admission.

    # Check Steamworks initialized successfully.
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)

    try:
        # Read request body.
        data = await request.json()
        item_id = data.get('item_id')

        if not item_id:
            return JSONResponse({
                "success": False,
                "error": "缺少必要参数",
                "message": "请求中缺少物品ID"
            }, status_code=400)

        # Convert item_id to integer.
        try:
            item_id_int = int(item_id)
        except ValueError:
            return JSONResponse({
                "success": False,
                "error": "无效的物品ID",
                "message": "提供的物品ID不是有效的数字"
            }, status_code=400)

        config_mgr = get_config_manager()

        # Reverse index: find Workshop characters by character_origin.source_id first,
        # merge disk .chara.json scan fallback (Steam may already have deleted folders).
        # All three helpers perform synchronous disk/Steamworks calls (_resolve_workshop_item_install_path
        # calls GetItemInstallInfo + disk fallback search), so offload away from event loop.
        candidate_names = await asyncio.to_thread(
            _collect_character_names_by_workshop_item_id, config_mgr, item_id_int
        )
        pre_item_path = await asyncio.to_thread(
            _resolve_workshop_item_install_path, steamworks, item_id_int
        )
        disk_names = await asyncio.to_thread(
            _scan_workshop_folder_character_names, pre_item_path
        )
        seen_names: set[str] = set(candidate_names)
        for disk_name in disk_names:
            if disk_name not in seen_names:
                candidate_names.append(disk_name)
                seen_names.add(disk_name)
        logger.info(
            f"取消订阅 {item_id_int}: 反向索引候选角色 {candidate_names}（磁盘扫描追加 {disk_names}）"
        )

        # Candidate discovery is intentionally outside the lock because it may
        # scan Steam/disk state.  Reload and revalidate every candidate only
        # after entering the shared characters.json transaction.
        await character_config_mutation_lock.acquire()
        mutation_lock_acquired = True

        target_item_id_str = str(item_id_int)

        def _is_confirmed_workshop_character(snapshot, name: str) -> bool:
            """
            Determine whether character `name` in `snapshot` (a snapshot of
            characters.json) is **explicitly bound** to the current `item_id_int`.
            The decision only looks at character_origin.source_id /
            avatar.asset_source_id in the config, never at the .chara.json files on disk.

            Used to intercept the scenario where "a same-named .chara.json on disk
            drags an innocent local character into the candidates and wrongly blocks
            the current catgirl from unsubscribing": only block when the current
            catgirl genuinely originates from this Workshop item.
            """
            if not isinstance(snapshot, dict):
                return False
            cg_map = snapshot.get('猫娘')
            if not isinstance(cg_map, dict):
                return False
            payload = cg_map.get(name)
            if not isinstance(payload, dict):
                return False
            origin_source = str(
                get_reserved(payload, 'character_origin', 'source', default='') or ''
            ).strip()
            origin_source_id = str(
                get_reserved(payload, 'character_origin', 'source_id', default='') or ''
            ).strip()
            asset_source = str(
                get_reserved(payload, 'avatar', 'asset_source', default='') or ''
            ).strip()
            asset_source_id = str(
                get_reserved(payload, 'avatar', 'asset_source_id', default='') or ''
            ).strip()
            return (
                origin_source == 'steam_workshop' and origin_source_id == target_item_id_str
            ) or (
                asset_source == 'steam_workshop' and asset_source_id == target_item_id_str
            )

        # Precheck: candidates include current catgirl → block unsubscribe and prompt character switch.
        try:
            current_characters = await config_mgr.aload_characters()
        except Exception as exc:
            logger.warning(f"取消订阅前读取 characters.json 失败: {exc}")
            current_characters = await asyncio.to_thread(config_mgr.load_characters)
        # List/string characters.json root makes .get() raise AttributeError;
        # controlled empty-dict fallback continues, empty candidates naturally no-op precheck.
        if not isinstance(current_characters, dict):
            logger.warning(
                f"取消订阅: characters.json 根对象不是 dict"
                f"（{type(current_characters).__name__}），按空配置处理"
            )
            current_characters = {}
        current_catgirl = str(current_characters.get('当前猫娘', '') or '')
        # Block only if current catgirl is genuinely bound to this Workshop item; name-only disk
        # matches (another same-name .chara.json) must not block unrelated local characters.
        if (
            current_catgirl
            and current_catgirl in candidate_names
            and _is_confirmed_workshop_character(current_characters, current_catgirl)
        ):
            logger.warning(
                f"取消订阅被阻止: item_id={item_id_int} 对应角色 {current_catgirl} 正是当前猫娘"
            )
            return JSONResponse({
                "success": False,
                "code": "CURRENT_CATGIRL_IN_USE",
                "error": f"不能取消订阅当前正在使用的猫娘「{current_catgirl}」，请先切换到其他角色后再取消订阅。",
                "character_name": current_catgirl,
                "details": {"character_name": current_catgirl},
            }, status_code=400)
        # Candidate visiting/playing, or background visit data still writing by name (finalization/summary/clear):
        # unsubscribe deletes it, requiring same rename/delete guard (OD-13). Character config mutation lock held;
        # memory-clear guard registers under same lock, preventing interleaving.
        from utils.external_route_registry import begin_character_mutation, is_character_lifecycle_locked

        confirmed_names = sorted(
            name for name in candidate_names if _is_confirmed_workshop_character(current_characters, name)
        )
        busy_names = [name for name in confirmed_names if is_character_lifecycle_locked(name)]
        if busy_names:
            logger.warning(f"取消订阅被阻止: item_id={item_id_int} 对应角色 {busy_names} 正在串门或小游戏中")
            return JSONResponse({
                "success": False,
                "code": "EXTERNAL_ROUTE_ACTIVE",
                "error": f"角色「{busy_names[0]}」正在串门或小游戏中，请结束后再取消订阅。",
                "character_name": busy_names[0],
                "details": {"character_name": busy_names[0]},
            }, status_code=400)
        # No await after checks: characters cannot start visits/games before characters.json write finishes.
        reserved_names = confirmed_names
        begin_character_mutation(*reserved_names)

        # Best-effort parallel prerelease of memory_server SQLite handles for candidates.
        # Unlike delete_catgirl, unsubscribe may target inactive characters for which memory_server
        # never held handles; /release_character returns non-success despite
        # no file lock. Hard rejection would permanently block unsubscribe.
        # Synchronous PermissionError retries are true fallback; warn only here.
        #
        # Parallel budget: 2.5s per call, 3s total, matching main_server shutdown.
        # Multiple candidates reduce O(N×RT) to O(max(RT)); single candidate unchanged.
        release_warnings: list[str] = []
        if candidate_names:
            try:
                from ..characters_router import (
                    create_derived_task_claim_token,
                    release_memory_server_character,
                )
            except Exception as exc:
                logger.error(
                    f"取消订阅前置 release: 无法 import release_memory_server_character: {exc}"
                )
                return JSONResponse({
                    "success": False,
                    "code": "INTERNAL_IMPORT_ERROR",
                    "error": f"内部组件加载失败: {exc}",
                    "details": {"error": str(exc)},
                }, status_code=500)

            release_results = await _release_workshop_character_handles(
                candidate_names,
                item_id_int,
                release_memory_server_character,
                create_derived_task_claim_token,
                released_derived_task_claims,
            )

            for name, ok, err in release_results:
                if ok:
                    continue
                release_warnings.append(name)
                logger.info(
                    f"取消订阅前置 release: {name} 返回 non-success"
                    f"{'（' + err + '）' if err else ''}，继续走清理流程"
                )

        # Synchronous memory/card/tombstone cleanup, matching DELETE /catgirl/{name}.
        # Finish before UnsubscribeItem so HTTP response immediately reports
        # deleted characters/memory paths, without waiting for asynchronous Steam callbacks.
        # Individual character-step failures only enter cleanup_summary.errors, never abort whole flow,
        # since Steam cannot roll back sent UnsubscribeItem; users retry visible memory residue errors.
        #
        # Performance:
        #   - Per-character delete_memory / tombstone / remove_one_catgirl are independent;
        #     gather concurrently with return_exceptions=True for individual failures.
        #   - characters.json deletions mutate dict only; batch one disk write after loop
        #     (N atomic writes → one).
        cleanup_summary: dict = {
            "candidate_characters": list(candidate_names),
            "cleaned_characters": [],
            "removed_memory_paths": [],
            "errors": [],
            # Character names whose memory_server release returned non-success (diagnostics only,
            # cleanup unaffected; usually no handle was held for them).
            "release_warnings": list(release_warnings),
        }

        if candidate_names:
            try:
                from ..characters_router import (
                    _build_character_tombstones_state,
                    notify_memory_server_reload,
                )
                from utils.character_memory import (
                    begin_character_recent_transaction,
                    delete_character_memory_storage,
                    finalize_character_recent_delete,
                    release_character_recent_transaction,
                )
                from ..shared_state import get_remove_one_catgirl
                from ..characters_router.crud import (
                    collect_numeric_v2_character_purge,
                    complete_numeric_v2_character_purge,
                    discard_numeric_v2_character_purge_intent,
                    persist_numeric_v2_character_purge_intent,
                )
                from services.theater.numeric_v2_archive import NumericV2ArchiveError
                from services.theater.numeric_v2_store import NumericV2StoreError
                from services.theater.paths import theater_root
            except Exception as exc:
                logger.error(
                    f"取消订阅同步清理: 无法 import 生命周期工具: {exc}"
                )
                return JSONResponse({
                    "success": False,
                    "code": "INTERNAL_IMPORT_ERROR",
                    "error": f"内部组件加载失败: {exc}",
                    "details": {"error": str(exc)},
                }, status_code=500)

            characters_mut = await config_mgr.aload_characters()
            # Cleanup deletes characters_mut['猫娘']; malformed root or 猫娘 field
            # aborts with LOCAL_CONFIG_CLEANUP_FAILED, avoiding
            # unsubscribe 500 via TypeError/AttributeError.
            if (
                not isinstance(characters_mut, dict)
                or not isinstance(characters_mut.get('猫娘'), dict)
            ):
                logger.error(
                    f"取消订阅同步清理被阻止: characters.json 结构无效 "
                    f"(root={type(characters_mut).__name__}, "
                    f"猫娘={type(characters_mut.get('猫娘')).__name__ if isinstance(characters_mut, dict) else 'N/A'})"
                )
                return JSONResponse({
                    "success": False,
                    "code": "LOCAL_CONFIG_CLEANUP_FAILED",
                    "error": "本地角色配置结构无效，已取消本次 Steam 退订请求，请修复 characters.json 后重试。",
                    "cleanup_summary": cleanup_summary,
                }, status_code=500)
            current_catgirl_now = str(characters_mut.get('当前猫娘', '') or '')
            # Recheck: user may switch to candidate between precheck and cleanup;
            # continue alone skips character deletion but still unsubscribes/deletes folders,
            # leaving current configuration pointing to missing assets. Abort instead.
            # Reuse _is_confirmed_workshop_character: block only when current catgirl genuinely binds
            # this item_id, avoiding same-name disk false positives.
            if (
                current_catgirl_now
                and current_catgirl_now in candidate_names
                and _is_confirmed_workshop_character(characters_mut, current_catgirl_now)
            ):
                logger.warning(
                    f"取消订阅同步清理被阻止: item_id={item_id_int} 对应角色 "
                    f"{current_catgirl_now} 已切换为当前猫娘"
                )
                return JSONResponse({
                    "success": False,
                    "code": "CURRENT_CATGIRL_IN_USE",
                    "error": f"不能取消订阅当前正在使用的猫娘「{current_catgirl_now}」，请先切换到其他角色后再取消订阅。",
                    "character_name": current_catgirl_now,
                    "details": {"character_name": current_catgirl_now},
                }, status_code=400)

            def _delete_memory_with_retry_sync(name: str) -> list:
                """Windows file locks → one retry after 300ms as a safety net."""
                transaction = begin_character_recent_transaction(config_mgr, name)
                try:
                    try:
                        removed_paths, transaction = delete_character_memory_storage(
                            config_mgr,
                            name,
                            capture_pending=True,
                            keep_recent_locks=True,
                            recent_transaction=transaction,
                        )
                    except PermissionError as exc:
                        logger.warning(
                            f"同步清理: delete_character_memory_storage({name}) "
                            f"PermissionError: {exc}，300ms 后重试"
                        )
                        time.sleep(0.3)
                        removed_paths, transaction = delete_character_memory_storage(
                            config_mgr,
                            name,
                            capture_pending=True,
                            keep_recent_locks=True,
                            recent_transaction=transaction,
                        )
                    finalize_character_recent_delete(transaction)
                    return list(removed_paths or [])
                except BaseException:
                    release_character_recent_transaction(transaction)
                    raise

            async def _delete_memory_with_retry(name: str) -> list:
                removed_paths, cancelled = await _await_thread_call_to_completion(
                    _delete_memory_with_retry_sync,
                    name,
                )
                if cancelled:
                    raise asyncio.CancelledError
                return removed_paths

            async def _write_tombstone(name: str) -> None:
                await asyncio.to_thread(
                    _write_deleted_character_tombstone,
                    config_mgr,
                    name,
                    _build_character_tombstones_state,
                )

            async def _remove_one(name: str) -> None:
                fn = get_remove_one_catgirl()
                if fn is not None:
                    await fn(name)

            pending_del_names: list[str] = []
            pending_avatar_uids: dict[str, str] = {}
            deleted_avatar_directory = chat_avatar_directory(config_mgr)
            # Theater data owned by each deleted character, collected with the
            # same strict preflight as DELETE /catgirl/{name}.
            numeric_purges: dict[str, object] = {}
            catgirl_map = characters_mut['猫娘']  # isinstance already guarded above.
            target_item_id_str = str(item_id_int)
            # characters.json is first irreversible commit. Outer flow completes local commit
            # and Steam request from here, then propagates cancellation to caller.
            commit_started.set()
            for name in candidate_names:
                if not name:
                    continue
                # Extra protection: never delete current catgirl (already prechecked, fallback here).
                if name == current_catgirl_now:
                    logger.warning(
                        f"取消订阅同步清理: 跳过当前猫娘 '{name}'（保护性双保险）"
                    )
                    continue

                # Candidate discovery ran before the lifecycle guard and is only
                # advisory.  Revalidate every candidate against the authoritative
                # snapshot loaded under the guard: an origin-index hit can have
                # been deleted/replaced while this request waited, just like a
                # disk-only same-name candidate can be unrelated from the start.
                if not _is_confirmed_workshop_character(characters_mut, name):
                    logger.warning(
                        f"取消订阅同步清理: 跳过锁内未确认来源的候选角色 '{name}' "
                        f"(item_id={item_id_int})"
                    )
                    cleanup_summary.setdefault("skipped_unverified_characters", []).append(name)
                    continue

                # Delete characters.json entries in memory only; batch persistence after loop.
                # Reuse captured catgirl_map reference, isinstance-guarded above,
                # avoiding repeated characters_mut.get('猫娘') or {} fallback.
                if name in catgirl_map:
                    # Collect the theater cascade before the irreversible
                    # characters.json commit. Like the ordinary character
                    # delete, an unreadable/unattributable theater file fails
                    # closed: the whole unsubscribe aborts before Steam is
                    # asked, instead of orphaning that character's transcripts.
                    try:
                        numeric_purges[name] = await collect_numeric_v2_character_purge(
                            theater_root(config_mgr),
                            character_id=str(
                                get_reserved(catgirl_map[name], "character_id", default="")
                                or ""
                            ).strip(),
                            legacy_catgirl_name=name,
                            # Like the ordinary delete: a corrupt theater file
                            # of any character gets one storage repair and a
                            # retry instead of blocking every unsubscribe until
                            # the theater page is opened.
                            config_manager=config_mgr,
                        )
                    except (OSError, NumericV2StoreError, NumericV2ArchiveError) as exc:
                        logger.error(
                            f"取消订阅同步清理: 剧场数据预检失败 {name}: {exc}",
                            exc_info=True,
                        )
                        cleanup_summary["errors"].append({
                            "character": name,
                            "stage": "theater_preflight",
                            "error": str(getattr(exc, "path", "") or exc),
                        })
                        continue
                    try:
                        uid = get_character_uid(catgirl_map[name])
                        if uid:
                            pending_avatar_uids[name] = uid
                        del catgirl_map[name]
                        pending_del_names.append(name)
                    except Exception as exc:
                        logger.error(
                            f"取消订阅同步清理: 内存 del characters[猫娘][{name}] 失败: {exc}",
                            exc_info=True,
                        )
                        cleanup_summary["errors"].append({
                            "character": name,
                            "stage": "delete_config",
                            "error": str(exc),
                        })

            # Failed config persistence/in-memory deletion forbids UnsubscribeItem:
            # unsubscribing removes Workshop folders while characters.json still references
            # them, causing missing assets and corrupt-card loading next startup.
            # Steam request unsent here, so safely abort and return summary.
            local_config_cleanup_failed = False
            # A failed theater preflight aborts before anything is committed:
            # no character of this item may be removed from characters.json.
            theater_preflight_failed = any(
                err.get("stage") == "theater_preflight"
                for err in cleanup_summary.get("errors") or []
            )

            # The theater purge runs only after characters.json is committed and
            # has no rollback, so list its targets durably first: a purge that
            # then fails is retried from this intent by startup maintenance. An
            # intent that cannot be written aborts before anything is committed.
            numeric_purge_intents: dict[str, object] = {}
            if pending_del_names and not theater_preflight_failed:
                for name in pending_del_names:
                    numeric_purge = numeric_purges.get(name)
                    if numeric_purge is None:
                        continue
                    try:
                        numeric_purge_intents[name] = (
                            await persist_numeric_v2_character_purge_intent(numeric_purge)
                        )
                    except Exception as exc:
                        logger.error(
                            f"取消订阅同步清理: 剧场清理意图写入失败 {name}: {exc}",
                            exc_info=True,
                        )
                        cleanup_summary["errors"].append({
                            "character": name,
                            "stage": "theater_preflight",
                            "error": str(exc),
                        })
                        theater_preflight_failed = True
                        break

            async def _discard_uncommitted_purge_intents() -> None:
                # The characters stay configured, so their intents must not
                # survive; startup maintenance also discards an intent whose
                # character is still configured, should this cleanup fail.
                for intent_name, intent_path in numeric_purge_intents.items():
                    try:
                        await discard_numeric_v2_character_purge_intent(intent_path)
                    except Exception as exc:
                        logger.warning(
                            f"取消订阅同步清理: 未提交的剧场清理意图删除失败 {intent_name}: {exc}"
                        )
                numeric_purge_intents.clear()

            if theater_preflight_failed:
                await _discard_uncommitted_purge_intents()

            # Retire visit residue: record pending_retire before characters.json commit, retire only afterward;
            # unsuccessful commit means character remains; remove marker only. Marker failure aborts like theater precheck failure.
            from main_routers.visit_router import character_hooks as visit_hooks

            visit_items: dict[str, dict] = {}
            visit_marker_failed = False
            if pending_del_names and not theater_preflight_failed:
                for name in pending_del_names:
                    try:
                        item = await visit_hooks.begin_retire(config_mgr, name, pending_avatar_uids.get(name))
                    except Exception as exc:
                        logger.error(f"取消订阅同步清理: 串门退役标记写入失败 {name}: {exc!r}")
                        cleanup_summary["errors"].append({
                            "character": name,
                            "stage": "visit_retire_marker",
                            "error": str(exc),
                        })
                        visit_marker_failed = True
                        break
                    if item is not None:
                        visit_items[name] = item

            async def _settle_visit_items() -> None:
                for item_name, item in visit_items.items():
                    try:
                        settled = await visit_hooks.settle_retire(config_mgr, item)
                    except Exception as exc:  # noqa: BLE001 - Retain marker for startup reconciliation.
                        settled = False
                        logger.warning(f"取消订阅同步清理: 串门数据退役失败 {item_name}: {exc!r}")
                    if not settled:
                        cleanup_summary["errors"].append({
                            "character": item_name,
                            "stage": "visit_retire",
                            "error": "visit data retirement pending",
                        })
                visit_items.clear()

            if visit_marker_failed:
                # No commit: abort branch below removes recorded markers as uncommitted deletion.
                await _discard_uncommitted_purge_intents()

            # Batch characters.json write (N deletions → one atomic write).
            if pending_del_names and not theater_preflight_failed and not visit_marker_failed:
                try:
                    await config_mgr.asave_characters(characters_mut)
                    cleanup_summary["cleaned_characters"] = list(pending_del_names)
                    logger.info(
                        f"取消订阅同步清理: 批量删除 {len(pending_del_names)} 个角色并写入 characters.json: "
                        f"{pending_del_names}"
                    )
                except Exception as exc:
                    local_config_cleanup_failed = True
                    logger.error(
                        f"取消订阅同步清理: 批量 asave_characters 失败: {exc}",
                        exc_info=True,
                    )
                    await _discard_uncommitted_purge_intents()
                    cleanup_summary["errors"].append({
                        "character": "<batch>",
                        "stage": "delete_config",
                        "error": str(exc),
                    })

            # Any local config cleanup failure (per-name deletion/batch persistence) aborts immediately.
            delete_config_failed = any(
                err.get("stage") in {"delete_config", "theater_preflight", "visit_retire_marker"}
                for err in cleanup_summary.get("errors") or []
            )
            if local_config_cleanup_failed or delete_config_failed:
                logger.error(
                    f"取消订阅同步清理: 本地角色配置清理失败（item_id={item_id_int}），"
                    f"已中止 Steam UnsubscribeItem 请求以避免配置-订阅不一致"
                )
                # Finalize markers against config: retire committed deletions, remove markers for undeleted characters.
                await _settle_visit_items()
                return JSONResponse({
                    "success": False,
                    "code": "LOCAL_CONFIG_CLEANUP_FAILED",
                    "error": "本地角色配置清理失败，已取消本次 Steam 退订请求，请修复后重试。",
                    "cleanup_summary": cleanup_summary,
                }, status_code=500)

            # Publish config successfully before irreversible memory/tombstone/runtime cleanup.
            # Config failure preserves memory files, avoiding false rollback where registry restores
            # but shutil.rmtree permanently deleted files.
            for name in pending_del_names:
                uid = pending_avatar_uids.get(name)
                if uid:
                    try:
                        await await_retirement(asyncio.to_thread(
                            remove_chat_avatar_record, deleted_avatar_directory, uid,
                        ))
                    except ChatAvatarError as exc:
                        cleanup_summary["errors"].append({
                            "character": name,
                            "stage": "delete_chat_avatar",
                            "error": exc.code,
                        })
                results = await asyncio.gather(
                    _delete_memory_with_retry(name),
                    _write_tombstone(name),
                    _remove_one(name),
                    return_exceptions=True,
                )
                rm_paths_or_exc, tombstone_or_exc, remove_or_exc = results

                if isinstance(rm_paths_or_exc, BaseException):
                    logger.error(
                        f"取消订阅同步清理: delete_memory({name}) 失败: {rm_paths_or_exc}",
                        exc_info=rm_paths_or_exc,
                    )
                    cleanup_summary["errors"].append({
                        "character": name,
                        "stage": "delete_memory",
                        "error": str(rm_paths_or_exc),
                    })
                else:
                    for entry_path in rm_paths_or_exc:
                        logger.info(f"取消订阅同步清理: 已删除记忆 {entry_path}")
                        cleanup_summary["removed_memory_paths"].append(str(entry_path))
                    if not rm_paths_or_exc:
                        logger.warning(
                            f"取消订阅同步清理: delete_memory({name}) 未返回任何路径 "
                            f"(memory_dir={getattr(config_mgr, 'memory_dir', None)})"
                        )

                if isinstance(tombstone_or_exc, BaseException):
                    logger.error(
                        f"取消订阅同步清理: tombstone({name}) 失败: {tombstone_or_exc}",
                        exc_info=tombstone_or_exc,
                    )
                    cleanup_summary["errors"].append({
                        "character": name,
                        "stage": "tombstone",
                        "error": str(tombstone_or_exc),
                    })
                else:
                    logger.info(f"取消订阅同步清理: 已写入 tombstone -> {name}")

                if isinstance(remove_or_exc, BaseException):
                    logger.warning(
                        f"取消订阅同步清理: remove_one_catgirl({name}) 失败: {remove_or_exc}"
                    )
                    cleanup_summary["errors"].append({
                        "character": name,
                        "stage": "remove_one_catgirl",
                        "error": str(remove_or_exc),
                    })

                # Same cascade as DELETE /catgirl/{name}: sessions, receipts,
                # public archives, forget intents and quarantined copies. The
                # character is already gone from characters.json, so this is
                # an irreversible post-commit cleanup like the memory delete;
                # a failure keeps the durable intent for startup retry.
                numeric_purge = numeric_purges.get(name)
                if numeric_purge is not None:
                    try:
                        await complete_numeric_v2_character_purge(
                            numeric_purge,
                            numeric_purge_intents.get(name),
                        )
                    except Exception as exc:
                        logger.error(
                            f"取消订阅同步清理: 剧场数据清理失败 {name}: {exc}",
                            exc_info=True,
                        )
                        cleanup_summary["errors"].append({
                            "character": name,
                            "stage": "delete_theater",
                            "error": str(exc),
                        })

            # characters.json committed: retire visit residue (visits/roster/personas) for these characters.
            await _settle_visit_items()

            # Notify memory_server reload once.
            try:
                reload_succeeded = await notify_memory_server_reload(
                    reason=f"取消订阅 item_id={item_id_int}",
                    release_derived_task_claims={
                        name: (token,)
                        for name, token in released_derived_task_claims.items()
                    },
                )
                if reload_succeeded:
                    released_derived_task_claims.clear()
            except Exception as exc:
                logger.warning(
                    f"取消订阅同步清理: notify_memory_server_reload 失败: {exc}"
                )

        # Everything that touches characters.json is done.  Release the shared
        # transaction here instead of in the ``finally``: the remaining work is
        # Steam-side (UnsubscribeItem plus its callback/delayed folder cleanup)
        # and can take arbitrarily long, and holding the lock across it would
        # block unrelated character mutations -- including the character-card
        # save and language-preference endpoints -- for that entire window.
        # The ``finally`` still releases on every early-return/exception path.
        if mutation_lock_acquired:
            mutation_lock_acquired = False
            character_config_mutation_lock.release()

        logger.info(
            f"取消订阅同步清理汇总 item_id={item_id_int}: "
            f"cleaned={cleanup_summary['cleaned_characters']}, "
            f"removed_paths={len(cleanup_summary['removed_memory_paths'])}, "
            f"errors={len(cleanup_summary['errors'])}"
        )

        # Callback/delayed fallback share idempotent first-winner flag.
        # Lock makes check+set atomic, preventing two threads passing together.
        #
        # Cards/memory/tombstones already handled synchronously above; perform_cleanup
        # only provides disk-deletion fallback for Steam subscription folder. No need to schedule
        # async work onto main loop (_run_async_in_main_loop / _purge_character_memory_and_config
        # removed); callback thread performs blocking shutil.rmtree directly.
        cleanup_event = threading.Event()
        cleanup_claim_lock = threading.Lock()

        # cleanup_claim_lock now guards running-state only;
        # set cleanup_event only after confirmed success, avoiding permanently latching five-second
        # fallback on deletion failure (swallowed ignore_errors / surviving directory / thrown
        # exception must all permit later retries).
        cleanup_in_progress = threading.Event()
        # Set on explicit Steam unsubscribe failure: user remains subscribed,
        # so five-second fallback must skip perform_cleanup to preserve still-subscribed
        # local Workshop folder (Steam would redownload next sync).
        unsubscribe_failed_event = threading.Event()

        def _is_item_still_subscribed(item_id: int) -> bool:
            """
            Fail-closed subscription check: returns True when still subscribed (or unverifiable).
            When Steamworks is unavailable / the query raises, conservatively treat
            it as "still subscribed", to avoid deleting local folders that the user
            is still subscribed to while the state is uncertain.
            """
            try:
                sw = get_steamworks()
                if sw is None:
                    logger.warning(
                        f"perform_cleanup({item_id}): Steamworks 不可用，"
                        f"无法确认订阅状态，按仍订阅处理"
                    )
                    return True
                state = sw.Workshop.GetItemState(item_id)
                return bool(state & 1)  # EItemState.SUBSCRIBED = 1
            except Exception as exc:
                logger.warning(
                    f"perform_cleanup({item_id}): GetItemState 失败，"
                    f"按仍订阅处理: {exc}"
                )
                return True

        def perform_cleanup(item_id: int, *, confirmed_unsubscribed: bool = False):
            """
            Subscription-folder deletion shared by the callback / delayed fallback. Idempotent:
              - cleanup_event.is_set() → already succeeded once, skip
              - cleanup_in_progress unset → claim execution, clear when done
              - cleanup_in_progress set → another path is running; avoid concurrent rmtree on the same directory
            Only set(cleanup_event) once the directory is confirmed gone; failure
            paths only clear in_progress so the 5-second delayed fallback can still retry.

            Fail-closed subscription check: unless `confirmed_unsubscribed=True`
            (passed only by the successful-callback path), `_is_item_still_subscribed()`
            must pass before rmtree. "No callback within 5 seconds" must not be taken
            as a successful unsubscribe — Steam may deliver a failure callback late,
            and deleting the local folder then would lose content for a user who is
            still subscribed.
            """
            with cleanup_claim_lock:
                if cleanup_event.is_set():
                    logger.debug(f"perform_cleanup({item_id}): 已成功过，跳过（幂等）")
                    return False
                # Check unsubscribe_failed_event inside critical section too. delayed_cleanup's
                # outer cleanup_event check → unsubscribe_failed_event check → perform_cleanup
                # lacks locking between checks; a Steam failure callback in that window
                # lets rmtree delete a still-subscribed local folder.
                # Make atomic under lock; successful callbacks never set failure event, unaffected.
                if unsubscribe_failed_event.is_set():
                    logger.warning(
                        f"perform_cleanup({item_id}): 已收到 Steam 退订失败信号，"
                        f"跳过订阅文件夹清理（用户仍处于订阅状态）"
                    )
                    return False
                if cleanup_in_progress.is_set():
                    logger.debug(f"perform_cleanup({item_id}): 已有并发清理在跑，跳过")
                    return False
                cleanup_in_progress.set()

            try:
                import shutil
                # Fail closed: without explicit confirmed success, first check Steam subscription bit
                # (GetItemState & 1). If still subscribed, skip cleanup and set failure
                # event to prevent later rmtree retries.
                if not confirmed_unsubscribed and _is_item_still_subscribed(item_id):
                    logger.warning(
                        f"perform_cleanup({item_id}): Steam 状态仍显示已订阅，"
                        f"跳过订阅文件夹清理"
                    )
                    unsubscribe_failed_event.set()
                    return False

                # Resolve path again; candidates may invalidate during unsubscribe.
                final_item_path = _resolve_workshop_item_install_path(
                    get_steamworks(), item_id
                ) or pre_item_path
                if final_item_path and os.path.isdir(final_item_path):
                    try:
                        shutil.rmtree(final_item_path, ignore_errors=True)
                    except Exception as rmtree_exc:
                        # ignore_errors=True usually does not raise, but guard anyway.
                        logger.error(
                            f"perform_cleanup({item_id}): rmtree 抛异常: {rmtree_exc}",
                            exc_info=True,
                        )
                    if os.path.exists(final_item_path):
                        logger.warning(
                            f"perform_cleanup({item_id}): 订阅文件夹仍存在（可能被占用）: {final_item_path}"
                        )
                        return False  # Failure leaves cleanup_event unset for delayed retry.
                    logger.info(
                        f"perform_cleanup({item_id}): 已删除订阅文件夹 {final_item_path}"
                    )
                else:
                    logger.debug(
                        f"perform_cleanup({item_id}): 订阅文件夹已不存在，视为成功"
                    )
                # Set cleanup_event only here, after confirming directory absent.
                cleanup_event.set()
                return True
            except Exception as exc:
                logger.error(
                    f"perform_cleanup({item_id}): 删除订阅文件夹时出错: {exc}",
                    exc_info=True,
                )
                return False
            finally:
                cleanup_in_progress.clear()

        def unsubscribe_callback(result):
            """Callback of Steamworks UnsubscribeItem (runs on the Steam callback thread)."""
            callback_item_id = getattr(
                result, 'publishedFileId', getattr(result, 'published_file_id', None)
            )
            logger.info(
                f"取消订阅回调被触发: 期望item_id={item_id_int}, 回调item_id={callback_item_id}, "
                f"result.result={getattr(result, 'result', None)}"
            )
            # Verify matching item_id, excluding callbacks from other unsubscribe operations.
            if callback_item_id and int(callback_item_id) != item_id_int:
                logger.warning(
                    f"回调item_id不匹配: 期望{item_id_int}, 实际{callback_item_id}，跳过处理"
                )
                return

            if getattr(result, 'result', None) == 1:  # k_EResultOK
                logger.info(f"取消订阅成功回调: {item_id_int}，开始执行清理")
                # Explicit Steam OK needs no GetItemState recheck; delete directly.
                perform_cleanup(item_id_int, confirmed_unsubscribed=True)
            else:
                # Explicit unsubscribe failure means subscription remains; never delete local folder.
                unsubscribe_failed_event.set()
                logger.warning(
                    f"取消订阅失败回调: {item_id_int}, 错误代码: {getattr(result, 'result', None)}，"
                    f"不执行订阅文件夹清理"
                )

        # Call Steamworks UnsubscribeItem with callback.
        try:
            steamworks.Workshop.UnsubscribeItem(
                item_id_int, callback=unsubscribe_callback, override_callback=True
            )
            logger.info(f"取消订阅请求已发送: {item_id_int}，等待回调...")

            # Delayed fallback: after five seconds without callback (cleanup_event unset),
            # execute idempotent perform_cleanup on background thread.
            def delayed_cleanup():
                import time as _time
                # noqa: BLOCKING-OK - Daemon background thread only, never blocks main event loop.
                _time.sleep(5)
                if cleanup_event.is_set():
                    logger.debug(f"延迟兜底: item_id={item_id_int} 已清理，跳过")
                    return
                if unsubscribe_failed_event.is_set():
                    # Explicit Steam failure already received; still subscribed, preserve local folder.
                    logger.warning(
                        f"延迟兜底: item_id={item_id_int} 已收到退订失败回调，"
                        f"跳过订阅文件夹清理"
                    )
                    return
                logger.warning(
                    f"延迟兜底: item_id={item_id_int} 5 秒内未收到回调，执行备用清理"
                )
                perform_cleanup(item_id_int)

            cleanup_thread = threading.Thread(target=delayed_cleanup, daemon=True)
            cleanup_thread.start()

        except Exception as e:
            # UnsubscribeItem call failure means Steam request unsent/unaccepted.
            # Never perform_cleanup: user remains subscribed, deleting local folder would
            # remove subscribed assets until Steam redownloads next time.
            # Synchronously deleted characters.json/memory cannot roll back, but at least
            # subscription/folder stay consistent for manual follow-up.
            logger.error(
                f"调用 UnsubscribeItem 失败: {e}，已保留本地 Workshop 文件夹，"
                f"不执行备用清理",
                exc_info=True,
            )
            return JSONResponse({
                "success": False,
                "code": "STEAM_UNSUBSCRIBE_FAILED",
                "error": f"Steam 退订请求发送失败: {e}",
                "cleanup_summary": cleanup_summary,
            }, status_code=500)

        logger.info(f"取消订阅请求已被接受，正在处理: {item_id_int}")
        return {
            "success": True,
            "status": "accepted",
            "message": "取消订阅请求已被接受，正在处理中。实际结果将在后台异步完成。",
            "candidate_character_count": len(candidate_names),
            # Actual synchronous cleanup results (memory/card/tombstone deletion);
            # Steam callback or five-second fallback deletes subscription folder.
            "cleanup_summary": cleanup_summary,
        }

    except Exception as e:
        logger.error(f"取消订阅物品时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": "服务器内部错误",
            "message": f"取消订阅过程中发生错误: {str(e)}"
        }, status_code=500)
    finally:
        try:
            if mutation_lock_acquired:
                character_config_mutation_lock.release()
            _end_reservation(reserved_names)
        finally:
            if released_derived_task_claims:
                await _finish_resume_preserving_cancellation(
                    _resume_released_derived_tasks(
                        config_mgr,
                        released_derived_task_claims,
                        item_id_int,
                    )
                )


def _end_reservation(names: list[str]) -> None:
    """Lift the route-admission reservation of an unsubscribe transaction (no-op when empty)."""
    if names:
        from utils.external_route_registry import end_character_mutation

        end_character_mutation(*names)
