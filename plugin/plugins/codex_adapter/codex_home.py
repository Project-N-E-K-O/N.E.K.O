"""Codex Adapter — CODEX_HOME 目录管理。

参考 Paperclip `codex-local` 适配器的 codex-home.ts，
提供 CODEX_HOME 目录的解析、初始化和 auth.json 写入能力。

Codex CLI 通过 ``CODEX_HOME`` 环境变量或默认 ``~/.codex`` 目录读取配置：
- ``auth.json``：认证凭据（ChatGPT 模式或 OPENAI_API_KEY 模式）
- ``config.toml``：模型、工具等配置
- ``config.json``：旧版配置
- ``instructions.md``：全局指令

N.E.K.O 插件支持两种模式：
1. **共享模式**（默认）：直接使用 ``~/.codex`` 或 ``CODEX_HOME`` 环境变量。
2. **管理模式**：在插件配置中指定 ``codex_home``，可选从共享目录 seed
   配置文件，并写入 API key 模式的 ``auth.json``。

注意：Codex CLI >= 0.122 忽略 ``OPENAI_API_KEY`` 环境变量，
只读 ``$CODEX_HOME/auth.json``，因此 API key 必须写入该文件。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

# 与 paperclip codex-home.ts 对齐
_COPIED_SHARED_FILES: tuple[str, ...] = ("config.json", "config.toml", "instructions.md")
_SYMLINKED_SHARED_FILES: tuple[str, ...] = ("auth.json",)


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def is_windows() -> bool:
    return sys.platform == "win32"


def resolve_shared_codex_home(env: Optional[dict[str, str]] = None) -> str:
    """解析共享 CODEX_HOME 目录（来自 paperclip resolveSharedCodexHomeDir）。

    优先级：
    1. ``CODEX_HOME`` 环境变量
    2. ``~/.codex``（用户主目录下的 .codex）
    """
    env_map = env if env is not None else os.environ
    from_env = (env_map.get("CODEX_HOME") or "").strip()
    if from_env:
        return os.path.abspath(from_env)
    return os.path.join(os.path.expanduser("~"), ".codex")


def resolve_effective_codex_home(
    configured: str,
    env: Optional[dict[str, str]] = None,
) -> str:
    """解析实际使用的 CODEX_HOME 目录。

    Parameters
    ----------
    configured:
        插件配置中的 ``codex_home`` 字段。空字符串表示使用共享目录。
    env:
        环境变量映射（默认使用 ``os.environ``）。
    """
    normalized = (configured or "").strip()
    if normalized:
        return os.path.abspath(normalized)
    return resolve_shared_codex_home(env)


# ---------------------------------------------------------------------------
# 文件存在性检查
# ---------------------------------------------------------------------------


def path_exists(candidate: str) -> bool:
    """检查路径是否存在（文件或目录）。"""
    return os.path.exists(candidate)


async def path_exists_async(candidate: str) -> bool:
    """异步检查路径是否存在。

    使用 ``asyncio.get_event_loop().run_in_executor`` 包装 ``os.path.exists``，
    避免在异步上下文中阻塞。
    """
    import asyncio

    return await asyncio.get_running_loop().run_in_executor(None, os.path.exists, candidate)


# ---------------------------------------------------------------------------
# auth.json 写入
# ---------------------------------------------------------------------------


def write_api_key_auth_json(home: str, api_key: str) -> str:
    """写入 API key 模式的 auth.json（来自 paperclip writeApiKeyAuthJson）。

    覆盖 ``home/auth.json`` 文件，内容为 ``{"OPENAI_API_KEY": "..."}``。
    文件权限设为 0600（仅所有者可读写）。

    Returns
    -------
    str
        写入的 auth.json 文件路径。
    """
    home_path = Path(home)
    home_path.mkdir(parents=True, exist_ok=True)
    target = home_path / "auth.json"

    # 移除已存在的文件或符号链接
    if target.exists() or target.is_symlink():
        try:
            target.unlink()
        except OSError:
            pass

    payload = {"OPENAI_API_KEY": api_key}

    # 以 0600 权限直接创建文件，避免先创建世界可读文件再 chmod 的权限窗口期
    if not is_windows():
        try:
            fd = os.open(
                str(target),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
        except OSError:
            # os.open 失败，回退到 write_text
            target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        else:
            # os.open 成功，尝试 fdopen
            try:
                f = os.fdopen(fd, "w", encoding="utf-8")
            except Exception:
                # os.fdopen 失败，fd 未被接管，需要手动关闭
                try:
                    os.close(fd)
                except OSError:
                    pass
                # 回退到 write_text
                target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
            else:
                # os.fdopen 成功，fd 已被接管，由 with 块管理
                try:
                    with f:
                        f.write(json.dumps(payload, indent=2))
                except Exception:
                    # f.write 失败，fd 已被 with 关闭，回退到 write_text
                    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    try:
                        os.chmod(target, 0o600)
                    except OSError:
                        pass
    else:
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return str(target)


# ---------------------------------------------------------------------------
# 符号链接管理
# ---------------------------------------------------------------------------


def ensure_symlink(target: str, source: str) -> bool:
    """确保 ``target`` 是指向 ``source`` 的符号链接（来自 paperclip ensureSymlink）。

    如果 ``target`` 已存在且不是符号链接，会被删除后重建。
    如果 ``target`` 已是指向 ``source`` 的符号链接，不做任何操作。

    Returns
    -------
    bool
        True 表示成功创建或已存在正确的符号链接；
        False 表示创建失败（如 Windows 无权限）。
    """
    target_path = Path(target)
    source_path = Path(source)

    # 检查现有符号链接
    if target_path.is_symlink():
        try:
            existing_target = os.readlink(target)
            if os.path.abspath(existing_target) == os.path.abspath(source):
                return True
        except OSError:
            pass
        # 指向不同源 — 删除重建
        try:
            target_path.unlink()
        except OSError:
            return False
    elif target_path.exists():
        # 已存在但不是符号链接（可能是旧的复制版本）
        # paperclip 的逻辑：如果是目录则跳过，如果是文件则删除
        if target_path.is_dir():
            return False
        try:
            target_path.unlink()
        except OSError:
            return False

    # 创建父目录
    target_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.symlink(source, target)
        return True
    except OSError:
        # Windows 无权限或非管理员无法创建符号链接
        return False


def ensure_copied_file(target: str, source: str) -> bool:
    """确保 ``target`` 是 ``source`` 的副本（来自 paperclip ensureCopiedFile）。

    如果 ``target`` 已存在，不做任何操作（保留现有内容）。

    Returns
    -------
    bool
        True 表示成功复制或文件已存在；
        False 表示复制失败。
    """
    target_path = Path(target)
    if target_path.exists():
        return True

    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, target)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# CODEX_HOME 初始化
# ---------------------------------------------------------------------------


def prepare_managed_codex_home(
    target_home: str,
    *,
    api_key: str = "",
    source_home: Optional[str] = None,
    logger: Any = None,
) -> str:
    """初始化受管理的 CODEX_HOME 目录（来自 paperclip prepareManagedCodexHome）。

    流程：
    1. 创建 ``target_home`` 目录
    2. 如果 ``target_home`` 与共享目录不同：
       - 从共享目录符号链接 ``auth.json``（ChatGPT 模式凭据）
       - 从共享目录复制 ``config.json`` / ``config.toml`` / ``instructions.md``
    3. 如果提供了 ``api_key``：覆盖写入 API key 模式的 ``auth.json``

    Parameters
    ----------
    target_home:
        受管理的 CODEX_HOME 目录路径。
    api_key:
        OPENAI_API_KEY。非空时写入 auth.json，覆盖符号链接。
    source_home:
        共享 CODEX_HOME 目录（用于 seed 配置）。None 表示自动解析。
    logger:
        可选的日志记录器（必须有 info 方法）。

    Returns
    -------
    str
        初始化完成的 CODEX_HOME 目录路径。
    """
    target_path = Path(target_home)
    target_path.mkdir(parents=True, exist_ok=True)

    # 解析共享目录
    shared_home = source_home or resolve_shared_codex_home()
    seed_from_shared = os.path.abspath(shared_home) != os.path.abspath(target_home)

    # 如果没有 api_key，且 auth.json 是普通文件（非符号链接），
    # 移除它以便恢复 ChatGPT 模式的符号链接
    if not api_key and seed_from_shared:
        auth_path = target_path / "auth.json"
        if auth_path.exists() and not auth_path.is_symlink():
            try:
                auth_path.unlink()
            except OSError:
                pass

    # 从共享目录 seed 配置
    if seed_from_shared and os.path.isdir(shared_home):
        # 符号链接 auth.json（ChatGPT 模式凭据，refresh token 会轮换）
        for name in _SYMLINKED_SHARED_FILES:
            source = os.path.join(shared_home, name)
            if not os.path.exists(source):
                continue
            target = str(target_path / name)
            ensure_symlink(target, source)

        # 复制配置文件（不会轮换）
        for name in _COPIED_SHARED_FILES:
            source = os.path.join(shared_home, name)
            if not os.path.exists(source):
                continue
            target = str(target_path / name)
            ensure_copied_file(target, source)

        if logger is not None:
            try:
                logger.info(
                    "CodexAdapter: using managed CODEX_HOME %s (seeded from %s)",
                    target_home,
                    shared_home,
                )
            except Exception:
                pass

    # 写入 API key 模式的 auth.json
    if api_key:
        write_api_key_auth_json(target_home, api_key)
        if logger is not None:
            try:
                logger.info(
                    "CodexAdapter: wrote API-key auth.json into CODEX_HOME %s",
                    target_home,
                )
            except Exception:
                pass

    return target_home


async def prepare_managed_codex_home_async(
    target_home: str,
    *,
    api_key: str = "",
    source_home: Optional[str] = None,
    logger: Any = None,
) -> str:
    """异步版本的 ``prepare_managed_codex_home``。

    使用 ``run_in_executor`` 包装阻塞的文件操作，
    避免违反 ruff ASYNC 规则。
    """
    import asyncio

    return await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: prepare_managed_codex_home(
            target_home,
            api_key=api_key,
            source_home=source_home,
            logger=logger,
        ),
    )


# ---------------------------------------------------------------------------
# 环境变量构建
# ---------------------------------------------------------------------------


def build_codex_home_env(
    codex_home: str,
    base_env: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """构建包含 CODEX_HOME 的环境变量映射。

    Parameters
    ----------
    codex_home:
        CODEX_HOME 目录路径。空字符串表示不设置（使用默认）。
    base_env:
        基础环境变量（默认使用 ``os.environ``）。
    """
    env = dict(base_env if base_env is not None else os.environ)
    normalized = (codex_home or "").strip()
    if normalized:
        env["CODEX_HOME"] = normalized
    return env


__all__ = [
    "is_windows",
    "resolve_shared_codex_home",
    "resolve_effective_codex_home",
    "path_exists",
    "path_exists_async",
    "write_api_key_auth_json",
    "ensure_symlink",
    "ensure_copied_file",
    "prepare_managed_codex_home",
    "prepare_managed_codex_home_async",
    "build_codex_home_env",
]
