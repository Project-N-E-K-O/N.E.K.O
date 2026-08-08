"""
Claude Code 异步任务管理器

支持后台执行 Claude Code 任务，避免 main_server 的 300 秒超时限制。
采用异步提交 + 轮询/主动通知的混合模式。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .executor import ClaudeCLIExecutor, build_cli_invocation
from .models import AdapterConfig, ExecuteResult
from .parser import ClaudeOutputParser

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class TaskRecord:
    """任务记录"""
    task_id: str
    prompt: str
    cwd: str
    model: str = ""
    effort: str = ""
    max_turns: int = 0
    resume_session_id: str = ""  # 用于判断是否是新会话
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[ExecuteResult] = None
    error_message: Optional[str] = None
    _task: Optional[asyncio.Task] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于返回给调用方）"""
        elapsed = 0.0
        if self.started_at:
            end_time = self.finished_at or time.time()
            elapsed = end_time - self.started_at

        data = {
            "task_id": self.task_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "elapsed": round(elapsed, 2),
        }

        if self.status == TaskStatus.DONE and self.result:
            data["result"] = self.result.to_llm_payload()
            # 返回 session_id 用于恢复
            data["session_id"] = self.result.session_id or ""
        elif self.status == TaskStatus.ERROR:
            data["error"] = self.error_message or "Unknown error"
            # 错误时也返回 session_id，用于恢复
            data["session_id"] = self.resume_session_id
        elif self.status == TaskStatus.CANCELLED:
            data["error"] = "Task was cancelled"
            data["session_id"] = self.resume_session_id
        elif self.status == TaskStatus.RUNNING:
            # 运行中时也返回已知的 session_id
            data["session_id"] = self.resume_session_id

        return data


class TaskManager:
    """
    Claude Code 任务管理器

    功能：
    - 提交任务到后台执行（asyncio.create_task）
    - 查询任务状态和结果
    - 取消正在运行的任务
    - 自动清理过期任务（7天后）
    - 并发控制（最多同时 2 个任务）
    - 持久化任务记录到 PluginStore，支持按 task_id 或 session_id 查询
    """

    def __init__(self, executor, config, store=None, *, logger=None):
        self._executor = executor
        self._config = config
        self._store = store  # PluginStore 实例，用于持久化
        self.logger = logger or logging.getLogger(__name__)
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._max_concurrent = 2  # 最大并发任务数
        self._result_ttl = 7 * 24 * 3600  # 结果保留时间（7天）

    async def start(self):
        """启动清理任务，并恢复历史任务"""
        # 从 PluginStore 恢复历史任务
        if self._store:
            await self._restore_tasks()

        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            self.logger.info(f"TaskManager started, restored {len(self._tasks)} tasks")

    async def stop(self):
        """停止所有任务"""
        # 取消清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        # 取消所有运行中的任务
        async with self._lock:
            for record in self._tasks.values():
                if record.status == TaskStatus.RUNNING and record._task:
                    record._task.cancel()
                    try:
                        await record._task
                    except (asyncio.CancelledError, Exception):
                        pass
            self._tasks.clear()

        self.logger.info("TaskManager stopped")

    async def submit(
        self,
        prompt: str,
        cwd: str = "",
        model: str = "",
        effort: str = "",
        max_turns: int = 0,
        resume_session_id: str = "",
    ) -> TaskRecord:
        """
        提交任务到后台执行

        Args:
            resume_session_id: 可选，恢复之前的会话 ID。如果提供，Claude Code 会在该会话上下文中继续工作。

        Returns:
            TaskRecord: 任务记录（包含 task_id 和初始状态）
        """
        # 在锁内检查并发限制并创建任务记录
        async with self._lock:
            running_count = sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)
            if running_count >= self._max_concurrent:
                raise RuntimeError(
                    f"已达到最大并发任务数 {self._max_concurrent}，请等待现有任务完成后再提交"
                )

            task_id = str(uuid.uuid4())[:8]
            record = TaskRecord(
                task_id=task_id,
                prompt=prompt,
                cwd=cwd,
                model=model,
                effort=effort,
                max_turns=max_turns,
                resume_session_id=resume_session_id,
                status=TaskStatus.PENDING,
            )
            self._tasks[task_id] = record

        # 在锁外启动后台任务，避免死锁
        record._task = asyncio.create_task(self._execute_task(record))
        self.logger.info(f"Task submitted: {task_id}, resume_session_id: {resume_session_id or 'new session'}")

        return record

    async def poll(self, task_id: str) -> Dict[str, Any]:
        """
        查询任务状态

        Returns:
            任务状态字典
        """
        async with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return {"error": f"Task not found: {task_id}"}
            return record.to_dict()

    async def cancel(self, task_id: str) -> Dict[str, Any]:
        """
        取消任务

        Returns:
            取消结果
        """
        async with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return {"error": f"Task not found: {task_id}"}

            if record.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                return {"error": f"Task already finished: {record.status.value}"}

            # 取消任务
            task = record._task
            record.status = TaskStatus.CANCELLED
            record.finished_at = time.time()

        # 在锁外取消任务
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        self.logger.info(f"Task cancelled: {task_id}")
        return {"status": "cancelled", "task_id": task_id}

    async def _execute_task(self, record: TaskRecord):
        """后台执行任务"""
        try:
            record.status = TaskStatus.RUNNING
            record.started_at = time.time()

            # 构建 CLI 调用
            invocation, build_err = build_cli_invocation(
                self._config,
                prompt=record.prompt,
                cwd=record.cwd,
                model=record.model,
                effort=record.effort,
                max_turns=record.max_turns,
                resume_session_id=record.resume_session_id,
            )

            if build_err:
                raise RuntimeError(f"Failed to build CLI invocation: {build_err}")

            # 执行
            parser = ClaudeOutputParser()
            stream, exec_err = await self._executor.execute(invocation, parser)

            if exec_err:
                raise RuntimeError(f"Execution failed: {exec_err}")

            # 构造结果
            result = ExecuteResult(
                session_id=stream.session_id,
                is_new_session=not bool(record.resume_session_id),
                messages=stream.messages,
                final_text=stream.final_text,
                total_cost_usd=stream.result.total_cost_usd if stream.result else 0.0,
                duration_ms=stream.result.duration_ms if stream.result else 0,
                num_turns=stream.result.num_turns if stream.result else 0,
                raw_result=stream.result.raw if stream.result else {},
            )

            record.result = result
            record.status = TaskStatus.DONE
            record.finished_at = time.time()
            self.logger.info(f"Task completed: {record.task_id}")

            # 持久化完成的任务
            await self._persist_task(record)

        except asyncio.CancelledError:
            record.status = TaskStatus.CANCELLED
            record.finished_at = time.time()
            self.logger.info(f"Task cancelled: {record.task_id}")
            # 持久化取消的任务
            await self._persist_task(record)
            raise

        except Exception as e:
            record.status = TaskStatus.ERROR
            record.error_message = str(e)
            record.finished_at = time.time()
            self.logger.error(f"Task failed: {record.task_id}, error: {e}")
            # 持久化失败的任务
            await self._persist_task(record)

    async def _cleanup_loop(self):
        """定期清理过期任务"""
        try:
            while True:
                await asyncio.sleep(60)  # 每分钟检查一次
                await self._cleanup_expired()
        except asyncio.CancelledError:
            pass

    async def _cleanup_expired(self):
        """清理过期的已完成任务"""
        async with self._lock:
            now = time.time()
            expired_ids = []

            for task_id, record in self._tasks.items():
                if record.status in (TaskStatus.DONE, TaskStatus.ERROR, TaskStatus.CANCELLED):
                    if record.finished_at and (now - record.finished_at) > self._result_ttl:
                        expired_ids.append(task_id)

            for task_id in expired_ids:
                del self._tasks[task_id]
                self.logger.debug(f"Cleaned up expired task: {task_id}")

    def get_running_count(self) -> int:
        """获取当前运行中的任务数"""
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)

    def get_all_tasks(self) -> List[Dict[str, Any]]:
        """获取所有任务的状态（用于调试/监控）"""
        return [record.to_dict() for record in self._tasks.values()]

    async def get_task_by_id(self, task_id: str) -> Optional[TaskRecord]:
        """根据 task_id 查询任务记录"""
        async with self._lock:
            return self._tasks.get(task_id)

    async def get_task_by_session_id(self, session_id: str) -> Optional[TaskRecord]:
        """根据 session_id 查询任务记录"""
        async with self._lock:
            for record in self._tasks.values():
                # 检查 result.session_id（任务完成后产生的实际 session_id）
                if record.result and record.result.session_id == session_id:
                    return record
                # 检查 resume_session_id（恢复的会话）
                if record.resume_session_id == session_id:
                    return record
        return None

    async def _persist_task(self, record: TaskRecord):
        """持久化任务记录到 PluginStore"""
        if not self._store:
            return

        try:
            # 构造可序列化的数据
            data = {
                "task_id": record.task_id,
                "prompt": record.prompt,
                "cwd": record.cwd,
                "model": record.model,
                "effort": record.effort,
                "max_turns": record.max_turns,
                "resume_session_id": record.resume_session_id,
                "status": record.status.value,
                "created_at": record.created_at,
                "started_at": record.started_at,
                "finished_at": record.finished_at,
                "error_message": record.error_message,
            }

            # 序列化 result
            if record.result:
                data["result"] = {
                    "session_id": record.result.session_id or "",
                    "is_new_session": record.result.is_new_session,
                    "messages": record.result.messages or [],
                    "final_text": record.result.final_text or "",
                    "total_cost_usd": record.result.total_cost_usd,
                    "duration_ms": record.result.duration_ms,
                    "num_turns": record.result.num_turns,
                }

            await self._store.set(f"task:{record.task_id}", data)
            self.logger.debug(f"Persisted task {record.task_id} to store")
        except Exception as e:
            self.logger.error(f"Failed to persist task {record.task_id}: {e}")

    async def _restore_tasks(self):
        """从 PluginStore 恢复历史任务"""
        try:
            keys = await self._store.keys(prefix="task:")
            for key in keys:
                try:
                    data = await self._store.get(key)
                    if not data:
                        continue

                    # 反序列化 result
                    result = None
                    if "result" in data and data["result"]:
                        result_data = data["result"]
                        result = ExecuteResult(
                            session_id=result_data.get("session_id", ""),
                            is_new_session=result_data.get("is_new_session", True),
                            messages=result_data.get("messages", []),
                            final_text=result_data.get("final_text", ""),
                            total_cost_usd=result_data.get("total_cost_usd", 0.0),
                            duration_ms=result_data.get("duration_ms", 0),
                            num_turns=result_data.get("num_turns", 0),
                        )

                    # 构造 TaskRecord
                    record = TaskRecord(
                        task_id=data["task_id"],
                        prompt=data.get("prompt", ""),
                        cwd=data.get("cwd", ""),
                        model=data.get("model", ""),
                        effort=data.get("effort", ""),
                        max_turns=data.get("max_turns", 0),
                        resume_session_id=data.get("resume_session_id", ""),
                        status=TaskStatus(data.get("status", "done")),
                        created_at=data.get("created_at", 0),
                        started_at=data.get("started_at"),
                        finished_at=data.get("finished_at"),
                        result=result,
                        error_message=data.get("error_message"),
                    )

                    self._tasks[record.task_id] = record
                    self.logger.debug(f"Restored task {record.task_id} from store")
                except Exception as e:
                    self.logger.error(f"Failed to restore task from {key}: {e}")

            self.logger.info(f"Restored {len(self._tasks)} tasks from store")
        except Exception as e:
            self.logger.error(f"Failed to restore tasks from store: {e}")
