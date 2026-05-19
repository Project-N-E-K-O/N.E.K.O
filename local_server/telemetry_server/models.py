# -*- coding: utf-8 -*-
"""
Telemetry Server — 数据模型

数据最小化：仅 token 计数，零对话内容、零 PII。
兼容 Pydantic v1 和 v2。
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

# Pydantic v1/v2 兼容
PYDANTIC_V2 = int(getattr(__import__('pydantic'), 'VERSION', '1.0').split('.')[0]) >= 2


def model_to_dict(obj):
    """兼容 .model_dump() (v2) / .dict() (v1)。"""
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    return obj.dict()


def model_to_json(obj):
    """兼容 .model_dump_json() (v2) / .json() (v1)。"""
    if hasattr(obj, 'model_dump_json'):
        return obj.model_dump_json()
    return obj.json()


def model_from_json(cls, data: str):
    """兼容 .model_validate_json() (v2) / .parse_raw() (v1)。"""
    if hasattr(cls, 'model_validate_json'):
        return cls.model_validate_json(data)
    return cls.parse_raw(data)


class ModelBucket(BaseModel):
    """按模型/调用类型聚合的统计桶。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    call_count: int = 0


class DailyStats(BaseModel):
    """一天的聚合统计。"""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    call_count: int = 0
    error_count: int = 0
    by_model: Dict[str, ModelBucket] = Field(default_factory=dict)
    by_call_type: Dict[str, ModelBucket] = Field(default_factory=dict)


class RecentRecord(BaseModel):
    """单次 LLM 调用记录（脱敏）。"""
    ts: float
    model: str = "unknown"
    pt: int = 0          # prompt_tokens（含 cached）
    ct: int = 0          # completion_tokens（生成）
    tt: int = 0          # total_tokens
    cch: int = 0         # cached_tokens
    type: str = "unknown"
    ok: bool = True


class HistogramStat(BaseModel):
    """单个 histogram 指标的桶分布。"""
    count: int = 0
    sum: float = 0.0
    buckets: List[int] = Field(default_factory=list)


class InstrumentSnapshot(BaseModel):
    """客户端 utils/instrument 的 60s 窗口 snapshot。

    counters / histograms 的 key 是 ``name`` 或 ``name|k1=v1,k2=v2``。
    bounds 是 histogram 桶边界数组，len == 任一 histogram.buckets 长度 - 1
    （多出来的那个桶是溢出桶）。

    服务端当前不强 schema 化（events.payload 列原样保存 JSON），dashboard /
    aggregation 是后续 Batch 的工作。这里声明只是为了让 server 代码能从
    submission.payload.instruments 类型安全地访问字段，不被当成 unknown
    field 静默忽略。
    """
    window_start: float = 0.0
    window_end: float = 0.0
    bounds: List[float] = Field(default_factory=list)
    counters: Dict[str, float] = Field(default_factory=dict)
    histograms: Dict[str, HistogramStat] = Field(default_factory=dict)


class TelemetryEvent(BaseModel):
    """客户端上报的遥测负载。"""
    device_id: str = Field(..., min_length=16, max_length=128)
    app_version: str = Field(default="unknown", max_length=64)
    # 三个用户维度字段。`branch` 在客户端首次启动时随机抽签后落盘，后续保持稳
    # 定，用于 A/B test 分流；`locale` / `timezone` 每次上报取实时值，同设备
    # 不同 locale/tz 仍视为同一 device，server 端覆写最新值即可。
    branch: str = Field(default="unknown", max_length=64)
    locale: str = Field(default="unknown", max_length=32)
    timezone: str = Field(default="unknown", max_length=64)
    # 发行渠道：steam（Steam 启动）/ release（编译版直启）/ source（源码运行）/ unknown
    distribution: str = Field(default="unknown", max_length=32)
    # Steam64 user id（string，避免 u64 在 JS 等消费方精度丢失）。仅在
    # Steamworks SDK 起来 + 拿到 Users.GetSteamID 时填值，否则为空 string。
    # max_length=24 给 u64 十进制（20 位）留余量，防止异常长串攻击。
    steam_user_id: str = Field(default="", max_length=24)
    daily_stats: Dict[str, DailyStats] = Field(default_factory=dict)
    recent_records: List[RecentRecord] = Field(default_factory=list)
    # 通用 counter / histogram 累积窗口（utils/instrument）。Optional —
    # 客户端只在窗口非空时发送，老客户端完全不会带这个字段。
    instruments: Optional[InstrumentSnapshot] = None


class TelemetrySubmission(BaseModel):
    """带 HMAC 签名信封的上报请求。"""
    timestamp: float
    signature: str = Field(..., min_length=64, max_length=64)
    payload: TelemetryEvent
    batch_id: Optional[str] = Field(default=None, max_length=64)


class SubmitResponse(BaseModel):
    ok: bool = True
    message: str = "accepted"
