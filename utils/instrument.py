# -*- coding: utf-8 -*-
"""
通用埋点 SDK（counter / histogram / event）

业务侧只需 import 三个函数 —— ``counter`` / ``histogram`` / ``event`` —— 不
关心 buffering / snapshot / 上报通道。所有数据由 TokenTracker 的 60s
periodic save 顺手收走，跟 daily_stats 同一条 HTTP 通道、同一个 device_id、
同一份 HMAC 签名走出去。

数据通道选择（什么用什么）：

================= ================================ ============================
通道              何时用                            后端展现
================= ================================ ============================
counter           累加型计数：消息条数 / 点击次数  汇总成"周期内总数 + 维度切片"
histogram         分布型测量：延迟 / FPS / size    桶分布 + count + sum
event             稀疏带 context：crash / step     原样存事件流（events.jsonl）
================= ================================ ============================

何时**不要**用：
- 不要每次 mouse move / scroll 都 counter()。挑有意义的事件（消息发出、按
  钮按下、功能首次使用），不然就是 noise。
- 不要把消息内容 / persona text / master_name 放进 fields。维度只能是 enum
  类标签（surface、feature_name、error_class 等）。

开销：
- counter / histogram：进程内 lock + dict op，~300ns / 次
- event：转交 event_logger，纳秒级 deque.append
- snapshot：每 60s 一次，clear-on-read，无累积
"""
from __future__ import annotations

import bisect
import threading
import time
from typing import Optional

from utils.event_logger import emit as _event_emit
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)


# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------

# Histogram 桶边界（毫秒/数值通用）。覆盖 1ms~10s 主要分布范围；超出最右边
# 界的样本进溢出桶。固定桶让服务端 schema 稳定，跨版本对比直接做。
#
# 选这组边界的理由：
# - 1/2/5/10/... 的"1-2-5 序列"是 logarithmic 但保留整数可读
# - 覆盖 TTFT (~100-2000ms)、FPS 倒数 (~16ms)、startup time (~1-30s) 主要范围
# - 9 个边界 = 10 个桶，序列化后 ~40B/histogram，便宜
_HIST_BOUNDS: tuple = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000)
_HIST_NUM_BUCKETS = len(_HIST_BOUNDS) + 1  # 最右边界右侧多一个溢出桶

# Counter / histogram 内存上限。理论上 key 是 (name, dims tuple)，不该爆，
# 但万一业务把高基数维度（如 user_id / 消息内容）塞进 dims，要兜底防内存
# 泄漏。超过此值丢弃新 key（保留已有的累积值），并打一次 warning。
_MAX_COUNTER_KEYS = 5000
_MAX_HISTOGRAM_KEYS = 1000


# ---------------------------------------------------------------------------
# Instrument 单例
# ---------------------------------------------------------------------------


class Instrument:
    """进程内单例的 counter + histogram 累积器。

    snapshot() 由 TokenTracker.save 顺手在 60s 周期里调用。业务代码用模块
    级 ``counter`` / ``histogram`` / ``event`` 即可，不直接动这个类。
    """

    _instance: Optional["Instrument"] = None
    _init_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "Instrument":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key 是 string："name|k1=v1,k2=v2"（dims 已经按 key 字典序拼好）。
        # value 是数字。flat dict 比嵌套 dict-of-dict 序列化更友好，服务端
        # 也更容易索引。
        self._counters: dict = {}
        # 同样的 key 结构，value 是 [count, sum, [bucket_counts...]]
        self._histograms: dict = {}
        # snapshot 窗口起点（每次 snapshot 重置）
        self._window_start: float = time.time()
        # 高基数告警节流
        self._cap_warned_counter: bool = False
        self._cap_warned_histogram: bool = False

    # ---- 公开 API ----

    def counter(self, name: str, value: int = 1, **dims) -> None:
        """累加一个计数器。

        Args:
            name: 指标名（snake_case，e.g. "user_message_sent"）
            value: 增量，默认 1。允许负数（减），但通常用不上。
            **dims: 维度标签。值必须是 string / int / bool 等可哈希简单类型。
                不要传消息内容、user_id 之类的高基数值。

        Example:
            counter("user_message_sent", 1, surface="pet_widget")
            counter("feature_invoked", 1, feature="galgame", first_use=True)
        """
        if not name or not isinstance(value, (int, float)):
            return
        key = _make_key(name, dims)
        with self._lock:
            if key in self._counters:
                self._counters[key] += value
            elif len(self._counters) < _MAX_COUNTER_KEYS:
                self._counters[key] = value
            else:
                # 容量保护：满了就静默丢，避免业务方误用高基数维度炸内存
                if not self._cap_warned_counter:
                    logger.warning(
                        f"instrument: counter map full ({_MAX_COUNTER_KEYS} keys), "
                        f"dropping new keys. Check if any dim has high cardinality."
                    )
                    self._cap_warned_counter = True

    def histogram(self, name: str, value: float, **dims) -> None:
        """记录一个分布型测量。

        Args:
            name: 指标名（snake_case，e.g. "ttft_ms"、"live2d_fps"）
            value: 测量值（数字）。会被分桶到 _HIST_BOUNDS 对应的 bucket。
            **dims: 同 counter，维度标签必须是低基数。

        Example:
            histogram("ttft_ms", 234)
            histogram("live2d_fps", 58.5, surface="pet_widget")
        """
        if not name or not isinstance(value, (int, float)):
            return
        # bisect_left：value <= bound 落到该桶；超过最右边界进溢出桶
        bucket_idx = bisect.bisect_left(_HIST_BOUNDS, value)
        key = _make_key(name, dims)
        with self._lock:
            entry = self._histograms.get(key)
            if entry is None:
                if len(self._histograms) >= _MAX_HISTOGRAM_KEYS:
                    if not self._cap_warned_histogram:
                        logger.warning(
                            f"instrument: histogram map full ({_MAX_HISTOGRAM_KEYS} keys), "
                            f"dropping new keys. Check if any dim has high cardinality."
                        )
                        self._cap_warned_histogram = True
                    return
                entry = [0, 0.0, [0] * _HIST_NUM_BUCKETS]
                self._histograms[key] = entry
            entry[0] += 1
            entry[1] += value
            entry[2][bucket_idx] += 1

    def event(self, name: str, **fields) -> None:
        """记录一个稀疏带 context 事件（直接转 event_logger）。

        与 counter 的区别：event 是离散事件流（"在 ts=X 发生了 name"），
        会被一条条原样保留；counter 是聚合数字（"窗口内 name 发生了 N 次"）。

        Example:
            event("crash", traceback_hash="a3f8", module="agent_router")
            event("onboarding_step", step="persona_selected", duration_ms=1500)
        """
        _event_emit(name, **fields)

    # ---- snapshot ----

    def has_data(self) -> bool:
        """是否有累积数据等着 snapshot。给上报通道在决定是否发请求时 peek 用。

        TokenTracker 在 daily_stats 为空时本来会跳过上报，但 instrument 自己
        可能有 counter/histogram 等着发；这个方法让上报通道在不消费数据的
        前提下判断"是否值得发一次请求"。
        """
        with self._lock:
            return bool(self._counters or self._histograms)

    def snapshot(self) -> dict:
        """取出当前累积值 + 清零 + 返回。由 TokenTracker 上报通道调用。

        Returns:
            dict with keys "window_start", "window_end", "counters",
            "histograms"，或者空 dict（无任何累积）。

        失败处理：返回的 snapshot 一旦丢给 token_tracker，instrument 内部
        立刻清零。如果上报失败，60s 窗口的 counter / histogram 数据丢失 —
        这是设计取舍：sparse_event 走 event_logger 有本地 jsonl 兜底，
        counter / histogram 是聚合数据，丢一个窗口对趋势分析影响小，不值得
        为它再维护一份 unsent 队列。daily_stats（LLM tokens）才需要不丢。
        """
        with self._lock:
            if not self._counters and not self._histograms:
                # 即使空也更新 window_start，避免下次 snapshot 把空窗口
                # 一直挂着 —— 否则前 30 min 没埋点活动、第 31 min 有一条，
                # 上报的 window 会显示 31min 而不是 1min。
                self._window_start = time.time()
                return {}
            counters = self._counters
            histograms = self._histograms
            window_start = self._window_start
            self._counters = {}
            self._histograms = {}
            self._window_start = time.time()
            # warning 标志保留 —— 反复打日志比反复 warn 更烦
        # 序列化在锁外，让 emit() 不被阻塞
        hist_out = {}
        for k, v in histograms.items():
            hist_out[k] = {"count": v[0], "sum": v[1], "buckets": list(v[2])}
        return {
            "window_start": window_start,
            "window_end": self._window_start,
            "bounds": list(_HIST_BOUNDS),
            "counters": counters,
            "histograms": hist_out,
        }


# ---------------------------------------------------------------------------
# 辅助：key 序列化
# ---------------------------------------------------------------------------


def _make_key(name: str, dims: dict) -> str:
    """把 (name, dims) 拼成一个稳定的 flat key。

    格式：``name`` 或 ``name|k1=v1,k2=v2``（dims 按 key 字典序拼）。
    没有 dims 时省略 ``|``，保持简单 case 的 key 短。

    值会用 ``str()`` 转换 —— 调用方有义务只传可序列化的低基数维度。
    """
    if not dims:
        return name
    parts = [f"{k}={dims[k]}" for k in sorted(dims.keys())]
    return f"{name}|{','.join(parts)}"


# ---------------------------------------------------------------------------
# 模块级便捷函数（业务侧首选入口）
# ---------------------------------------------------------------------------


def counter(name: str, value: int = 1, **dims) -> None:
    Instrument.get_instance().counter(name, value, **dims)


def histogram(name: str, value: float, **dims) -> None:
    Instrument.get_instance().histogram(name, value, **dims)


def event(name: str, **fields) -> None:
    Instrument.get_instance().event(name, **fields)


def snapshot() -> dict:
    """供 TokenTracker 调用，业务代码一般不用。"""
    return Instrument.get_instance().snapshot()


def has_data() -> bool:
    """供 TokenTracker peek 用，业务代码一般不用。"""
    return Instrument.get_instance().has_data()
