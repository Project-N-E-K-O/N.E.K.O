# -*- coding: utf-8 -*-
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

"""
Telemetry Server — data models

Data minimization: token counts only, zero conversation content, zero PII.
Compatible with both Pydantic v1 and v2.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Dict, List, Optional

# Pydantic v1/v2 兼容
PYDANTIC_V2 = int(getattr(__import__('pydantic'), 'VERSION', '1.0').split('.')[0]) >= 2


def model_to_dict(obj):
    """Compat for .model_dump() (v2) / .dict() (v1)."""
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    return obj.dict()


def model_to_json(obj):
    """Compat for .model_dump_json() (v2) / .json() (v1)."""
    if hasattr(obj, 'model_dump_json'):
        return obj.model_dump_json()
    return obj.json()


def model_from_json(cls, data: str):
    """Compat for .model_validate_json() (v2) / .parse_raw() (v1)."""
    if hasattr(cls, 'model_validate_json'):
        return cls.model_validate_json(data)
    return cls.parse_raw(data)


class ModelBucket(BaseModel):
    """Stats bucket aggregated by model/call type."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    call_count: int = 0


class DailyStats(BaseModel):
    """Aggregated stats for one day."""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    call_count: int = 0
    error_count: int = 0
    by_model: Dict[str, ModelBucket] = Field(default_factory=dict)
    by_call_type: Dict[str, ModelBucket] = Field(default_factory=dict)


class RecentRecord(BaseModel):
    """A single LLM call record (redacted)."""
    ts: float
    model: str = "unknown"
    pt: int = 0          # prompt_tokens（含 cached）
    ct: int = 0          # completion_tokens（生成）
    tt: int = 0          # total_tokens
    cch: int = 0         # cached_tokens
    type: str = "unknown"
    ok: bool = True


class HistogramStat(BaseModel):
    """Bucket distribution of a single histogram metric."""
    count: int = 0
    sum: float = 0.0
    buckets: List[int] = Field(default_factory=list)


class InstrumentSnapshot(BaseModel):
    """60s-window snapshot from the client's utils/instrument.

    Keys of counters / histograms are ``name`` or ``name|k1=v1,k2=v2``.
    bounds is the histogram bucket-boundary array, len == length of any
    histogram.buckets - 1 (the extra bucket is the overflow bucket).

    The server currently does not enforce a schema (the events.payload column stores
    the JSON verbatim); dashboards / aggregation are later Batch work. This
    declaration only exists so server code can access fields on
    submission.payload.instruments type-safely instead of having them silently
    dropped as unknown fields.
    """
    window_start: float = 0.0
    window_end: float = 0.0
    # 客户端本地日历天（``YYYY-MM-DD``）。服务端按这个落 stat_date，跟
    # ``daily_stats`` 的 key 同口径；老客户端缺失时服务端按 window_end
    # 时间戳回退（见 storage.py ``_apply_instruments``）。
    stat_date: str = ""
    bounds: List[float] = Field(default_factory=list)
    counters: Dict[str, float] = Field(default_factory=dict)
    histograms: Dict[str, HistogramStat] = Field(default_factory=dict)


class TelemetryEvent(BaseModel):
    """Telemetry payload reported by the client."""
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
    # 设备硬件画像（低基数 enum 复合串，形如 "win|x86_64|16to32|9to16" =
    # os|arch|ram_tier|cpu_tier）。设备属性，server preserve-known UPSERT；
    # 空 string 不覆写。max_length=64 挡异常长串。
    device_hw: str = Field(default="", max_length=64)
    daily_stats: Dict[str, DailyStats] = Field(default_factory=dict)
    recent_records: List[RecentRecord] = Field(default_factory=list)
    # 通用 counter / histogram 累积窗口（utils/instrument）。Optional —
    # 客户端只在窗口非空时发送，老客户端完全不会带这个字段。
    instruments: Optional[InstrumentSnapshot] = None


class TelemetrySubmission(BaseModel):
    """Submission request with an HMAC-signed envelope."""
    timestamp: float
    signature: str = Field(..., min_length=64, max_length=64)
    payload: TelemetryEvent
    batch_id: Optional[str] = Field(default=None, max_length=64)


class SubmitResponse(BaseModel):
    ok: bool = True
    message: str = "accepted"
