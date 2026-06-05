from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class STS2StrategyParser:
    _CHARACTER_STRATEGY_ALIASES = {
        "defect": "defect",
        "the_defect": "defect",
        "故障机器人": "defect",
        "鸡煲": "defect",
        "雞煲": "defect",
        "机器人": "defect",
        "機器人": "defect",
        "ironclad": "ironclad",
        "the_ironclad": "ironclad",
        "铁甲战士": "ironclad",
        "鐵甲戰士": "ironclad",
        "战士": "ironclad",
        "戰士": "ironclad",
        "铁甲": "ironclad",
        "鐵甲": "ironclad",
        "red": "ironclad",
        "silent_hunter": "silent_hunter",
        "silent": "silent_hunter",
        "the_silent": "silent_hunter",
        "静默猎手": "silent_hunter",
        "靜默獵手": "silent_hunter",
        "necrobinder": "necrobinder",
        "regent": "regent",
        "摄政王": "regent",
        "攝政王": "regent",
    }
    _SCENE_ALIASES = {
        "combat": "combat",
        "event": "event",
        "map": "map",
        "reward": "reward",
        "selection": "reward",
        "card_selection": "reward",
        "card_selection_reward": "reward",
        "card_selection_unusefull": "remove",
        "card_selection_delet": "remove",
        "shop": "shop",
        "shop_show": "shop",
        "rest": "remove",
        "chest": "reward",
    }
    _SCENE_FILES = ("base", "combat", "event", "reward", "remove", "exhaust", "map", "shop")
    _USER_OVERRIDE_DIR = "user_overrides"

    def __init__(self, logger: Any, *, strategies_dir: Path | None = None) -> None:
        self.logger = logger
        self._strategies_dir = strategies_dir or Path(__file__).with_name("strategies")
        self._prompt_cache: dict[tuple[str, str], str] = {}
        self._constraints_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def available_strategies(self) -> list[str]:
        if not self._strategies_dir.exists() or not self._strategies_dir.is_dir():
            return []
        candidates: list[str] = []
        for path in self._strategies_dir.iterdir():
            if not path.is_dir():
                continue
            if path.name.startswith("__"):
                continue
            if path.joinpath("base.md").is_file():
                candidates.append(path.name)
        return sorted(candidates)

    def normalize_strategy_name(self, strategy_name: Any) -> str:
        fallback = "defect" if strategy_name is None else strategy_name
        raw = str(fallback).strip().lower().replace(" ", "_")
        alias = self._CHARACTER_STRATEGY_ALIASES.get(raw)
        if alias:
            return alias
        normalized = re.sub(r"[^a-z0-9_-]", "", raw)
        return normalized or "defect"

    def normalize_scene_name(self, scene_name: Any) -> str:
        raw = str(scene_name or "combat").strip().lower().replace(" ", "_")
        return self._SCENE_ALIASES.get(raw, "combat")

    def load_prompt(self, strategy_name: str, scene_name: str | None = None) -> str | None:
        normalized = self.normalize_strategy_name(strategy_name)
        scene = self.normalize_scene_name(scene_name)
        cache_key = (normalized, scene)
        if cache_key in self._prompt_cache:
            prompt = self._prompt_cache[cache_key]
            return prompt or None
        parts = self._load_prompt_parts(normalized, scene)
        prompt = "\n\n".join(part for part in parts if part).strip()
        self._prompt_cache[cache_key] = prompt
        return prompt or None

    def load_constraints(self, strategy_name: str, scene_name: str | None = None) -> dict[str, Any]:
        normalized = self.normalize_strategy_name(strategy_name)
        scene = self.normalize_scene_name(scene_name)
        cache_key = (normalized, scene)
        if cache_key in self._constraints_cache:
            constraints = dict(self._constraints_cache[cache_key])
            return constraints
        constraints = self._empty_constraints()
        for path in self._bundle_paths(normalized, scene):
            prompt = self._read_strategy_file(path)
            if not prompt:
                continue
            parsed = self._parse_constraints(prompt)
            constraints = self._merge_constraints(constraints, parsed)
        self._constraints_cache[cache_key] = constraints
        return dict(constraints)

    def prompt_for_state(self, strategy_name: str, state_name: str) -> str | None:
        return self.load_prompt(strategy_name, state_name)

    def _load_prompt_parts(self, strategy_name: str, scene_name: str) -> list[str]:
        parts: list[str] = []
        bundle_paths = self._bundle_paths(strategy_name, scene_name)
        for path in bundle_paths:
            prompt = self._read_strategy_file(path)
            if prompt:
                parts.append(prompt)
        return parts

    def _bundle_paths(self, strategy_name: str, scene_name: str) -> list[Path]:
        role_dir = self._resolve_strategy_dir(strategy_name)
        scene = self.normalize_scene_name(scene_name)
        paths = [role_dir / "base.md", role_dir / f"{scene}.md"]
        override_dir = self._strategies_dir / self._USER_OVERRIDE_DIR / strategy_name
        if override_dir.is_dir():
            paths.extend([
                override_dir / "base.md",
                override_dir / f"{scene}.md",
            ])
        legacy_override = self._strategies_dir / "player_overrides.md"
        if legacy_override.is_file():
            paths.append(legacy_override)
        return [path for path in paths if path.is_file()]

    def _resolve_strategy_dir(self, strategy_name: str) -> Path:
        normalized = self.normalize_strategy_name(strategy_name)
        path = self._strategies_dir / normalized
        if path.exists() and path.is_dir() and path.joinpath("base.md").is_file():
            return path
        fallback = self._strategies_dir / "defect"
        return fallback

    def _read_strategy_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            self.logger.warning(f"读取策略文档失败 {path}: {exc}")
            return ""

    def _parse_sections(self, prompt: str) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for raw_line in prompt.splitlines():
            match = re.match(r"^##\s+(.+?)\s*$", raw_line)
            if match:
                current = {"title": match.group(1).strip(), "body_lines": []}
                sections.append(current)
                continue
            if current is not None:
                current["body_lines"].append(raw_line)
        return sections

    def _parse_constraints(self, prompt: str) -> dict[str, Any]:
        frontmatter = self._parse_frontmatter(prompt)
        if frontmatter is not None:
            return frontmatter
        return self._empty_constraints()

    def _parse_frontmatter(self, prompt: str) -> dict[str, Any] | None:
        text = (prompt or "").lstrip("﻿")
        if not text.startswith("---"):
            return None
        match = re.match(r"^---\s*\r?\n([\s\S]*?)\r?\n---\s*(?:\r?\n|$)", text)
        if not match:
            return None
        try:
            data = yaml.safe_load(match.group(1)) or {}
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        constraints = data.get("constraints")
        return dict(constraints) if isinstance(constraints, dict) else None

    def _empty_constraints(self) -> dict[str, Any]:
        return {
            "required": {},
            "high_priority": {},
            "conditional": {},
            "low_priority": {},
            "map_preferences": {},
            "route_policy": {},
            "combat_preferences": {},
            "combat_estimators": {},
            "shop_preferences": {},
        }

    def _merge_constraints(self, base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in extra.items():
            existing = merged.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                nested = dict(existing)
                nested.update(value)
                merged[key] = nested
            else:
                merged[key] = value
        return merged


__all__ = ["STS2StrategyParser"]
