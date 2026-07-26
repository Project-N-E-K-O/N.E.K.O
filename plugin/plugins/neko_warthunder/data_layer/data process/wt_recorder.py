"""服务端会话录制器（调试开关，默认关闭）。

用途：把长真实对局的遥测帧与事件流转存到磁盘，供离线分析 / 阈值校准 / 长期参考留存。

存储格式抉择（兼顾“长对局体量大、可能永久留存”与“便于即时读写”）：
  采用 JSONL（每行一条 JSON）+ 大流分段滚动 gzip：
    · 当前段为明文 .jsonl（行缓冲），可被其它进程即时 tail/读取，进程崩溃也只丢最后一行；
    · 写满 segment_bytes 后在【后台线程】压成 .NNN.jsonl.gz（遥测 JSON 高度重复，压缩比约
      10~20×），适合长期/永久留存。
  相比 Parquet/SQLite：纯标准库、顺序时序友好、人与现有离线工具（与 wt_capture 同构）可直接消费，
  且 Parquet 不利于流式追加、SQLite 不利于即时人读，均不如本方案契合“即时读写 + 长期留存”。

为避免“累积型数组每帧重复转存”导致 O(n²) 膨胀，按流分文件：
  frames.NNN.jsonl[.gz]  定频快照（默认 1Hz），已剔除 hud_events/chat/hud_notices/combat.feed/
                          proximity.events（这些累积数组改走下方增量流）
  hudmsg.jsonl           增量新 HUD 事件（击杀/通知的原始来源，可离线再解析）
  chat.jsonl             增量新聊天
  proximity.jsonl        敌军接近边沿事件
  events.jsonl           录制生命周期标记（session_start/stop、battle_reset）
  meta.json              会话元信息（起止/间隔/各流计数/服务版本）

线程安全：服务多线程轮询，所有写入加锁；分段压缩在后台守护线程进行，不阻塞轮询。
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import threading
import time
from typing import Any, Callable

_MIN_SEGMENT_BYTES = 1 << 20  # 1MB


def _gzip_file(path: str) -> None:
    """把明文段压成 .gz 并删除原文件；空文件直接删除。失败静默（录制不应影响主服务）。"""
    try:
        if not os.path.exists(path):
            return
        if os.path.getsize(path) == 0:
            os.remove(path)
            return
        archive = path + ".gz"
        tmp = f"{archive}.tmp.{threading.get_ident()}"
        with open(path, "rb") as f_in, gzip.open(tmp, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out, length=1 << 20)
        os.replace(tmp, archive)
        os.remove(path)
    except OSError:
        try:
            if "tmp" in locals() and os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        pass


class _Stream:
    """单条明文 JSONL 追加流（行缓冲，崩溃只丢最后一行）。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self._fh = open(path, "a", encoding="utf-8", buffering=1)
        self.count = 0

    def write(self, rec: dict[str, Any]) -> None:
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.count += 1

    def close(self, gzip_on_close: bool = True) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
        if gzip_on_close:
            _gzip_file(self.path)


class _RollingStream:
    """大流：当前段明文，写满 segment_bytes 后后台 gzip，滚动到下一段。"""

    def __init__(self, dir_: str, base: str, segment_bytes: int) -> None:
        self.dir = dir_
        self.base = base
        self.segment_bytes = max(_MIN_SEGMENT_BYTES, segment_bytes)
        self.seg_idx = 0
        self.count = 0
        self._bytes = 0
        self._compression_threads: list[threading.Thread] = []
        self._fh = open(self._seg_path(), "a", encoding="utf-8", buffering=1)

    def _seg_path(self) -> str:
        return os.path.join(self.dir, f"{self.base}.{self.seg_idx:03d}.jsonl")

    def write(self, rec: dict[str, Any]) -> None:
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        self._fh.write(line)
        self.count += 1
        self._bytes += len(line.encode("utf-8"))
        if self._bytes >= self.segment_bytes:
            self._rotate()

    def _rotate(self) -> None:
        path = self._seg_path()
        try:
            self._fh.close()
        except Exception:
            pass
        self._compression_threads = [
            thread for thread in self._compression_threads if thread.is_alive()
        ]
        # 后台压缩已完成的段，不阻塞调用线程（轮询线程）
        thread = threading.Thread(target=_gzip_file, args=(path,), daemon=True)
        thread.start()
        self._compression_threads.append(thread)
        self.seg_idx += 1
        self._bytes = 0
        self._fh = open(self._seg_path(), "a", encoding="utf-8", buffering=1)

    def close(self) -> None:
        path = self._seg_path()
        try:
            self._fh.close()
        except Exception:
            pass
        for thread in self._compression_threads:
            thread.join()
        self._compression_threads.clear()
        _gzip_file(path)  # 收尾段同步压缩（停止时一次性，可接受）


class SessionRecorder:
    """会话级录制器：定频写快照 + 增量写事件流。默认不录，需显式 start()。"""

    _EVENT_STREAMS = ("hudmsg", "chat", "proximity", "events")

    def __init__(
        self,
        root_dir: str = "records",
        interval: float = 1.0,
        segment_bytes: int = 32 * 1024 * 1024,
        server_version: str = "",
    ) -> None:
        self.root_dir = root_dir
        self.interval = max(0.05, interval)
        self.segment_bytes = max(_MIN_SEGMENT_BYTES, segment_bytes)
        self.server_version = server_version

        self._lock = threading.Lock()
        self._recording = False
        self._session_dir: str | None = None
        self._frames: _RollingStream | None = None
        self._streams: dict[str, _Stream] = {}
        self._started_at = 0.0
        self._last_frame_ts = 0.0

    @property
    def recording(self) -> bool:
        return self._recording

    # -- 生命周期 ----------------------------------------------------------

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._recording:
                return self._status_locked()
            os.makedirs(self.root_dir, exist_ok=True)
            session = time.strftime("rec_%Y%m%d_%H%M%S")
            suffix = 0
            while True:
                name = session if suffix == 0 else f"{session}_{suffix}"
                d = os.path.join(self.root_dir, name)
                try:
                    os.makedirs(d, exist_ok=False)
                    break
                except FileExistsError:
                    suffix += 1
            self._session_dir = d
            self._frames = _RollingStream(d, "frames", self.segment_bytes)
            self._streams = {
                name: _Stream(os.path.join(d, f"{name}.jsonl"))
                for name in self._EVENT_STREAMS
            }
            self._started_at = time.time()
            self._last_frame_ts = 0.0
            self._recording = True
            self._streams["events"].write({
                "ts": round(self._started_at, 3),
                "_event": "session_start",
                "server_version": self.server_version,
                "interval_sec": self.interval,
            })
            self._write_meta_locked(active=True)
            return self._status_locked()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._recording:
                return self._status_locked()
            self._streams["events"].write({"ts": round(time.time(), 3), "_event": "session_stop"})
            self._write_meta_locked(active=False)
            status = self._status_locked()  # 关闭前取计数（含 session_dir）
            status["recording"] = False     # 本次调用结束后即为停止态
            for s in self._streams.values():
                s.close(gzip_on_close=True)
            if self._frames is not None:
                self._frames.close()
            self._recording = False
            self._frames = None
            self._streams = {}
            self._session_dir = None
            return status

    # -- 写入 --------------------------------------------------------------

    def offer_frame(self, provider: Callable[[], dict[str, Any]]) -> None:
        """高频轮询每拍调用；仅在达到记录间隔时才真正构建(provider)并写入帧。

        provider 在锁外调用（其内部会拿服务自身的锁），避免与录制器锁长时间嵌套。
        """
        if not self._recording:
            return
        now = time.time()
        with self._lock:
            if not self._recording or self._frames is None:
                return
            if now - self._last_frame_ts < self.interval:
                return
            self._last_frame_ts = now
            frames = self._frames
        try:
            rec = provider()
        except Exception:
            return
        if not isinstance(rec, dict):
            return
        with self._lock:
            if self._recording and self._frames is frames:
                frames.write(rec)

    def write_events(self, stream: str, items: list[Any]) -> None:
        """把一批新事件追加到指定增量流。items 非 dict 时包成 {"value": ...}。"""
        if not self._recording or not items:
            return
        with self._lock:
            s = self._streams.get(stream)
            if s is None:
                return
            ts = round(time.time(), 3)
            for it in items:
                rec = dict(it) if isinstance(it, dict) else {"value": it}
                if "ts" not in rec:
                    rec = {"ts": ts, **rec}
                s.write(rec)

    def mark(self, event: dict[str, Any]) -> None:
        """写一条生命周期标记到 events 流（如 battle_reset）。"""
        if not self._recording:
            return
        with self._lock:
            s = self._streams.get("events")
            if s is not None:
                s.write({"ts": round(time.time(), 3), **event})

    # -- 状态 / 元信息 -----------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> dict[str, Any]:
        return {
            "recording": self._recording,
            "session_dir": os.path.abspath(self._session_dir) if self._session_dir else None,
            "interval_sec": self.interval,
            "segment_bytes": self.segment_bytes,
            "started_at": self._started_at if self._recording else None,
            "elapsed_sec": round(time.time() - self._started_at, 1) if self._recording else None,
            "counts": {
                "frames": self._frames.count if self._frames else 0,
                "hudmsg": self._streams["hudmsg"].count if self._streams else 0,
                "chat": self._streams["chat"].count if self._streams else 0,
                "proximity": self._streams["proximity"].count if self._streams else 0,
            },
        }

    def _write_meta_locked(self, active: bool) -> None:
        if not self._session_dir:
            return
        meta = {
            "active": active,
            "server_version": self.server_version,
            "started_at": self._started_at,
            "ended_at": None if active else time.time(),
            "interval_sec": self.interval,
            "segment_bytes": self.segment_bytes,
            "counts": {
                "frames": self._frames.count if self._frames else 0,
                "hudmsg": self._streams["hudmsg"].count if self._streams else 0,
                "chat": self._streams["chat"].count if self._streams else 0,
                "proximity": self._streams["proximity"].count if self._streams else 0,
            },
            "updated_at": time.time(),
        }
        try:
            with open(os.path.join(self._session_dir, "meta.json"), "w", encoding="utf-8") as fh:
                json.dump(meta, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass
