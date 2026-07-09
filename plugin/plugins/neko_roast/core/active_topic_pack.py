"""Topic-pack classification for active-engagement materials."""

from __future__ import annotations

from . import active_topic_materials


def active_topic_pack(material: dict | None) -> str:
    if not isinstance(material, dict):
        return ""
    explicit = str(material.get("topic_pack") or "").strip()
    if explicit:
        return explicit
    live_column = str(material.get("live_column") or "").lower()
    family = active_topic_materials.host_material_family(material)
    combined = " ".join(
        str(material.get(field) or "").lower()
        for field in ("key", "title", "fun_axis", "preferred_shape", "shape", "reply_affordance")
    )
    if family in {"tease", "host_self_test"} or any(
        marker in live_column for marker in ("verdict", "court", "award", "score")
    ):
        return "neko_verdict"
    if family == "short_callback" or any(marker in live_column for marker in ("callback", "password", "command")):
        return "viewer_callback"
    if family == "choice_vote" or any(marker in live_column for marker in ("poll", "vote", "choice", "button")):
        return "micro_poll"
    if family == "micro_challenge" or any(marker in live_column for marker in ("challenge", "mission")):
        return "micro_challenge"
    if family == "object_scene" or any(marker in live_column for marker in ("observation", "patrol", "detective")):
        return "room_observation"
    if family == "room_mood" or any(marker in live_column for marker in ("radio", "weather", "thermometer", "filter", "mood")):
        return "room_mood"
    if "stance" in combined:
        return "neko_stance"
    return family or "general"
