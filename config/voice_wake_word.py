"""Local wake-word deployment configuration (optional until models are installed).

Set NEKO_WAKE_WORD_MODEL_DIR to the provisioned sherpa-onnx model directory
before starting the server. An empty/unset directory keeps voiceprint-only
activation. Model loading and validation belong to the asynchronous detector.
"""

from __future__ import annotations

import os


# Phonetic token sequences, not English letter-by-letter spelling of the name.
# Near pronunciations are intentionally accepted and share the Chinese wake label.
# These are model vocabulary inputs; actual recognition quality needs recordings.
DEFAULT_WAKE_WORD_KEYWORDS = (
    "y ōu y í @悠宜",
    "Y UW1 IY0 @yui",
    "y ōu y ú @悠宜",
    "l iú y ú @悠宜",
)


def wake_word_model_dir() -> str | None:
    """Return the opt-in path without filesystem I/O on the session setup path."""
    value = os.environ.get("NEKO_WAKE_WORD_MODEL_DIR", "").strip()
    return value or None


__all__ = ["DEFAULT_WAKE_WORD_KEYWORDS", "wake_word_model_dir"]
