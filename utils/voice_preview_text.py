# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Shared language selection for Clone and Design voice preview templates."""

from config.prompts.prompts_voice import VOICE_PREVIEW_TEXTS
from utils.language_utils import is_supported_language_code, normalize_language_code


def normalize_voice_preview_language(raw_language: object) -> str | None:
    """Return a supported preview-template locale, or ``None`` when invalid."""
    raw = str(raw_language or "").strip()
    if not raw or not is_supported_language_code(raw):
        return None
    normalized = normalize_language_code(raw, format="full")
    return normalized if normalized in VOICE_PREVIEW_TEXTS else None
