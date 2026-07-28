"""Compatibility exports for the shared card-forge character resolver."""

from main_logic.card_forge_facts import (
    ActiveNekoContext,
    resolve_active_neko_context,
)
from main_logic.card_forge_facts import (
    _safe_character_segment as safe_character_segment,
)

__all__ = [
    "ActiveNekoContext",
    "resolve_active_neko_context",
    "safe_character_segment",
]
