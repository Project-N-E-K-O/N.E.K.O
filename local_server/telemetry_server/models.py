# -*- coding: utf-8 -*-
"""
Telemetry Server — 数据模型

数据最小化：仅 token 计数，零对话内容、零 PII。
兼容 Pydantic v1 和 v2。
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Dict, List, Optional

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


class TelemetryEvent(BaseModel):
    """客户端上报的遥测负载。"""
    device_id: str = Field(..., min_length=16, max_length=128)
    app_version: str = Field(default="unknown", max_length=64)
    daily_stats: Dict[str, DailyStats] = Field(default_factory=dict)
    recent_records: List[RecentRecord] = Field(default_factory=list)


class TelemetrySubmission(BaseModel):
    """带 HMAC 签名信封的上报请求。"""
    timestamp: float
    signature: str = Field(..., min_length=64, max_length=64)
    payload: TelemetryEvent


class SubmitResponse(BaseModel):
    ok: bool = True
    message: str = "accepted"
