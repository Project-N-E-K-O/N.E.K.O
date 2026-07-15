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

"""Small provider-neutral helpers shared by ASR workers."""

from __future__ import annotations


def normalize_zh_en_language(language: str, *, provider_name: str) -> str | None:
    """Normalize the shared auto/Chinese/English language contract."""

    normalized = language.strip().lower()
    if normalized == "auto":
        return None
    if normalized in {"zh", "zh-cn"}:
        return "zh"
    if normalized in {"en", "en-us"}:
        return "en"
    raise ValueError(
        f"ASR_LANGUAGE_NOT_SUPPORTED: {provider_name} language is unsupported"
    )


def is_auth_rejection(exc: BaseException) -> bool:
    """Return whether a provider exception carries an auth rejection status."""

    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is None:
        status_code = getattr(exc, "status_code", None)
    return status_code in {401, 403}
