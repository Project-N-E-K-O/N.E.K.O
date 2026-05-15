"""Provider-bound helpers for the lightweight local TTS server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "kokoro"
DEFAULT_VOICE = "default"
DEFAULT_KOKORO_REPO_ID = "hexgrad/Kokoro-82M-v1.1-zh"
DEFAULT_KOKORO_VOICE = "zf_001"
KOKORO_ZH_MODEL_DIR_NAME = "Kokoro-82M-v1.1-zh"
KOKORO_MODEL_FILE_NAME = "kokoro-v1_1-zh.pth"


@dataclass(frozen=True)
class VoiceSpec:
    """Parsed lightweight local TTS voice selector.

    Accepted examples:
    - "kokoro:zf_001"
    - "melotts:zh"
    - "chattts:default"
    - "zf_001" -> uses LOCAL_TTS_DEFAULT_MODEL
    """

    model: str
    voice: str


def local_tts_server_dir() -> Path:
    return Path(__file__).resolve().parent


def kokoro_models_root() -> Path:
    return local_tts_server_dir() / "kokoro_models"


def default_kokoro_model_dir() -> Path:
    return kokoro_models_root() / KOKORO_ZH_MODEL_DIR_NAME


def parse_local_tts_voice(raw_voice: str) -> VoiceSpec:
    default_model = os.getenv("LOCAL_TTS_DEFAULT_MODEL", DEFAULT_MODEL).strip().lower() or DEFAULT_MODEL
    value = (raw_voice or "").strip()
    if ":" not in value:
        return VoiceSpec(default_model, value or DEFAULT_VOICE)
    model, voice = value.split(":", 1)
    model = model.strip().lower() or default_model
    return VoiceSpec(model, voice.strip() or DEFAULT_VOICE)


def resolve_kokoro_model_dir() -> Path | None:
    raw = os.getenv("LOCAL_TTS_KOKORO_MODEL_DIR", "").strip()
    if raw:
        path = Path(raw)
        return path if path.is_dir() else None
    path = default_kokoro_model_dir()
    return path if path.is_dir() else None


def find_kokoro_model_file(model_dir: Path | None) -> Path | None:
    if model_dir is None:
        return None
    preferred = model_dir / KOKORO_MODEL_FILE_NAME
    if preferred.is_file():
        return preferred
    candidates = sorted(model_dir.glob("*.pth"))
    return candidates[0] if candidates else None


def available_kokoro_voices(model_dir: Path | None = None) -> list[str]:
    model_dir = model_dir if model_dir is not None else resolve_kokoro_model_dir()
    if model_dir is None:
        return []
    voices_dir = model_dir / "voices"
    if not voices_dir.is_dir():
        return []
    return sorted(path.stem for path in voices_dir.glob("*.pt") if path.is_file())


def resolve_kokoro_voice_file(voice: str, model_dir: Path | None = None) -> str:
    if model_dir is None or voice.endswith(".pt"):
        return voice
    local_voice = model_dir / "voices" / f"{voice}.pt"
    return str(local_voice) if local_voice.is_file() else voice
