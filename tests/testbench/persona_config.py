"""Pydantic schema for the testbench session's persona bundle.

The "persona" here is the **session-level** identity declaration — who
the AI thinks it is (``character_name``) and who the user is
(``master_name``) — plus the raw ``system_prompt`` that will eventually
be fed into the prompt builder (P08). It is purposefully *not* identical
to the upstream :mod:`memory.persona` three-tier long-term persona
storage, which is a separate concept (entity-level facts / reflections).

Design notes:
- All 4 fields default to empty strings so a freshly-created session
  starts with a blank form — the Persona UI nudges the tester to fill
  them, or to use the Import sub-page to copy from a real character.
- ``language`` is a free-form string (e.g. ``"zh-CN"`` / ``"en"`` /
  ``"ja"``) — the Persona UI limits the <select> to known values but
  the backend is tolerant so future locales don't need a schema bump.
- ``system_prompt`` may contain ``{LANLAN_NAME}`` / ``{MASTER_NAME}``
  placeholders (upstream convention); substitution is deferred to P08.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PersonaConfig(BaseModel):
    """Editable per-session persona metadata.

    All fields optional at input time — the Persona form saves whatever
    the tester has typed, allowing partial drafts.
    """

    master_name: str = Field(default="", description="Human/user display name.")
    character_name: str = Field(default="", description="AI/character display name.")
    language: str = Field(default="zh-CN", description="UI locale for prompt rendering.")
    system_prompt: str = Field(default="", description="Raw system prompt text.")

    def summary(self) -> dict[str, Any]:
        """Compact JSON-safe dict for ``GET /api/persona``."""
        return {
            "master_name": self.master_name,
            "character_name": self.character_name,
            "language": self.language,
            "system_prompt": self.system_prompt,
            "is_configured": bool(self.character_name and self.system_prompt),
        }

    @classmethod
    def from_session_value(cls, raw: Any) -> "PersonaConfig":
        """Normalize whatever sits on ``Session.persona`` into an instance.

        Accepts ``None`` / ``dict`` / existing :class:`PersonaConfig`.
        ``dict`` values go through Pydantic validation to keep the shape
        honest.
        """
        if not raw:
            return cls()
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, dict):
            return cls.model_validate(raw)
        raise TypeError(f"Unsupported persona payload type: {type(raw)!r}")
