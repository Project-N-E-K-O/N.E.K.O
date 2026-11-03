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

# Pydantic v1/v2 compatibility.
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
    pt: int = 0          # prompt_tokens, including cached tokens.
    ct: int = 0          # completion_tokens, generated tokens.
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
    # Client-local calendar day (``YYYY-MM-DD``). The server uses it as stat_date, matching
    # ``daily_stats`` keys; when absent in older clients, fall back to window_end's
    # timestamp (see storage.py ``_apply_instruments``).
    stat_date: str = ""
    bounds: List[float] = Field(default_factory=list)
    counters: Dict[str, float] = Field(default_factory=dict)
    histograms: Dict[str, HistogramStat] = Field(default_factory=dict)


class TelemetryEvent(BaseModel):
    """Telemetry payload reported by the client."""
    device_id: str = Field(..., min_length=16, max_length=128)
    app_version: str = Field(default="unknown", max_length=64)
    # Three user-dimension fields. `branch` is randomly assigned and persisted on first client launch, then remains
    # stable for A/B routing. `locale` / `timezone` use current values on every report; different locales/time zones
    # still identify the same device, so the server simply overwrites them with the latest values.
    branch: str = Field(default="unknown", max_length=64)
    locale: str = Field(default="unknown", max_length=32)
    timezone: str = Field(default="unknown", max_length=64)
    # Distribution: steam (Steam launch) / release (direct compiled launch) / source (source execution) / unknown.
    distribution: str = Field(default="unknown", max_length=32)
    # Steam64 user ID as string to avoid u64 precision loss in JavaScript and other consumers. Set only when
    # Steamworks SDK initializes and Users.GetSteamID succeeds; otherwise leave an empty string.
    # max_length=24 leaves headroom for 20-digit u64 decimals while blocking abnormally long strings.
    steam_user_id: str = Field(default="", max_length=24)
    # Device hardware profile: low-cardinality composite enums, e.g. "win|x86_64|16to32|9to16" =
    # os|arch|ram_tier|cpu_tier. Device property; server preserve-known UPSERT
    # does not overwrite with empty strings. max_length=64 blocks abnormally long input.
    device_hw: str = Field(default="", max_length=64)
    daily_stats: Dict[str, DailyStats] = Field(default_factory=dict)
    recent_records: List[RecentRecord] = Field(default_factory=list)
    # General counter/histogram accumulation window (utils/instrument). Optional:
    # clients send only nonempty windows; older clients omit this field entirely.
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
