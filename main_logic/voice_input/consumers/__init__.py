"""Built-in voice-input consumer adapters."""

from .core_chat import CoreChatVoiceInputConsumer
from .game import GameVoiceInputConsumer

__all__ = ["CoreChatVoiceInputConsumer", "GameVoiceInputConsumer"]
