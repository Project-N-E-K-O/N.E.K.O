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

"""One multipart WAV upload that returns ``{"text": ...}``.

Shared by the GLM segmented worker and the offline (whole-recording) layer,
and reused by the offline OpenAI / xAI file-transcription routes, which speak
the same request shape. Delivery bookkeeping stays with the caller: the
optional ``on_sent`` hook runs right after the provider accepted the upload
body and before the status is checked, which is where the realtime worker
records its transport write.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import httpx

from ._shared import (
    RETRY_LATER_STATUSES,
    AsrRequestFailure,
    is_auth_rejection,
    provider_status_code,
)


class HttpResponse(Protocol):
    status_code: int

    def raise_for_status(self) -> None:
        """Raise ``httpx.HTTPStatusError`` for a 4xx/5xx status."""

    def json(self) -> Any:
        """Return the decoded JSON body."""


class HttpClient(Protocol):
    async def post(self, url: str, **kwargs: Any) -> HttpResponse:
        """Send one POST request (``httpx.AsyncClient.post`` shape)."""


async def post_wav_transcription(
    client: HttpClient,
    *,
    url: str,
    api_key: str,
    wav_audio: bytes,
    form_fields: Mapping[str, str],
    code_prefix: str,
    provider_label: str,
    on_sent: Callable[[], None] | None = None,
) -> str:
    """Upload one WAV file and return its stripped transcript.

    Failures raise :class:`AsrRequestFailure` with ``{code_prefix}_ERROR`` /
    ``_TIMEOUT`` / ``_WORKER_FAILED`` / ``_PROTOCOL_ERROR`` codes, or the
    shared ``ASR_CREDENTIALS_REJECTED`` code for 401/403. Form fields are sent
    before the file part (xAI ignores options that follow the file).
    """

    try:
        response = await client.post(
            url,
            # A self-hosted server may run without a key; some reject an
            # empty bearer token, so send no Authorization header at all.
            headers={"Authorization": f"Bearer {api_key}"} if api_key.strip() else {},
            data=dict(form_fields),
            files={"file": ("audio.wav", wav_audio, "audio/wav")},
        )
        if on_sent is not None:
            on_sent()
        response.raise_for_status()
    except asyncio.CancelledError:
        raise
    except httpx.HTTPStatusError as exc:
        if is_auth_rejection(exc):
            raise AsrRequestFailure(
                "ASR_CREDENTIALS_REJECTED",
                f"{provider_label} credentials were rejected",
                kind="auth",
            ) from exc
        status = provider_status_code(exc)
        raise AsrRequestFailure(
            f"{code_prefix}_ERROR",
            f"{provider_label} transcription request was rejected",
            kind=(
                "rate_limited"
                if status in RETRY_LATER_STATUSES
                else "rejected"
                if status is not None and 400 <= status < 500
                else "failed"
            ),
        ) from exc
    except httpx.TimeoutException as exc:
        raise AsrRequestFailure(
            f"{code_prefix}_TIMEOUT",
            f"{provider_label} transcription request timed out",
            kind="timeout",
        ) from exc
    except Exception as exc:
        raise AsrRequestFailure(
            f"{code_prefix}_WORKER_FAILED",
            f"{provider_label} transcription request failed",
        ) from exc

    try:
        payload = response.json()
    except Exception as exc:
        raise AsrRequestFailure(
            f"{code_prefix}_PROTOCOL_ERROR",
            f"{provider_label} returned an invalid response",
            kind="invalid_response",
        ) from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("text"), str):
        raise AsrRequestFailure(
            f"{code_prefix}_PROTOCOL_ERROR",
            f"{provider_label} returned an invalid transcript response",
            kind="invalid_response",
        )
    return payload["text"].strip()
