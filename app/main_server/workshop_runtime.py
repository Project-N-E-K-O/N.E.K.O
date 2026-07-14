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

"""Mount workshop assets and schedule background workshop synchronization."""

import asyncio
import importlib
import os

from fastapi.staticfiles import StaticFiles
from main_routers.workshop_router import (
    get_subscribed_workshop_items,
    sync_workshop_character_cards,
    warmup_ugc_cache,
)
from utils.cloudsave_runtime import is_write_fence_active
from utils.workshop_utils import get_workshop_path, get_workshop_root

from ._shared import runtime

_config_manager = runtime.config_manager
app = runtime.app
logger = runtime.logger


def _schedule_workshop_sync(steamworks) -> None:
    """Push the genuinely slow parts of the workshop (UGC cache warmup + character card network sync) to a background task.

    Directory mounting is done synchronously by the caller before ready
    (``_init_and_mount_workshop``); this only schedules the network-heavy
    warmup/sync — same as the original behavior before this refactor (they were
    already ``create_task``). greeting does not depend on these two steps.
    """
    try:
        if not steamworks:
            return

        # ugc submodule, not the package facade — see _cancel_workshop_background_tasks.
        _wr = importlib.import_module("main_routers.workshop_router.ugc")

        async def _warmup_only():
            try:
                await warmup_ugc_cache()
            except Exception as e:
                logger.warning(f"UGC 缓存预热失败: {e}")

        async def _sync_characters_only():
            max_fence_retries = 15
            retry_interval_seconds = 2
            for attempt in range(1, max_fence_retries + 1):
                if not is_write_fence_active(_config_manager):
                    break
                logger.info(
                    "创意工坊角色卡同步检测到维护态写围栏，等待解除后重试 (%s/%s)",
                    attempt,
                    max_fence_retries,
                )
                await asyncio.sleep(retry_interval_seconds)
            else:
                logger.info("创意工坊角色卡同步等待维护态解除超时，30s 后重新排队重试")

                async def _retry_sync_after_delay():
                    try:
                        await asyncio.sleep(30)
                        await _sync_characters_only()
                    except Exception as retry_exc:
                        logger.warning(f"创意工坊角色卡同步重试任务失败: {retry_exc}")

                _wr._ugc_sync_task = asyncio.create_task(_retry_sync_after_delay())
                return
            if _wr._ugc_warmup_task is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(_wr._ugc_warmup_task), timeout=20
                    )
                except asyncio.TimeoutError:
                    logger.warning("等待 UGC 预热任务超时（20s），继续角色卡同步")
                except Exception as e:
                    logger.debug(f"等待 UGC 预热任务时异常（不影响角色卡同步）: {e}")
            try:
                sync_result = await sync_workshop_character_cards()
                if sync_result["added"] > 0:
                    logger.info(
                        f"✅ 创意工坊角色卡同步完成：新增 {sync_result['added']} 个，跳过 {sync_result['skipped']} 个"
                    )
                else:
                    logger.info("创意工坊角色卡同步完成：无新增角色卡")
            except Exception as e:
                logger.warning(f"创意工坊角色卡同步失败（不影响启动）: {e}")

        _wr._ugc_warmup_task = asyncio.create_task(_warmup_only())
        _wr._ugc_sync_task = asyncio.create_task(_sync_characters_only())
    except Exception as e:
        logger.warning(f"创意工坊 UGC 预热/同步调度失败（不影响启动）: {e}")


async def _init_and_mount_workshop():
    """
    Initialize and mount the workshop directory

    Design principles:
    - the main layer only calls; it keeps no state
    - the path is computed by the utils layer and persisted into the config layer
    - other code that needs the path calls get_workshop_path()
    """
    try:
        # 1. 获取订阅的创意工坊物品列表
        workshop_items_result = await get_subscribed_workshop_items()

        # 2. 提取物品列表传给 utils 层
        subscribed_items = []
        if isinstance(workshop_items_result, dict) and workshop_items_result.get(
            "success", False
        ):
            subscribed_items = workshop_items_result.get("items", [])

        # 3. 调用 utils 层函数获取/计算路径（路径会被持久化到 config）
        workshop_path = get_workshop_root(subscribed_items)

        # 4. 挂载静态文件目录
        if (
            workshop_path
            and os.path.exists(workshop_path)
            and os.path.isdir(workshop_path)
        ):
            try:
                app.mount(
                    "/workshop", StaticFiles(directory=workshop_path), name="workshop"
                )
                logger.info(f"✅ 成功挂载创意工坊目录: {workshop_path}")
            except Exception as e:
                logger.error(f"挂载创意工坊目录失败: {e}")
        else:
            logger.warning(
                f"创意工坊目录不存在或不是有效的目录: {workshop_path}，跳过挂载"
            )
    except Exception as e:
        logger.error(f"初始化创意工坊目录时出错: {e}")
        # 降级：确保至少有一个默认路径可用
        workshop_path = get_workshop_path()
        logger.info(f"使用配置中的默认路径: {workshop_path}")
        if (
            workshop_path
            and os.path.exists(workshop_path)
            and os.path.isdir(workshop_path)
        ):
            try:
                app.mount(
                    "/workshop", StaticFiles(directory=workshop_path), name="workshop"
                )
                logger.info(f"✅ 降级模式下成功挂载创意工坊目录: {workshop_path}")
            except Exception as mount_err:
                logger.error(f"降级模式挂载创意工坊目录仍然失败: {mount_err}")
