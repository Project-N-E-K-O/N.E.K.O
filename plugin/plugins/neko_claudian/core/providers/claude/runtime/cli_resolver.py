"""
1:1 ported from claudian/src/providers/claude/cli/findClaudeCLIPath.ts + ClaudeCliResolver.ts

寻找 / 解析 `claude` CLI 路径。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from .spawn import detect_claude_cli, is_windows, which_cmd


# 常见安装位置（仿 Claudian ClaudeCliResolver）
_COMMON_POSIX = [
    Path.home() / ".claude" / "local" / "claude",
    Path.home() / ".local" / "bin" / "claude",
    Path("/usr/local/bin/claude"),
    Path("/opt/homebrew/bin/claude"),
]

_COMMON_WINDOWS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude" / "claude.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "claude.exe",
    Path(os.environ.get("USERPROFILE", "")) / ".claude" / "local" / "claude.exe",
]


def _normalize_path(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    return str(Path(p).resolve())


def resolve_claude_cli() -> Optional[str]:
    """
    顺序：
    1. 环境变量 CLAUDE_CLI_PATH / CLAUDE_PATH
    2. which claude（跨平台）
    3. 常见安装位置
    4. 常见 npm/pnpm 全局路径
    """
    # 1) 环境变量
    for var in ("CLAUDE_CLI_PATH", "CLAUDE_PATH", "CLAUDE_BIN"):
        v = os.environ.get(var)
        if v and Path(v).exists():
            return _normalize_path(v)

    # 2) which
    cli = detect_claude_cli()
    if cli:
        return _normalize_path(cli)

    # 3) 常见位置
    candidates: List[Path] = []
    if is_windows():
        candidates.extend(_COMMON_WINDOWS)
    else:
        candidates.extend(_COMMON_POSIX)
    for c in candidates:
        if c.exists():
            return _normalize_path(str(c))

    # 4) npm/pnpm 全局
    for glob in (
        Path.home() / ".npm" / "bin" / "claude",
        Path.home() / ".pnpm" / "bin" / "claude",
    ):
        if glob.exists():
            return _normalize_path(str(glob))

    return None


class ClaudeCliResolver:
    """封装 cli 解析逻辑，缓存结果。"""

    def __init__(self):
        self._cached: Optional[str] = None

    def resolve(self, force: bool = False) -> Optional[str]:
        if force or self._cached is None:
            self._cached = resolve_claude_cli()
        return self._cached

    def invalidate(self):
        self._cached = None

    def exists(self) -> bool:
        return self.resolve() is not None

    def path_or_default(self) -> str:
        return self.resolve() or "claude"
