"""Built-in transcript consumers owned by the Core process."""

from .core_chat import CoreChatTurnContext, CoreChatVoiceInputConsumer
from .game import GameVoiceInputConsumer

__all__ = [
    "CoreChatTurnContext",
    "CoreChatVoiceInputConsumer",
    "GameVoiceInputConsumer",
]
