# -*- coding: utf-8 -*-
"""
稀疏事件日志（sparse-event JSONL writer）

用途：记录 session_start / crash / onboarding_step / app_exit 等数量稀少但
单条信息量高的事件。与 token_tracker 的 daily aggregates（counter / histogram
类）互补 —— 后者是数字汇总，本模块是带 context 的离散事件。

存储设计：
- events-YYYYMMDD.jsonl，每行一个 JSON event，按天分片
- append-only：写入永不修改已有行，只追加
- Retention：删除 > 7 天的分片
- 单文件硬上限 500 KB（超了拒收新事件，防单天爆量打挂磁盘）
- 目录总硬上限 20 MB（超了强删最老分片，防 disk full）

开销保证：
- emit() 进 in-memory deque，纳秒级，不阻塞主路径
- flush() 由 TokenTracker 60s 定时调用一次，单次写盘 < 50 KB
- cleanup() 顺便在 flush 时跑，成本是 listdir + 几个 stat

线程安全：进程内用 _lock 串行；多进程不严格同步，依赖每行 jsonl < 4KB 时
单 write 系统调用的原子性（POSIX O_APPEND / Windows append mode 都保证）。

不持久化哪些字段：消息内容、用户名、master_name、prompt 文本。本模块的事件
应该是"什么发生了"+"哪个 surface"+ 几个 enum 标签，不要塞业务数据。
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)


# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------

_DIR_NAME = "telemetry_events"
_FILE_PREFIX = "events-"
_FILE_SUFFIX = ".jsonl"

# Retention：超过 N 天的分片删除（cleanup 触发）
_RETENTION_DAYS = 7

# 单文件硬上限：超了不再写新事件，防单天异常爆量
_FILE_SIZE_CAP = 500 * 1024  # 500 KB

# 目录总硬上限：超了强删最老分片，防 disk full
_DIR_SIZE_CAP = 20 * 1024 * 1024  # 20 MB

# 内存 buffer 上限：flush 任务卡死时也不让无限增长（保留最新的）
_BUFFER_MAX = 1000

# Cleanup 节流：连续多次 flush 没必要每次都 listdir/stat，攒一段时间跑一次
_CLEANUP_MIN_INTERVAL = 5 * 60  # 5 min


# ---------------------------------------------------------------------------
# EventLogger 单例
# ---------------------------------------------------------------------------


class EventLogger:
    """进程内单例的稀疏事件 JSONL writer。

    典型用法：
        EventLogger.get_instance().emit("session_start", surface="pet_widget")

    flush() 由 TokenTracker 的 periodic_save_loop 顺手调用，调用方不用关心。
    """

    _instance: Optional["EventLogger"] = None
    _init_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "EventLogger":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffer: deque = deque(maxlen=_BUFFER_MAX)
        self._last_cleanup_ts: float = 0.0
        # 写盘失败计数：连续失败 N 次就静默（写日志只在 transition 上记一次，
        # 避免磁盘满 / 权限错误期间日志被刷爆）
        self._fail_count: int = 0
        # 单天分片 sealed 标记：超 _FILE_SIZE_CAP 后本天不再写，下天滚动
        self._sealed_days: set = set()

    # ---- 路径 ----

    @property
    def _dir(self) -> Path:
        return get_config_manager().config_dir / _DIR_NAME

    def _file_for(self, day: str) -> Path:
        return self._dir / f"{_FILE_PREFIX}{day}{_FILE_SUFFIX}"

    # ---- 公开 API ----

    def emit(self, name: str, **fields) -> None:
        """记录一个稀疏事件。线程安全，非阻塞。

        Args:
            name: 事件名（snake_case，建议 < 32 chars）
            **fields: 业务字段。不要塞消息内容 / PII / master_name。

        实现：append 到内存 deque，flush() 时一次性写盘。emit() 本身不做
        I/O，所以即使每秒调用几百次也不会有可感知开销。
        """
        if not name:
            return
        rec = {"ts": time.time(), "name": name}
        # fields 直接平铺进 rec；冲突时 ts/name 优先（防 caller 覆盖语义字段）
        if fields:
            for k, v in fields.items():
                if k in ("ts", "name"):
                    continue
                rec[k] = v
        with self._lock:
            self._buffer.append(rec)

    def flush(self) -> int:
        """落盘 buffer + 顺便清理过期分片。线程安全。

        Returns:
            实际写入磁盘的事件数。0 表示 buffer 为空或写盘失败。

        调用时机：由 TokenTracker._periodic_save_loop 每 60s 调用一次。
        atexit 时也会被 token_tracker 的 _atexit_save 间接触发（如果接上的话）。
        """
        # 锁内只做：取 buffer + 决定是否要 cleanup。所有 I/O 放到锁外，
        # emit() 不会被磁盘 I/O 阻塞。
        with self._lock:
            buf = list(self._buffer)
            self._buffer.clear()
            now_mono = time.monotonic()
            should_cleanup = (now_mono - self._last_cleanup_ts) >= _CLEANUP_MIN_INTERVAL
            if should_cleanup:
                self._last_cleanup_ts = now_mono

        written = 0
        if buf:
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                self._log_fail(f"event_logger: mkdir failed: {e}")
                # 写盘失败：丢回 buffer，下次重试。deque maxlen 保证长期故障
                # 下 buffer 自动退化（最老的事件被挤掉），不会无限增长。
                self._push_back(buf)
                # 仍走 cleanup —— 也许是 disk full，清掉老分片可能腾出空间。
            else:
                # 按 day key 分组（跨午夜的 flush 可能含多天事件）
                by_day: dict = {}
                for rec in buf:
                    day = self._day_of(rec["ts"])
                    by_day.setdefault(day, []).append(rec)

                # 写盘失败的 day 数据要回到 buffer 重试；写成功的 day 已经
                # 持久化、不能 push_back（否则下次 flush 重复落地）。区分
                # 两种 _append_day 返回路径：raise = IO 失败，return 0 =
                # sealed/empty 正常丢弃。
                unwritten: list = []
                for day, recs in by_day.items():
                    if day in self._sealed_days:
                        continue  # 本天已封板（爆量保护），丢弃新事件
                    try:
                        written += self._append_day(day, recs)
                    except OSError:
                        # _append_day 内已经走过 _log_fail 节流日志，这里
                        # 不再重复 log；只把数据丢回 buffer 等下次重试。
                        unwritten.extend(recs)
                if unwritten:
                    self._push_back(unwritten)

        if should_cleanup:
            self._cleanup()

        return written

    # ---- 内部：写盘 ----

    @staticmethod
    def _day_of(ts: float) -> str:
        """时间戳→ YYYYMMDD（本地时区，与 token_usage.json 的 date.today 对齐）。"""
        return datetime.fromtimestamp(ts).strftime("%Y%m%d")

    def _append_day(self, day: str, recs: list) -> int:
        """把 recs 追加到 day 对应分片。返回实际写入条数。"""
        path = self._file_for(day)

        # 单文件 cap：超了 seal 当天，剩余事件丢弃
        try:
            current_size = path.stat().st_size if path.exists() else 0
        except OSError:
            current_size = 0
        if current_size >= _FILE_SIZE_CAP:
            self._sealed_days.add(day)
            logger.info(
                f"event_logger: sealed {path.name} (size={current_size} >= cap), dropping {len(recs)} events"
            )
            return 0

        # 拼接 jsonl 字节
        # ensure_ascii=False：日志里允许中文/日文事件名和字段值，节省字节
        lines = []
        remaining = _FILE_SIZE_CAP - current_size
        for rec in recs:
            try:
                line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
                b = line.encode("utf-8")
            except (TypeError, ValueError) as e:
                # 不可序列化字段 —— 静默丢弃单条，不连累其它事件
                logger.debug(f"event_logger: drop unserializable event {rec.get('name')}: {e}")
                continue
            if remaining < len(b):
                self._sealed_days.add(day)
                logger.info(f"event_logger: sealing {path.name} mid-batch (would exceed cap)")
                break
            lines.append(b)
            remaining -= len(b)

        if not lines:
            return 0

        payload = b"".join(lines)
        # append-only：依赖 OS 的 O_APPEND 原子性，每行 < 4KB 时单次 write 是原子的。
        # IO 失败 raise OSError 让 caller (flush) 把 recs push_back 重试；用
        # raise 而不是 return 0 是为了跟 sealed/empty 路径区分 —— 后者是"按
        # 设计丢弃"，不该 push_back 占 buffer。
        try:
            with open(path, "ab") as f:
                f.write(payload)
            self._fail_count = 0
            return len(lines)
        except OSError as e:
            self._log_fail(f"event_logger: write failed for {path.name}: {e}")
            raise

    def _push_back(self, recs: list) -> None:
        """写盘失败时把事件丢回 buffer。尊重 maxlen，老数据自动出队。"""
        with self._lock:
            # 老的在前，新的在后：deque.extendleft 是逆序的，所以用普通 extend
            # 但要先把当前 buffer 内容（更新）挪到后面
            current = list(self._buffer)
            self._buffer.clear()
            for r in recs:
                self._buffer.append(r)
            for r in current:
                self._buffer.append(r)

    def _log_fail(self, msg: str) -> None:
        """连续失败时只在 transition 上记日志，防日志被刷爆。"""
        self._fail_count += 1
        if self._fail_count == 1:
            logger.warning(msg)
        elif self._fail_count % 100 == 0:
            logger.warning(f"{msg} (×{self._fail_count} since last log)")

    # ---- 内部：清理 ----

    def _cleanup(self) -> None:
        """删 > 7 天的分片；目录总大小超 20MB 时强删最老。

        触发频率：5min 节流（_CLEANUP_MIN_INTERVAL）。即使每 60s flush 也只
        会真正 listdir 一次/5min，成本可忽略。
        """
        try:
            if not self._dir.exists():
                return
            # 列出所有分片，按文件名（天）排序，老的在前
            cutoff_day = (date.today() - timedelta(days=_RETENTION_DAYS)).strftime("%Y%m%d")
            entries: list = []
            for entry in self._dir.iterdir():
                name = entry.name
                if not (name.startswith(_FILE_PREFIX) and name.endswith(_FILE_SUFFIX)):
                    continue
                day_key = name[len(_FILE_PREFIX): -len(_FILE_SUFFIX)]
                # 校验是 8 位数字，防误删
                if len(day_key) != 8 or not day_key.isdigit():
                    continue
                try:
                    size = entry.stat().st_size
                except OSError:
                    continue
                entries.append((day_key, entry, size))

            entries.sort(key=lambda x: x[0])  # 老的在前

            # 第一轮：按 retention 删
            kept: list = []
            for day_key, path, size in entries:
                if day_key < cutoff_day:
                    try:
                        path.unlink()
                        logger.debug(f"event_logger: pruned {path.name} (age > {_RETENTION_DAYS}d)")
                    except OSError as e:
                        logger.debug(f"event_logger: failed to prune {path.name}: {e}")
                        kept.append((day_key, path, size))
                else:
                    kept.append((day_key, path, size))

            # 第二轮：目录总 cap，超了从最老开始删
            total = sum(s for _, _, s in kept)
            while total > _DIR_SIZE_CAP and kept:
                day_key, path, size = kept.pop(0)
                try:
                    path.unlink()
                    total -= size
                    logger.info(
                        f"event_logger: force-pruned {path.name} (dir size > {_DIR_SIZE_CAP}B)"
                    )
                except OSError as e:
                    logger.debug(f"event_logger: failed to force-prune {path.name}: {e}")
                    break  # 删不掉也别死循环

            # 清理 sealed_days 里已经过期的天，让重新进入下一周期时可写
            today_key = date.today().strftime("%Y%m%d")
            self._sealed_days = {d for d in self._sealed_days if d == today_key}

        except Exception as e:
            logger.debug(f"event_logger: cleanup error (non-critical): {e}")


# ---------------------------------------------------------------------------
# 便捷模块级函数
# ---------------------------------------------------------------------------


def emit(name: str, **fields) -> None:
    """记录一个稀疏事件。等价于 EventLogger.get_instance().emit(name, **fields)。"""
    EventLogger.get_instance().emit(name, **fields)


def flush() -> int:
    """落盘 buffer。由 TokenTracker periodic save 调用，业务代码一般不用。"""
    return EventLogger.get_instance().flush()
