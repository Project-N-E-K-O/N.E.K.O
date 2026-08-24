# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""MiniMax voice identifiers, endpoints, and storage helpers shared by TTS features."""

import logging
import uuid
from typing import Optional


MINIMAX_DOMESTIC_BASE_URL = "https://api.minimaxi.com"
MINIMAX_INTL_BASE_URL = "https://api.minimax.io"
MINIMAX_VOICE_STORAGE_KEY = '__MINIMAX__'
MINIMAX_INTL_VOICE_STORAGE_KEY = '__MINIMAX_INTL__'
MINIMAX_PREFIX_MAX_LENGTH = 10

logger = logging.getLogger(__name__)


def get_minimax_base_url(provider: str = 'minimax') -> str:
    """Return the MiniMax API base URL for the given provider."""
    if provider == 'minimax_intl':
        return MINIMAX_INTL_BASE_URL
    return MINIMAX_DOMESTIC_BASE_URL


def get_minimax_storage_prefix(provider: str = 'minimax') -> str:
    """Return the voice-storage key prefix for the given provider."""
    if provider == 'minimax_intl':
        return MINIMAX_INTL_VOICE_STORAGE_KEY
    return MINIMAX_VOICE_STORAGE_KEY


def sanitize_minimax_voice_prefix(
    prefix: str,
    default_prefix: str = 'voice',
    *,
    max_length: Optional[int] = MINIMAX_PREFIX_MAX_LENGTH,
) -> str:
    """Restrict an upstream MiniMax ``voice_id`` to ASCII alphanumerics."""
    normalized = ''.join(ch for ch in str(prefix or '') if ch.isascii() and ch.isalnum())
    if max_length is not None:
        normalized = normalized[:max_length]
    if normalized:
        return normalized

    fallback = ''.join(ch for ch in str(default_prefix or '') if ch.isascii() and ch.isalnum())
    if max_length is not None:
        fallback = fallback[:max_length]
    return fallback or 'voice'


def build_minimax_request_voice_id(prefix: str, provider_label: str) -> tuple[str, str]:
    """Return the saved prefix and a MiniMax-compatible remote voice ID.

    MiniMax requires an ASCII alphanumeric ``voice_id`` prefix. Both Clone and
    Design create MiniMax voices with this same upstream constraint.
    """
    original_prefix = str(prefix or '').strip()
    safe_prefix = sanitize_minimax_voice_prefix(
        original_prefix,
        max_length=MINIMAX_PREFIX_MAX_LENGTH,
    )
    if safe_prefix != original_prefix:
        logger.info(
            "%s voice prefix normalized: %r -> %r",
            provider_label,
            original_prefix,
            safe_prefix,
        )
    return original_prefix, f"{safe_prefix}{uuid.uuid4().hex[:8]}"
