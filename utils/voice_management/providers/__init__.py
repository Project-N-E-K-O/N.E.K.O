"""Provider implementations; the shared contract does not import this module."""

from .cosyvoice import CosyVoiceAdapter
from .doubao import DoubaoVoiceAdapter
from .elevenlabs import ElevenLabsVoiceAdapter
from .glm import GlmVoiceAdapter
from .minimax import MiniMaxVoiceAdapter

_ADAPTERS = {
    "cosyvoice": CosyVoiceAdapter(),
    "cosyvoice_intl": CosyVoiceAdapter("cosyvoice_intl"),
    "minimax": MiniMaxVoiceAdapter(),
    "minimax_intl": MiniMaxVoiceAdapter("minimax_intl"),
    "elevenlabs": ElevenLabsVoiceAdapter(),
    "doubao_tts": DoubaoVoiceAdapter(),
    "glm_tts": GlmVoiceAdapter(),
}


def get_adapter(provider):
    return _ADAPTERS.get(provider)


def register_adapter(provider, adapter):
    """Register a provider-declared implementation in the shared lookup table."""
    if not isinstance(provider, str) or not provider.strip() or adapter is None:
        raise ValueError("VOICE_MANAGEMENT_ADAPTER_INVALID")
    _ADAPTERS[provider.strip().lower()] = adapter
