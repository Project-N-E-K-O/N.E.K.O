"""Provider-neutral payload validation and receipt state for submitted recordings."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import time
from dataclasses import dataclass


AUDIO_MESSAGE_MODE = "audio_message"
MAX_AUDIO_BYTES = 16_000 * 2 * 30
MESSAGE_LIFETIME_SECONDS = 15 * 60
_MESSAGE_ID = re.compile(r"^(\d{13})-[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")


class AudioMessageManualModeUnconfirmed(ConnectionError):
    """The provider did not confirm manual turns before any recording was sent."""


@dataclass(slots=True)
class AudioMessageRecord:
    digest: bytes
    expires_at: float
    state: str = "sending"
    code: str = ""


def decode_audio_message(message: dict) -> tuple[str, bytes, bytes, float]:
    message_id = message.get("request_id")
    match = _MESSAGE_ID.fullmatch(message_id) if isinstance(message_id, str) else None
    if match is None or message.get("sample_rate_hz") != 16_000:
        raise ValueError("invalid")
    created_at = int(match[1]) / 1000
    now = time.time()
    if created_at > now + 60 or now - created_at > MESSAGE_LIFETIME_SECONDS:
        raise ValueError("expired")
    encoded = message.get("audio")
    if not isinstance(encoded, str) or len(encoded) > MAX_AUDIO_BYTES * 4 // 3:
        raise ValueError("invalid")
    try:
        pcm = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid") from exc
    if not 3200 <= len(pcm) <= MAX_AUDIO_BYTES or len(pcm) % 2:
        raise ValueError("invalid")
    return message_id, pcm, hashlib.sha256(pcm).digest(), created_at + MESSAGE_LIFETIME_SECONDS + 60
