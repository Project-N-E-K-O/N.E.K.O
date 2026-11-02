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


class SurveyPayload(BaseModel):
    """The signed body of one survey submission.

    ``answers`` maps ``question_id -> value`` where value is a string (single
    choice / free text) or a list of strings (multiple choice). On a ``skip``
    action it is empty. We keep it loosely typed (``Any``) so adding question
    types later never breaks ingest — the server stores answers verbatim as JSON
    and the dashboard interprets them per the survey definition.
    """
    device_id: str
    # Include the old device-ID algorithm during migration, aligned with telemetry, to JOIN the same person across tables.
    device_id_legacy: str = ""
    app_version: str = "unknown"
    # Survey version (= the app version triggering it, from config/surveys/<ver>.json).
    survey_version: str = "unknown"
    locale: str = "unknown"
    branch: str = "unknown"
    distribution: str = "unknown"
    # Steam64 (decimal string, cache first). Surveys are Steam-only but allow an empty value for
    # Steam installations where no login ID has ever been observed, matching telemetry's Steam + empty ID case.
    steam_user_id: str = ""
    # 'submit' when the user completes the survey, or 'skip' when skipped. Report skips too,
    # to calculate the display/skip/completion funnel.
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
