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

"""主题工坊的本体 API（``/api/theme_studio``）。

这些端点在插件时代是「插件宿主 + 插件 entry」，现在直接由本体提供：

* ``GET  /theme.json``            —— 加载器每 1.5s 轮询的调色板（与面板同源）。
* ``POST /run/{entry_id}``        —— 通用操作调度（对应原来的 26 个 plugin_entry）。
* ``GET  /backgrounds/{filename}`` —— 聊天背景图。
* ``GET  /presets/{filename}``     —— 内置主题原始 JSON（只读，悬停预演用）。
* ``GET  /presets/images/{filename}`` —— 内置主题自带预览图。
* ``GET  /themes/{filename}``       —— 用户仓库单条主题原始 JSON。
* ``GET  /exports/{filename}``    —— 「导出到本体目录」产出的文件。
* ``GET  /entries``               —— 已注册操作清单（调试 / 面板自检）。

安全边界与原插件一致：所有输入都不可信，**校验在 service 层完成**（变量名白名单、
值格式、zip 大小上限等），这里只做「路径不得逃出数据目录」这类外壳校验。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from main_routers.cookies_login_router import verify_local_access
from theme_studio import (
    backgrounds_dir,
    entries,
    exports_dir,
    get_theme_document,
    library_dir,
    presets_dir,
    run_entry,
    startup_service,
)

router = APIRouter(
    prefix="/api/theme_studio",
    tags=["theme-studio"],
    dependencies=[Depends(verify_local_access)],
)

#: 背景图 / 导出文件名白名单。与 service 里的 ``RE_BG_NAME`` 同源，但这里更严：
#: 只允许「字母数字点下划线短横」，从根上杜绝 ``..`` 与路径分隔符。
_RE_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_RE_ENTRY_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _no_store(payload: dict[str, Any]) -> JSONResponse:
    """调色板响应：必须禁缓存，否则加载器 1.5s 的轮询会读到浏览器缓存。"""
    response = JSONResponse(content=payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _safe_file(directory: Path, filename: str) -> Path:
    """把「目录 + 用户给的文件名」解成绝对路径，并确认没有逃出目录。"""
    if not filename or not _RE_SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=400, detail="文件名不合法")
    root = Path(directory).resolve()
    candidate = (root / filename).resolve()
    if candidate.parent != root:
        raise HTTPException(status_code=400, detail="文件名不合法")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return candidate


@router.get("/theme.json")
async def get_theme_json() -> JSONResponse:
    """当前调色板。加载器与面板都读这一个端点。"""
    startup_service()
    return _no_store(get_theme_document())


@router.get("/palette")
async def get_palette() -> JSONResponse:
    """``get_theme`` 的 REST 别名（语义同上，供脚本 / 调试使用）。"""
    startup_service()
    return _no_store(get_theme_document())


@router.post("/run/{entry_id}")
async def run_theme_entry(
    entry_id: str,
    request: Request,
    args: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """通用操作调度：``{ok, result}`` 或 ``{ok: false, error}``。

    与插件的 run 协议不同，这里是**同步返回**：主题工坊所有操作都在毫秒级完成
    （最大的一个 —— 导入分享包 —— 也只是本地 zip 解析），不需要 run_id + 轮询。
    面板侧只改一处 ``callPluginOnce`` 就能对接。
    """
    if not _RE_ENTRY_ID.match(entry_id or ""):
        raise HTTPException(status_code=400, detail="操作名不合法")
    startup_service()
    result = await run_entry(entry_id, args)
    if result.ok:
        return JSONResponse(content={"ok": True, "result": result.value})
    error = result.error
    message = str(error) if error is not None else "操作失败"
    return JSONResponse(
        content={"ok": False, "error": message, "entry_id": entry_id},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/entries")
async def list_theme_entries() -> dict[str, Any]:
    """已注册的操作清单（面板自检 / 调试用）。"""
    startup_service()
    return {"count": len(entries()), "entries": entries()}


@router.get("/backgrounds/{filename}")
async def get_theme_background(filename: str) -> FileResponse:
    """聊天背景图。加载器把这个 URL 写进 ``--nts-chat-bg-image``。"""
    startup_service()
    path = _safe_file(backgrounds_dir(), filename)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/exports/{filename}")
async def get_theme_export(filename: str) -> FileResponse:
    """「导出到本体目录」产出的文件（下载链接）。"""
    startup_service()
    path = _safe_file(exports_dir(), filename)
    return FileResponse(path, filename=path.name)


# ---------------------------------------------------------------------------
# 只读主题文件
#
# 面板的「悬停预演」要读单条主题的原始 JSON（``presets/<id>.json`` 与
# ``themes/<id>.json``），预演图还要 ``presets/images/<hash>``。插件时代这些
# 就是 ``/plugin/neko_theme_studio/ui/`` 下的普通静态文件；内置之后它们分散在
# 「代码目录（内置预设，只读）」与「用户数据目录（仓库）」，所以各给一条路由。
# 走静态文件而不是 entry：悬停是高频动作，entry 调度（含校验）比读盘慢一个量级。
# ---------------------------------------------------------------------------


@router.get("/presets/{filename}")
async def get_theme_preset(filename: str) -> FileResponse:
    """内置主题的原始 JSON（只读，随本体发布）。"""
    path = _safe_file(presets_dir(), filename)
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/presets/images/{filename}")
async def get_theme_preset_image(filename: str) -> FileResponse:
    """内置主题自带的预览图。"""
    path = _safe_file(presets_dir() / "images", filename)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/themes/{filename}")
async def get_theme_library_item(filename: str) -> FileResponse:
    """用户主题仓库里单条主题的原始 JSON（悬停预演用）。"""
    startup_service()
    path = _safe_file(library_dir(), filename)
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-store"})