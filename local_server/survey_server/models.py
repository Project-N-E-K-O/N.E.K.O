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
Survey Server — data models

Data minimization: one anonymous device id + the per-question answers, zero
conversation content. Compatible with both Pydantic v1 and v2.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any, Dict

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


class SurveyPayload(BaseModel):
    """The signed body of one survey submission.

    ``answers`` maps ``question_id -> value`` where value is a string (single
    choice / free text) or a list of strings (multiple choice). On a ``skip``
    action it is empty. We keep it loosely typed (``Any``) so adding question
    types later never breaks ingest — the server stores answers verbatim as JSON
    and the dashboard interprets them per the survey definition.
    """
    device_id: str
    # 迁移期同时带旧算法 device id（与 telemetry 对齐），便于跨表 JOIN 同一个人。
    device_id_legacy: str = ""
    app_version: str = "unknown"
    # 问卷自身的版本号（= 触发它的 app 版本，来自 config/surveys/<ver>.json）。
    survey_version: str = "unknown"
    locale: str = "unknown"
    branch: str = "unknown"
    distribution: str = "unknown"
    # Steam64（十进制字符串，缓存优先）。survey 是 steam-only，但允许为空：
    # Steam 版从未观测到登录 id 的尾部情况（与 telemetry 的 "steam + 空 id" 一致）。
    steam_user_id: str = ""
    # 'submit'（用户填完提交）或 'skip'（用户点跳过）。skip 也上报一条，
    # 用来算弹出量/跳过率/完成率漏斗。
    action: str = "submit"
    answers: Dict[str, Any] = Field(default_factory=dict)


class SurveySubmission(BaseModel):
    """The signed envelope: payload + HMAC signature + replay/idempotency keys."""
    timestamp: float
    signature: str
    payload: SurveyPayload
    batch_id: str


class SubmitResponse(BaseModel):
    ok: bool = True
    message: str = "stored"
