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
"""Shared web-scraper primitives, user agents, and region detection."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, List, Any
from utils.logger_config import get_module_logger
import os

# Import bs4 lazily on first use in each parser, with utils.module_warmup background warmup; system_router imports this module
# at top level on main_server's startup chain, so eager bs4 delays port readiness.
if TYPE_CHECKING:
    pass
from pathlib import Path
import sys
from utils.file_utils import atomic_write_json

logger = get_module_logger("utils.web_scraper")

def _extract_llm_text_content(content: Any) -> str:
    """
    Best-effort extraction of usable text from LLM content of various shapes.
    Returns an empty string for empty packets or no valid text.
    """
    if content is None:
        return ""

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            text = ""
            if isinstance(item, str):
                text = item
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
            else:
                text = getattr(item, "text", "") or getattr(item, "content", "") or ""

            if isinstance(text, str):
                text = text.strip()
                if text:
                    parts.append(text)

        return "\n".join(parts).strip()

    if isinstance(content, dict):
        text = content.get("text") or content.get("content") or ""
        if isinstance(text, str):
            text = text.strip()
        return text if text else ""

    return str(content).strip()

def _fix_bilibili_api_env():
    """
    Fix-up function for the Nuitka packaged environment:
    detects at runtime and force-creates bilibili_api's missing data directory and key JSON config files.
    """
    logger.info("正在检查 Bilibili API 运行环境兼容性...")
    
    # Detect packaged execution (Nuitka defines __nuitka_binary_dir).
    is_compiled = "__nuitka_binary_dir" in globals() or getattr(sys, 'frozen', False)

    try:
        # Locate the installed path with find_spec rather than importing bilibili_api: repair needs only the directory plus
        # mkdir/JSON writes. find_spec executes no module code and takes milliseconds; importing takes about 0.4 s.
        # This function runs during module loading on main_server's startup chain. Preserve the timing contract:
        # repair still completes during web_scraper import, before plugin/plugins/bilibili_*
        # import bilibili_api (see call-site comments below, PR #1496).
        import importlib.util

        # 1. Locate the bilibili_api library.
        try:
            spec = importlib.util.find_spec("bilibili_api")
            origin = spec.origin if spec else None
            if not origin:
                logger.info("未检测到 bilibili_api 库，跳过环境修复逻辑。")
                return
            base_path = Path(os.path.dirname(origin))
            logger.info(f"检测到 bilibili_api 安装路径: {base_path}")
        except ImportError:
            logger.info("未检测到 bilibili_api 库，跳过环境修复逻辑。")
            return
        except Exception as e:
            logger.warning(f"无法确定 bilibili_api 安装路径，尝试跳过修复: {e}")
            return

        data_dir = base_path / "data"

        # 2. Ensure the data directory exists.
        if not data_dir.exists():
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"✅ 已补全缺失的 B站数据目录: {data_dir}")
            except Exception as e:
                logger.warning(f"❌ 无法创建数据目录 (可能是权限问题): {data_dir}, 错误: {e}")
                return
        else:
            logger.debug("B站数据目录已存在，检查配置文件...")

        # 3. Define required configuration files and defaults.
        # video_uploader_lines.json: the main failing file, which must contain a dict {}.
        # gevent_patch.json: patch configuration required in some environments, usually {}.
        missing_files = {
            "video_uploader_lines.json": {},
            "gevent_patch.json": {}
        }

        fixed_count = 0
        for file_name, default_content in missing_files.items():
            file_path = data_dir / file_name
            if not file_path.exists():
                try:
                    atomic_write_json(file_path, default_content)
                    logger.info(f"✅ 已强制补全缺失配置文件: {file_name}")
                    fixed_count += 1
                except Exception as e:
                    logger.warning(f"❌ 写入配置文件 {file_name} 失败: {e}")
            else:
                # Optionally check whether the file is empty or corrupt.
                try:
                    if file_path.stat().st_size == 0:
                        atomic_write_json(file_path, default_content)
                        logger.info(f"⚠️ 发现空文件 {file_name}，已重置为默认值")
                except Exception as e:
                    logger.warning(f"重置空文件 {file_name} 失败: {e}")

        if is_compiled:
            if fixed_count > 0:
                logger.info(f"打包环境修复完成，共修复 {fixed_count} 个资源文件。")
            else:
                logger.info("打包环境资源完整，无需修复。")

    except ImportError:
        logger.info("未检测到 bilibili_api 库，跳过环境修复逻辑。")
    except Exception as e:
        # Final fallback: this function must never crash the main program.
        logger.warning(f"⚠️ 尝试自修复 B站 API 环境时发生非预期异常: {e}")

# Run immediately during module loading: repair creates missing data files in bilibili_api's installation directory
# once, at disk level and independently of processes. Besides web_scraper, plugin/plugins/bilibili_*
# directly import bilibili_api and depend on these files already existing. Therefore run at import
# time rather than lazily when web_scraper's Bilibili functions are called, or plugins in fresh environments
# encounter missing files (PR #1496 codex review). find_spec now avoids actual import;
# repair only checks directories/files, while module_warmup imports bilibili_api in the background.
_fix_bilibili_api_env()

# ==================================================
# Import region detection from language_utils.
# ==================================================

try:
    from utils.language_utils import is_china_region as is_china_region
except ImportError:
    # Fall back if language_utils is unavailable.
    import locale
    def is_china_region() -> bool:
        """
        Region detection fallback

        Returns True only for mainland China (zh_cn and variants)
        Hong Kong/Macau/Taiwan (zh_tw, zh_hk) return False
        Chinese-language Windows systems return True
        """
        mainland_china_locales = {'zh_cn', 'chinese_china', 'chinese_simplified_china'}
       
        def normalize_locale(loc: str) -> str:
            """Normalize a locale string: lowercase, replace hyphens, strip encoding"""
            if not loc:
                return ''
            loc = loc.lower()
            loc = loc.replace('-', '_')
            if '.' in loc:
                loc = loc.split('.')[0]
            return loc

        def check_locale(loc: str) -> bool:
            """Check whether the normalized locale is mainland China"""
            normalized = normalize_locale(loc)
            if not normalized:
                return False
            if normalized in mainland_china_locales:
                return True
            if normalized.startswith('zh_cn'):
                return True
            if 'chinese' in normalized and 'china' in normalized:
                return True
            return False

        try:
            try:
                system_locale = locale.getlocale()[0]
                if system_locale and check_locale(system_locale):
                    return True
            except Exception:
                # Locale probing is best-effort and must not break detection.
                pass

            try:
                default_locale = locale.getdefaultlocale()[0]
                if default_locale and check_locale(default_locale):
                    return True
            except Exception:
                # Deprecated locale APIs can fail on partially configured hosts.
                pass

            return False
        except Exception:
            return False

__all__ = (
    "USER_AGENTS",
    "_extract_llm_text_content",
    "_fix_bilibili_api_env",
    "get_random_user_agent",
    "is_china_region",
    "logger",
)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
]

def get_random_user_agent() -> str:
    """Get a random User-Agent"""
    return random.choice(USER_AGENTS)
