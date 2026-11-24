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

"""主题工坊加载器两件套的磁盘指纹（只给模板拼缓存版本号用）。

内置之后，模板里是普通的两行静态引用：

.. code-block:: html

    <link rel="stylesheet" href="/static/theme-studio/loader/neko-theme-studio.css?v=<版本>-<css8>">
    <script src="/static/theme-studio/loader/neko-theme-studio.js?v=<版本>-<加载器版本>" defer></script>

``?v=`` 后面的指纹不是装饰：浏览器侧加载器（``neko-theme-studio.js``）每 15 秒复检
一次「页面里生效的样式表是不是磁盘上那一份」，判断依据就是 URL 里那段 ``-<css8>``。
页面 URL 不带指纹时，它只能把 158KB 的 CSS 整份下载回来比规则签名 —— 每个窗口每
15 秒一次，正是插件时代被用户抱怨的耗电点（1.6.x 之前的老模板）。

这里只做 ``stat`` + 读两个小文件，不构造主题服务、不碰配置目录，所以页面渲染路径
（``main_routers/pages_router.py``、``app/monitor.py``）可以随便调；文件缺失时返回
空字典，模板里的 ``?v=`` 退化成普通 URL，加载器也会自己回落到下载比对的老路径。

指纹按文件的 ``(mtime_ns, size)`` 缓存：改了就重算，没改就零开销。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

LOADER_DIR_NAME = "loader"
CSS_FILE_NAME = "neko-theme-studio.css"
JS_FILE_NAME = "neko-theme-studio.js"

# 加载器脚本里的 `SCRIPT_VERSION = '1.6.39'`。只认点分数字，别的写法一律当没有。
_RE_SCRIPT_VERSION = re.compile(r"SCRIPT_VERSION\s*=\s*'([0-9][0-9.]*)'")

_CACHE: tuple[tuple, dict] = ((), {})


def loader_asset_meta(loader_dir: str | Path) -> dict:
    """返回 ``{"css_sha8": ..., "loader_version": ...}``；资源不在时返回 ``{}``。"""
    global _CACHE

    base = Path(loader_dir)
    css_path = base / CSS_FILE_NAME
    js_path = base / JS_FILE_NAME
    try:
        css_stat = css_path.stat()
        js_stat = js_path.stat()
    except OSError:
        return {}

    key = (
        str(base),
        css_stat.st_mtime_ns,
        css_stat.st_size,
        js_stat.st_mtime_ns,
        js_stat.st_size,
    )
    cached_key, cached_meta = _CACHE
    if cached_key == key and cached_meta:
        return dict(cached_meta)

    try:
        css_sha8 = hashlib.sha256(css_path.read_bytes()).hexdigest()[:8]
    except OSError:
        return {}

    loader_version = ""
    try:
        match = _RE_SCRIPT_VERSION.search(js_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        match = None
    if match:
        loader_version = match.group(1)

    meta = {"css_sha8": css_sha8, "loader_version": loader_version}
    _CACHE = (key, meta)
    return dict(meta)


def loader_versions(loader_dir: str | Path, static_asset_version: str) -> dict:
    """拼成模板上下文里可直接用的两个版本串。

    ``theme_loader_js_version`` 优先用脚本自己声明的版本号（读得出来时）；读不出来
    就退回 CSS 指纹 —— 两个变量只是 URL 的一部分，真实的新旧判断在加载器内部。
    """
    meta = loader_asset_meta(loader_dir)
    if not meta:
        return {}
    fallback = meta["css_sha8"]
    return {
        "theme_loader_css_version": f"{static_asset_version}-{meta['css_sha8']}",
        "theme_loader_js_version": f"{static_asset_version}-{meta['loader_version'] or fallback}",
    }