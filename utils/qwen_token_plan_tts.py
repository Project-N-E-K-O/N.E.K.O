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

"""Alibaba Bailian Token Plan speech synthesis (qwen-audio TTS) protocol helpers.

Dual to :mod:`utils.doubao_tts`: endpoint derivation, headers, payload and
response parsing live here; the worker lives in
``main_logic/tts_client/workers/qwen_token_plan.py`` and the settings probe in
``main_routers/config_router/connectivity.py``.

Token Plan serves TTS through the DashScope-native HTTP endpoint
``POST /api/v1/services/audio/tts/SpeechSynthesizer`` (not the OpenAI-compatible
``/audio/speech``). ``X-DashScope-SSE: enable`` streams base64 audio in
``output.audio.data``; without it the response only carries a signed
``output.audio.url``.
"""

from __future__ import annotations

import base64
import binascii
import urllib.parse
from typing import Any

from utils.tts.provider_registry import PresetCatalog

# Do not import httpx at module scope (see utils/doubao_tts.py for the startup cost).

# Both hosts resolve to the same cn-beijing gateway; the console shows either one.
# Token Plan keys are subscription credentials, so they only go to these hosts over HTTPS.
QWEN_TOKEN_PLAN_HOSTS = frozenset({
    "token-plan.cn-beijing.maas.aliyuncs.com",
    "token-plan.maas.qianwenaiapi.com",
})
QWEN_TOKEN_PLAN_TTS_DEFAULT_BASE_URL = "https://token-plan.cn-beijing.maas.aliyuncs.com"
QWEN_TOKEN_PLAN_TTS_PATH = "/api/v1/services/audio/tts/SpeechSynthesizer"
QWEN_TOKEN_PLAN_TTS_DEFAULT_MODEL = "qwen-audio-3.0-tts-plus"
QWEN_TOKEN_PLAN_TTS_DEFAULT_VOICE = "longanlingxin"
QWEN_TOKEN_PLAN_TTS_SAMPLE_RATE = 24000
QWEN_TOKEN_PLAN_TTS_FORMAT = "pcm"
# Token Plan keys start with this prefix; ordinary Bailian ``sk-`` keys are rejected.
QWEN_TOKEN_PLAN_KEY_PREFIX = "sk-sp-"
# qwen-audio-3.0-tts-plus also accepts 500+ basic voices named with this prefix.
QWEN_TOKEN_PLAN_TTS_BASIC_VOICE_PREFIX = "qwen-audio-3.0-tts-plus-"

# Named voices of qwen-audio-3.0-tts-plus. Voices are bound to the model version:
# sending another provider's voice id (Cherry, a clone id, ...) fails with
# InvalidParameter, so anything else falls back to the default voice.
QWEN_TOKEN_PLAN_TTS_PRESET_CATALOG = PresetCatalog(
    catalog={"longanlingxin": "Female", "longanlufeng": "Male"},
    default_voice=QWEN_TOKEN_PLAN_TTS_DEFAULT_VOICE,
    catalog_prefix="Token Plan",
)


class QwenTokenPlanTtsError(Exception):
    """An error response from the Token Plan speech synthesis endpoint."""


def is_qwen_token_plan_url(url: str | None) -> bool:
    """Whether ``url`` is an HTTPS URL on a Token Plan host (any path)."""
    try:
        parts = urllib.parse.urlsplit((url or "").strip())
    except ValueError:
        return False
    # The subscription key must never be sent in plaintext or to another host.
    return parts.scheme.lower() == "https" and (parts.hostname or "").lower() in QWEN_TOKEN_PLAN_HOSTS


def qwen_token_plan_tts_url(base_url: str | None) -> str:
    """The synthesis endpoint on the host of ``base_url``.

    Only the host is taken from ``base_url``: users often paste the
    ``/compatible-mode/v1`` address shown in the console, which serves text
    models only. Raises ValueError for a URL outside the Token Plan hosts.
    """
    candidate = (base_url or "").strip() or QWEN_TOKEN_PLAN_TTS_DEFAULT_BASE_URL
    if not is_qwen_token_plan_url(candidate):
        raise ValueError("not an HTTPS Token Plan URL")
    host = (urllib.parse.urlsplit(candidate).hostname or "").lower()
    return f"https://{host}{QWEN_TOKEN_PLAN_TTS_PATH}"


def qwen_token_plan_audio_download_url(url: str | None) -> str:
    """The signed result URL to download, or "" when it must not be fetched.

    The API documentation shows ``http://dashscope-result-*.aliyuncs.com`` OSS
    links; those are upgraded to HTTPS (the OSS signature does not cover the
    scheme). Any other non-HTTPS URL is refused.
    """
    try:
        parts = urllib.parse.urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return ""
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme == "https" and host:
        return urllib.parse.urlunsplit(parts)
    if scheme == "http" and host.startswith("dashscope-result") and host.endswith(".aliyuncs.com") \
            and not parts.username and not parts.password and port in (None, 80):
        return urllib.parse.urlunsplit(("https", host, parts.path, parts.query, ""))
    return ""


def qwen_token_plan_api_headers(api_key: str, *, stream: bool = True) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if stream:
        headers["X-DashScope-SSE"] = "enable"
    return headers


def build_qwen_token_plan_tts_payload(
    text: str,
    voice: str,
    *,
    model: str = QWEN_TOKEN_PLAN_TTS_DEFAULT_MODEL,
    audio_format: str = QWEN_TOKEN_PLAN_TTS_FORMAT,
    sample_rate: int = QWEN_TOKEN_PLAN_TTS_SAMPLE_RATE,
) -> dict[str, Any]:
    return {
        "model": model,
        "input": {
            "text": text,
            "voice": voice,
            "format": audio_format,
            "sample_rate": sample_rate,
        },
    }


def normalize_qwen_token_plan_tts_voice(voice_id: str | None) -> tuple[str, bool]:
    """Return (voice to send, recognized); unrecognized ids map to the default voice."""
    voice = (voice_id or "").strip()
    if voice.lower().startswith(QWEN_TOKEN_PLAN_TTS_BASIC_VOICE_PREFIX) and len(voice) > len(
        QWEN_TOKEN_PLAN_TTS_BASIC_VOICE_PREFIX
    ):
        return voice, True
    return QWEN_TOKEN_PLAN_TTS_PRESET_CATALOG.normalize(voice)


def select_qwen_token_plan_tts_voice(*voice_ids: str | None) -> tuple[str, bool]:
    """Pick the first voice this model accepts.

    Returns (voice, fell_back): ``fell_back`` is True when a non-empty voice id
    was rejected and the default voice is used instead.
    """
    rejected = False
    for voice_id in voice_ids:
        if not str(voice_id or "").strip():
            continue
        voice, recognized = normalize_qwen_token_plan_tts_voice(voice_id)
        if recognized:
            return voice, False
        rejected = True
    return QWEN_TOKEN_PLAN_TTS_DEFAULT_VOICE, rejected


def qwen_token_plan_key_looks_like_bailian_key(api_key: str | None) -> bool:
    """An ordinary Bailian ``sk-`` key entered where a Token Plan ``sk-sp-`` key belongs."""
    key = (api_key or "").strip()
    return key.startswith("sk-") and not key.startswith(QWEN_TOKEN_PLAN_KEY_PREFIX)


def _raise_for_error_event(event: dict[str, Any]) -> None:
    code = event.get("code")
    if code in (None, "", 0, "0", 200, "200", "Success", "success"):
        return
    message = str(event.get("message") or "").strip()
    raise QwenTokenPlanTtsError(f"{code}: {message}" if message else str(code))


def extract_qwen_token_plan_audio(event: object) -> tuple[bytes, str, bool]:
    """Parse one response object.

    Returns (audio bytes from ``output.audio.data``, ``output.audio.url``,
    finished). Raises QwenTokenPlanTtsError for an error object.
    """
    if not isinstance(event, dict):
        return b"", "", False
    _raise_for_error_event(event)
    output = event.get("output")
    if not isinstance(output, dict):
        return b"", "", False
    audio = output.get("audio")
    data, url = "", ""
    if isinstance(audio, dict):
        data = audio.get("data") or ""
        url = audio.get("url") or ""
    chunk = b""
    if isinstance(data, str) and data.strip():
        try:
            chunk = base64.b64decode(data.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise QwenTokenPlanTtsError("invalid base64 audio") from exc
    finished = str(output.get("finish_reason") or "").lower() == "stop"
    return chunk, url if isinstance(url, str) else "", finished
