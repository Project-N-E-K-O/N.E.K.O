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

"""Which text-model routes go through the native google-genai SDK.

A route-string test only, with no import of the SDK: the offline client uses it
before deciding to import google-genai, and the background warmup uses it to
decide whether google-genai is worth importing ahead of time. It lives in
``utils`` so both layers can share one definition.
"""

from __future__ import annotations

GENAI_NATIVE_BASE_URL_HINTS: tuple[str, ...] = (
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
)

GENAI_NATIVE_MODEL_HINTS: tuple[str, ...] = ("gemini",)


def is_native_genai_route(model: str | None, base_url: str | None) -> bool:
    """The URL is Google's native Gemini endpoint, or there is no URL and the model is a Gemini one.

    A Gemini model behind any other URL (a proxy, OpenRouter, the free route)
    speaks the OpenAI-compatible protocol and is not a native route.
    """
    bl = (base_url or "").lower()
    ml = (model or "").lower()
    return (
        any(hint in bl for hint in GENAI_NATIVE_BASE_URL_HINTS)
        or (not bl and any(hint in ml for hint in GENAI_NATIVE_MODEL_HINTS))
    )
