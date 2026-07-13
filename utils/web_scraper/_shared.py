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

# bs4 惰性 import（各解析函数内首用加载，utils.module_warmup 后台预热兜底）：本模块被
# system_router 顶层引用、坐在 main_server 启动 import 链上，顶层 bs4 会拖慢端口就绪。
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
    
    # 检查是否处于打包环境 (Nuitka 会定义 __nuitka_binary_dir)
    is_compiled = "__nuitka_binary_dir" in globals() or getattr(sys, 'frozen', False)

    try:
        # 用 find_spec 定位安装路径而非 import bilibili_api：修复只需要目录位置 +
        # mkdir/写 JSON，find_spec 不执行模块代码（毫秒级），而真 import 要 ~0.4s
        # 且本函数在模块加载期跑、坐在 main_server 启动 import 链上。时序契约不变：
        # 修复仍在 web_scraper import 期完成、先于 plugin/plugins/bilibili_* 的
        # import bilibili_api（见下方调用处注释，PR #1496）。
        import importlib.util

        # 1. 定位 bilibili_api 库路径
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

        # 2. 强制创建 data 目录
        if not data_dir.exists():
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"✅ 已补全缺失的 B站数据目录: {data_dir}")
            except Exception as e:
                logger.warning(f"❌ 无法创建数据目录 (可能是权限问题): {data_dir}, 错误: {e}")
                return
        else:
            logger.debug("B站数据目录已存在，检查配置文件...")

        # 3. 定义必须存在的配置文件及其默认内容
        # video_uploader_lines.json: 核心报错文件，必须是字典格式 {}
        # gevent_patch.json: 部分环境需要的补丁配置，通常是 {}
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
                # 检查文件是否为空或损坏 (可选)
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
        # 最后的兜底，确保此函数无论如何不会导致主程序崩溃
        logger.warning(f"⚠️ 尝试自修复 B站 API 环境时发生非预期异常: {e}")

# 在模块加载时立即执行：该修复会在 bilibili_api 安装目录里创建缺失的 data 文件
# （磁盘级、进程无关、一次性）。除了 web_scraper 自身，plugin/plugins/bilibili_*
# 也会直接 import bilibili_api 并依赖这些文件已就位——所以这一步必须在 import
# 期跑（而非 lazy 到 web_scraper 的 B 站函数被调时），否则那些插件在全新环境下
# 会踩到缺文件错误（见 PR #1496 codex review）。现用 find_spec 免去真 import，
# 修复本身只剩目录/文件检查，bilibili_api 的 import 由 module_warmup 后台预热。
_fix_bilibili_api_env()

# ==================================================
# 从 language_utils 导入区域检测功能
# ==================================================

try:
    from utils.language_utils import is_china_region as is_china_region
except ImportError:
    # 如果 language_utils 不可用，使用回退方案
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
