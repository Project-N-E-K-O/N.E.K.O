"""
1:1 ported from claudian/src/providers/claude/runtime/customSpawn.ts + utils/windowsCmdShim.ts

跨平台 spawn 工具 — 处理 Windows 上 `claude` 是 .cmd shim 的情况。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def is_windows() -> bool:
    return sys.platform == "win32"


def which_cmd(name: str) -> Optional[str]:
    """跨平台 which。"""
    return shutil.which(name)


def find_windows_shim(name: str) -> Optional[str]:
    """
    Windows 上 `claude` 命令经常是 .cmd shim。
    `shutil.which('claude')` 在 Windows 上默认返回 .exe 而非 .cmd，
    但用户直接调用 `claude` 时会优先走 .cmd。

    解决方法：显式找 .cmd / .bat 版本。
    """
    if not is_windows():
        return which_cmd(name)
    # 优先 .cmd
    for ext in (".cmd", ".bat", ".exe", ""):
        candidate = which_cmd(name + ext) if ext else which_cmd(name)
        if candidate:
            return candidate
    return None


async def run_process(
    cmd: List[str],
    *,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    stdin_data: Optional[bytes] = None,
    timeout: Optional[float] = None,
    on_stdout_line: Optional[Any] = None,  # async (line: str) -> None
    on_stderr_line: Optional[Any] = None,  # async (line: str) -> None
) -> Dict[str, Any]:
    """
    通用进程执行（一次性 / 短任务）。
    对应 claudian customSpawn.run。
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env or os.environ.copy(),
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def read_stream(stream, callback):
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if callback is not None:
                try:
                    res = callback(text)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

    tasks = []
    if on_stdout_line is not None:
        tasks.append(asyncio.create_task(read_stream(proc.stdout, on_stdout_line)))
    else:
        tasks.append(asyncio.create_task(_drain(proc.stdout)))
    if on_stderr_line is not None:
        tasks.append(asyncio.create_task(read_stream(proc.stderr, on_stderr_line)))
    else:
        tasks.append(asyncio.create_task(_drain(proc.stderr)))

    try:
        if stdin_data is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_data)
                await proc.stdin.drain()
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        if timeout is not None:
            try:
                rc = await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                rc = -1
        else:
            rc = await proc.wait()
    finally:
        for t in tasks:
            try:
                t.cancel()
            except Exception:
                pass

    return {"returncode": rc}


async def _drain(stream):
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            break


# ---------------------------------------------------------------------------
# AbortSignal 兼容（Python 用 asyncio.Event 替代）
# ---------------------------------------------------------------------------

class AbortSignal:
    """
    AbortSignal 兼容包装（对应 Node.js AbortController.signal）。
    用 asyncio.Event 实现。
    """

    def __init__(self):
        self._aborted = asyncio.Event()
        self._reason: Optional[str] = None

    @property
    def aborted(self) -> bool:
        return self._aborted.is_set()

    def abort(self, reason: Optional[str] = None):
        if not self._aborted.is_set():
            self._reason = reason
            self._aborted.set()

    def reason(self) -> Optional[str]:
        return self._reason

    async def wait(self):
        await self._aborted.wait()


# ---------------------------------------------------------------------------
# 一次性检测 claude CLI 是否存在
# ---------------------------------------------------------------------------

def detect_claude_cli() -> Optional[str]:
    """
    寻找 `claude` 可执行文件。
    - POSIX: 直接 which claude
    - Windows: 找 .cmd / .bat / .exe
    """
    if is_windows():
        return find_windows_shim("claude")
    return which_cmd("claude")
