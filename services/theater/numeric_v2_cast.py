"""Project the author's two protagonist names onto the current player and catgirl."""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from .name_projection import replace_names


_NAME_SEPARATOR_RE = re.compile(r"[，,；;：:\n（(]")
_PROTOCOL_FIELDS = frozenset({
    "id", "type", "owner", "timing", "mode", "evidence_mode", "source", "sources",
    "delivery_type", "output_field", "dialogue_policy_after", "state_effects",
    "cognition_state", "memory_state", "self_reference_mode", "persona_scope",
    "dialogue_policy", "relationship_ceiling", "conditions", "metric", "op",
})


def _identity_name(identity: Any) -> str:
    """The first identity segment is the author's character name; avoid unsafe full-text guesses when no separate name exists."""  # noqa: DOCSTRING_CJK

    text = str(identity or "").strip()
    separator = _NAME_SEPARATOR_RE.search(text)
    if separator is None:
        return ""
    first = text[:separator.start()].strip()
    if not first or len(first) > 24:
        return ""
    return first


@dataclass(frozen=True, slots=True)
class NumericV2CastProjection:
    """Fixed at runtime: the player plays the male lead and the current catgirl plays the female lead."""  # noqa: DOCSTRING_CJK

    source_player_name: str
    source_catgirl_name: str
    player_name: str
    catgirl_name: str

    @classmethod
    def from_story(
        cls,
        story: Mapping[str, Any],
        *,
        player_name: str,
        catgirl_name: str,
    ) -> "NumericV2CastProjection":
        intro = story.get("intro") if isinstance(story.get("intro"), Mapping) else {}
        return cls(
            # New Workshop packages record full names explicitly; legacy packages use the validated first identity segment without rewriting files.
            source_player_name=str(intro["player_name"]) if "player_name" in intro else _identity_name(intro.get("player_identity")),
            source_catgirl_name=str(intro["catgirl_name"]) if "catgirl_name" in intro else _identity_name(intro.get("catgirl_identity")),
            player_name=str(player_name or "你").strip() or "你",
            catgirl_name=str(catgirl_name or "当前猫娘").strip() or "当前猫娘",
        )

    def text(self, value: Any) -> str:
        lead_pair = f"{self.player_name}和{self.catgirl_name}"
        replacements = (
            ("男女主人公", lead_pair),
            ("男女主角", lead_pair),
            ("男女主", lead_pair),
            (self.source_player_name, self.player_name),
            (self.source_catgirl_name, self.catgirl_name),
        )
        return replace_names(value, replacements)

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            # Nicknames may be player/turn; name projection affects narration only, never character enums or reference IDs.
            return {
                str(key): deepcopy(item) if (
                    str(key) in _PROTOCOL_FIELDS or str(key).endswith(("_id", "_ids"))
                ) else self.value(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.value(item) for item in value)
        return deepcopy(value)

    def intro(self, story: Mapping[str, Any]) -> dict[str, Any]:
        return self.value(dict(story.get("intro") or {}))


__all__ = ["NumericV2CastProjection"]
