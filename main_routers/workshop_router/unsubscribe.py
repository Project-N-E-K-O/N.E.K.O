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
from fastapi import Request
from fastapi.responses import JSONResponse
from ..shared_state import ensure_steamworks as get_steamworks, get_config_manager
from utils.config_manager import get_reserved


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

    # characters.json 是用户可写文件，根对象或 猫娘 字段被写成 list/string 时
    # 直接 .get() / .items() 会抛异常，把退订流程打成 500。这里受控降级。
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
                # Workshop 文件属于外部输入，任何畸形（顶层非 dict、档案名为 list/dict
                # 等）不应中断整个 os.walk；校验失败跳过该卡片继续扫描。
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
    """
    Unsubscribe from a Steam Workshop item.
    Accepts a POST request containing the item ID.
    """
    steamworks = get_steamworks()

    # 检查Steamworks是否初始化成功
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)

    try:
        # 获取请求体中的数据
        data = await request.json()
        item_id = data.get('item_id')

        if not item_id:
            return JSONResponse({
                "success": False,
                "error": "缺少必要参数",
                "message": "请求中缺少物品ID"
            }, status_code=400)

        # 转换item_id为整数
        try:
            item_id_int = int(item_id)
        except ValueError:
            return JSONResponse({
                "success": False,
                "error": "无效的物品ID",
                "message": "提供的物品ID不是有效的数字"
            }, status_code=400)

        config_mgr = get_config_manager()

        # 反向索引：优先用 character_origin.source_id 找到来自该 Workshop 物品的角色，
        # 再用磁盘上 .chara.json 的扫描结果兜底合并（文件夹可能已被 Steam 删除）。
        # 三个 helper 都是同步磁盘 / Steamworks 调用（_resolve_workshop_item_install_path
        # 会调 GetItemInstallInfo + 磁盘兜底搜索），必须 offload 避免阻塞事件循环。
        candidate_names = await asyncio.to_thread(
            _collect_character_names_by_workshop_item_id, config_mgr, item_id_int
        )
        pre_item_path = await asyncio.to_thread(
            _resolve_workshop_item_install_path, steamworks, item_id_int
        )
        disk_names = await asyncio.to_thread(
            _scan_workshop_folder_character_names, pre_item_path
        )
        # 跟踪每个候选角色的来源：
        #   "origin" = 从 characters.json 的 character_origin.source_id 反查命中，
        #              配置明确标记来自该 item_id，可放心删除。
        #   "disk"   = 仅来自磁盘 .chara.json 的名字扫描，只是"名字碰撞"，
        #              不能证明这角色就是该 item_id 的；删除前必须对每个
        #              候选在 characters.json 里二次确认 source_id / asset_source_id。
        candidate_sources: dict[str, str] = {name: "origin" for name in candidate_names}
        seen_names: set[str] = set(candidate_names)
        for disk_name in disk_names:
            if disk_name not in seen_names:
                candidate_names.append(disk_name)
                candidate_sources[disk_name] = "disk"
                seen_names.add(disk_name)
        logger.info(
            f"取消订阅 {item_id_int}: 反向索引候选角色 {candidate_names}（磁盘扫描追加 {disk_names}）"
        )

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

        # 前置校验：候选角色中若包含当前猫娘，直接阻止取消订阅并提示用户切换。
        try:
            current_characters = await config_mgr.aload_characters()
        except Exception as exc:
            logger.warning(f"取消订阅前读取 characters.json 失败: {exc}")
            current_characters = await asyncio.to_thread(config_mgr.load_characters)
        # characters.json 根对象若被写成 list/string，.get() 会抛 AttributeError；
        # 受控降级为空 dict 并继续，候选角色为空时前置校验自然 no-op。
        if not isinstance(current_characters, dict):
            logger.warning(
                f"取消订阅: characters.json 根对象不是 dict"
                f"（{type(current_characters).__name__}），按空配置处理"
            )
            current_characters = {}
        current_catgirl = str(current_characters.get('当前猫娘', '') or '')
        # 只在当前猫娘**确实绑定该 Workshop item** 时才阻断；仅靠名字匹配的磁盘
        # 候选（如工坊另有同名 .chara.json）不应把无辜的本地猫娘挡住退订。
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

        # 前置尝试释放 memory_server 对候选角色的 SQLite 句柄（best-effort + 并行）。
        # 与 delete_catgirl 不同：取消订阅场景下，memory_server 对非活跃角色
        # 可能本来就没持有句柄，/release_character 会返回 non-success，但此时
        # 也根本不存在文件锁 —— 硬拒绝会导致用户永远无法取消订阅。
        # 真正的安全网是同步清理里的 PermissionError retry；这里只记录 warning。
        #
        # 并行预算：per-call 2.5s，整体 3s（参考 main_server.py 关机阶段做法）。
        # 多候选时耗时从 O(N * RT) 降到 O(max(RT))；单候选表现不变。
        release_warnings: list[str] = []
        if candidate_names:
            try:
                from ..characters_router import release_memory_server_character
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

            async def _release_one(name: str) -> tuple[str, bool, str | None]:
                try:
                    released = await asyncio.wait_for(
                        release_memory_server_character(
                            name,
                            reason=f"取消订阅前释放 SQLite 句柄: {name}（item_id={item_id_int}）",
                        ),
                        timeout=2.5,
                    )
                    return name, bool(released), None
                except Exception as exc:
                    return name, False, str(exc)

            try:
                release_results = await asyncio.wait_for(
                    asyncio.gather(
                        *(_release_one(n) for n in candidate_names),
                        return_exceptions=False,
                    ),
                    timeout=3.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"取消订阅前置 release 总预算 3s 超时（item_id={item_id_int}），"
                    f"视为全部 non-success 继续清理"
                )
                release_results = [(n, False, "overall_timeout") for n in candidate_names]

            for name, ok, err in release_results:
                if ok:
                    continue
                release_warnings.append(name)
                logger.info(
                    f"取消订阅前置 release: {name} 返回 non-success"
                    f"{'（' + err + '）' if err else ''}，继续走清理流程"
                )

        # 同步执行记忆/角色卡/tombstone 清理（与 DELETE /catgirl/{name} 对齐）。
        # 这一步必须在 UnsubscribeItem 之前完成，这样 HTTP 响应就能直接汇报
        # "删了哪些角色、删了哪些记忆路径"，用户能立刻确认结果，不用等 Steam 异步回调。
        # 任意角色子步骤失败都只记录到 cleanup_summary.errors，不中断整体流程
        # （因为 UnsubscribeItem 一旦发出，Steam 端已无法回滚；记忆残留由用户看到错误后重试）。
        #
        # 性能优化：
        #   - 单角色内 delete_memory / tombstone / remove_one_catgirl 三步互相独立，
        #     用 asyncio.gather 并发（return_exceptions=True 各自兜异常）。
        #   - characters.json 的 del 只改内存 dict，循环末尾批量一次写盘
        #     （N 次 atomic_write → 1 次）。
        cleanup_summary: dict = {
            "candidate_characters": list(candidate_names),
            "cleaned_characters": [],
            "removed_memory_paths": [],
            "errors": [],
            # memory_server release 返回 non-success 的角色名（不影响清理流程，
            # 仅用于诊断，一般表示该角色在 memory_server 侧本来就没持有句柄）。
            "release_warnings": list(release_warnings),
        }

        if candidate_names:
            try:
                from ..characters_router import (
                    _build_character_tombstones_state,
                    notify_memory_server_reload,
                )
                from utils.character_memory import delete_character_memory_storage
                from ..shared_state import get_remove_one_catgirl
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
            # 同步清理会对 characters_mut['猫娘'] 做 del；根对象或 猫娘 字段
            # 结构异常时直接按 LOCAL_CONFIG_CLEANUP_FAILED 中止，避免
            # TypeError/AttributeError 把退订流程打成 500。
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
            # 二次校验：前置校验后、同步清理前用户可能切到候选角色；此时
            # 仅 `continue` 会跳过角色删除但仍执行 UnsubscribeItem + 删除订阅
            # 文件夹，留下指向已删 Workshop 资源的当前猫娘配置，应直接中止。
            # 同样复用 _is_confirmed_workshop_character：只有当前猫娘确实绑定
            # 当前 item_id 才阻断，避免磁盘同名误挡。
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

            async def _delete_memory_with_retry(name: str) -> list:
                """Windows file locks → one retry after 300ms as a safety net."""
                try:
                    return list(
                        await asyncio.to_thread(
                            delete_character_memory_storage, config_mgr, name
                        )
                        or []
                    )
                except PermissionError as exc:
                    logger.warning(
                        f"同步清理: delete_character_memory_storage({name}) "
                        f"PermissionError: {exc}，300ms 后重试"
                    )
                    await asyncio.sleep(0.3)
                    return list(
                        await asyncio.to_thread(
                            delete_character_memory_storage, config_mgr, name
                        )
                        or []
                    )

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
            catgirl_map = characters_mut['猫娘']  # 上面 isinstance 已守卫
            target_item_id_str = str(item_id_int)
            for name in candidate_names:
                if not name:
                    continue
                # 保护性双保险：绝不删当前猫娘（前置校验已覆盖，这里兜底）
                if name == current_catgirl_now:
                    logger.warning(
                        f"取消订阅同步清理: 跳过当前猫娘 '{name}'（保护性双保险）"
                    )
                    continue

                # 磁盘兜底候选必须二次确认来源：名字一致 ≠ 同一 item_id。
                # 如果用户本地已有同名非 Workshop 角色（或同名但来自别的
                # item_id 的 Workshop 角色），按磁盘名字盲删会误删。
                # 反向索引候选（"origin"）已经是在 characters.json 里按
                # source_id 匹配到的，不需要二次校验。
                if candidate_sources.get(name) == "disk":
                    payload = catgirl_map.get(name) if isinstance(catgirl_map, dict) else None
                    origin_source = str(
                        get_reserved(payload, 'character_origin', 'source', default='') or ''
                    ).strip() if isinstance(payload, dict) else ''
                    origin_source_id = str(
                        get_reserved(payload, 'character_origin', 'source_id', default='') or ''
                    ).strip() if isinstance(payload, dict) else ''
                    asset_source = str(
                        get_reserved(payload, 'avatar', 'asset_source', default='') or ''
                    ).strip() if isinstance(payload, dict) else ''
                    asset_source_id = str(
                        get_reserved(payload, 'avatar', 'asset_source_id', default='') or ''
                    ).strip() if isinstance(payload, dict) else ''
                    confirmed_workshop_match = (
                        origin_source == 'steam_workshop' and origin_source_id == target_item_id_str
                    ) or (
                        asset_source == 'steam_workshop' and asset_source_id == target_item_id_str
                    )
                    if not confirmed_workshop_match:
                        logger.warning(
                            f"取消订阅同步清理: 跳过未确认来源的磁盘候选角色 '{name}' "
                            f"(item_id={item_id_int}, origin_source={origin_source!r}, "
                            f"origin_source_id={origin_source_id!r}, "
                            f"asset_source={asset_source!r}, asset_source_id={asset_source_id!r})"
                        )
                        cleanup_summary.setdefault("skipped_unverified_characters", []).append(name)
                        continue

                # 三步独立：并发执行
                results = await asyncio.gather(
                    _delete_memory_with_retry(name),
                    _write_tombstone(name),
                    _remove_one(name),
                    return_exceptions=True,
                )
                rm_paths_or_exc, tombstone_or_exc, remove_or_exc = results

                # delete_memory 结果
                if isinstance(rm_paths_or_exc, Exception):
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

                # tombstone 结果
                if isinstance(tombstone_or_exc, Exception):
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

                # remove_one_catgirl 结果
                if isinstance(remove_or_exc, Exception):
                    logger.warning(
                        f"取消订阅同步清理: remove_one_catgirl({name}) 失败: {remove_or_exc}"
                    )
                    cleanup_summary["errors"].append({
                        "character": name,
                        "stage": "remove_one_catgirl",
                        "error": str(remove_or_exc),
                    })

                # characters.json 条目仅做内存删除，循环结束一次性批量写盘。
                # 复用前面捕获的 catgirl_map 引用（上面 isinstance 已守卫），
                # 避免每次都走 characters_mut.get('猫娘') or {} 的兜底链路。
                if name in catgirl_map:
                    try:
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

            # 本地角色配置写盘失败 / 内存 del 失败 → 绝不能继续发 UnsubscribeItem：
            # Steam 订阅一旦取消，订阅文件夹会被删；但 characters.json 仍保留
            # 该角色，配置会指向不存在的 Workshop 资源，且下次启动可能加载坏卡。
            # 这里 Steam 请求还没发，安全地提前中止并把 summary 返回给前端。
            local_config_cleanup_failed = False

            # 批量写 characters.json（N 个 del → 1 次 atomic write）
            if pending_del_names:
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
                    cleanup_summary["errors"].append({
                        "character": "<batch>",
                        "stage": "delete_config",
                        "error": str(exc),
                    })

            # 若任一本地配置清理失败（per-name del 或批量写盘），立即中止。
            delete_config_failed = any(
                err.get("stage") == "delete_config"
                for err in cleanup_summary.get("errors") or []
            )
            if local_config_cleanup_failed or delete_config_failed:
                logger.error(
                    f"取消订阅同步清理: 本地角色配置清理失败（item_id={item_id_int}），"
                    f"已中止 Steam UnsubscribeItem 请求以避免配置-订阅不一致"
                )
                return JSONResponse({
                    "success": False,
                    "code": "LOCAL_CONFIG_CLEANUP_FAILED",
                    "error": "本地角色配置清理失败，已取消本次 Steam 退订请求，请修复后重试。",
                    "cleanup_summary": cleanup_summary,
                }, status_code=500)

            # 通知 memory_server 重新加载（一次即可）
            try:
                await notify_memory_server_reload(
                    reason=f"取消订阅 item_id={item_id_int}"
                )
            except Exception as exc:
                logger.warning(
                    f"取消订阅同步清理: notify_memory_server_reload 失败: {exc}"
                )

        logger.info(
            f"取消订阅同步清理汇总 item_id={item_id_int}: "
            f"cleaned={cleanup_summary['cleaned_characters']}, "
            f"removed_paths={len(cleanup_summary['removed_memory_paths'])}, "
            f"errors={len(cleanup_summary['errors'])}"
        )

        # 回调与延迟兜底共享的幂等标志（first-winner 模式）。
        # 使用 Lock 保证 check + set 的原子性，避免两线程同时通过闸口。
        #
        # 角色卡/记忆/tombstone 已经在同步路径（上方）处理完毕；perform_cleanup
        # 只负责 Steam 订阅文件夹的磁盘删除兜底。不再需要把 async 任务调回主
        # 事件循环（_run_async_in_main_loop / _purge_character_memory_and_config
        # 已移除），回调线程做的事纯粹是阻塞 IO（shutil.rmtree），可以直接跑。
        cleanup_event = threading.Event()
        cleanup_claim_lock = threading.Lock()

        # cleanup_claim_lock 含义变更：现在只保护 "是否正在执行" 判定，
        # cleanup_event 只在 **确认成功** 后 set，避免删除失败时把 5 秒延迟
        # 兜底门闩锁死（rmtree ignore_errors 吞掉异常 / 目录仍存在 / 抛出
        # 异常的三种失败路径都必须允许后续重试）。
        cleanup_in_progress = threading.Event()
        # Steam 明确返回取消订阅失败时设置：此时用户仍处于订阅状态，
        # 5 秒延迟兜底必须跳过 perform_cleanup，否则会删掉仍在订阅中的
        # 本地 Workshop 文件夹（Steam 下次同步会再下回来）。
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
                # 把 unsubscribe_failed_event 的判定也放进临界区。delayed_cleanup
                # 外层的先 check cleanup_event → check unsubscribe_failed_event →
                # 再调 perform_cleanup 两次 check 之间没锁，Steam 失败回调若恰好
                # 落在这个窗口里，rmtree 还是会把仍订阅中的本地工坊目录删掉。
                # 在锁内原子化闭环；成功回调路径本来就不会 set 失败 event，不会误伤。
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
                # Fail-closed: 未明确确认成功时，必须先查 Steam 的订阅位
                # （GetItemState & 1）。仍订阅中就跳过清理，同时 set 失败
                # event 防止后续路径重复发起 rmtree。
                if not confirmed_unsubscribed and _is_item_still_subscribed(item_id):
                    logger.warning(
                        f"perform_cleanup({item_id}): Steam 状态仍显示已订阅，"
                        f"跳过订阅文件夹清理"
                    )
                    unsubscribe_failed_event.set()
                    return False

                # 重新解析一次路径（候选路径可能在取消订阅过程中失效）
                final_item_path = _resolve_workshop_item_install_path(
                    get_steamworks(), item_id
                ) or pre_item_path
                if final_item_path and os.path.isdir(final_item_path):
                    try:
                        shutil.rmtree(final_item_path, ignore_errors=True)
                    except Exception as rmtree_exc:
                        # ignore_errors=True 通常不会外抛，但兜底一下
                        logger.error(
                            f"perform_cleanup({item_id}): rmtree 抛异常: {rmtree_exc}",
                            exc_info=True,
                        )
                    if os.path.exists(final_item_path):
                        logger.warning(
                            f"perform_cleanup({item_id}): 订阅文件夹仍存在（可能被占用）: {final_item_path}"
                        )
                        return False  # 未成功 → 不 set cleanup_event，留给延迟兜底重试
                    logger.info(
                        f"perform_cleanup({item_id}): 已删除订阅文件夹 {final_item_path}"
                    )
                else:
                    logger.debug(
                        f"perform_cleanup({item_id}): 订阅文件夹已不存在，视为成功"
                    )
                # 只有走到这里（目录确认不存在）才锁死 cleanup_event
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
            # 验证 item_id 是否匹配（防止其他取消订阅操作触发此回调）
            if callback_item_id and int(callback_item_id) != item_id_int:
                logger.warning(
                    f"回调item_id不匹配: 期望{item_id_int}, 实际{callback_item_id}，跳过处理"
                )
                return

            if getattr(result, 'result', None) == 1:  # k_EResultOK
                logger.info(f"取消订阅成功回调: {item_id_int}，开始执行清理")
                # Steam 明确回调 OK，不必再用 GetItemState 二次确认；直接删。
                perform_cleanup(item_id_int, confirmed_unsubscribed=True)
            else:
                # Steam 明确退订失败 → 订阅仍然存在，不能删本地文件夹。
                unsubscribe_failed_event.set()
                logger.warning(
                    f"取消订阅失败回调: {item_id_int}, 错误代码: {getattr(result, 'result', None)}，"
                    f"不执行订阅文件夹清理"
                )

        # 调用 Steamworks 的 UnsubscribeItem 方法，并提供回调函数
        try:
            steamworks.Workshop.UnsubscribeItem(
                item_id_int, callback=unsubscribe_callback, override_callback=True
            )
            logger.info(f"取消订阅请求已发送: {item_id_int}，等待回调...")

            # 延迟兜底：5 秒后若回调仍未触发（cleanup_event 未 set），
            # 在后台线程里直接执行一次 perform_cleanup（幂等）。
            def delayed_cleanup():
                import time as _time
                # noqa: BLOCKING-OK - 只在 daemon 后台线程跑，不阻塞主事件循环。
                _time.sleep(5)
                if cleanup_event.is_set():
                    logger.debug(f"延迟兜底: item_id={item_id_int} 已清理，跳过")
                    return
                if unsubscribe_failed_event.is_set():
                    # 已收到 Steam 明确失败回调，用户仍订阅中 → 不删本地文件夹。
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
            # UnsubscribeItem 调用失败 = Steam 退订请求根本没发出 / 没被接受。
            # 此时不能再 perform_cleanup：用户仍处于订阅状态，删本地文件夹会
            # 让他保持订阅却丢失本地 Workshop 文件（下次 Steam 会再下载一遍）。
            # 同步阶段已经删了的 characters.json / memory 无法回滚，但至少
            # 订阅-文件夹状态保持一致，由用户手动处理后续。
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
            # 同步阶段的实际清理结果（记忆/角色卡/tombstone 已删除），
            # 订阅文件夹由 Steam 异步回调或 5 秒延迟兜底负责删除。
            "cleanup_summary": cleanup_summary,
        }

    except Exception as e:
        logger.error(f"取消订阅物品时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": "服务器内部错误",
            "message": f"取消订阅过程中发生错误: {str(e)}"
        }, status_code=500)
