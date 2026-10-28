"""Persona reading and prompt-text budget helpers for Numeric v2."""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from utils.tokenize import truncate_to_tokens


# These are existing character-card fields, not a semantic classifier for arbitrary
# memories. Sharing the allowlist with Actor keeps card fallback and prompt
# projection aligned without importing real-world identity, powers or relationships.
THEATER_STYLE_PERSONA_FIELDS = frozenset({
    "自称", "性格", "核心特质", "核心特点", "口癖", "常用口癖",
    "说话风格", "语言风格", "表达风格", "语气",
})
THEATER_BEHAVIOR_PERSONA_FIELDS = frozenset({
    "行为特征", "行为特点", "爱好", "不喜欢", "雷点",
})
THEATER_PERSONA_FIELDS = THEATER_STYLE_PERSONA_FIELDS | THEATER_BEHAVIOR_PERSONA_FIELDS


def theater_persona_field(line: str) -> tuple[str, str]:
    """Read explicit card labels only; never infer traits from the fact's wording."""
    value = str(line or "").strip()
    bracketed = re.match(r"^[【\[]\s*([^】\]]{1,64})\s*[】\]]\s*[:：]?\s*(.*)$", value, re.DOTALL)
    if bracketed:
        label, body = bracketed.groups()
    else:
        labelled = re.match(r"^([^:：\n]+)[:：]\s*(.*)$", value, re.DOTALL)
        if not labelled:
            return "", ""
        label, body = labelled.groups()
    return re.sub(r"[\s*`\\]+", "", label).casefold(), body.strip()


_THEATER_EXCLUDED_PERSONA_FIELDS = frozenset(
    {
        "外貌特征",
        "特殊能力",
        "居住地点",
        "pngtuber",
        "pngtuber_idle_image",
        "pngtuber_talking_image",
        "年龄",
        "档案名",
        "种族",
        "一句话台词",
    }
)


def truncate_prompt_value(value: Any, *, max_tokens: int, max_items: int = 8) -> Any:
    """Recursively bound dynamic text and collection sizes entering theater prompts."""  # noqa: DOCSTRING_CJK
    if isinstance(value, str):
        return truncate_to_tokens(value, max_tokens)
    if isinstance(value, dict):
        items = list(value.items())
        if len(items) > max_items:
            items = items[: max(0, max_items - 1)] + (items[-1:] if max_items else [])
        return {
            str(key): truncate_prompt_value(item, max_tokens=max_tokens, max_items=max_items)
            for key, item in items
        }
    if isinstance(value, list):
        return [
            truncate_prompt_value(item, max_tokens=max_tokens, max_items=max_items)
            for item in value[:max_items]
        ]
    if isinstance(value, tuple):
        return tuple(
            truncate_prompt_value(item, max_tokens=max_tokens, max_items=max_items)
            for item in value[:max_items]
        )
    return value


def _load_character_profile(
    config_manager: Any | None,
    lanlan_name: str,
) -> str:
    """Read the bound card first, then compatible persona-memory supplements.

    The live card must not wait for background memory synchronization. It wins
    for the same explicit field, while the existing memory file can supplement
    other traits. This is read-only and never creates or synchronizes persona data.
    """  # noqa: DOCSTRING_CJK
    root = getattr(config_manager, "app_docs_dir", None) if config_manager is not None else None
    if not lanlan_name:
        return ""
    name = str(lanlan_name).strip()
    try:
        characters = config_manager.load_characters()
    except Exception:
        return ""
    catgirls = characters.get("猫娘") if isinstance(characters, dict) else None
    current_name = str(characters.get("当前猫娘") or "").strip() if isinstance(characters, dict) else ""
    # Request parameters must not read other catgirls' personas; Numeric v2 remains bound to the current user's character.
    if not isinstance(catgirls, dict) or name != current_name or name not in catgirls:
        return ""
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        return ""
    lines: list[str] = []
    card_fields: set[str] = set()
    card = catgirls[name]
    if isinstance(card, dict):
        for field, value in card.items():
            if field not in THEATER_PERSONA_FIELDS:
                continue
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                value = "、".join(item.strip() for item in value if item.strip())
            if isinstance(value, str) and value.strip():
                lines.append(f"{field}: {value.strip()}")
                card_fields.add(field)
    if not root:
        return "\n\n".join(lines)
    try:
        memory_root = (Path(root) / "memory").resolve()
        path = (memory_root / name / "persona.json").resolve()
    except (OSError, RuntimeError):
        return "\n\n".join(lines)
    if not path.is_relative_to(memory_root):
        return "\n\n".join(lines)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "\n\n".join(lines)
    for section_name in ("neko", "relationship"):
        section = payload.get(section_name) if isinstance(payload, dict) else None
        if not isinstance(section, dict):
            continue
        for fact in section.get("facts") or []:
            text = str(fact.get("text") or "").strip() if isinstance(fact, dict) else ""
            if not text or _theater_persona_field_excluded(text):
                continue
            field, _ = theater_persona_field(text)
            if field in card_fields or (field and field not in THEATER_PERSONA_FIELDS):
                continue
            # Unlabelled character facts retain the legacy expressive fallback.
            # Unlabelled relationship history is not a personality or story fact.
            if not field and section_name != "neko":
                continue
            lines.append(text)
    # Blank lines preserve fact ownership: a multiline card value stays one
    # labelled fact, while a legacy unlabelled supplement cannot inherit the
    # previous card field's behavioral scope.
    profile = "\n\n".join(dict.fromkeys(lines))
    # Actor packs complete facts and complete turns; do not truncate text inside persona facts here.
    return profile


def _load_player_address(
    config_manager: Any | None,
    *,
    characters: Any | None = None,
) -> str:
    """Read the current catgirl's structured form of address for the player."""  # noqa: DOCSTRING_CJK
    # Callers that already hold a characters snapshot pass it in, so one
    # request does not pay a second stat + deepcopy of the whole config.
    if characters is None:
        if config_manager is None:
            return ""
        try:
            characters = config_manager.load_characters()
        except Exception:
            return ""
    master = characters.get("主人") if isinstance(characters, dict) else None
    if not isinstance(master, dict):
        return ""
    for field in ("昵称", "档案名"):
        value = str(master.get(field) or "").strip()
        if value:
            return value
    return ""


def _theater_persona_field_excluded(text: str) -> bool:
    """Filter only by persona field labels, without guessing content from body keywords."""  # noqa: DOCSTRING_CJK
    value = str(text or "").strip()
    bracketed = re.match(r"^[【\[]\s*([^】\]]{1,64})\s*[】\]]", value)
    labelled = re.match(r"^([^:：\n]{1,64})\s*[:：]", value)
    match = bracketed or labelled
    if match is None:
        return False
    field_name = re.sub(r"[\s*`\\]+", "", match.group(1)).casefold()
    return field_name in _THEATER_EXCLUDED_PERSONA_FIELDS
