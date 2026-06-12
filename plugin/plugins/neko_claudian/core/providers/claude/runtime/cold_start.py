"""
1:1 ported from claudian/src/providers/claude/runtime/claudeColdStartQuery.ts

冷启动查询 — 一次性 `claude -p <prompt>`，无持久子进程。
用于 inline edit / title generation / instruction refine。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from .cli_resolver import ClaudeCliResolver
from .query_options import build_cold_start_args
from .spawn import is_windows
from .types import ChatRuntimeQueryOptions, StreamChunk, ChunkType
from ...stream.transform import transform_sdk_message


class ColdStartQuery:
    """
    冷启动 Claude CLI 子进程，读取 stream-json 输出，转 StreamChunk 流。
    与 Claudian claudeColdStartQuery.claudeColdStartQuery 一致。
    """

    def __init__(self, cli_resolver: Optional[ClaudeCliResolver] = None):
        self.cli_resolver = cli_resolver or ClaudeCliResolver()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._abort = asyncio.Event()

    async def query(
        self,
        prompt: str,
        options: ChatRuntimeQueryOptions,
    ) -> AsyncIterator[StreamChunk]:
        """启动冷查询，yield StreamChunk。"""
        cli = self.cli_resolver.resolve()
        if not cli:
            raise FileNotFoundError(
                "找不到 claude CLI。请先安装 Claude Code："
                "npm install -g @anthropic-ai/claude-code"
            )

        opts = build_cold_start_args(prompt, options)
        # Windows .cmd shim 处理
        cmd = [cli] + opts["args"]
        if is_windows() and cli.lower().endswith(".cmd"):
            cmd = ["cmd", "/c"] + cmd
        cwd = opts.get("cwd") or "."

        env = os.environ.copy()
        env.update(opts.get("env", {}))

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # 关闭 stdin（不需要）
        if self._proc.stdin is not None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass

        try:
            async for chunk in self._consume(self._proc):
                yield chunk
        finally:
            await self._cleanup()

    async def _consume(self, proc: asyncio.subprocess.Process) -> AsyncIterator[StreamChunk]:
        """
        读取 stdout 的 stream-json 行，转换。
        """
        assert proc.stdout is not None
        pending_text: List[str] = []
        async for line in proc.stdout:
            if self._abort.is_set():
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not text.strip():
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                # 非 JSON 行 — 当 stderr-like 处理
                yield StreamChunk(
                    type=ChunkType.STATUS.value,
                    data={"text": text, "source": "stdout"},
                )
                continue
            for chunk in transform_sdk_message(msg):
                yield chunk

        # 等待进程退出
        rc = await proc.wait()
        yield StreamChunk(
            type=ChunkType.DONE.value,
            data={"returncode": rc},
        )

    async def _cleanup(self):
        if self._proc is not None:
            try:
                if self._proc.returncode is None:
                    self._proc.kill()
                    await self._proc.wait()
            except Exception:
                pass
            self._proc = None

    def abort(self):
        self._abort.set()


async def cold_start_query(
    prompt: str,
    options: ChatRuntimeQueryOptions,
    *,
    cli_resolver: Optional[ClaudeCliResolver] = None,
) -> AsyncIterator[StreamChunk]:
    """便捷函数。"""
    q = ColdStartQuery(cli_resolver=cli_resolver)
    async for chunk in q.query(prompt, options):
        yield chunk
