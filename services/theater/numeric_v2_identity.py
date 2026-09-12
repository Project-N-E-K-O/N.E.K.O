"""Numeric v2 对猫娘角色卡的稳定身份投影。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from utils.config_manager import get_reserved, normalize_character_id

from .llm_context import _load_player_address


def numeric_v2_character_ids(config_manager: Any) -> dict[str, str]:
    """返回当前已安装猫娘角色卡的稳定身份索引。"""  # noqa: DOCSTRING_CJK

    # This index authorizes destructive storage audit. Chat's fallback profiles
    # and IDs that failed to persist cannot prove that a saved character is gone.
    try:
        characters = config_manager.load_characters(require_authoritative=True)
    except (OSError, ValueError) as exc:
        raise ValueError("numeric_character_config_unavailable") from exc
    catgirls = characters.get("猫娘") if isinstance(characters, dict) else None
    if not isinstance(catgirls, dict):
        raise ValueError("numeric_character_config_unavailable")
    result: dict[str, str] = {}
    for name, profile in catgirls.items():
        if not isinstance(profile, dict):
            raise ValueError("numeric_character_config_unavailable")
        character_id = normalize_character_id(
            get_reserved(profile, "character_id", default="")
        )
        normalized_name = str(name or "").strip()
        if normalized_name and character_id:
            result[normalized_name] = character_id
        else:
            raise ValueError("numeric_character_config_unavailable")
    return result


def numeric_v2_catgirl_binding(
    config_manager: Any,
    catgirl_name: str | None = None,
) -> dict[str, str]:
    characters = config_manager.load_characters()
    selected_name = str(
        catgirl_name
        or (characters.get("当前猫娘") if isinstance(characters, dict) else "")
        or ""
    ).strip()
    catgirls = characters.get("猫娘") if isinstance(characters, dict) else None
    profile = catgirls.get(selected_name) if isinstance(catgirls, dict) else None
    if not selected_name or not isinstance(profile, dict):
        raise ValueError("current_catgirl_unavailable")
    character_id = normalize_character_id(
        get_reserved(profile, "character_id", default="")
    )
    if not character_id:
        raise ValueError("current_catgirl_identity_unavailable")

    # character_id 是存储身份，不属于人格内容；排除它可让既有角色补 ID 时
    # 继续匹配迁移前的 profile_hash。
    profile_for_hash = deepcopy(profile)
    reserved = profile_for_hash.get("_reserved")
    if isinstance(reserved, dict):
        reserved.pop("character_id", None)
        if not reserved:
            profile_for_hash.pop("_reserved", None)
    canonical = json.dumps(
        profile_for_hash,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    profile_hash = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    return {
        "character_id": character_id,
        "catgirl_id": f"catgirl:{character_id}",
        "catgirl_name": selected_name,
        "player_address": _load_player_address(config_manager) or "你",
        "profile_revision": f"characters:{profile_hash.removeprefix('sha256:')[:16]}",
        "profile_hash": profile_hash,
    }


def numeric_v2_authoring_names(config_manager: Any) -> dict[str, str]:
    """Provide the author with one name snapshot; knowing the nickname does not disclose it within the story."""

    binding = numeric_v2_catgirl_binding(config_manager)
    return {"player_name": binding["player_address"], "catgirl_name": binding["catgirl_name"]}


__all__ = ["numeric_v2_catgirl_binding", "numeric_v2_character_ids", "numeric_v2_authoring_names"]
