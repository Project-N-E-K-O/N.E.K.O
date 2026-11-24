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

"""主题工坊（Theme Studio）—— N.E.K.O 内置功能模块。

历史：它原本是一个独立插件（``neko_theme_studio``，跑在插件的 48916 端口上，
并且靠一个「应用侧补丁」把 loader 与模板标记注入本体）。现在它是本体的一部分：

* 没有插件宿主、没有独立端口、没有注入器（``_app_patch.py`` 已删除）。
* 面板页面 ``/theme_studio`` 由 ``main_routers/pages_router.py`` 渲染。
* 数据读写走 ``/api/theme_studio/*``（``main_routers/theme_studio_router.py``）。
* loader 两件套是本体正常静态资源：``static/theme-studio/loader/``，
  由模板直接 ``<script>`` / ``<link>`` 引入。

本模块只负责「服务实例 + entry 调度」这一层薄胶水，业务逻辑全在
:mod:`theme_studio.service` 里。
"""

from __future__ import annotations

import inspect
import logging
import threading
from pathlib import Path
from typing import Any

from ._sdk_compat import ENTRY_REGISTRY, Err, Ok, SdkError
from .service import (
    API_BASE,
    PRESETS_DIR,
    ThemeStudioService,
)

__all__ = [
    "API_BASE",
    "PRESETS_DIR",
    "ThemeStudioService",
    "backgrounds_dir",
    "data_dir",
    "entries",
    "exports_dir",
    "get_service",
    "get_theme_document",
    "library_dir",
    "presets_dir",
    "run_entry",
    "service_started",
    "set_data_dir_override",
    "shutdown_service",
    "startup_service",
]

logger = logging.getLogger("neko.theme_studio")

_service: ThemeStudioService | None = None
_service_lock = threading.RLock()
_service_started = False
_data_dir_override: Path | None = None


def set_data_dir_override(path: Path | str | None) -> None:
    """测试 / 工具用：指定数据目录（必须在 :func:`get_service` 之前调用）。"""
    global _data_dir_override
    _data_dir_override = Path(path) if path else None


def get_service() -> ThemeStudioService:
    """返回进程内唯一的主题工坊服务实例（首次调用时构造）。"""
    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is None:
            _service = ThemeStudioService(data_dir=_data_dir_override)
        return _service


def service_started() -> bool:
    """是否已经跑过启动初始化（建目录 / 建 theme.json / 起排程线程）。"""
    return _service_started


def startup_service() -> ThemeStudioService:
    """构造并初始化服务（同步，幂等）。应用启动钩子与路由初始化都调它。"""
    global _service_started
    service = get_service()
    if _service_started:
        return service
    with _service_lock:
        if not _service_started:
            service.startup_sync()
            _service_started = True
    return service


async def startup_service_async() -> ThemeStudioService:
    """异步启动（FastAPI lifespan 里用）。"""
    return startup_service()


def shutdown_service() -> None:
    """停掉排程线程（应用退出时调用，尽力而为）。"""
    global _service_started
    service = _service
    if service is None:
        return
    try:
        service.shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.warning("主题工坊停止时出现异常（忽略）：%s", exc)
    _service_started = False


def entries() -> list[str]:
    """当前注册的 entry 列表（面板 / 调试用）。"""
    return sorted(ENTRY_REGISTRY)


def _resolve_entry(service: Any, entry_id: str) -> Any:
    """按 entry id 找到实现函数。

    优先用与 entry id 同名的方法：插件时代有一个 entry 的装饰器错挂在了内部工具
    函数上（``apply_saved_theme`` 的 ``@plugin_entry`` 落在 ``_apply_theme_record``
    头上），只按装饰器查表会拿到签名里没有 ``id`` 的那个实现，于是面板用
    ``{id: ...}`` 调用就报「缺少 theme_id 参数」—— 内置化时必须把它修掉。
    """
    candidate = getattr(service, entry_id, None)
    if callable(candidate) and getattr(candidate, "__name__", "") == entry_id:
        # 取未绑定函数：run_entry 统一按 ``fn(service, **kwargs)`` 调用。
        return getattr(candidate, "__func__", candidate)
    return ENTRY_REGISTRY.get(entry_id)


async def run_entry(entry_id: str, args: dict[str, Any] | None = None) -> Ok | Err:
    """调度一个 entry，返回 ``Ok`` / ``Err``（与插件时代语义一致）。"""
    payload = args if isinstance(args, dict) else {}
    startup_service()
    service = get_service()
    fn = _resolve_entry(service, str(entry_id or ""))
    if fn is None:
        return Err(SdkError(f"未知的主题工坊操作：{entry_id}"))
    try:
        result = fn(service, **_clean_kwargs(fn, payload))
        if inspect.isawaitable(result):
            result = await result
    except SdkError as exc:
        return Err(exc)
    except Exception as exc:  # noqa: BLE001 - 任何异常都必须变成可读错误
        logger.exception("主题工坊操作失败：%s", entry_id)
        return Err(SdkError(f"操作失败：{exc}"))
    if isinstance(result, (Ok, Err)):
        return result
    return Ok(result)


def _clean_kwargs(fn: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """只保留函数签名接受的参数（面板有时会多带字段）。

    签名里若声明了 ``**_``，则原样透传 —— 与原插件宿主的行为一致。
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # pragma: no cover - 内置函数才有
        return payload
    params = list(sig.parameters.values())[1:]
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return payload
    allowed = {
        p.name
        for p in params
        if p.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return {k: v for k, v in payload.items() if k in allowed}


def get_theme_document() -> dict[str, Any]:
    """加载器轮询用的调色板文档（含资源指纹，供换表 / 自升级判断）。"""
    startup_service()
    service = get_service()
    palette = service._read_unlocked()  # noqa: SLF001
    try:
        palette = service._with_asset_meta(palette)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 loader 资源指纹失败（忽略）：%s", exc)
    return palette


def data_dir() -> Path:
    """用户数据目录（theme.json / themes / backgrounds / schedule / exports）。"""
    return get_service().data_dir


def backgrounds_dir() -> Path:
    """背景图目录。"""
    return get_service()._backgrounds_dir()  # noqa: SLF001


def exports_dir() -> Path:
    """导出文件目录（面板「导出到文件 → 本体目录」的目标）。"""
    return get_service()._export_dir("plugin")  # noqa: SLF001


def presets_dir() -> Path:
    """内置主题目录（随本体发布、只读；含 ``images/`` 子目录）。"""
    return PRESETS_DIR


def library_dir() -> Path:
    """用户主题仓库目录（``themes/<id>.json``）。"""
    return get_service()._library_dir()  # noqa: SLF001