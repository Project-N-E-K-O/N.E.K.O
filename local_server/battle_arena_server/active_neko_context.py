# -*- coding: utf-8 -*-
"""Resolve the active NEKO character context for Neko Brawl.

The forge machine must follow the catgirl currently selected by NEKO itself:
that is the catgirl who invited the player into Neko Brawl, and therefore the
only correct memory source for facts and generated card stories.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ActiveNekoContext:
    master_name: str
    lanlan_name: str
    memory_dir: Path | None
    facts_path: Path | None
    lanlan_prompt: str = ""
    source: str = "neko-config"


def safe_character_segment(name: str | None) -> str | None:
    if not name or not isinstance(name, str):
        return None
    value = name.strip()
    if not value or len(value) > 80:
        return None
    if any(part in value for part in ("/", "\\", "..", "\x00")):
        return None
    return value


def _resolve_memory_dir(config_manager: Any) -> Path | None:
    env_memory_dir = os.environ.get("NEKO_MEMORY_DIR", "").strip()
    if env_memory_dir:
        return Path(env_memory_dir)

    memory_dir = getattr(config_manager, "memory_dir", None)
    return Path(memory_dir) if memory_dir else None


def _resolve_prompt(config_manager: Any, lanlan_name: str, master_name: str) -> str:
    try:
        character_data = config_manager.get_character_data()
        prompt_map = character_data[5] if len(character_data) > 5 and isinstance(character_data[5], dict) else {}
        prompt = str(prompt_map.get(lanlan_name, "") or "")
        return prompt.replace("{LANLAN_NAME}", lanlan_name).replace("{MASTER_NAME}", master_name)
    except Exception:
        return ""


def _build_context(config_manager: Any, character_override: str | None = None) -> ActiveNekoContext:
    master_name, current_lanlan, *_rest = config_manager.get_character_data()
    master = str(master_name or "").strip()
    active_lanlan = str(current_lanlan or "").strip()

    # The active NEKO catgirl is authoritative. `character_override` exists only
    # for debug endpoints and old callers; normal forge flow should omit it.
    debug_override = safe_character_segment(character_override)
    lanlan = debug_override or active_lanlan

    direct_facts = os.environ.get("NEKO_FACTS_JSON", "").strip()
    memory_dir = _resolve_memory_dir(config_manager)
    if direct_facts:
        facts_path = Path(direct_facts)
        source = "env-facts-json"
    elif memory_dir and safe_character_segment(lanlan):
        facts_path = memory_dir / lanlan / "facts.json"
        source = "neko-config"
    else:
        facts_path = None
        source = "unresolved"

    return ActiveNekoContext(
        master_name=master,
        lanlan_name=lanlan,
        memory_dir=memory_dir,
        facts_path=facts_path,
        lanlan_prompt=_resolve_prompt(config_manager, lanlan, master),
        source=source,
    )


async def resolve_active_neko_context(character_override: str | None = None) -> ActiveNekoContext:
    from utils.config_manager import get_config_manager

    config_manager = get_config_manager()
    return await asyncio.to_thread(_build_context, config_manager, character_override)
