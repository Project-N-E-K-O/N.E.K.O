"""Looking at the battle.

Telemetry gives exact numbers; it cannot show the minimap, smoke, torpedo
wakes or where the team actually is. This package captures the game window
so the character can read the things numbers do not carry.

Split by responsibility:

* ``window``  — find the World of Warships window (or admit there isn't one)
* ``capture`` — turn a window (or the whole screen) into a 720p JPEG
* ``live``    — is the user already sharing their screen with the character?
* ``store``   — keep the last N frames on disk behind opaque handles
* ``tool``    — rate limiting, telemetry fusion, and the tool result shape
"""

from .capture import capture_jpeg
from .live import LiveVisionProbe
from .store import ShotRecord, ShotStore
from .tool import WOWS_VISION_PROMPT, ScreenshotService
from .window import GameWindow, find_game_window

__all__ = [
    "WOWS_VISION_PROMPT",
    "GameWindow",
    "LiveVisionProbe",
    "ScreenshotService",
    "ShotRecord",
    "ShotStore",
    "capture_jpeg",
    "find_game_window",
]
